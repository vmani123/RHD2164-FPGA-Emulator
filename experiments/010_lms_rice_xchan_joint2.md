# 010 — LMS+Rice+xchan_joint2: joint asymmetric 2-parent adaptive sign-LMS spatial predictor

- **Cycle:** 10
- **Date:** 2026-07-19
- **Branch:** `compression-cycle-2026-07-19`
- **Candidate:** `LMS+Rice+xchan_joint2` (a.k.a. `xj2`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #2/#3 — the robust concrete joint solve)

The residual after one parent still carries *local* spatial MI on high-|corr| arrays. Removing a
second parent's contribution correctly requires a **joint** solve that accounts for parent–parent
covariance. Realize it as ONE joint backward-adaptive sign-sign LMS: predict channel c from BOTH
causal grid parents (up + left) with `pred=(w_u·x[up]+w_l·x[left])>>shift`, both taps co-adapting
against the SHARED post-subtraction residual `e=x[c]-pred`. This is the stochastic-gradient form of
the 2×2 normal-equations solve — explicitly overcoming BOTH retired failures: (a) unlike the summed
`xchan_multiparent`, the taps see the *actual* shared residual so they cannot double-count correlated
parents; (b) unlike the energy-preserving `iklt_adaptive` rotation, injection is **asymmetric**
(residual-only; parents left clean), so estimation noise never touches the parents. Zero side-info,
look-ahead 0, order-4 LMS + Rice back-end.

## Implementation

Only `research/registry.py`: `xj2_encode`/`xj2_decode` + helpers. Both taps re-derived by the decoder
from bit-identical reconstructed parents (idx<c) and the coded residual → ZERO side-info. Integer/
fixed only (shift=`ec.CROSS_SHIFT`=8). `rtl/`, `sim/` untouched. Registry self-test: round-trip OK,
emb_ok OK, neural OK, cost 0.0366.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok, neural_ok), from `results/cycle_bench.csv`, cost 0.0366:

| dataset | joint2 | current best (0.0394) | vs best | xchan gain (vs LMS+Rice) | best-partner xchan gain |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | 2.149676 | 2.161938 | −0.57% | +17.77% | +18.44% |
| **hyser_1dof_f1_s1 (128 ch)** | **1.493003** | 1.480384 | **+0.85%** | **+12.26%** | +11.31% |
| capgmyo_dba_s1 (128 ch) | 1.350441 | 1.350480 | −0.003% | +1.36% | +1.37% |
| cemhsey_s1_d1t1 (320 ch) | 1.954270 | 1.955547 | −0.065% | +13.01% | +13.08% |

4-set mean 1.73685 vs best 1.73709 (−0.014%, essentially tied); hyser+otb mean 1.82134 vs 1.82116
(+0.010%, essentially tied).

## Attribution

Temporal (order-4) and back-end (Rice) unchanged → the only lever is the **joint 2-parent spatial
front-end** vs the promoted best's single best-partner. **The joint solve genuinely recovers a second
parent's MI on the LARGE array:** hyser xchan gain **+12.26%** is the *highest of any codec on Hyser*
(beats best-partner +11.31% and single-fixed-parent +10.8%). **But on the tight OTB array joint2's
fixed up+left pair (+17.77%) is below best-partner's *selected* neighbour (+18.44%)** — where one
optimally-chosen partner already captures the dominant local mode, a second *fixed orthogonal* parent
adds less than partner *selection*.

## Mechanism (joint gradient fixes marginal-vs-multiple; selection vs count trades off by geometry)

The retired summed multi-parent used two *marginal* betas (each ⟨x_c,x_p⟩/⟨x_p,x_p⟩, estimated as if
its parent were the sole regressor) and over-subtracted the correlated parents' shared mode. Here both
taps descend the *shared* residual after both subtract, so each adapts to the correlation *remaining*
once the other's contribution is out — the marginal→multiple regression fix. That is why joint2 **adds**
where multiparent *lost* (hyser +12.26% joint vs multiparent's roughly +5% summed). The second spatial
degree of freedom (how many parents, jointly solved) and best-partner's degree (which single parent) are
substitutes, not complements: on large arrays with diffuse local structure the joint 2-parent wins; on
tight arrays with a dominant diagonal neighbour, selecting that one parent wins. Net: essentially tied
with the best on real-data ratio, at lower cost and zero side-info.

## Pareto check

Cost 0.0366 < best 0.0394. **joint2 DOMINATES the current best on hyser (higher ratio AND lower cost)
and capgmyo (≈equal ratio, lower cost); on otb and cemhsey it is a non-dominated cheaper corner (lower
ratio, lower cost).** Genuinely non-dominated everywhere — in fact **cost-dominant** across all four real
sets. NOT conclusively Pareto-dominated → kept registered. A strong zero-side-info value entry.

## Sanity gates

- Max real ratio 2.1497× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.0366.
- No incumbent regression (`LMS+Rice+xchan` hyser 1.473819, otb 2.142618; current best reproduces).

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** Passes verification. It **wins the primary Hyser
(+0.85%)** but **regresses on OTB (−0.57%) and CEMHSEY (−0.065%)**, and is a dead tie on the 4-set mean
(−0.014%) — so it does **not robustly beat the current best on real data** (the cycle-7 promotion bar was
a win on all 4 sets; a one-set win with two-set regressions and an aggregate tie does not clear it).
Kept as a genuinely non-dominated, **cost-dominant, zero-side-info** Pareto entry (cheaper than the best on
every real set; dominates it on hyser+capgmyo). The joint 2-parent solve is proven to **work** (frontier
#2/#3 spent *positive*), but it does not decisively beat single best-partner selection. Headline/port pick
unchanged.
