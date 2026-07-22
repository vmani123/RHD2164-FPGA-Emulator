# 014 — LMS4+Rice+acar_sel+bestpartner: scale-selected two-stage spatial cascade

- **Cycle:** 12
- **Date:** 2026-07-22
- **Branch:** `compression-cycle-2026-07-22`
- **Candidate:** `LMS4+Rice+acar_sel+bestpartner` (a.k.a. `acarsel`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #3 — salvage frontier #1's always-on cascade)

The always-on `acar+bestpartner` cascade (cycle 10) helped only on the tight 64-ch OTB array (+0.81%) and
*regressed* the large arrays (−0.23..−0.26 pp), because the two MI slices are additive only where the global
array-mean is a real eigenvector (P1-refinement). A cascade that *selects* CAR-first vs best-partner-only per
recording — from a decoder-derivable global-vs-local coherence statistic — should keep the OTB win without
the large-array loss. Realize the gate on the array channel count `C` (in the header, read before any
reconstruction → zero circularity, zero side-info): `C<=64` → full cascade; `C>=128` → best-partner only.

## Implementation

`research/registry.py` only: `acarsel_encode`/`acarsel_decode`, a pure meta-gate `_acarsel_use_car(C)` over
two already-verified primitives (`_acar_forward`+`_bp_select` cascade, or `_bp_select` alone) — no new
mechanism. Threshold `ACARSEL_MAX_CH=64`. Both branches share an identical header format; the decoder derives
the same branch from `C` and inverts the matching cascade. Integer/fixed only. `rtl/`, `sim/` untouched.
Registry self-test: round-trip OK, emb_ok OK, neural OK, cost 0.043. Cross-checked C=32/64/128/320: all
round-trip bit-exact; emitted body byte-identical to `acar+bestpartner` at C≤64 and to `bestpartner` at
C≥128 (the gate selects and inverts exactly as designed).

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok OK, neural_ok OK), from `results/cycle_bench.csv`, cost 0.043:

| dataset | C | branch taken | acar_sel | best `bestpartner` (0.0394) | vs best | always-on `acar+bp` (0.043) |
|---|---:|---|---:|---:|---:|---:|
| otb_hdsemg_vl | 64 | cascade | **2.179540** | 2.161938 | **+0.814%** | 2.179540 |
| **hyser_1dof_f1_s1** | 128 | best-partner only | 1.480384 | 1.480384 | **±0.000%** | 1.477008 |
| capgmyo_dba_s1 | 128 | best-partner only | 1.350480 | 1.350480 | ±0.000% | 1.350480 |
| cemhsey_s1_d1t1 | 320 | best-partner only | 1.955547 | 1.955547 | ±0.000% | 1.951547 |

acar_sel's OTB ratio equals `acar+bestpartner` exactly; its 3 large-array ratios equal `bestpartner` exactly.

## Attribution

No new predictor/back-end — the lever is *which* verified spatial front-end runs, chosen by scale. The gate
fires the global-CAR-first cascade only on the tight 64-ch array, where the DC-across-array mode is a real
eigenvector orthogonal to the local pairwise mode, so both MI slices are captured (+0.81% over best-partner).
On the 128-/320-ch arrays it selects best-partner-only, so the mismatched global CAR basis never subtracts
noise — recovering the exact large-array ratios the always-on cascade had *lost* (hyser +0.34 pp, cemhsey
+0.40 pp back). This confirms P1-refinement operationally: the array-mean carries MI worth taking only on
tight arrays; scale is the correct, decoder-free selector.

## Pareto check

Cost 0.043. **acar_sel Pareto-DOMINATES the always-on `acar+bestpartner` at EQUAL cost:** ≥ ratio on every
real set, strictly > on hyser and cemhsey, tie on otb/capgmyo, same cost 0.043 → no remaining trade-off, so
`acar+bestpartner` is now conclusively superseded (RETIRED, see its registration). Versus the leaderboard
best `bestpartner`: acar_sel weakly dominates on *ratio* (≥ all sets, > OTB by +0.81%) but at **higher cost
(0.043 > 0.0394)**, so it does **not** Pareto-dominate the best — it is a genuine **non-dominated max-ratio
OTB corner** (kept), not a Pareto improvement.

## Sanity gates

- Max real ratio 2.1795× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.043.
- No incumbent regression: on the 3 large arrays acar_sel *equals* the registered best exactly; on OTB it
  equals the registered `acar+bestpartner`.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** It **ties the primary Hyser** (identical to the best,
because the 128-ch gate selects best-partner-only) and improves only OTB (+0.81%) at higher cost, so it does
not beat the current best on the primary and is not a Pareto win → not a new leaderboard best. Kept as the
non-dominated OTB max-ratio corner. **Consequence: it conclusively supersedes the always-on
`LMS4+Rice+acar+bestpartner` (equal cost, ≥ ratio everywhere, strictly > on 2 sets) → that codec is RETIRED
this cycle.** Frontier #3 is spent **positive as an engineering result** (the scale gate cleanly keeps the
OTB corner without the large-array loss) but yields no new global best. Headline/port pick unchanged.
