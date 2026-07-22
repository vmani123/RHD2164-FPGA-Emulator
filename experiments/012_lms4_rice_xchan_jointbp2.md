# 012 — LMS4+Rice+xchan_jointbp2: best-partner PAIR selection fused with a joint 2-tap adaptive sign-LMS

- **Cycle:** 12
- **Date:** 2026-07-22
- **Branch:** `compression-cycle-2026-07-22`
- **Candidate:** `LMS4+Rice+xchan_jointbp2` (a.k.a. `jbp2`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #1 — the one untried STACK of selection AND count)

`joint2` proved a jointly-solved second parent adds MI on large arrays (won Hyser) but used a *fixed*
up+left pair, so it lost the tight OTB array where partner *selection* matters; `bestpartner` won the
tight array by *selecting* one neighbour but is single-parent. Selection (which parent) and count (how
many, jointly solved) are **substitutes** by geometry (P1b) — the one untried lever is to **stack** them:
per channel/block, backward-adaptively SELECT the best *pair* of causal neighbours (per-block re-selection
from the previous reconstructed block; decoder mirrors → zero side-info), then predict from that pair with
ONE joint co-adaptive sign-sign LMS whose two spatial taps descend the *shared* post-subtraction residual.
Prediction: selection recovers the tight-array corner while the joint count recovers the large-array MI.

## Implementation

`research/registry.py` only: `_jbp2_select_block` (Stage 1, per-block integer-LS pair/single/none search
reusing the best-partner scoring path with a genuine 2×2 joint solve for pairs), `_jbp2_forward`/
`_jbp2_inverse` (Stage 2, joint 2-tap sign-sign LMS, asymmetric residual-only injection). Order-4 LMS +
adaptive Rice back-end. Header carries only magic/cols/C/N → ZERO side-info; both the selected pair and the
two taps are recomputed by the decoder from bit-identical reconstructed history (all parents idx<c). Integer/
fixed only; big-int intermediates in the 2×2 solve. `rtl/`, `sim/` untouched. Registry self-test: round-trip
OK, emb_ok OK, neural OK, cost 0.0468.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok OK, neural_ok OK), from `results/cycle_bench.csv`, cost 0.0468:

| dataset | jointbp2 | current best `bestpartner` (0.0394) | vs best | jbp2 xchan gain (vs LMS+Rice) | best-partner xchan gain |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | 2.152244 | 2.161938 | −0.448% | +17.91% | +18.44% |
| **hyser_1dof_f1_s1 (128 ch)** | **1.496924** | 1.480384 | **+1.117%** | **+12.55%** | +11.31% |
| capgmyo_dba_s1 (128 ch) | 1.350287 | 1.350480 | −0.014% | +1.35% | +1.37% |
| cemhsey_s1_d1t1 (320 ch) | 1.952260 | 1.955547 | −0.168% | +12.89% | +13.08% |

4-set mean +0.122% (driven entirely by the Hyser win). `LMS+Rice` temporal baseline (for the xchan-gain
column): otb 1.825352, hyser 1.329992, capgmyo 1.332259, cemhsey 1.729317 (all from `cycle_bench.csv`).

## Attribution

Temporal (order-4) and back-end (Rice) unchanged → the only lever is the spatial front-end: **best-pair
selection + joint 2-tap solve** vs the promoted best's single selected parent. **The stack genuinely works
on the large array:** jointbp2's Hyser xchan gain **+12.55%** is the *highest of any codec on Hyser* — above
`joint2` (+12.26%, fixed pair) and above `bestpartner` (+11.31%, single selected). Selecting the *best pair*
AND jointly solving it recovers more MI than either lever alone, exactly as frontier #1 predicted, on the
diffuse-local large array. **But on the tight 64-ch OTB it is +17.91% — still BELOW best-partner's single
selected neighbour (+18.44%)** (though above `joint2`'s fixed-pair +17.77%). Where one diagonal neighbour
carries the dominant local mode, a jointly-solved second parent adds less than it costs in prediction noise;
selecting the *pair* does not rescue it. capgmyo/cemhsey ≈ neutral to slightly negative.

## Pareto check

Cost 0.0468 (highest of the cross-channel family). jointbp2 has the **highest embeddable Hyser ratio of any
codec (1.496924)** → on that axis nothing dominates it, so it is **non-dominated** (not Pareto-dominated,
not retired). But it is a costlier corner than the best and loses on 3 of 4 real sets, so it is not a Pareto
improvement over `bestpartner`.

## Sanity gates

- Max real ratio 2.1522× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.0468.
- No incumbent regression (`LMS4+Rice+xchan_bestpartner` reproduces its registered ratios exactly).

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** Passes verification and **wins the primary Hyser (+1.117%,
the highest embeddable Hyser ratio measured)**, but **regresses on OTB (−0.448%) and CEMHSEY (−0.168%)** and
is ≈neutral on CapgMyo → it does **not robustly beat the current best on real data** (the promotion bar,
cycle 7, is a win across the real sets, not a one-set win with two-set regressions; same disposition as
`joint2`, cycle 11). Kept as the non-dominated **max-Hyser corner**. Frontier #1 is now spent: the
selection+count *stack* is confirmed to work (highest large-array gain of any codec) but does **not** clear
the best on tight arrays, so it does not become a global best. Headline/port pick unchanged.
