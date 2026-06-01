# Agent operating manual — example (bench-driven loop)

> Replace this file with your own brief. This example shows the
> pattern: bench-driven autoresearch that optimises a numeric metric.
> See `README.md` §"Writing program.md" for the full contract.

You are working in this repo. Your job is to iterate on **<the thing
being optimised>**: each iteration produces a new candidate, scores
it, writes the result.

## What you optimise

**<Insert your bench metric here.>** Examples:

- `masked_rmse_mm` — lower is better (depth prediction)
- `silhouette_iou` — higher is better
- `latency_ms` — lower is better (perf optimisation)
- All of the above with a weighted sum

Operator votes in `votes.jsonl` are a **tie-breaker** when two
candidates score identically — not the primary signal.

## Each iteration

1. **Read** the operator feedback in the active run dir:
   - `votes.jsonl` — `+`/`−` on existing candidates (latest-wins,
     keyed by `id`)
   - `comments.jsonl` — free-form operator notes
   - `seeds.jsonl` — pending work items the operator wants you to
     fulfil first (highest priority)
   - `refs/` — reference data (images, docs, ground-truth files)
2. **Read** existing `iterations/<id>/manifest.json` files so you
   don't re-implement something already tried. Skim the metric
   summaries to find the highest-scoring family + variant.
3. **Pick ONE direction** to work for ~5–15 minutes (dwell-and-jump).
   Either:
   - Fulfil a pending seed (top priority — operator asked for it)
   - Iterate on a high-scoring existing candidate (variant)
   - Propose + implement a new approach from a fresh family
4. **Implement** the candidate. Write its code / config / weights
   wherever your project conventions dictate. Examples:
   - `iterations/<id>/predictor.py` (Python)
   - `iterations/<id>/shader.frag` (GLSL)
   - `iterations/<id>/model.onnx` (ML checkpoint)
5. **Score** it. Run your bench harness against the candidate,
   capture the metrics, write them as a row of `results.tsv` AND a
   per-candidate manifest:

   ```json
   {
     "id":          "<short id>",
     "agent":       "<see prompt>",
     "model":       "<see prompt>",
     "technique":   "linear | kalman | unet | …",
     "description": "one-line idea",
     "rationale":   "why this might work",
     "lineage":     ["parent-id-if-any"],
     "metrics": {
       "<your_metric>": <number>,
       "…":            <number>
     }
   }
   ```

   Include `agent` + `model` fields exactly as told in the driver's
   prompt — they're trace fields the operator uses for attribution.
6. **Exit.** The driver loops back automatically.

## Constraints

- **Deterministic.** Same inputs → same outputs, byte-identical.
  Bench results need reproducibility.
- **No heavyweight retraining inside the loop.** A candidate that
  needs a 30-min training session is fine, but train it ONCE (write
  a `prepare_<id>.py` / `train_<id>.py`) then save the checkpoint to
  `iterations/<id>/weights/` for the bench to load. Don't re-train
  every bench invocation.
- **Pre-existing baselines stay.** Identity / linear / "naive"
  baselines are sacred — every new candidate gets scored against
  them, so they can't be deleted or replaced.

## If the bench harness isn't ready

If your project's bench script isn't implemented yet (Phase 0/1
incomplete) or the inputs aren't available (no recordings, no
ground-truth data), **stop**. Write a single comment in
`comments.jsonl`:

```jsonl
{"ts": "<iso>", "by": "agent", "kind": "blocked", "msg": "<reason>"}
```

Then exit. Do NOT spin generating candidates against no fitness
signal — they won't score, they just clutter the run dir + burn
agent quota.

## What "dwell-and-jump" means

Don't grind on one variant for an hour. Spend 5–15 min on a
direction, get a score, move to a different family. After ~5
families have at least one candidate each, come back and refine the
most promising one. This is breadth-first search for the candidate
space — depth comes later via operator-led shortlist.
