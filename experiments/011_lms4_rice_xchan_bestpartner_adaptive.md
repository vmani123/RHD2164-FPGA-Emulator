# 011 — LMS4+Rice+xchan_bestpartner_adaptive: backward-adaptive per-block best-partner RE-SELECTION (port-caveat closure)

- **Cycle:** 10
- **Date:** 2026-07-19
- **Branch:** `compression-cycle-2026-07-19`
- **Candidate:** `LMS4+Rice+xchan_bestpartner_adaptive` (a.k.a. `lms4bp_adaptive`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #3 — embeddability / port-caveat closure, NOT a ratio play)

The PROMOTED best `LMS4+Rice+xchan_bestpartner` derives its (partner id, β) offline over the whole
recording and ships a 2×int16/ch header — its last port caveat (P4: not producible on-node). Replace
that with per-block backward re-selection: for each channel/block i>0, scan the same ≤4 causal grid
neighbours over the PREVIOUS reconstructed raw block, derive each candidate's integer LS gain and score
its Rice bits, keep the min-bits (partner, β), apply that rank-1 subtract to the CURRENT block. The
decoder mirrors the identical selection from bit-identical reconstructed history → **zero side-info,
look-ahead 0**. **Question to measure: does per-block re-selection HOLD the offline ratio despite
stale-partner risk across burst boundaries?**

## Implementation

Only `research/registry.py`: `lms4bpa_encode`/`lms4bpa_decode` + `_bpa_select_block` (shared enc/dec
selection). Reuses `_bp_candidates`/`_bp_opt_beta`/`_bp_score` verbatim; order-4 LMS + adaptive Rice
unchanged. Block 0 bootstraps to no-partner. Integer-only. `rtl/`, `sim/` untouched. Registry
self-test: round-trip OK, emb_ok OK, neural OK, cost 0.0387.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok, neural_ok), from `results/cycle_bench.csv`, cost 0.0387:

| dataset | bpa (zero side-info) | offline best-partner (0.0394, 2×int16/ch header) | vs best | xchan gain | best-partner xchan gain |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | 2.153106 | 2.161938 | −0.41% | +17.96% | +18.44% |
| hyser_1dof_f1_s1 (128 ch) | 1.477020 | 1.480384 | −0.23% | +11.05% | +11.31% |
| capgmyo_dba_s1 (128 ch) | **1.352866** | 1.350480 | **+0.18%** | +1.55% | +1.37% |
| cemhsey_s1_d1t1 (320 ch) | 1.953948 | 1.955547 | −0.08% | +12.99% | +13.08% |

## Attribution

Everything but the *estimation* of (partner, β) is byte-identical to the promoted best. The only lever
is offline-whole-signal selection → backward per-block re-selection. Effect: the ratio is held to within
**−0.08% to −0.41%** on the three high-correlation sets and is **+0.18% on CapgMyo** (per-block
re-selection tracks the low-corr array's mild non-stationarity better than one whole-signal choice).
**The port caveat is closeable at essentially zero ratio cost** — and the 2×int16/ch header AND the
whole-signal look-ahead are both removed.

## Cross-channel gain (isolated, real, vs `LMS+Rice` 0.0523)

+17.96% otb, +11.05% hyser, +1.55% capgmyo, +12.99% cemhsey — within ~0.4 pp of the offline
best-partner's isolated gain on every set, and above it on capgmyo. Backward-adaptive selection HOLDS the
spatial lever.

## Mechanism (why re-selection holds the ratio)

The best-partner identity is stable within a recording — neighbour geometry does not change — so a
per-block re-derived (partner, β) from the previous reconstructed block lands on essentially the same
choice the offline whole-signal search makes, minus a small transient at burst boundaries where the
previous block's covariance is briefly stale. The tiny −0.2..−0.4 pp give-up is exactly that transient;
on the low-corr CapgMyo, per-block adaptation actually *beats* one static choice. This confirms P4: any
parameter the decoder can recompute from reconstructed causal history costs zero side-info and — when the
parameter is slowly varying — sacrifices essentially no ratio.

## Pareto check

Cost 0.0387 < best 0.0394. **bpa DOMINATES the current best on capgmyo (higher ratio AND lower cost);
on otb/hyser/cemhsey it is a non-dominated cheaper corner (lower ratio, lower cost) that additionally
removes all side-info + look-ahead.** Not conclusively Pareto-dominated → kept registered. It is not
dominated by joint2 either (bpa has higher ratio than joint2 on otb and capgmyo).

## Sanity gates

- Max real ratio 2.1531× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.0387.
- No incumbent regression (current best reproduces: hyser 1.4803843, otb 2.161938).

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** Passes verification. Does not beat the current best on
the primary real set (Hyser 1.477 < 1.480), so not promoted — and it was never a ratio play. But it
**succeeds at its actual goal**: it holds the promoted codec's ratio within ~0.4% while making it **fully
on-node, streaming-legal, zero-side-info** (drops the offline whole-signal selection AND the 2×int16/ch
header), at slightly lower cost. It dominates the best on capgmyo and is non-dominated elsewhere → not
retired. **Frontier #3 (port-caveat closure) is spent POSITIVE: the promoted best's last port caveat is
closeable at essentially no ratio cost.** The port recommendation can now cite this codec as the
zero-side-info on-node realization of the promoted best.
