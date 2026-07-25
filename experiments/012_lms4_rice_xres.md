# 012 — LMS4+Rice+xres: residual-domain (innovation) rank-1 cross-channel subtract

- **Cycle:** 13
- **Date:** 2026-07-25
- **Branch:** `compression-cycle-2026-07-25`
- **Candidate:** `LMS4+Rice+xres`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey
- **Verdict: NOT PROMOTED → RETIRED** (conclusively Pareto-dominated on all 4 real sets)

## Hypothesis (SURVEY cycle-13 pick #1; axis = *domain* of the spatio-temporal cascade)

Move the cross-channel subtract from **before** the temporal predictor to **after** it: code
`ê_c[t] = e_c[t] − (β·e_p[t] >> 8)` against the parent's already-computed order-4 LMS residual,
with β backward-adaptive (sign-sign on the residual pair) and the partner re-selected per block by
estimated Rice bits of `ê_c` (Choi's DF-on-residuals gate). Information-theoretic argument: Rice
length grows like `log2 E|ê_c|`, so the coding-optimal gain is `argmin_β Var(e_c − β e_p)` — an
**innovation-domain** functional — whereas every incumbent front-end estimates
`argmin_β Var(x_c − β x_p)`, i.e. it minimizes **raw power**. The two objectives coincide only when
both channels' temporal predictors are identical; since each channel's LMS taps adapt independently
they should differ, and the incumbent should be leaving shared *innovation* (temporally-unpredictable
common drive/reference noise) on the table.

**Pre-registered risk (from the implementer's note and the codec's in-code comment):** *"if the
per-channel LMS taps converge to near-identical values across the array, the raw and residual domains
nearly coincide and the gain collapses toward zero."*

## Implementation

`research/registry.py` only (`rtl/`, `sim/`, `host_tools/embedded_codec.py` untouched). New section
`XRES_*` + `_xres_select_block` / `_xres_forward` / `_xres_inverse` + `xres_encode` / `xres_decode`.
Temporal stage is `ec.lms_forward(x, order=4)` run independently per channel on the RAW signal (P2
respected); spatial stage is an asymmetric rank-1 injection into the innovation row only (parent row
left clean); β is a multiplierless ±1 sign-sign update clamped to int16; partner re-selected per block
from the **previous** block of innovations over the ≤4 causal grid neighbours, with the no-partner
option scored too. Zero side-info, look-ahead 0, integer-only. Registry self-test bit-exact on first
run; implementer additionally round-tripped 11 degenerate/saturation cases bit-exact.

Cost (`research/embedded_cost.py`): enc_ops = dec_ops = 42/sample-ch → 50.4 cyc/sample-ch,
28 state bytes/ch, block 256, `embedded_ok` OK, `neural_ok` OK, **cost 0.0412**.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

REAL data, from `results/cycle_bench.csv` (all rows `ok=True`, `embedded=OK`, `neural=OK`):

| dataset | `xres` (cost 0.0412) | best `LMS4+Rice+xchan_bestpartner` (0.0394) | vs best | `xres` comp bytes |
|---|---:|---:|---:|---:|
| hyser_1dof_f1_s1 (primary) | 1.466300 | 1.480384 | **−0.951%** | 2 618 837 |
| otb_hdsemg_vl | 2.093606 | 2.161938 | **−3.161%** | 917 078 |
| capgmyo_dba_s1 | 1.349227 | 1.350480 | **−0.093%** | 2 846 074 |
| cemhsey_s1_d1t1 | 1.937150 | 1.955547 | **−0.941%** | 4 955 734 |
| **4-set mean** | **1.7116** | **1.7371** | **−1.47%** | — |

Synthetic (mechanism illustration only): synth_sc0.6 2.5774 vs best 2.5960; synth_sc0.9 2.5227 vs 2.5617
— **also below** the raw-domain best, i.e. the synthetic shared-innovation grid the implementer used to
show the lever was "live" does not reproduce in the harness's synthetic sets either.

## Cross-channel gain (isolated, REAL, vs temporal-only `LMS+Rice`, the established convention)

| dataset | `xres` achieved | best-partner (raw domain) achieved | Δ |
|---|---:|---:|---:|
| otb_hdsemg_vl | **+14.70%** | +18.44% | −3.74 pp |
| hyser_1dof_f1_s1 | **+10.25%** | +11.31% | −1.06 pp |
| capgmyo_dba_s1 | **+1.27%** | +1.37% | −0.10 pp |
| cemhsey_s1_d1t1 | **+12.02%** | +13.08% | −1.06 pp |

The residual-domain subtract **is live** — it captures +10.2…+14.7% of real cross-channel gain, so the
mechanism works — but it captures **strictly less** than the same rank-1 subtract applied in the raw
domain, on every real array.

## Attribution

Only one thing changed vs the KEPT `LMS4+Rice+xchan_bestpartner_adaptive` (also order-4, also per-block
backward re-selection, also zero side-info): the **domain** the subtract and the selection operate in.
Temporal predictor (order-4 sign-sign LMS) and entropy back-end (adaptive per-block Golomb-Rice) are
unchanged. So the entire −0.95…−3.16% is attributable to the **cross-channel front-end's domain**, and
specifically to the innovation domain being a *worse* place to estimate the rank-1 gain than the raw
domain. Against the zero-side-info sibling `bpa` (2.153106 otb / 1.477020 hyser) the give-up is
−2.77% otb / −0.70% hyser — the comparison that isolates domain alone.

## Pareto check

Dominated: worse ratio than `LMS4+Rice+xchan_bestpartner` on **all 4** real sets **and** higher cost
(0.0412 > 0.0394). Also worse than the cheaper `LMS4+Rice+xchan_bestpartner_adaptive` (0.0387) and the
cheaper `LMS+Rice+xchan_joint2` (0.0366) on all 4. Not a corner on any axis — including throughput
(0.51–0.64 MB/s enc, the slowest embeddable codec in the sweep).

## Sanity gates

- Bit-exact: `ok=True` on all 6 datasets. ✅
- `embedded_ok` = OK, `neural_ok` = OK on all 6. ✅
- Max real ratio in the whole sweep 2.1795× (acar+bestpartner, OTB) ≪ 6× leak ceiling. ✅
- Regression check: `xres` regresses vs the leaderboard best on every real set — that *is* the finding.

## Verifier verdicts

- **Verifier A: PROMOTE** (correctness/embeddability gate — bit-exact round-trip re-run, cost audit).
- **Verifier B: PROMOTE** (same gate, independent re-run).
- **Unanimous, no split.** Note: the verifier gate is correctness/embeddability only; it does not
  imply the codec beats the best (it does not).

## Decision

**NOT PROMOTED** (does not beat the current best on real data — loses on all 4 sets).
**RETIRED** — `retired=True` + `retired_reason` set on the `Codec("LMS4+Rice+xres", …)` registration
in `research/registry.py`.

## Mechanism / why it lost (the durable lesson → INSIGHTS P1c)

The pre-registered risk is exactly what the data shows. Two compounding reasons:

1. **Domain collapse.** All channels of an HD-sEMG array see the same band-limited process, so their
   independently-adapted order-4 sign-sign LMS taps converge to *near-identical* values. When
   `w_c ≈ w_p`, the innovation `e = (1 − W(z))x` is the same *fixed* whitening filter applied to both
   rows, and a linear filter commutes with the rank-1 subtract: `e_c − β e_p ≈ (1 − W(z))(x_c − β x_p)`.
   The two domains are therefore **almost the same subspace**, so there is no extra MI to harvest — the
   hypothesis's "the objectives genuinely differ" premise fails empirically on this data.
2. **Worse-conditioned regressor.** Given (1), the residual-domain estimator gets no new information but
   pays a real penalty: it estimates β from the *whitened* pair, which has a far lower SNR for the gain
   than the raw pair (whitening strips the low-frequency shared power that made the raw regressor
   well-conditioned, leaving a near-white, high-variance regressor). Estimation noise in β then injects
   directly into the coded innovation. That is the −1 to −3.7 pp of lost cross-channel gain.

The OTB result (−3.74 pp, the largest loss) confirms the mechanism: OTB is the tightest, most strongly
common-mode array, so it has the most low-frequency shared power for the raw-domain estimator to exploit
and the most to lose when the estimator is moved into the whitened domain.
