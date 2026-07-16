# 006 — LMS4+Rice+xchan_bestpartner: order-4 predictor under the best-partner front-end

- **Cycle:** 7
- **Date:** 2026-07-16
- **Branch:** `compression-cycle-2026-07-16`
- **Candidate:** `LMS4+Rice+xchan_bestpartner` (a.k.a. `lms4bp`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis

Right-size the temporal predictor (order 8→4, INSIGHTS P2) beneath the already-shipped,
non-dominated best-partner cross-channel front-end. The spatial lever (best-of-4 causal-neighbour
selection + integer gain) is unchanged; only the over-provisioned predictor shrinks. Expected to
**dominate** the incumbent on BOTH axes (lower cost AND ≥ ratio), with no new mechanism risk — the
winning cross-channel lever is untouched.

## Implementation

Only `research/registry.py`: `lms4bp_encode`/`lms4bp_decode` + `LMS4_ORDER=4`, `LMS4BP_MAGIC`.
The encoder reuses the best-partner front-end VERBATIM (`_bp_select`, `_bp_inverse`, identical
`(parents,betas)` 2×int16/ch side-info); the ONLY change vs `LMS+Rice+xchan_bestpartner` is calling
`ec.lms_forward/lms_inverse` with `order=4` instead of the family default 8. Matched enc/dec pair,
zero temporal side-info, integer-only, causal, look-ahead = block(256). `rtl/`, `sim/` untouched.
Self-test: `LMS4+Rice+xchan_bestpartner` round-trip OK, emb_ok OK, neural OK, cost 0.039; `registry
self-test: ALL round-trips bit-exact`.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok), from `results/cycle_bench.csv`, cost 0.0394:

| dataset | lms4bp | vs incumbent LMS+Rice+xchan (0.0572) | vs order-8 bestpartner (0.0629) | vs LMS+Rice (xchan off, 0.0523) |
|---|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | **2.1619×** | +0.90% (2.1426×) | +0.50% (2.1512×) | **+18.4%** (1.8254×) |
| hyser_1dof_f1_s1 (128 ch) | **1.4804×** | +0.45% (1.4738×) | +0.15% (1.4782×) | +11.3% (1.3300×) |
| capgmyo_dba_s1 (128 ch) | **1.3505×** | +0.09% (1.3493×) | +0.09% (1.3493×) | +1.37% (1.3323×) |
| cemhsey_s1_d1t1 (320 ch) | **1.9555×** | +0.02% (1.9551×) | +0.03% (1.9549×) | +13.1% (1.7293×) |

It is **rank #1 embeddable on every real set** (only offline lzma sits above it on hyser/cemhsey).

Search (`results/cycle_search.csv`, hyser+otb mean, single-parent hill-climb): best `lms4s7+x6/b512`
= 1.8204 / cost 0.0271; ablation **cross on→off +14.83%** (dominant lever), **order 4→8 +0.79%**
(deeper prediction *hurts* — P2 confirmed a 3rd real cycle). lms4bp's own hyser+otb mean = 1.8212,
marginally above the single-parent search corner at higher cost (a non-dominated point vs it).

## Attribution

Front-end (best-partner) and back-end (Rice) are byte-identical to the order-8 bestpartner; the ONLY
lever moved is the **temporal predictor order 8→4**. That change simultaneously *raised* the ratio
(marginally, on all 4 real sets) and *cut* cost 0.0629→0.0394 (37% cheaper). The ratio level itself
is delivered by the **cross-channel best-partner front-end** (+11–18% over temporal-only, unchanged).
So: the spatial front-end owns the compression; order-4 right-sizing owns the cost win and a small
ratio bonus (order-8's extra taps were fitting residual noise — P2).

## Cross-channel gain (isolated, real)

Same best-partner front-end as the shipped codec → same spatial lever. Achieved gain over the
temporal-only baseline `LMS+Rice`: **+18.4% (otb), +13.1% (cemhsey), +11.3% (hyser), +1.37%
(capgmyo)** — tracks each array's real neighbour correlation (P1); near-zero on the low-corr CapgMyo
negative control. Search-isolated cross on→off for the order-4 single-parent config: **+14.83%**
(hyser+otb mean).

## Pareto check

**Dominates on BOTH axes** the prior incumbent `LMS+Rice+xchan` (higher ratio AND lower cost, all 4
sets) and the order-8 `LMS+Rice+xchan_bestpartner` (higher ratio AND lower cost, all 4 sets → that
codec is now RETIRED). New max-ratio corner of the embeddable Pareto front. The cheaper single-parent
`lms4s7+x6/b512` (0.0271) remains a non-dominated lower-cost corner (essentially tied ratio, no
partner side-info).

## Sanity gates

- Max real ratio 2.162× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; lms4bp round-trip OK, embedded_ok OK, neural_ok OK, cost 0.039.
- No incumbent regression: `LMS+Rice+xchan` reproduces the headline (hyser 1.4738×, otb 2.1426×).

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**PROMOTED — new leaderboard best embeddable on all 4 real HD-sEMG sets.** Beats the current best
(`LMS+Rice+xchan`) on real data AND unanimous PROMOTE → promotion rule satisfied. Its order-8
predecessor `LMS+Rice+xchan_bestpartner` is conclusively Pareto-dominated and RETIRED. Port caveat
unchanged in kind: best-partner id + β derived offline over the whole signal; embeddable realization
selects per block (look-ahead = block). The minimal-hardware value pick remains the single-parent
order-4 (`lms4s7+x6/b512`), which carries no partner side-info at essentially the same ratio.
