# Compression research — cycle log

**This is the file to read to see progress across cycles.** `LEADERBOARD.md` is a
*snapshot* — it gets overwritten every cycle with only the current best. This file
is **append-only**: one row is added per automated research cycle
(`COMPRESSION_RESEARCH_AGENT_PROMPT.md`) and never removed or rewritten. Full detail
for each cycle (hypothesis, exact commands, verifier transcripts) lives in
`experiments/NNN_slug.md`, linked from the row.

| # | cycle date | branch | candidate tried | real dataset(s) | measured ratio | vs. prior best | embedded_ok | verifier verdict | promoted? | experiment record | PR |
|--:|---|---|---|---|---|---|:--:|---|:--:|---|---|
| 1 | 2026-07-08 | `compression-cycle-2026-07-08` | LMS+Rice+xchan_adaptive | otb_hdsemg_vl | 2.13× | −0.01× (−0.48%) vs 2.14× | ✅ | PROMOTE (embeddability audit) | no — kept, not new best (Pareto-dominated; see note) | [001](../experiments/001_lms_rice_xchan_adaptive.md) | (pending) |

**Column note:** *verifier verdict* is the double-adversarial-verifier gate on
correctness/embeddability only (bit-exact round-trip, `embedded_ok`, cost audit) —
it decides whether a codec is legitimate enough to stay registered. *promoted?* is
the separate, stricter question of whether it beat the current best on real data and
became the new leaderboard headline (non-negotiable: if it doesn't beat the current
best, keep it registered but don't promote it). A codec can pass verification and
still not be promoted, as cycle 1 shows.
