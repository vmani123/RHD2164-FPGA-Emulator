# 003 — LMS+Rice+iklt_adaptive: data-dependent backward-adaptive integer-KLT rotation

- **Cycle:** 4
- **Date:** 2026-07-14
- **Branch:** `compression-cycle-2026-07-13`
- **Candidate:** `LMS+Rice+iklt_adaptive`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb/capgmyo/cemhsey

## Hypothesis

The retired fixed integer-KLT (`LMS+Rice+iklt`, exp 002) failed because a fixed 45°
rotation is the true KLT only for a *stationary isotropic* pair; INSIGHTS P3 predicted a
multi-tap spatial transform is only worth its cost if its **basis adapts**. This candidate
makes the Givens rotation **angle** backward-adaptive per grid-neighbour pair per time-block
(quantized θ chosen to minimise post-rotation off-diagonal covariance, accumulated over the
previous reconstructed raw block), keeping the multiplierless 3-lift shear butterfly verbatim
(lossless, integer-only, zero side-info, look-ahead 0). Tests open-frontier lever #2.

## Implementation

Only `research/registry.py` touched: `itsklt_encode`/`itsklt_decode` + a `Codec(...)`
registration, `family="cross-channel"`. Temporal predictor (order-8 sign-sign LMS) and Rice
back-end byte-identical to the incumbent `LMS+Rice+xchan`; only the cross-channel front-end
differs (fixed 45° → data-dependent per-pair/per-block angle). `rtl/`, `sim/` untouched.

Self-test (`research/registry.py --selftest`): `LMS+Rice+iklt_adaptive 2.72x round-trip OK
emb_ok OK neural OK cost 0.083`; `registry self-test: ALL round-trips bit-exact`.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok), from `results/cycle_bench.csv`:

| dataset | iklt_adaptive | cost | incumbent LMS+Rice+xchan | LMS+Rice (xchan off) | iso. xchan gain | vs incumbent |
|---|---:|---:|---:|---:|---:|---:|
| otb_hdsemg_vl | 1.8855× | 0.0833 | 2.1426× / 0.0572 | 1.8254× | **+3.29%** | −12.00% |
| hyser_1dof_f1_s1 | 1.3523× | 0.0833 | 1.4738× / 0.0572 | 1.3300× | **+1.68%** | −8.25% |
| capgmyo_dba_s1 | 1.3257× | 0.0833 | 1.3493× / 0.0572 | 1.3323× | **−0.49%** | −1.75% |
| cemhsey_s1_d1t1 | 1.7609× | 0.0833 | 1.9551× / 0.0572 | 1.7293× | **+1.82%** | −9.94% |

Synthetic (mechanism only): sc0.6 2.4241× / sc0.9 2.2552× — below `LMS+Rice+xchan`
(2.5717× / 2.5330×) at every correlation.

## Attribution

Temporal predictor and Rice back-end unchanged → the only lever is the cross-channel
front-end. The adaptive-angle rotation captures only **+1.7% to +3.3%** achieved
cross-channel gain on real data (isolated, xchan on vs off), versus the single-neighbour
subtract's **+10.8% to +17.4%** (`xchan_gain` CSV cells). On CapgMyo it is **negative
(−0.49%)** — worse than temporal-only. This is even worse than the retired **fixed** iklt
(+8.8% on OTB at 8000 samp, exp 002).

**Mechanism (why adaptive is *worse* than fixed here):** the angle is estimated from the
*previous* block's 2×2 covariance — a stale, noisy estimate on non-stationary HD-sEMG. A
Givens rotation is energy-preserving and mixes **both** channels, so angle-estimation error
corrupts both outputs; the rank-1 single-neighbour subtract only injects estimation noise
into the residual channel and leaves the parent clean. Cascading the noisy rotation over all
horizontal+vertical pairs compounds the error. So a *data-dependent basis* did not rescue the
multi-tap transform — it made it worse than the fixed one.

## Pareto check

**Conclusively dominated on ALL 4 real sets** by the already-registered `LMS+Rice+xchan`
(higher ratio AND lower cost everywhere: otb 2.143×/0.057 vs 1.885×/0.083; hyser 1.474× vs
1.352×; capgmyo 1.349× vs 1.326×; cemhsey 1.955× vs 1.761×). Also dominated by bestpartner,
delta+Rice+xchan, and acar on multiple sets; on capgmyo even plain `LMS+Rice` (1.332×/0.052)
dominates it.

## Sanity gates

- Max real ratio in run 2.151× ≪ 6× leak ceiling → no leak.
- No FAIL bit-exact rows: every row `ok=True`; iklt_adaptive round-trip OK, embedded_ok=OK,
  neural_ok=OK.
- No incumbent regression (incumbent numbers unchanged).

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous on the
correctness/embeddability audit (bit-exact round-trip, embedded_ok, cost 0.083). No split.

## Decision

**RETIRED (2026-07-14); NOT promoted.** Unanimous verification is only the legitimacy gate;
promotion additionally requires beating the current best on real data, which it fails on
every set. Conclusively Pareto-dominated on all real data by `LMS+Rice+xchan` → `retired=True`
+ `retired_reason` set on its `Codec(...)` in `research/registry.py` (kept bit-exact, excluded
from the default sweep). **Negative result, kept honest:** making the multi-tap basis
*data-dependent* does not beat the adaptive rank-1 subtract — a backward-estimated rotation is
too stale/noisy on non-stationary HD-sEMG and corrupts both channels. This spends open-frontier
lever #2. Port recommendation unchanged.
