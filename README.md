# cli_autoresearch

Tiny single-file Python driver to **loop a coding-agent CLI on a
brief for a wall-clock budget**. Picks one of claude / codex / agy /
copilot, calls it until `--minutes` expires or you Ctrl+C. Detects
claude rate-limits + codex credit depletion mid-loop and recovers
(wait-for-reset / tier-degrade) instead of stopping.

stdlib only. Whatever the agent reads / writes / scores is your
`program.md`'s business — the driver just hands it the prompt and
streams output.

## ⚠ Permission model

**All four backends are invoked with their "skip permission" /
"bypass approval" flags ON by default**, so the agent can run
shell commands, edit files, install things, etc. without per-
action prompts. This is required for an unattended loop — but
means the agent can do anything within `--cwd` (and outside it,
depending on the backend) without asking.

| Backend | Flag set by `run.py` |
|---|---|
| `claude` | `--dangerously-skip-permissions` |
| `codex` | `--dangerously-bypass-approvals-and-sandbox` |
| `agy` (antigravity) | `--dangerously-skip-permissions` |
| `copilot` | `--allow-all-tools` |

Run inside a disposable working dir, a VM, or a container if the
agent's blast radius is a concern. There's no way to "softly" run
the loop today — disabling these flags causes the agent to stall
on the first tool prompt with no operator to answer it.

## Usage

```bash
git clone https://github.com/tomana/cli_autoresearch.git
cd cli_autoresearch

uv run run.py program.md                          # claude, 30 min
uv run run.py --agent codex --minutes 60 program.md
uv run run.py --agent agy program.md
uv run run.py --agent copilot program.md
uv run run.py --cwd /path/to/your/project program.md
```

## Flags

| Flag | Default | What |
|---|---|---|
| (positional) `program` | required | path to the brief the agent reads each iteration |
| `--agent claude\|codex\|agy\|copilot` | `claude` | which CLI |
| `--minutes N` | `30` | wall-clock budget |
| `--cwd PATH` | `.` | agent's working directory |
| `--model ID` | (codex) flagship | pin a single codex model; disables tier fallback |

## Backends

| Agent | Default model | Tier fallback | Recovery |
|---|---|---|---|
| `claude` | `claude-opus-4-8` | none | rate-limit reset detected via stdout regex, waits + restarts budget |
| `codex` | `gpt-5.5-pro` | → `gpt-5.5` → `gpt-5.4-mini` | scans `~/.codex/sessions/rollout-*.jsonl` for `credits.has_credits=false` and degrades the active tier |
| `agy` (antigravity) | google default | none | (no built-in recovery) |
| `copilot` | github default | none | (no built-in recovery) |

Codex fallback chain editable in `run.py` (`CODEX_MODELS = […]`).

### Authenticating the backends

Sign in once with each CLI's own login flow (they run on your
existing subscription). Docs:

- [claude](https://docs.anthropic.com/en/docs/claude-code/setup)
- [codex](https://github.com/openai/codex#authentication)
- [copilot](https://docs.github.com/en/copilot/concepts/agents/about-copilot-coding-agent)
- [agy](https://antigravity.google/docs)

For unattended `copilot` runs, set `COPILOT_GITHUB_TOKEN` to a
[fine-grained PAT](https://github.com/settings/personal-access-tokens)
with only **Copilot Requests: Read-only** (no repo access).

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
