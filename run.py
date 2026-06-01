#!/usr/bin/env python3
"""run.py — drive a CLI-agent autoresearch loop.

Picker-free, bench-driven autoresearch driver. Loops a coding-agent CLI
(claude / codex / agy) on `program.md` (or your own brief) for a
wall-clock budget. The agent reads operator feedback in the run dir
each iteration, produces work, writes its own progress files, exits.
Driver handles rate limits, codex tier degradation, and the
SIGINT-to-stop dance.

This is the chroma_rt autoresearch playbook with the picker UI bits
stripped — useful when you want the multi-agent + tier-fallback
discipline but the output is metric-driven (`results.tsv`, ONNX
checkpoints, etc.) rather than GIF-card taste votes. Drop it into
any project, write a `program.md` describing each iteration's
contract, point at it via --program.

Usage:
    # Claude (Opus default), 60 min, fresh run dir:
    uv run run.py --minutes 60

    # Codex with tier fallback (gpt-5.5-pro → gpt-5.5 → gpt-5.4-mini):
    uv run run.py --agent codex --minutes 60
    #   --model <id> overrides the chain (no fallback)

    # Agy (Antigravity / Google AI Pro):
    uv run run.py --agent antigravity --minutes 60

    # Continue an existing run dir:
    uv run run.py --run run_1781234567 --minutes 30

    # Just bootstrap the run dir, don't loop:
    uv run run.py --init-only

    # Point at a different brief:
    uv run run.py --program ./agents/my_bench/program.md --minutes 45

stdlib only — no pip deps.
"""

import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DEFAULT_PROGRAM = HERE / "program.md"

# Nested-agent invocations inherit these and refuse to start; drop them.
for var in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_VERSION",
            "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"):
    os.environ.pop(var, None)

AGENT = "claude"
CLAUDE_MODEL = "claude-opus-4-8"   # latest Opus
AGENT_CMD = ["claude", "--print", "--model", CLAUDE_MODEL]
# Codex tier-fallback: start at the flagship; on credit depletion mid-loop,
# degrade to the next tier instead of exiting. Stops only when the LAST
# tier depletes.
CODEX_MODELS = ["gpt-5.5-pro", "gpt-5.5", "gpt-5.4-mini"]
_CODEX_MODEL_IDX = 0


def codex_current_model() -> str:
    return CODEX_MODELS[_CODEX_MODEL_IDX]


_active_proc = None
_interrupt_count = 0


class Interrupted(Exception):
    pass


def _handle_sigint(signum, frame):
    global _interrupt_count, _active_proc
    _interrupt_count += 1
    if _interrupt_count >= 2:
        print("\n\n  Force exit.")
        sys.exit(1)
    print("\n\n  Ctrl+C — stopping... (press again to force exit)")
    if _active_proc and _active_proc.poll() is None:
        _active_proc.terminate()
        try:
            _active_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _active_proc.kill()
            _active_proc.wait()
    raise Interrupted()


signal.signal(signal.SIGINT, _handle_sigint)


# ---- Run-dir bootstrap ----

def bootstrap_run(runs_dir: Path, run_id: str | None) -> str:
    """Create (or reuse) a run dir at <runs_dir>/<run_id>/.

    Layout:
        runs/run_<unix_ts>/
            iterations/        # agent writes its output here
            refs/              # operator-supplied reference data
            votes.jsonl        # operator +/- feedback (optional)
            comments.jsonl     # free-form operator notes
            seeds.jsonl        # operator-supplied pending work items

    The agent reads votes/comments/seeds before deciding what to do.
    Fresh ids are `run_<unix_ts>` so newest sorts last alphabetically
    (and first if you reverse-sort).
    """
    if run_id is None:
        run_id = f"run_{int(time.time())}"
    rp = runs_dir / run_id
    for sub in ("iterations", "refs"):
        (rp / sub).mkdir(parents=True, exist_ok=True)
    for jl in ("votes.jsonl", "comments.jsonl", "seeds.jsonl"):
        (rp / jl).touch(exist_ok=True)
    return run_id


# ---- Rate-limit handling (claude-CLI heuristic) ----

