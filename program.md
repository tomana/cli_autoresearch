# Agent brief (example)

> Replace this file with your own. The driver reads its path from
> the positional `program` arg and tells the agent to read it +
> follow its contract each iteration. The contract is yours to
> define — `cli_autoresearch` makes no assumptions about what
> "an iteration" produces.

## Example contract

Each iteration:

1. Read any operator notes (e.g. `notes.md`, `votes.jsonl`, …) —
   you define where.
2. Pick ONE direction to work for ~5–15 minutes.
3. Produce a candidate (a `.py` file, a `.frag` shader, a
   `results.tsv` row, an ONNX checkpoint — whatever fits your
   project).
4. Score / log it so the next iteration knows what's been tried.
5. Exit. Driver loops back.

Optional discipline a project might enforce:

- Every artefact tagged with `"agent"` + `"model"` fields so
  multi-agent loops are attributable.
- A `STARTING_COMMIT` file + `prepare.sh` setup script + per-iter
  `results.tsv` row for full reproducibility (chroma_reproj
  pattern).
- Stop gracefully and write a "blocked" note rather than spinning
  if the bench harness or input data isn't ready yet.
