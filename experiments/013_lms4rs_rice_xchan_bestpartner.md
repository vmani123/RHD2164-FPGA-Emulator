# 013 — LMS4rs+Rice+xchan_bestpartner: regime-switched (activity-gated) order-4 temporal predictor bank

- **Cycle:** 12
- **Date:** 2026-07-22
- **Branch:** `compression-cycle-2026-07-22`
- **Candidate:** `LMS4rs+Rice+xchan_bestpartner` (a.k.a. `rsbp`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #2 — attack the temporal residual entropy)

Every spatial lever is within ~1% of a shared ceiling; the remaining bits may live in the *temporal*
residual, larger in bursty high-activity segments where one linear LMS under-fits. Keep the promoted order-4
best-partner spatial front-end verbatim; replace the single order-4 sign-LMS with a BANK of 3 order-4
sign-LMS predictors, one selected per sample-channel by a backward-derived activity regime (recent-vs-
long-term reconstructed |residual| energy: quiescent/normal/burst). Each regime adapts only on its own
samples; decoder mirrors the regime → zero side-info. Prediction: `H(e | regime) < H(e)` → lower coded bits.

## Implementation

`research/registry.py` only: `_rsw_regime`/`_rsw_forward`/`_rsw_inverse` + `rsbp_encode`/`rsbp_decode`.
Spatial front-end reused verbatim (`_bp_select`/`_bp_inverse`). Regime = fast vs slow leaky |residual|
integrator compared by exact integer cross-multiplication (scale-free relative split, no per-dataset
threshold), both accumulators updated *after* each sample → causal, zero side-info. Order stays 4 (P2); only
the number of coefficient sets grows. Integer/fixed only. `rtl/`, `sim/` untouched. Registry self-test:
round-trip OK, emb_ok OK, neural OK, cost 0.0524.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok OK, neural_ok OK), from `results/cycle_bench.csv`, cost 0.0524:

| dataset | rsbp | current best `bestpartner` (0.0394) | vs best | rsbp xchan gain (vs LMS+Rice) | best-partner xchan gain |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | 2.126434 | 2.161938 | **−1.642%** | +16.49% | +18.44% |
| **hyser_1dof_f1_s1 (128 ch)** | 1.475982 | 1.480384 | −0.297% | +10.98% | +11.31% |
| capgmyo_dba_s1 (128 ch) | 1.340118 | 1.350480 | −0.767% | +0.59% | +1.37% |
| cemhsey_s1_d1t1 (320 ch) | 1.953825 | 1.955547 | −0.088% | +12.98% | +13.08% |

**Loses on ALL 4 real sets** at **higher cost (0.0524 > 0.0394)**. The regime bank *lowered* the achieved
cross-channel gain on every set (otb +16.49% vs +18.44%, hyser +10.98% vs +11.31%, capgmyo +0.59% vs +1.37%,
cemhsey +12.98% vs +13.08%).

## Attribution

Same spatial front-end and Rice back-end as the best → the only lever is the regime-switched temporal bank,
and it is **uniformly negative**. `H(e | regime)` did NOT drop below `H(e)`. Two compounding failures: (1)
after an order-4 LMS the HD-sEMG residual is already near-white (P2) — the "burst" segments the bank meant to
exploit are higher-*variance* noise, not distinct predictable linear dynamics, so a separate filter has no
extra structure to fit; (2) partitioning into 3 regimes fragments each bank's adaptation to ~1/3 the samples,
so every bank's taps are estimated from fewer points and track the signal *noisier* than the single shared
filter — raising, not lowering, the coded residual entropy. The uniform drop in achieved xchan gain is the
fingerprint: worse temporal prediction feeds a slightly larger residual into the (unchanged) spatial stage.

## Pareto check

Worse ratio on all 4 real sets AND higher cost than the registered `LMS4+Rice+xchan_bestpartner` →
**conclusively Pareto-dominated**. RETIRED.

## Sanity gates

- Max real ratio 2.1264× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.0524.
- The regression is the candidate itself vs the (unchanged) best; no registered-codec regression.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split. (Verifiers gate correctness/
embeddability — the candidate is a legitimate, bit-exact codec; it simply loses on ratio.)

## Decision

**RETIRED** (`retired=True` + `retired_reason` on its `Codec(...)` in `research/registry.py`). Conclusively
Pareto-dominated on all 4 real sets. The temporal residual-entropy lever (frontier #2), *via predictor-
coefficient regime switching*, is spent **NEGATIVE**: with the residual already near-white after order-4 LMS,
adding coefficient sets fits noise and fragmenting adaptation raises coded bits — the predictor-side analogue
of the retired `xctx` context-modeling failure (P5). Do not re-propose activity-regime predictor banks.
Headline/port pick unchanged.