def parse_rate_limit(output: str):
    """Return a reset datetime if `output` shows a claude rate-limit
    notice, else None. Match phrasing: 'hit your limit ... resets 3pm
    (America/Los_Angeles)'."""
    match = re.search(
        r"hit your limit.*resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)",
        output, re.IGNORECASE,
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    ampm = match.group(3).lower()
    tz_name = match.group(4)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None
    now = datetime.now(tz)
    reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return reset


def wait_for_rate_limit(reset_time):
    global _interrupt_count
    _interrupt_count = 0
    now = datetime.now(reset_time.tzinfo)
    wait_secs = (reset_time - now).total_seconds() + 60
    if wait_secs <= 0:
        return
    print(f"\n    Rate limit hit. Resets at {reset_time.strftime('%H:%M %Z')}")
    print(f"    Waiting {int(wait_secs // 60)} minutes... (Ctrl+C to skip)")
    while wait_secs > 0:
        mins, secs = int(wait_secs // 60), int(wait_secs % 60)
        print(f"\r    {mins:02d}:{secs:02d} remaining", end="", flush=True)
        time.sleep(min(30, wait_secs))
        now = datetime.now(reset_time.tzinfo)
        wait_secs = (reset_time - now).total_seconds() + 60
    print("\r    Rate limit reset. Resuming...                          ")


# ---- Agent invocation ----

def _build_agent_command(prompt: str, cwd: Path):
    if AGENT == "claude":
        return AGENT_CMD + ["--output-format", "stream-json", "--verbose", "-p", prompt]
    if AGENT == "codex":
        # codex exec: non-interactive; -m picks the model; -C pins the
        # working dir. FULL access (bypass sandbox+approvals) is REQUIRED
        # for most real autoresearch loops — workspace-write blocks GPU,
        # network, etc. Matches claude --dangerously-skip-permissions /
        # agy --dangerously-skip-permissions in scope.
        return AGENT_CMD + [
            "exec",
            "-m", codex_current_model(),
            "-C", str(cwd),
            "--dangerously-bypass-approvals-and-sandbox",
            prompt,
        ]
    if AGENT == "antigravity":
        # agy (Antigravity CLI — Google's successor to gemini CLI per
        # their 2026-05-19 announcement; gemini CLI sunset 2026-06-18).
        return AGENT_CMD + [
            "--dangerously-skip-permissions",
            "--add-dir", str(cwd),
            "--print", prompt,
        ]
    raise ValueError(f"unsupported agent: {AGENT}")


def invoke_agent(prompt: str, cwd: Path, logfile: Path, deadline: float | None):
    """Run the agent once; stream output; return (text, rate_limit_reset)."""
    global _active_proc, _interrupt_count
    _interrupt_count = 0
    cmd = _build_agent_command(prompt, cwd)
    full = []

    with open(logfile, "a") as log:
        log.write(f"\n{'='*60}\nTimestamp: {datetime.now().isoformat()}\n\n")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
            stdin=subprocess.DEVNULL,
        )
        _active_proc = proc
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        try:
            while proc.poll() is None:
                if deadline and time.time() > deadline:
                    print("\n  Time limit reached. Stopping session...")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
                    break
                for key, _ in sel.select(timeout=5):
                    raw = key.fileobj.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    full.append(line)
                    if AGENT != "claude":
                        print(f"  {line}"); log.write(line + "\n"); continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        print(f"  {line}"); log.write(line + "\n"); continue
                    if event.get("type") == "assistant":
                        for block in event.get("message", {}).get("content", []):
                            if block.get("type") == "text":
                                sys.stdout.write(block["text"]); sys.stdout.flush()
                                log.write(block["text"])
                            elif block.get("type") == "tool_use":
                                tool = block.get("name", "?")
                                inp = block.get("input", {})
                                if tool == "Bash":
                                    desc = inp.get("command", "")[:80]
                                elif tool in ("Edit", "Write", "Read"):
                                    desc = inp.get("file_path", "")[-50:]
                                else:
                                    desc = str(inp)[:60]
                                print(f"\n  >> [{tool}] {desc}", flush=True)
                                log.write(f"\n[tool: {tool}] {desc}\n")
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    full.append(line)
            if proc.poll() is None:
                proc.wait(timeout=5)
        finally:
            sel.close()
            _active_proc = None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    text = "\n".join(full)
    return text, parse_rate_limit(text)


def sanity_check(repo_root: Path):
    print("=== Sanity checks ===")
    print(f"  [1/2] {AGENT} binary...", end=" ", flush=True)
    try:
        r = subprocess.run([AGENT_CMD[0], "--version"], capture_output=True,
                           text=True, timeout=10)
        print(f"OK ({r.stdout.strip()})")
    except FileNotFoundError:
        print(f"FAIL — {AGENT_CMD[0]} not in PATH"); sys.exit(1)

    if AGENT == "claude":
        print("  [2/2] --dangerously-skip-permissions...", end=" ", flush=True)
        r = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "-p", "reply with just OK"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            print("OK"); AGENT_CMD.append("--dangerously-skip-permissions")
        else:
            print("SKIP (will require manual approval)")
    elif AGENT == "codex":
        global _CODEX_MODEL_IDX
        while _CODEX_MODEL_IDX < len(CODEX_MODELS):
            m = codex_current_model()
            print(f"  [2/2] codex exec -m {m}...", end=" ", flush=True)
            r = subprocess.run(
                ["codex", "exec", "-m", m, "-C", str(repo_root),
                 "--dangerously-bypass-approvals-and-sandbox", "reply with just OK"],
                capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and r.stdout.strip():
                print("OK")
                break
            depleted, _ = codex_is_depleted()
            if depleted and _CODEX_MODEL_IDX + 1 < len(CODEX_MODELS):
                _CODEX_MODEL_IDX += 1
                print(f"DEPLETED → degrading to {codex_current_model()}")
                continue
            print("FAIL"); sys.stderr.write(r.stdout); sys.stderr.write(r.stderr)
            sys.exit(1)
    else:  # antigravity
        print("  [2/2] agy --dangerously-skip-permissions --print...", end=" ", flush=True)
        r = subprocess.run(
            ["agy", "--dangerously-skip-permissions",
             "--add-dir", str(repo_root), "--print", "reply with just OK"],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        if r.returncode == 0 and r.stdout.strip():
            print("OK")
        else:
            print("FAIL"); sys.stderr.write(r.stdout); sys.stderr.write(r.stderr)
            sys.exit(1)
    print(f"  Agent command: {' '.join(AGENT_CMD)}\n")


# ---- Loop ----

def build_prompt(program_path: Path, run_dir: Path, repo_root: Path,
                 start: bool) -> str:
    """Compose the per-iteration prompt the agent receives. Tells it where
    to find the brief + the active run dir, and tags trace fields so any
    artefacts the agent writes record who produced them."""
    rel_prog = program_path.relative_to(repo_root) if program_path.is_relative_to(repo_root) \
                                                    else program_path
    rel_run = run_dir.relative_to(repo_root) if run_dir.is_relative_to(repo_root) \
                                              else run_dir
    verb = "start" if start else "continue"
    model_hint = {
        "claude":      CLAUDE_MODEL,
        "codex":       codex_current_model(),
        "antigravity": "gemini-3.1-pro-high",
    }.get(AGENT, "unknown")
    return (
        f"Read {rel_prog} and {verb} this autoresearch loop. The active "
        f"run dir is {rel_run}/ — read the operator's feedback there "
        f"(votes.jsonl, comments.jsonl, seeds.jsonl, refs/) and any "
        f"existing iterations/ before proposing new work. Follow the "
        f"contract in {rel_prog} for what each iteration produces."
        f"\n\nIMPORTANT — TRACE FIELDS: every JSON/manifest you write this "
        f"session MUST include the fields  \"agent\": \"{AGENT}\"  and  "
        f"\"model\": \"{model_hint}\"  so operator can attribute artefacts "
        f"to who produced them. If you know the actual model id more "
        f"precisely than the hint above, use that instead."
    )


_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


def codex_is_depleted() -> tuple[bool, str]:
    """Scan codex's most-recent session rollouts for the rate_limits.credits
    payload. Returns (depleted?, reason). depleted == True when has_credits=
    false AND balance='0' — same heuristic chroma_rt's picker uses for its
    'depleted' UI state. Looks only at very-recent files so stale states
    don't pin us forever."""
    if not _CODEX_SESSIONS_DIR.is_dir():
        return False, ""
    cutoff = time.time() - 120
    files = sorted(_CODEX_SESSIONS_DIR.rglob("rollout-*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    latest_rl = None
    latest_ts = 0.0
    for f in files:
        if f.stat().st_mtime < cutoff:
            break
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for line in reversed(text.splitlines()):
            if '"rate_limits"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rl = rec.get("payload", {}).get("rate_limits")
            if not rl:
                continue
            ts = f.stat().st_mtime
            if ts > latest_ts:
                latest_ts = ts
                latest_rl = rl
            break
    if not latest_rl:
        return False, ""
    credits = latest_rl.get("credits") or {}
    if (credits.get("has_credits") is False
            and str(credits.get("balance", "")).strip() == "0"):
        return True, (f"codex reports no credits available "
                      f"(has_credits=false, balance='{credits.get('balance')}')")
    return False, ""


def run_loop(run_dir: Path, program: Path, repo_root: Path, minutes: int):
    log_dir = HERE / "logs"
    log_dir.mkdir(exist_ok=True)
    logfile = log_dir / f"run_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    print(f"\n{'='*60}")
    print(f"  cli_autoresearch")
    print(f"  run dir:  {run_dir}")
    print(f"  program:  {program}")
    print(f"  agent:    {AGENT}")
    print(f"  budget:   {minutes} min")
    print(f"  log:      {logfile}")
    print(f"{'='*60}\n")

    start_time = time.time()
    end_time = start_time + minutes * 60
    prompt = build_prompt(program, run_dir, repo_root, start=True)
    iteration = 0

    while time.time() < end_time:
        iteration += 1
        elapsed = (time.time() - start_time) / 60
        print(f"\n--- Iteration {iteration} | {elapsed:.0f}/{minutes} min ---\n")
        try:
            _, reset_time = invoke_agent(prompt, repo_root, logfile, deadline=end_time)
            if reset_time:
                wait_for_rate_limit(reset_time)
                end_time = time.time() + minutes * 60   # restart budget after wait
            if AGENT == "codex":
                depleted, reason = codex_is_depleted()
                if depleted:
                    global _CODEX_MODEL_IDX
                    if _CODEX_MODEL_IDX + 1 < len(CODEX_MODELS):
                        _CODEX_MODEL_IDX += 1
                        nxt = codex_current_model()
                        print(f"\n  Tier depleted ({reason}); "
                              f"degrading to {nxt} and continuing.")
                    else:
                        print(f"\n  All codex tiers depleted "
                              f"({CODEX_MODELS}); stopping.")
                        return
            prompt = build_prompt(program, run_dir, repo_root, start=False)
        except Interrupted:
            print("\n  Interrupted.")
            return
    print(f"\n  Done: {iteration} iterations in {(time.time()-start_time)/60:.1f} min")


# ---- Main ----

def main():
    global AGENT, AGENT_CMD, _CODEX_MODEL_IDX
    minutes = 30
    run_id = None
    init_only = False
    program_path: Path = DEFAULT_PROGRAM
    runs_dir: Path = HERE / "runs"
    repo_root: Path = HERE

    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--minutes" and i + 1 < len(sys.argv):
            minutes = int(sys.argv[i + 1]); i += 2
        elif a == "--agent" and i + 1 < len(sys.argv):
            AGENT = sys.argv[i + 1]
            if AGENT == "claude":
                AGENT_CMD = ["claude", "--print", "--model", CLAUDE_MODEL]
            elif AGENT == "codex":
                AGENT_CMD = ["codex"]
            elif AGENT in ("antigravity", "agy"):
                AGENT = "antigravity"
                AGENT_CMD = ["agy"]
            else:
                print(f"Unknown agent: {AGENT}. Expected claude, codex, "
                      f"or antigravity."); sys.exit(1)
            i += 2
        elif a == "--model" and i + 1 < len(sys.argv):
            CODEX_MODELS.clear()
            CODEX_MODELS.append(sys.argv[i + 1])
            _CODEX_MODEL_IDX = 0
            i += 2
        elif a == "--run" and i + 1 < len(sys.argv):
            run_id = sys.argv[i + 1]; i += 2
        elif a == "--program" and i + 1 < len(sys.argv):
            program_path = Path(sys.argv[i + 1]).resolve(); i += 2
        elif a == "--runs-dir" and i + 1 < len(sys.argv):
            runs_dir = Path(sys.argv[i + 1]).resolve(); i += 2
        elif a == "--cwd" and i + 1 < len(sys.argv):
            repo_root = Path(sys.argv[i + 1]).resolve(); i += 2
        elif a == "--init-only":
            init_only = True; i += 1
        else:
            print(f"Unknown arg: {a}"); sys.exit(1)

    if not program_path.is_file():
        print(f"program.md not found at {program_path}")
        print(f"Pass --program <path> to point at your own brief, or place "
              f"one at {DEFAULT_PROGRAM}.")
        sys.exit(1)

    run_id = bootstrap_run(runs_dir, run_id)
    run_dir = runs_dir / run_id
    print(f"Run dir: {run_dir}")

    if init_only:
        print(f"\n--init-only: run dir ready. Drive the agent yourself, or:")
        print(f"  uv run {Path(__file__).name} --run {run_id} --minutes 30")
        return

    sanity_check(repo_root)
    run_loop(run_dir, program_path, repo_root, minutes)


if __name__ == "__main__":
    main()
