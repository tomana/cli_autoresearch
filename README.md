# cli_autoresearch

Tiny single-file Python driver to **loop a coding-agent CLI on a
brief for a wall-clock budget**. Picks one of claude / codex / agy,
calls it until `--minutes` expires or you Ctrl+C. Detects claude
rate-limits + codex credit depletion mid-loop and recovers
(wait-for-reset / tier-degrade) instead of stopping.

stdlib only. No GUI, no picker, no project assumptions — whatever
the agent reads / writes / scores is your `program.md`'s business.

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

## License

MIT.
