#!/usr/bin/env python3
"""run.py — loop a coding-agent CLI on a brief, within a wall-clock budget.

Single-file driver. Picks one of claude / codex / agy / copilot,
calls it repeatedly until --minutes expires or you Ctrl+C. Detects
claude rate-limits + codex credit depletion mid-loop and either
waits or degrades to a cheaper codex tier instead of stopping.

Whatever the agent reads / writes / scores is your project's
business — the driver just hands it the prompt and harvests output.

Usage:
    uv run run.py program.md                            # claude, 30 min
    uv run run.py --agent codex --minutes 60 program.md
    uv run run.py --agent agy program.md
    uv run run.py --agent copilot program.md
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Strip env vars that prevent nested coding-agent invocations.
for v in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
          "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"):
    os.environ.pop(v, None)

CLAUDE_MODEL  = "claude-opus-4-8"
CODEX_MODELS  = ["gpt-5.5-pro", "gpt-5.5", "gpt-5.4-mini"]   # tier fallback
_codex_idx    = 0

_active_proc, _interrupts = None, 0


class Interrupted(Exception):
    pass


def _sigint(*_):
    global _interrupts, _active_proc
    _interrupts += 1
    if _interrupts >= 2:
        sys.exit(1)
    print("\n  Ctrl+C — stopping (press again to force).", flush=True)
    if _active_proc and _active_proc.poll() is None:
        _active_proc.terminate()
        try: _active_proc.wait(timeout=5)
        except subprocess.TimeoutExpired: _active_proc.kill()
    raise Interrupted()


signal.signal(signal.SIGINT, _sigint)


def build_cmd(agent: str, prompt: str, cwd: Path) -> list[str]:
    if agent == "claude":
        return ["claude", "--print", "--model", CLAUDE_MODEL,
                "--dangerously-skip-permissions", "-p", prompt]
    if agent == "codex":
        return ["codex", "exec", "-m", CODEX_MODELS[_codex_idx],
                "-C", str(cwd),
                "--dangerously-bypass-approvals-and-sandbox", prompt]
    if agent == "antigravity":
        return ["agy", "--dangerously-skip-permissions",
                "--add-dir", str(cwd), "--print", prompt]
    if agent == "copilot":
        # GitHub Copilot CLI. --allow-all-tools is the equivalent of
        # claude --dangerously-skip-permissions / codex bypass /
        # agy --dangerously-skip-permissions. --no-color keeps stdout
        # free of ANSI escape codes so the streamed output is parseable.
        return ["copilot", "-p", prompt, "--allow-all-tools",
                "--add-dir", str(cwd), "--no-color"]
    raise ValueError(f"unknown agent: {agent}")


# Claude rate-limit notice — "...resets 3pm (America/Los_Angeles)".
_RL_RE = re.compile(
    r"hit your limit.*resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def parse_rate_limit(text: str):
    m = _RL_RE.search(text)
    if not m: return None
    h, mn, ampm, tz_name = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower(), m.group(4)
    if ampm == "pm" and h != 12: h += 12
    elif ampm == "am" and h == 12: h = 0
    try: tz = ZoneInfo(tz_name)
    except Exception: tz = None
    now = datetime.now(tz)
    reset = now.replace(hour=h, minute=mn, second=0, microsecond=0)
    if reset <= now: reset += timedelta(days=1)
    return reset


def wait_for_reset(reset):
    global _interrupts
    _interrupts = 0
    while True:
        now = datetime.now(reset.tzinfo)
        secs = (reset - now).total_seconds() + 60
        if secs <= 0:
            print("\r  Rate limit reset. Resuming.                ", flush=True); return
        print(f"\r  Rate limit; resumes in {int(secs // 60):02d}:{int(secs % 60):02d}",
              end="", flush=True)
        time.sleep(min(30, secs))


# Detect "codex credits depleted" via the most-recent session rollout file
# (~/.codex/sessions/rollout-*.jsonl). Scoped to the last 2 minutes so a
# stale depleted reading doesn't pin us forever.
_CODEX_SESSIONS = Path.home() / ".codex" / "sessions"


def codex_depleted() -> bool:
    if not _CODEX_SESSIONS.is_dir(): return False
    cutoff = time.time() - 120
    files = sorted(_CODEX_SESSIONS.rglob("rollout-*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    for f in files:
        if f.stat().st_mtime < cutoff: break
        for line in reversed(f.read_text(errors="ignore").splitlines()):
            if '"rate_limits"' not in line: continue
            try: rec = json.loads(line)
            except json.JSONDecodeError: continue
            cr = (rec.get("payload", {}).get("rate_limits") or {}).get("credits") or {}
            if cr.get("has_credits") is False and str(cr.get("balance", "")).strip() == "0":
                return True
            break
    return False


def invoke_agent(agent: str, prompt: str, cwd: Path, deadline: float):
    """Run the agent once; stream stdout to ours; return collected text."""
    global _active_proc
    cmd = build_cmd(agent, prompt, cwd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=cwd, stdin=subprocess.DEVNULL,
                            bufsize=1, text=True)
    _active_proc = proc
    chunks = []
    try:
        while True:
            if time.time() > deadline:
                print("\n  Time's up. Terminating agent.", flush=True)
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill()
                break
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None: break
                continue
            chunks.append(line)
            sys.stdout.write(line); sys.stdout.flush()
    finally:
        _active_proc = None
    return "".join(chunks)


def loop(agent: str, program: Path, cwd: Path, minutes: int):
    global _codex_idx
    end = time.time() + minutes * 60
    it = 0
    while time.time() < end:
        it += 1
        elapsed = (time.time() - (end - minutes * 60)) / 60
        print(f"\n=== iter {it} | {elapsed:.0f}/{minutes} min "
              f"| {agent}{f':{CODEX_MODELS[_codex_idx]}' if agent == 'codex' else ''} ===\n",
              flush=True)
        verb = "Start" if it == 1 else "Continue"
        prompt = (f"{verb} the autoresearch loop. Read {program.relative_to(cwd) if program.is_relative_to(cwd) else program} "
                  f"and follow its contract for this iteration.")
        try:
            text = invoke_agent(agent, prompt, cwd, deadline=end)
        except Interrupted:
            return
        reset = parse_rate_limit(text)
        if reset:
            wait_for_reset(reset)
            end = time.time() + minutes * 60   # restart budget post-wait
            continue
        if agent == "codex" and codex_depleted():
            if _codex_idx + 1 < len(CODEX_MODELS):
                _codex_idx += 1
                print(f"\n  Codex tier depleted; degrading to "
                      f"{CODEX_MODELS[_codex_idx]}.", flush=True)
            else:
                print("\n  All codex tiers depleted. Stopping.", flush=True)
                return
    print(f"\n  Done. {it} iterations in {minutes} min budget.", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("program", help="Path to the agent's brief (program.md).")
    p.add_argument("--agent",
                   choices=["claude", "codex", "antigravity", "agy", "copilot"],
                   default="claude")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--cwd", default=".", help="Agent's working directory.")
    p.add_argument("--model", help="Pin a single codex model (disables tier fallback).")
    args = p.parse_args()

    agent = "antigravity" if args.agent == "agy" else args.agent
    if args.model:
        CODEX_MODELS.clear(); CODEX_MODELS.append(args.model)

    program = Path(args.program).resolve()
    cwd     = Path(args.cwd).resolve()
    if not program.is_file():
        sys.exit(f"program file not found: {program}")
    if not cwd.is_dir():
        sys.exit(f"cwd not a directory: {cwd}")

    print(f"loop: agent={agent}  budget={args.minutes}m  cwd={cwd}  program={program}")
    loop(agent, program, cwd, args.minutes)


if __name__ == "__main__":
    main()
