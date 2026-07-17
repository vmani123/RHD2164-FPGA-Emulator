# 007 — LMS+Rice+xchan_multiparent: two-parent (up+left) summed rank-1 subtract

- **Cycle:** 7
- **Date:** 2026-07-16
- **Branch:** `compression-cycle-2026-07-16`
- **Candidate:** `LMS+Rice+xchan_multiparent`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis

Extend the single grid-parent to TWO causal parents per channel — up (g−cols) and left (g−1), each
with its OWN backward-adaptive integer β = ⟨x_c,x_p⟩/⟨x_p,x_p⟩, the two rank-1 residual subtracts
SUMMED. A rank-2 *local* decorrelation realized as two independent asymmetric rank-1 subtracts (not a
joint 2×2 solve), each leaving the raw parent clean → robust under estimation noise (P3-refinement).
Zero side-info (both β recomputed by the decoder). Target the residual *local* spatial MI one parent
leaves on high-|corr| arrays (INSIGHTS open-frontier #2).

## Implementation

Only `research/registry.py`: `_mp_forward`/`_mp_inverse`/`mp_encode`/`mp_decode`, `MP_MAGIC`. Both
parents have grid index < c → their rows are fully reconstructed before channel c; within a channel,
block i's β come from the already-reconstructed block i−1 (block 0 bootstraps β=0). Order-8 sign-sign
LMS + adaptive Rice back-end unchanged. Integer-only, causal, look-ahead 0. `rtl/`, `sim/` untouched.
Self-test: round-trip OK, emb_ok OK, neural OK (enc_ops=62 → 74.4 cyc/sample-ch < 125 neural budget),
cost 0.078; `registry self-test: ALL round-trips bit-exact`.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok), from `results/cycle_bench.csv`, cost 0.0777:

| dataset | multiparent | incumbent LMS+Rice+xchan (0.0572) | iso. gain vs LMS+Rice (0.0523) | single-parent xchan gain |
|---|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | 1.9714× | **2.1426×** | +8.0% (1.8254×) | +17.4% |
| hyser_1dof_f1_s1 (128 ch) | 1.3976× | **1.4738×** | +5.1% (1.3300×) | +10.8% |
| capgmyo_dba_s1 (128 ch) | 1.3471× | **1.3493×** | +1.1% (1.3323×) | +1.3% |
| cemhsey_s1_d1t1 (320 ch) | 1.8725× | **1.9551×** | +8.3% (1.7293×) | +13.1% |

## Attribution

Predictor and back-end unchanged → the only lever is the two-parent front-end. It captures roughly
**HALF** the single-parent cross-channel gain (otb +8.0% vs +17.4%; hyser +5.1% vs +10.8%; cemhsey
+8.3% vs +13.1%). **Adding a second parent HURT.**

**Mechanism (theory):** the two parents (up, left) are themselves spatially correlated
(Cov(up,left) > 0 — they are neighbours of each other as well as of c). Each marginal
β_p = ⟨x_c,x_p⟩/⟨x_p,x_p⟩ is the correct coefficient only when its parent is the *sole* regressor; the
correct *joint* coefficients (2×2 normal-equations solve) are smaller and account for the parent–parent
covariance. Summing two independent marginal subtracts therefore **double-counts the parents' shared
common mode → over-subtracts**, injecting more noise into the residual than the extra MI it removes.
This is the textbook gap between *marginal* and *multiple* regression under collinear predictors. A
correct multi-parent decorrelation needs a **joint** solve — which is exactly the multi-tap
rotation/lifting already proven a dead end (P3). So richer spatial *topology* only pays via joint
decorrelation, not a sum of independent rank-1 subtracts.

## Pareto check

**Conclusively Pareto-dominated on ALL 4 real sets** by `LMS+Rice+xchan` (worse ratio AND higher cost
0.078 > 0.057), and further by `LMS4+Rice+xchan_bestpartner`, `LMS+Rice+acar`, and `delta+Rice+xchan`.
Not on the front on any real set.

## Sanity gates

- Max real ratio 1.971× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.078.
- No incumbent regression.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous on correctness/embeddability only, no split.

## Decision

**RETIRED (conclusively Pareto-dominated on all 4 real sets); NOT promoted.** Passes verification but
loses on every real set at higher cost. `retired=True` in `research/registry.py` (kept bit-exact,
excluded from the default sweep, `--include-retired` re-checks). Durable learning: summed independent
marginal rank-1 subtracts over-subtract correlated parents — multi-parent needs a joint solve.
