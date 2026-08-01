# 016 — LMS4+Rice+xchan_lagbp: propagation-matched (lagged) cross-channel predictor

- **Cycle:** 13
- **Date:** 2026-08-01
- **Branch:** `compression-cycle-2026-08-01`
- **Candidate:** `LMS4+Rice+xchan_lagbp` (a.k.a. `lagbp`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (SURVEY cycle-16 rec #2 — the untouched time axis of the spatial lever)

Every cross-channel codec tried so far subtracts a neighbour at **zero lag**. But an HD-sEMG motor-unit
action potential *propagates* along the fibre at ~4 m/s, so the neighbour electrode sees the same waveform
**delayed**. If the true spatial coupling is `x_c[n] ≈ β·x_p[n−d]`, a zero-lag subtract is subtracting a
phase-shifted copy and leaves the propagating component in the residual. Adding an integer lag axis
`d ∈ [−8,+8]` to the per-block backward (partner, β) selection should recover that mutual information at
one extra index — the per-sample apply cost is unchanged.

## Implementation

`research/registry.py` only (510 insertions, 0 deletions): `_lagbp_lagged`, `_lagbp_int_beta`,
`_lagbp_rows_bits`, `_lagbp_select_block`, `_lagbp_forward`/`_lagbp_inverse`, `lagbp_encode`/`lagbp_decode`.
ONE asymmetric rank-1 subtract `e_c[n] = x_c[n] − (β·x_p[n−d])>>8`; the (partner, lag, β) triple is
re-derived per 256-sample block from the **previous reconstructed block** → zero side-info. Candidate set is
the unchanged `_bp_candidates` (left / up / up-left / up-right, all grid idx < g) × 17 lags, scanned in
|d|-ascending order with `d=0` first and strict comparisons, so it degenerates **exactly** to
`bestpartner_adaptive` when the correlogram peaks at zero (verified: forcing `LAGBP_LAGS=[0]` makes the
payload byte-identical to `lms4bpa_encode`'s). Look-ahead 8 samples (parent row only, bounded, streaming-legal).

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact `ok=True`, `embedded_ok=OK`, `neural_ok=−`, cost 0.0991), from `results/cycle_bench.csv`:

| dataset | lagbp | best `bestpartner` (0.0394) | vs best | matched D=0 twin `bpa` (0.0387) | **lag lever, isolated** |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl | 2.055296 | 2.161938 | **−4.933%** | 2.153106 | **−4.543%** |
| **hyser_1dof_f1_s1** | 1.476255 | 1.480384 | −0.279% | 1.477020 | −0.052% |
| capgmyo_dba_s1 | **1.357758** | 1.350480 | **+0.539%** | 1.352866 | **+0.362%** |
| cemhsey_s1_d1t1 | 1.953162 | 1.955547 | −0.122% | 1.953948 | −0.040% |
| 4-set mean | 1.710618 | 1.737087 | −1.524% | 1.734235 | −1.362% |

The right-hand column is the clean isolation of the lag axis: `bestpartner_adaptive` is the identical codec
with `D=0` (same backward per-block selection, same zero side-info, same rank-1 apply, same predictor and
back-end), so the difference is the lag axis and nothing else.

## Attribution

Isolated cross-channel gain on REAL data (vs `LMS4+Rice` temporal-only, measured this cycle at otb 1.834664,
hyser 1.332070, capgmyo 1.333589, cemhsey 1.728021): lagbp **+12.03% / +10.82% / +1.81% / +13.03%** versus
`bestpartner`'s +17.84% / +11.13% / +1.27% / +13.17%. So the front-end is doing *less* decorrelation
everywhere except CapgMyo.

**Direct evidence of what the selector actually chose** (probe over every block of every real set, using the
shipped `_lagbp_select_block`):

| dataset | selections with d≠0 | dominant lags |
|---|---:|---|
| otb_hdsemg_vl | **1097/3653 = 30.0%** | d=+1 (549), d=−1 (362) |
| hyser_1dof_f1_s1 | 197/7366 = 2.7% | d=−1 (46), d=+1 (26) |
| capgmyo_dba_s1 | **4814/7362 = 65.4%** | d=−1 (2557), d=+1 (1013) |
| cemhsey_s1_d1t1 | 517/18451 = 2.8% | d=+1 (63), d=±4 (81) |

**Two compounding mechanisms explain the loss.**

1. **Objective mismatch between the front-end's selection metric and the codeword length.** The selector
   scores the Rice length of the *cross-channel residual*, but the bits actually paid are the Rice length of
   that residual **after** the order-4 temporal LMS. Scaling by β is spectrally neutral (it changes the
   residual's amplitude, not its autocorrelation structure), so at `d=0` the proxy and the true objective
   move together. A **lag rotates the parent's phase**: the subtracted term injects the parent's own
   temporal structure at a shifted phase, which de-whitens the signal the LMS then has to predict. The
   selector cannot see that cost, so it happily picks lags that lower cross-residual entropy and raise final
   coded bits. This is why the damage is worst exactly where the temporal predictor matters most (OTB, where
   `LMS4+Rice` alone already reaches 1.835×, the highest temporal-only ratio of the four sets).
2. **Selection variance from a 17× larger candidate set estimated on one held-out block.** Going from 4
   candidates (`bpa`, backward) to 68 (`lagbp`, backward) is the only other change. The backward-vs-offline
   penalty for 4 candidates is small (`bpa` vs `bp` on OTB: −0.41%); the penalty for 68 candidates is
   **−4.54%**, an 11× amplification. A (partner, lag) pair that minimizes bits on block *i−1* generalizes
   worse to block *i* the larger the candidate set — classic model-selection overfitting, with no held-out
   validation available inside a zero-side-info backward scheme.

**Where the lag axis is genuinely real: CapgMyo.** It is the only set where lagbp wins (+0.36% vs its D=0
twin, and 1.357758× — the **highest CapgMyo ratio of any codec in the sweep**, above wavpack's 1.3472×), and
the only set where d≠0 is the *majority* choice (65.4%, peaked at d=−1). CapgMyo's differential,
heavily band-pass-filtered 8×16 array has near-zero instantaneous neighbour correlation (|corr| ≈ 0.29,
+1.3% xchan gain) — the filtering destroys the zero-lag common mode but **cannot destroy the propagation
delay**, so the little spatial MI that survives lives at a one-sample shift. That is exactly the predicted
physics, showing up on exactly the array where nothing else can find spatial MI.

## Pareto check

Cost 0.0991 (the price is the *scan*, not the apply: 76 of 105 enc ops/sample-ch are the 4×17 (partner,lag)
search). Automated domination scan over all `embedded_ok` codecs on the 4 real sets: **nothing dominates
it** — it holds the strict maximum CapgMyo ratio of the entire registry, so no codec is ≥ on all four sets.
**Not retired.** It is a genuine (if expensive and narrow) max-ratio corner on the negative-control array.

`neural_ok = −`: 126 cyc/sample-ch encode vs the tight 125-cyc 30 kS/s neural budget. It remains
`embedded_ok` at ~7% of the roomy 2 kS/s sEMG budget it targets.

## Sanity gates

- Max real ratio 2.0553× for this codec, 2.1795× across the sweep ≪ the 6× ceiling → no leak.
- All 120 rows `ok=True` → no FAIL bit-exact.
- No incumbent regression (all registered codecs reproduce their promoted values exactly).
- The −4.93% OTB result is a **candidate regression, not a harness regression**: the same run reproduces
  every incumbent's prior number.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** It loses to the best on 3 of 4 real sets and by −1.52% on
the 4-set mean, at 2.5× the cost. Kept only as the non-dominated CapgMyo max-ratio corner. The lag axis is
**not spent as physics but is spent as a selection axis**: widening a backward, zero-side-info selector along
an axis that changes the residual's temporal spectrum is a net loss, because the selector's objective is the
cross-residual entropy while the bill is the post-predictor residual entropy. Any revival must either (a)
score candidates on the *post-LMS* residual, or (b) restrict the lag to the one array class where the
zero-lag mode is filtered away (CapgMyo-like differential arrays).
