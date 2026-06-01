# cli_autoresearch

A minimal, picker-free driver for **multi-agent autoresearch loops** —
loops a coding-agent CLI (`claude` / `codex` / `agy`) on a brief you
write (`program.md`), within a wall-clock budget. The agent reads
operator feedback in the run dir each iteration, produces work,
writes its own progress files, exits. Driver handles rate limits,
codex tier degradation, and the SIGINT-to-stop dance.

Single Python file (`run.py`), stdlib only — no pip deps. Drop it
into any project that wants the autoresearch discipline without a
custom GUI / picker.

---

## Why this exists

Most coding-agent autoresearch setups need:

- A way to **invoke an agent CLI** repeatedly inside a budget
- **Rate-limit recovery** that doesn't lose your loop
- **Tier-fallback** for paid plans (`gpt-5.5-pro → gpt-5.5 → gpt-5.4-mini`)
  so a depleted credit wallet downgrades instead of stopping
- **Trace fields** in every artefact (`agent`, `model`) so the operator
  can attribute outputs to who produced them
- A clean **operator-feedback contract** (votes / comments / seeds) the
  agent reads each iteration

These are all in `run.py`. What's deliberately NOT here:

- No GUI / picker / web server
- No project-specific scoring or rendering logic
- No assumptions about what an "iteration" produces (`.frag` shaders?
  ONNX models? a `results.tsv` row? a Markdown report? — your call,
  documented in your `program.md`)

This is the **bench-driven / CLI-only** sibling of the picker-coupled
autoresearch loops in [chroma_rt](https://github.com/tomana/chroma_rt)
(GIF-card taste votes) and [chroma_reproj](https://github.com/tomana/chroma_reproj)
(bench-metric optimisation). Use those if you want the full operator
loop; use `cli_autoresearch` if you just want the agent-driving core.

---

## Quick start

```bash
git clone https://github.com/tomana/cli_autoresearch.git
cd cli_autoresearch

# Edit program.md to describe your iteration contract (what each
# iteration reads, writes, scores).

# Claude (Opus 4.8 default), 60 min, fresh run dir:
uv run run.py --minutes 60

# Codex with tier-fallback:
uv run run.py --agent codex --minutes 60

# Agy (Antigravity, Google AI Pro):
uv run run.py --agent antigravity --minutes 60

# Continue an existing run:
uv run run.py --run run_1781234567 --minutes 30

# Just bootstrap the run dir (drive the agent in chat yourself):
uv run run.py --init-only
```

CLI flags:

| Flag | Default | What |
|---|---|---|
| `--minutes N` | `30` | wall-clock budget |
| `--agent claude\|codex\|antigravity` | `claude` | which CLI |
| `--model <id>` | (codex only) flagship | pin a single codex model; disables fallback chain |
| `--run <id>` | fresh `run_<ts>` | reuse existing run dir |
| `--program <path>` | `./program.md` | brief the agent reads each iteration |
| `--runs-dir <path>` | `./runs/` | where run dirs are bootstrapped |
| `--cwd <path>` | this dir | agent's working directory + path to inject as `--add-dir` |
| `--init-only` | off | scaffold the run dir + exit (no looping) |

---

## Backends

All three CLIs are mature autoresearch backends. They differ in cost
model + interactive behaviour:

| Agent | Default model | Tier fallback | Cost model | Notes |
|---|---|---|---|---|
| `claude` | `claude-opus-4-8` | none (single tier) | burns Claude's 5h-window + weekly limits | Highest signal-to-noise per token in this author's experience |
| `codex` | `gpt-5.5-pro` | → `gpt-5.5` → `gpt-5.4-mini` on credit depletion | bills against credits wallet, then ChatGPT plan minutes | Reads codex's session rollouts to detect depletion; auto-degrades mid-loop |
| `antigravity` (`agy`) | Google's auto-pick (gemini 3.1 pro typical) | none | free until quota | Successor to gemini CLI (sunset 2026-06-18) |

The fallback chain is configurable in `run.py` (`CODEX_MODELS = [...]`).
Pass `--model <id>` to override.

---

## Run-dir layout

`run.py` creates this structure:

```
runs/run_<unix_ts>/
├── iterations/        # agent writes per-iteration output here
├── refs/              # operator drops in reference data (images, docs, …)
├── votes.jsonl        # operator +/- feedback (optional)
├── comments.jsonl     # free-form operator notes
└── seeds.jsonl        # operator-supplied pending work items (priority queue)
```

Plus a session log at `./logs/run_<date>-<time>.log`.

The agent reads `votes.jsonl`, `comments.jsonl`, `seeds.jsonl`, and
the `refs/` dir at the start of every iteration. **What it WRITES
into `iterations/` is entirely up to your `program.md`** — JSON
manifests, code files, results tables, etc.

`votes.jsonl` / `comments.jsonl` are JSONL with `latest-wins`
semantics keyed by some operator-defined id. The driver doesn't
parse them — your `program.md` describes the schema.

---

## Writing `program.md`

This is the agent's operating manual. Read each iteration. Should
cover:

1. **What the agent is trying to optimise** — bench metric? aesthetic?
   passing a test?
2. **What it reads** — which feedback files matter; where reference
   data lives; which existing artefacts to look at before proposing
   new ones
3. **What it writes** — exact file paths, JSON schemas with required
   fields (including the trace fields the driver enforces:
   `"agent": ...` and `"model": ...`)
4. **The dwell-and-jump cadence** — work one idea for ~5-15 min then
   switch direction, so each iteration explores breadth
5. **Stop conditions** — when to write a `comments.jsonl` "blocked"
   entry and exit instead of spinning

Two patterns to mirror, depending on your loop type:

- **Bench-driven** (numeric metric is the signal): each iteration
  writes a new row to `results.tsv` + a checkpoint in `iterations/`.
  Inspired by [chroma_reproj's `autoresearch_implements_47_48_49`](https://github.com/tomana/chroma_reproj/tree/main/agents/autoresearch_implements_47_48_49) —
  also includes a `STARTING_COMMIT` file + `prepare.sh` for full
  reproducibility per iteration.
- **Taste-driven** (operator votes): each iteration writes a
  candidate manifest the operator skims later. Mirror
  [chroma_rt's `autoresearch_3dcolormap`](https://github.com/tomana/chroma_rt/tree/main/agents/autoresearch_3dcolormap)
  schema if you want compatibility with the picker server.

---

## Rate limits + recovery

The driver listens for these conditions and recovers without losing
state:

| Condition | Detection | Action |
|---|---|---|
| Claude "limit hit, resets at HH:MM (TZ)" | regex on streamed output | Sleeps until reset + 60 s, then resumes with budget restarted |
| Codex tier credits depleted | scans `~/.codex/sessions/rollout-*.jsonl` for `rate_limits.credits` with `has_credits=false` | Walks to next tier in `CODEX_MODELS`; only stops when the last tier depletes |
| Ctrl+C | SIGINT handler | First press: gracefully terminate the active agent + raise `Interrupted`. Second press: hard exit |
| Wall-clock budget | per-iteration deadline check | Terminates the active agent, exits cleanly |

---

## License

MIT. See `LICENSE`.
