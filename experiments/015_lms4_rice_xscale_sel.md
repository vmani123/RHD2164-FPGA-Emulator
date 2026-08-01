# 015 — LMS4+Rice+xscale_sel: scale-selected spatial front-end (best-partner ⊕ jointbp2)

- **Cycle:** 13
- **Date:** 2026-08-01
- **Branch:** `compression-cycle-2026-08-01`
- **Candidate:** `LMS4+Rice+xscale_sel` (a.k.a. `xscalesel`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #1 — the highest-payoff live lever)

Cycle 12 established the per-scale winner empirically: on tight arrays (`C<=64`) the single *selected*
best-partner wins; on large arrays (`C>=128`) the jointly-solved best-*pair* (`jointbp2`) wins (+1.12% on
Hyser, the highest embeddable Hyser ratio). `acar_sel` proved that a zero-side-info, header-read
channel-count gate cleanly picks a regime per recording. **Gate best-partner (C≤64) vs jointbp2 (C≥128)**
and the codec should hold the tight-array OTB corner *and* inherit the large-array Hyser win — the first
single construction to clear the best on both scales.

## Implementation

`research/registry.py` only (199 insertions, 0 deletions): `_xscalesel_use_bp(C)` (integer compare
`C <= XSCALESEL_MAX_CH = 64` on the channel count in the fixed 12-byte header, read before any
reconstruction → zero circularity, zero side-info, look-ahead 0 for the gate itself), plus
`xscalesel_encode`/`xscalesel_decode` dispatching to the verbatim `_bp_select`/`_bp_inverse` (tight) or
`_jbp2_forward`/`_jbp2_inverse` (extended) primitives. Back-end unchanged: order-4 sign-sign LMS (P2) +
adaptive Rice (P5). Registered cost is a conservative **worst-case union** (0.0482), above either branch's
own cost (0.0394 tight / 0.0468 extended), so the codec is never scored cheaper than the branch it runs.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact `ok=True`, `embedded_ok=OK`, `neural_ok=OK`, cost 0.0482), from `results/cycle_bench.csv`:

| dataset | C | branch taken | xscale_sel | best `bestpartner` (0.0394) | vs best | `jointbp2` (0.0468) |
|---|---:|---|---:|---:|---:|---:|
| otb_hdsemg_vl | 64 | best-partner | **2.161938** | 2.161938 | ±0.000% | 2.152244 |
| **hyser_1dof_f1_s1** | 128 | jointbp2 | **1.496924** | 1.480384 | **+1.117%** | 1.496924 |
| capgmyo_dba_s1 | 128 | jointbp2 | 1.350287 | 1.350480 | **−0.014%** | 1.350287 |
| cemhsey_s1_d1t1 | 320 | jointbp2 | 1.952260 | 1.955547 | **−0.168%** | 1.952260 |
| 4-set mean | | | 1.740352 | 1.737087 | +0.188% | 1.737929 |

The gate is mechanically perfect: the OTB ratio equals `bestpartner` to all printed digits, and all three
`C>=128` ratios equal `jointbp2` to all printed digits — exactly as designed, at zero side-info.

## Attribution

**No new mechanism; the entire lever is *which* verified front-end runs.** The temporal predictor (order-4
sign-sign LMS) and the entropy back-end (adaptive Rice) are byte-for-byte the same in both branches, so
every delta here is cross-channel front-end. Isolated cross-channel gain on REAL data (candidate ÷ the same
back-end with the front-end removed — `LMS4+Rice` temporal-only, measured this cycle at otb 1.834664,
hyser 1.332070, capgmyo 1.333589, cemhsey 1.728021):

| dataset | xscale_sel | `bestpartner` | `jointbp2` |
|---|---:|---:|---:|
| otb_hdsemg_vl | **+17.84%** | +17.84% | +17.31% |
| hyser_1dof_f1_s1 | **+12.38%** | +11.13% | +12.38% |
| capgmyo_dba_s1 | +1.25% | +1.27% | +1.25% |
| cemhsey_s1_d1t1 | +12.98% | +13.17% | +12.98% |

**The gate works; its gating VARIABLE is wrong.** The construction inherits each branch exactly, so it
proves the mechanism — but the premise it was built on ("jointbp2 is the per-scale winner at C≥128") is
**false on 2 of the 3 large real arrays**: jointbp2 wins Hyser (+1.12%) but *loses* CapgMyo (−0.014%) and
CEMHSEY (−0.168%) to plain best-partner. Channel count `C` is not a sufficient statistic for whether the
local spatial covariance has effective rank ≥ 2 — CEMHSEY has 320 channels and still prefers rank-1, while
CapgMyo's 128 channels carry almost no spatial MI at all (+1.3%). Cycle 12 generalized from a single large
array (Hyser) to "large arrays"; four real sets say the discriminator is array *physics* (pitch, filtering,
local coherence), not array *size*.

**Headroom for a correct gate (measured, not assumed):** a per-set oracle that always picked the better of
`bestpartner`/`jointbp2` would reach otb 2.161938, hyser 1.496924, capgmyo 1.350480, cemhsey 1.955547 —
4-set mean 1.741222, **+0.238% over the best**. The C-gate captures 1.740352, i.e. **79% of the oracle** —
so the remaining ~0.05% is what a better gating statistic can buy. That is the honest ceiling of this lever.

## Pareto check

Cost 0.0482 (worst-case union). Automated domination scan over all `embedded_ok` codecs on the 4 real sets:
**nothing dominates it** (no registered codec has ≤ cost and ≥ ratio on all four) — it strictly beats
`bestpartner` on Hyser and strictly beats `jointbp2` on OTB. It is therefore a legitimate non-dominated
point and **is not retired**.

But the honest deployment reading: on **every individual** real set it is weakly dominated — Hyser by
`jointbp2` (equal ratio, cost 0.0468), OTB by `bestpartner` (equal ratio, cost 0.0394), CapgMyo and CEMHSEY
by `bestpartner` (strictly higher ratio, lower cost). On any array whose channel count you already know at
build time you would ship the winning branch directly and pay less. On the 4-set mean it also sits below the
already-registered `acar_sel+bestpartner` (1.741488 at cost 0.043) on both axes. So it is non-dominated by
the letter of the rule, and of no deployment value in practice — a *mechanism* result, not a codec.

## Sanity gates

- Max real ratio this cycle 2.1795× (acar_sel, OTB) ≪ the 6× broadband ceiling; xscale_sel's own max is
  2.1619× (OTB) → no leak, no degenerate data.
- All 120 rows in `results/cycle_bench.csv` have `ok=True` → no FAIL bit-exact anywhere.
- No incumbent regression: `bestpartner` measures 2.161938 / 1.480384 / 1.350480 / 1.955547, identical to
  the promoted values on the leaderboard; `jointbp2` measures 1.496924 on Hyser, identical to cycle 12.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** It wins the primary Hyser (+1.12%) but **regresses CEMHSEY
(−0.168%) and CapgMyo (−0.014%)** versus the leaderboard best, so it does not beat the best on real data;
and it is out-ratioed on the 4-set mean by the cheaper, already-registered `acar_sel+bestpartner`. The
scale-gating *pattern* is confirmed a second time (zero side-info, exact branch inheritance); the
channel-count *statistic* is refuted. Next step is a gate on a measured signal statistic, not on array shape
— see INSIGHTS P1b-refinement and open-frontier #1.
