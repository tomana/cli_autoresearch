# cli_autoresearch

Tiny single-file Python driver to **loop a coding-agent CLI on a
brief for a wall-clock budget**. Picks one of claude / codex / agy,
calls it until `--minutes` expires or you Ctrl+C. Detects claude
rate-limits + codex credit depletion mid-loop and recovers
(wait-for-reset / tier-degrade) instead of stopping.

stdlib only. Whatever the agent reads / writes / scores is your
`program.md`'s business — the driver just hands it the prompt and
streams output.

## Usage

```bash
git clone https://github.com/tomana/cli_autoresearch.git
cd cli_autoresearch

uv run run.py program.md                          # claude, 30 min
uv run run.py --agent codex --minutes 60 program.md
uv run run.py --agent agy program.md
uv run run.py --cwd /path/to/your/project program.md
```

## Flags

| Flag | Default | What |
|---|---|---|
| (positional) `program` | required | path to the brief the agent reads each iteration |
| `--agent claude\|codex\|agy` | `claude` | which CLI |
| `--minutes N` | `30` | wall-clock budget |
| `--cwd PATH` | `.` | agent's working directory |
| `--model ID` | (codex) flagship | pin a single codex model; disables tier fallback |

## Backends

| Agent | Default model | Tier fallback | Recovery |
|---|---|---|---|
| `claude` | `claude-opus-4-8` | none | rate-limit reset detected via stdout regex, waits + restarts budget |
| `codex` | `gpt-5.5-pro` | → `gpt-5.5` → `gpt-5.4-mini` | scans `~/.codex/sessions/rollout-*.jsonl` for `credits.has_credits=false` and degrades the active tier |
| `agy` (antigravity) | google default | none | (no built-in recovery) |

Codex fallback chain editable in `run.py` (`CODEX_MODELS = […]`).

## What gets written to disk

**`run.py` itself writes nothing.** No run dirs, no logs, no
state. It only spawns the agent and streams stdout back to your
terminal.

Files that *do* appear during a loop:

| Source | What | Where |
|---|---|---|
| The agent (per `program.md`) | Whatever your brief asks it to produce — code, manifests, score files, etc. | `--cwd` |
| codex CLI | Session rollouts the driver scans for credit depletion | `~/.codex/sessions/rollout-*.jsonl` |
| claude CLI | Nothing in `--print` mode (default) | — |
| agy CLI | Nothing in `--print` mode | — |

If you want a transcript of the loop, redirect stdout:

```bash
uv run run.py program.md > /tmp/loop.log 2>&1
```

Or `tee` it if you want both:

```bash
uv run run.py program.md 2>&1 | tee /tmp/loop.log
```

## Example

See [`example_cpp/`](example_cpp/) for a minimal C++ target with a
`CMakeLists.txt` + `main.cpp` + per-iteration `program.md`. Showcases
the `--cwd` flag.

## License

MIT.
