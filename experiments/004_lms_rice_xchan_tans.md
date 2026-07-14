# 004 — LMS+Rice+xchan_tans: table-driven tANS entropy back-end vs Rice

- **Cycle:** 4
- **Date:** 2026-07-14
- **Branch:** `compression-cycle-2026-07-13`
- **Candidate:** `LMS+Rice+xchan_tans`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb/capgmyo/cemhsey

## Hypothesis

INSIGHTS P5 / open-frontier #1: the entropy back-end is the one axis never touched in 4
cycles. Golomb-Rice is the optimal prefix code only for an *exactly* geometric residual; if
real HD-sEMG residual blocks deviate sub-Golomb, a table-driven tANS coder could recover the
fraction — but the per-block frequency table (side-info) + reverse-encode buffer may cost more
than the bits gained. Head-to-head: keep the incumbent `LMS+Rice+xchan` front-end verbatim and
swap ONLY Rice → LOCO-ANS-style tANS (category = bit-length of zigzag residual, tANS-coded;
c−1 raw mantissa bits shipped uncoded; static per-block category-freq table, division-free
runtime).

## Implementation

Only `research/registry.py`: `ans_encode`/`ans_decode` + one `Codec(...)`,
`family="entropy-backend"`. LMS predictor and grid-neighbour cross-channel front-end (with its
fixed-point beta side-info) byte-identical to `LMS+Rice+xchan`; only the residual coder changes.
`rtl/`, `sim/` untouched. Self-test: `LMS+Rice+xchan_tans 2.66x round-trip OK emb_ok OK neural
OK cost 0.109`; `registry self-test: ALL round-trips bit-exact`.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok), from `results/cycle_bench.csv` — the clean isolation is
tANS vs Rice with the **identical** front-end:

| dataset | tANS | cost | Rice (LMS+Rice+xchan) | cost | back-end Δ |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl | 2.1032× | 0.1090 | 2.1426× | 0.0572 | **−1.84%** |
| hyser_1dof_f1_s1 | 1.4514× | 0.1090 | 1.4738× | 0.0572 | **−1.52%** |
| capgmyo_dba_s1 | 1.3301× | 0.1090 | 1.3493× | 0.0572 | **−1.42%** |
| cemhsey_s1_d1t1 | 1.9219× | 0.1090 | 1.9551× | 0.0572 | **−1.70%** |

Synthetic (near-geometric Gaussian residuals, Rice-optimal): tANS 2.5195× / 2.4853× vs Rice
2.5717× / 2.5330× — also smaller, as expected.

## Attribution

Predictor + front-end identical → the **only** lever is the entropy back-end, and it is
**negative on every real set: tANS is 1.4–1.8% SMALLER than Rice at ~2× the cost** (0.109 vs
0.0572). **Mechanism:** real HD-sEMG residual blocks are near-geometric (Laplacian-ish), which
is exactly where Golomb-Rice is the optimal prefix code — there is no meaningful sub-Golomb
fraction to recover. The LOCO-ANS category+mantissa split plus the per-block static
category-frequency table is pure side-info overhead on top of a coder that is already at the
entropy floor, so the swap *loses* bits rather than gaining them. This directly measures and
confirms P5's predicted "small/uncertain payoff" — here it resolves to a net loss.

## Pareto check

**Conclusively dominated on ALL 4 real sets** by the already-registered `LMS+Rice+xchan`
(higher ratio AND lower cost everywhere). Also dominated by bestpartner on all sets, and by
delta+Rice+xchan/acar on several.

## Sanity gates

- Max real ratio in run 2.151× ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; tANS round-trip OK, embedded_ok=OK, neural_ok=OK, cost 0.109.
  De-risked in a standalone harness (20 cases incl. all-zero and spike blocks) before edit.
- No incumbent regression.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous on the correctness/embeddability
audit (bit-exact round-trip, division-free runtime path, cost 0.109). No split.

## Decision

**RETIRED (2026-07-14); NOT promoted.** Passes verification (legitimacy) but fails promotion:
it does not beat the current best on any real set — it is *below* the incumbent whose back-end
it replaces. Conclusively Pareto-dominated on all real data → `retired=True` + `retired_reason`
in `research/registry.py`. **This spends open-frontier lever #1 with a negative result:** the
entropy back-end is not a lever on this data — Rice is already near-optimal for the near-geometric
residual, and a heavier ANS coder with per-block table side-info costs more than it saves. Port
recommendation unchanged.
