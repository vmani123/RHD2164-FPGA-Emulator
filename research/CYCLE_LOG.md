# Compression research — cycle log

**This is the file to read to see progress across cycles.** `LEADERBOARD.md` is a
*snapshot* — it gets overwritten every cycle with only the current best. This file
is **append-only**: one row is added per automated research cycle
(`COMPRESSION_RESEARCH_AGENT_PROMPT.md`) and never removed or rewritten. Full detail
for each cycle (hypothesis, exact commands, verifier transcripts) lives in
`experiments/NNN_slug.md`, linked from the row.

| # | cycle date | branch | candidate tried | real dataset(s) | measured ratio | vs. prior best | embedded_ok | verifier verdict | promoted? | retired? | experiment record | PR |
|--:|---|---|---|---|---|---|:--:|---|:--:|:--:|---|---|
| 1 | 2026-07-08 | `compression-cycle-2026-07-08` | LMS+Rice+xchan_adaptive | otb_hdsemg_vl | 2.13× | −0.01× (−0.48%) vs 2.14× | ✅ | PROMOTE (embeddability audit) | no — kept, not new best (Pareto-dominated; see note) | **yes** (2026-07-13, conclusively dominated) | [001](../experiments/001_lms_rice_xchan_adaptive.md) | [#1](https://github.com/vmani123/RHD2164-FPGA-Emulator/pull/1) merged |
| 2 | 2026-07-10 | `compression-cycle-2026-07-10` | LMS+Rice+xchan_bestpartner | otb_hdsemg_vl | 2.15× | +0.01× (+0.40%) vs 2.14× | ✅ | PROMOTE (both verifiers, no split) | registered as a non-dominated Pareto-front entry; did not displace the port pick (`lms4s7+x6/b512`) | no — non-dominated (higher ratio AND higher cost, not dominated by the incumbent) | (none filed) | [#2](https://github.com/vmani123/RHD2164-FPGA-Emulator/pull/2) merged |
| 3 | 2026-07-13 | `compression-cycle-2026-07-13` | LMS+Rice+iklt | otb_hdsemg_vl | 2.07× | −0.17× (−7.78%) vs 2.24× | ✅ | **verifier split** (A REJECT, B PROMOTE) — held for human review; not promoted | no — does not beat best AND verifier split (held for human review) | **yes** (conclusively Pareto-dominated: iklt 2.07×/0.068 vs LMS+Rice+xchan 2.24×/0.057) | [002](../experiments/002_lms_rice_iklt.md) | TBD |

**Column note:** *verifier verdict* is the double-adversarial-verifier gate on
correctness/embeddability only (bit-exact round-trip, `embedded_ok`, cost audit) —
it decides whether a codec is legitimate enough to stay registered. *promoted?* is
the separate, stricter question of whether it beat the current best on real data and
became the new leaderboard headline (non-negotiable: if it doesn't beat the current
best, keep it registered but don't promote it). A codec can pass verification and
still not be promoted, as cycle 1 shows. *retired?* (added 2026-07-13, see
`research/registry.py`'s `Codec.retired` field) is a separate, stricter-than-"not
promoted" call: a codec is only retired once it is **conclusively Pareto-dominated**
(worse ratio AND higher cost than an already-registered codec) — a merely-marginal,
non-dominated codec like cycle 2's stays active and keeps being benchmarked, because
it's still a legitimate point on the Pareto front. Retirement excludes a codec from
`bench.py`'s default sweep and future leaderboard tables so cycles stop
re-litigating settled questions; it is never a deletion (`--include-retired`
re-checks any retired codec on demand). From this cycle onward the loop targets
2-3 genuinely distinct candidates per cycle (see
`../COMPRESSION_RESEARCH_AGENT_PROMPT.md`), not one.

**Note on cycle 2's missing row:** this row was backfilled 2026-07-13 — the
`compression-cycle-2026-07-10` branch was cut before `CYCLE_LOG.md` existed on
`compression-wip` (PR #1 added it), so cycle 2's PR never touched this file and
its row was silently absent until now. Future cycles should always pull the
latest `compression-wip` (which includes this file) before branching, not just
whichever commit happened to be `HEAD` at survey time.
