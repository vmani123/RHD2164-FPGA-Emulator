# 009 — LMS4+Rice+acar+bestpartner: two-stage scale-matched spatial cascade (global CAR → local best-partner)

- **Cycle:** 10
- **Date:** 2026-07-19
- **Branch:** `compression-cycle-2026-07-19`
- **Candidate:** `LMS4+Rice+acar+bestpartner` (a.k.a. `acar+lms4bp`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #1)

The cross-channel MI decomposes into distinct, non-interchangeable slices (P1-refinement): the
**global rank-1 common-mode** (removed by ACAR) and **local pairwise** correlation (removed by
best-partner). Cascade them — ACAR lift first, then the PROMOTED order-4 best-partner subtract on
the CAR *residual* — to capture **both** slices where both exist. Unlike the retired summed
multi-parent, the two stages are orthogonal by construction (a global array mean is uncorrelated
with the local pairwise residual it leaves), so they cannot double-count. **Expected to raise the
ratio where both slices are present; risk: on large arrays CAR may not clear its gate and add
nothing after best-partner already took the local slice.**

## Implementation

Pure cascade of two already-verified primitives, reused verbatim: `_acar_forward`/`_acar_inverse`
(global reversible-integer adaptive-CAR, backward-gated, zero side-info) then `_bp_select`/
`_bp_inverse` (best-of-4 causal-neighbour subtract, 2×int16/ch side-info) on the CAR residual;
order-4 LMS + adaptive Rice back-end. Decode inverts in reverse (Rice → lms_inverse(4) →
bp_inverse → acar_inverse); ACAR gate recomputed by the decoder from reconstructed raw history →
no ACAR side-info. Both stages exact integer inverses → bit-exact. Only `research/registry.py`
touched; `rtl/`, `sim/` untouched. Registry self-test: round-trip OK, emb_ok OK, neural OK, cost
0.043.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok, neural_ok), from `results/cycle_bench.csv`, cost 0.043:

| dataset | acar+bp | current best `LMS4+Rice+xchan_bestpartner` (0.0394) | vs best | CAR-stage marginal |
|---|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | **2.179540** | 2.161938 | **+0.81%** | **+0.96 pp** xchan gain |
| hyser_1dof_f1_s1 (128 ch) | 1.477008 | 1.480384 | −0.23% | −0.26 pp |
| capgmyo_dba_s1 (128 ch) | 1.350480 | 1.350480 | +0.00% (bit-identical) | 0.00 pp (gate never fired) |
| cemhsey_s1_d1t1 (320 ch) | 1.951547 | 1.955547 | −0.20% | −0.23 pp |

## Attribution

Temporal (order-4 LMS) and back-end (Rice) are byte-identical to the promoted best; best-partner is
reused verbatim. The ONLY lever changed is the **added global-CAR front stage**. Its isolated
marginal effect over best-partner alone: **+0.96 pp of cross-channel gain on the tight 64-ch OTB
array** (+0.81% ratio), **zero on CapgMyo** (the CAR gate never fired — the result is bit-identical
to best-partner), and **negative on the large 128-/320-ch arrays** (−0.26 pp hyser, −0.23 pp
cemhsey). So the win is entirely the OTB common-mode slice; on large arrays the added stage is a net
loss.

## Cross-channel gain (isolated, real, vs temporal-only `LMS+Rice` 0.0523)

+19.40% otb, +11.05% hyser, +1.37% capgmyo, +12.85% cemhsey. Compare the single best-partner
front-end (order-4): +18.44% / +11.31% / +1.37% / +13.08%. The cascade **adds** MI only on OTB
(+0.96 pp) and is neutral/negative elsewhere.

## Mechanism (why the slices are additive only on tight arrays)

CAR removes exactly one eigenvector — DC-across-array. On the small, tightly-coupled 64-ch OTB array
that global mode is physically real and largely *orthogonal* to the local pairwise mode best-partner
takes, so the two slices are genuinely additive (+0.96 pp on top of +18.44%). On the large 128-/320-ch
arrays the shared content is spatially **local** (P1-refinement); once best-partner has removed the
local pairwise slice, the residual's remaining array-mean energy is mostly independent noise, so the
gated CAR lift — when it fires — subtracts a *mismatched global basis* and injects slightly more noise
than it removes (−0.2 pp). On CapgMyo (|corr|≈0.29) the gate never clears → exactly no change.
**Frontier #1's "capture both slices everywhere" hope is disproven: the slices are additive only where
the global common-mode is a real eigenvector (tight arrays).**

## Pareto check

Cost 0.043 > best 0.0394. On hyser/cemhsey/capgmyo it has worse-or-equal ratio at higher cost →
dominated there by `LMS4+Rice+xchan_bestpartner`. **BUT on OTB it is the max-ratio corner (2.179540 —
higher than every registered codec) at cost 0.043 → genuinely non-dominated.** So it is **NOT
conclusively Pareto-dominated** → kept registered (analogous to `LMS+Rice+acar` on OTB).

## Sanity gates

- Max real ratio 2.1795× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; acar+bp round-trip OK, embedded_ok OK, neural_ok OK, cost 0.043.
- No incumbent regression: `LMS+Rice+xchan` reproduces (hyser 1.473819, otb 2.142618); the current
  best `LMS4+Rice+xchan_bestpartner` reproduces exactly (hyser 1.4803843, otb 2.161938).

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** Passes verification. Does not beat the current best
on the primary real set (Hyser 1.477 < 1.480) and is higher cost, so not promoted. But it is a
genuinely non-dominated max-ratio corner on OTB (the tight array where the global common-mode is real)
→ not retired. The two-stage cascade helps only on tight arrays; it is not a new global best. Port
recommendation unchanged.
