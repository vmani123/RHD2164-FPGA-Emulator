# 013 — LMS4+Rice+xchan_joint2_bpa: best-PAIR selection fused with the joint 2-parent solve

- **Cycle:** 13
- **Date:** 2026-07-25
- **Branch:** `compression-cycle-2026-07-25`
- **Candidate:** `LMS4+Rice+xchan_joint2_bpa`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey
- **Verdict: NOT PROMOTED, NOT RETIRED** — kept as a genuinely non-dominated corner (best embeddable ratio on the primary Hyser array of any registered codec)

## Hypothesis (INSIGHTS open-frontier #1 — stack, don't trade, the two spatial degrees of freedom)

P1b established that parent **selection** and parent **count** are *substitutes*: `xchan_joint2` (fixed
up+left pair, jointly solved) won the large Hyser array (+12.26% xchan gain, the highest of any codec)
but lost tight OTB, where best-partner *selection* wins with a single parent. The untried fusion:
jointly solve the best **pair** of causal neighbours, the pair re-selected per block from the previous
reconstructed block (à la `bestpartner_adaptive` → zero side-info). If selection and count stack, this
should clear the best on *both* array scales at once.

**Pre-registered risk (implementer's note + in-code):** *"P1b's substitution may bind again — if the
second jointly-solved parent adds little on tight arrays even when the pair is SELECTED, this lands as
another tie at the same spatial ceiling rather than a stacked win."*

## Implementation

`research/registry.py` only. APPLICATION is `xchan_joint2` verbatim (one joint 2-tap spatial predictor,
`pred = (w1·x[q1,t] + w2·x[q2,t]) >> 8`, both taps co-adapting sign-sign against the **same**
post-subtraction residual; parents left clean). SELECTION lifts `bestpartner_adaptive`'s per-block
backward re-selection from singles to **pairs**: the ≤C(4,2)=6 unordered pairs of the causal
4-neighbourhood, each scored from one **shared block Gram** (Scc, Scu, Suu, Sul) by 2×2 Cramer solve +
closed-form scaled-integer SSE, plus the no-pair option. Taps persist per candidate parent. Documented
deviation from `bpa`: pairs are scored by joint-LS SSE (O(1) from the Gram) rather than materialized
Rice-bits, to keep the codec inside the 125-cyc neural budget — the SSE is exactly the objective the
applied co-adaptive LMS descends, so the criterion stays matched to the mechanism.

Cost: enc_ops = dec_ops = 51/sample-ch → ~61 cyc/sample-ch, 34 state bytes/ch, block 256, zero
side-info, look-ahead 0, `embedded_ok` OK, `neural_ok` OK, **cost 0.0500**.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

REAL data, from `results/cycle_bench.csv` (all rows `ok=True`, `embedded=OK`, `neural=OK`):

| dataset | `joint2_bpa` (0.0500) | best `LMS4+Rice+xchan_bestpartner` (0.0394) | vs best | `xchan_joint2` fixed pair (0.0366) | vs joint2 |
|---|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 (primary) | **1.495635** | 1.480384 | **+1.030%** | 1.493003 | +0.176% |
| otb_hdsemg_vl | 2.144398 | 2.161938 | −0.811% | 2.149676 | −0.245% |
| capgmyo_dba_s1 | **1.350632** | 1.350480 | **+0.011%** | 1.350441 | +0.014% |
| cemhsey_s1_d1t1 | 1.951457 | 1.955547 | −0.209% | 1.954270 | −0.144% |
| **4-set mean** | **1.73553** | **1.73709** | **−0.090%** | 1.73685 | −0.008% |

Synthetic: synth_sc0.6 **2.6310** (2nd of 19, behind only `LMS+Rice+xchan_joint2` 2.6360),
synth_sc0.9 **2.6014** (2nd, behind 2.6100) — both **above** the leaderboard best (2.5960 / 2.5617).

## Cross-channel gain (isolated, REAL, vs temporal-only `LMS+Rice`)

| dataset | `joint2_bpa` | `xchan_joint2` (fixed pair) | best-partner (selected single) |
|---|---:|---:|---:|
| hyser_1dof_f1_s1 | **+12.45%** | +12.26% | +11.31% |
| otb_hdsemg_vl | +17.48% | +17.77% | **+18.44%** |
| capgmyo_dba_s1 | **+1.38%** | +1.36% | +1.37% |
| cemhsey_s1_d1t1 | +12.85% | +13.01% | +13.08% |

**+12.45% on Hyser is the highest achieved cross-channel gain of any codec ever measured on the primary
array** (previous record: joint2's +12.26%). Selection genuinely adds **+0.19 pp on top of the joint
solve** on the large array — but *subtracts* 0.29 pp on tight OTB and 0.16 pp on CEMHSEY.

## Attribution

Two levers moved vs the KEPT `xchan_joint2`, and only one of them is new: (a) predictor order 8→4
(P2, worth a small positive everywhere), (b) fixed up+left pair → per-block backward-**selected** pair.
Isolating (b) against the same-order sibling family: the selected pair beats the fixed pair on the two
128-ch arrays (+0.176% hyser, +0.014% capgmyo) and loses on the tight 64-ch OTB (−0.245%) and the 320-ch
CEMHSEY (−0.144%). The **entropy back-end and Rice-k are untouched** (P5), the temporal predictor is the
standard order-4 sign-sign LMS — so 100% of the movement is the **cross-channel front-end**, and the
character of the movement is *selection interacting with count*, not either alone.

Why selection helps the joint solve less than it helps a single parent: with two taps the front-end
already spans a 2-D subspace of the neighbourhood, so a *selected* pair and a *fixed* pair often span
nearly the same subspace — the marginal value of choosing which pair is small. On OTB it is actually
**negative**, because the per-block Gram-SSE criterion is estimated from one 256-sample block and, with
6 candidate pairs, the argmin over-fits that block's noise (selection variance) more than the single-parent
argmin over 4 candidates does. Selection cost scales with the candidate-set size; its benefit does not.

## Pareto check — NON-DOMINATED

Not dominated by anything: it has the **highest ratio of any registered codec on the primary Hyser set**
(1.495635 vs the best's 1.480384) and the highest on CapgMyo. It is *not* the best on OTB/CEMHSEY and it
is the most expensive of the joint-family (0.0500 > 0.0394 > 0.0366), so it does not dominate either.
Genuine Pareto corner → **KEPT registered**. Total real bytes 11 225 343 vs the best's 11 234 558
(byte-weighted it is ahead) — but the leaderboard ranks on per-dataset ratio + primary set + 4-set mean,
on which it is a −0.090% loss.

## Sanity gates

- Bit-exact: `ok=True` on all 6 datasets. ✅
- `embedded_ok` = OK, `neural_ok` = OK (61 cyc < 125-cyc neural budget). ✅
- Max real ratio 2.1795× (sweep-wide) ≪ 6× leak ceiling; this codec's max is 2.1444×. ✅
- Regression: −0.811% OTB, −0.209% CEMHSEY vs the best — the reason it is not promoted.

## Verifier verdicts

- **Verifier A: PROMOTE.**
- **Verifier B: PROMOTE.**
- **Unanimous, no split.**

## Decision

**NOT PROMOTED** — the promotion rule requires beating the current best *on real data*; this candidate
wins the primary Hyser (+1.03%) but regresses on OTB (−0.81%) and CEMHSEY (−0.21%) and is −0.090% on the
4-set mean. This is the identical shape of cycle 11's `xchan_joint2` verdict (won Hyser, lost OTB, dead
tie on the mean), and is decided the same way.

**NOT RETIRED** — it is not Pareto-dominated (highest Hyser ratio of any registered codec, zero side-info,
look-ahead 0). Kept in the default sweep.

## Mechanism (the durable lesson → INSIGHTS P1b refinement)

Fusing selection with count **does not stack** — it produces the *max* of the two levers on each array,
never their sum. On the large diffuse array (Hyser) count dominates and selection adds a residual
+0.19 pp; on the tight array (OTB) selection dominates but the *pair* criterion's estimation variance eats
it. Theoretical statement: both levers are attempts to better approximate the same object — the conditional
expectation `E[x_c | causal neighbourhood]` — and once the joint 2-tap solve has captured most of the
neighbourhood's mutual information, *which* pair you solve is a second-order refinement whose estimation
variance (6 candidates from a 256-sample Gram) is comparable to its bias reduction. **The spatial front-end
has hit a shared ceiling at ~1.48–1.50× on Hyser / ~2.15–2.18× on OTB that four structurally different
mechanisms now agree on within ±1%.** Further spatial degrees of freedom are not the lever.
