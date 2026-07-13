# `experiments/` — per-cycle experiment records

One file per automated research cycle: `NNN_slug.md`, numbered sequentially
(`001_`, `002_`, ...). Each record is written once the cycle's verifier phase has
returned a verdict and is never edited afterward — it is the permanent record of
what was tried, measured, and decided that cycle, even for negative results.

Each record should contain:

- **Hypothesis** — the one testable claim this cycle tested.
- **Implementation** — what was added to `research/registry.py`, and the exact
  self-test command + output proving bit-exactness.
- **Measurement** — the exact `research/bench.py` (and `research/search.py`, if run)
  command(s) and the real numbers they printed (never a number from reasoning).
- **Verification** — both independent verifier verdicts (PROMOTE/REJECT) with their
  reproduced evidence, and the combined outcome (promote only on double-PROMOTE).
- **Outcome** — promoted / kept-but-not-promoted / reverted, and why.

See `../research/CYCLE_LOG.md` for the one-line-per-cycle index across all records,
and `../research/LEADERBOARD.md` for the current best-known state (overwritten each
cycle, not a history).
