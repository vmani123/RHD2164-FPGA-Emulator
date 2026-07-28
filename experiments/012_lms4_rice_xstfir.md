# 012 — LMS4+Rice+xstfir: delay-compensated spatio-temporal cross-channel FIR

- **Cycle:** 13
- **Date:** 2026-07-28
- **Branch:** `compression-cycle-2026-07-28`
- **Candidate:** `LMS4+Rice+xstfir` (cost 0.0474, zero side-info, look-ahead 0)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis

Every registered spatial front-end subtracts the parent at **lag 0 only**
(`beta*x_p[n]`), so its achievable gain is capped by the *zero-lag* neighbour
correlation ρ(0). A motor-unit action potential **propagates** along the fibre, so
the redundancy between two electrodes is a *delayed* copy, not a scaled
simultaneous one — the true cross-channel MI sits at ρ(τ) for some τ > 0.
Replace the scalar subtract with a 4-tap spatio-temporal FIR

```
pred[c,n] = (w0*x_p[n] + w1*x_p[n-1] + w2*x_p[n-2] + w3*x_q[n-1]) >> 8
e[c,n]    = x[c,n] - pred[c,n]
```

(3 parent lags = a fractional-delay FIR; `x_q[n-1]` = the *opposite-side* grid
neighbour, index > c, legally readable only at lag ≥ 1 — which is why the stage
runs **time-major**), all four taps co-adapting by **one joint sign-sign LMS**
against the shared post-subtraction residual. Prediction: LAG is the freedom
orthogonal to the "which parent / how many parents" axis P1b showed are
substitutes (both capped by ρ(0)); it should raise the cap.

## Implementation

Only `research/registry.py`: `XSTFIR_*`, `_xstfir_neighbours/_xstfir_taps/_xstfir_upd/`
`_xstfir_forward/_xstfir_inverse`, `xstfir_encode/xstfir_decode`, plus the
`Codec("LMS4+Rice+xstfir", ...)` registration. Order-4 sign-sign LMS (P2) +
adaptive Rice (P5) behind it, unchanged. Asymmetric (residual-only injection;
parent and opposite-neighbour rows are inputs, left clean). Integer-only, no
float, 12-byte header, **no side-info**. `rtl/`, `sim/` untouched. Registry
self-test: round-trip OK, `emb_ok` OK, `neural` OK, cost 0.0474.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data, from `results/cycle_bench.csv` (every row bit-exact `ok=True`, `embedded=OK`):

| dataset | `xstfir` (0.0474) | best `LMS4+Rice+xchan_bestpartner` (0.0394) | vs best | xchan gain (vs `LMS+Rice`) | best's xchan gain |
|---|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 (128 ch) | 1.472533 | 1.480384 | **−0.53%** | +10.72% | +11.31% |
| otb_hdsemg_vl (64 ch) | 2.108745 | 2.161938 | **−2.46%** | +15.53% | +18.44% |
| capgmyo_dba_s1 (128 ch) | **1.362200** | 1.350480 | **+0.87%** | **+2.25%** | +1.37% |
| cemhsey_s1_d1t1 (320 ch) | 1.950256 | 1.955547 | **−0.27%** | +12.78% | +13.08% |

`capgmyo_dba_s1` 1.362200× is the **highest ratio of any codec, embeddable or
offline reference, on that set** (next: `bpa` 1.352866×, lzma 1.173168×).

## Isolated mechanism measurement (REAL data, achieved — not a ceiling)

The lag taps were switched off **in memory** (`_xstfir_taps` monkey-patched in a
scratch script; the repo was not modified), so every other byte of the codec —
order-4 LMS, Rice, the joint sign-sign update, the time-major order, the parent
choice — is identical. Three variants, same real arrays, 15 000 samples:

| dataset | (A) full 4-tap | (B) zero-lag only `w0*x_p[n]` | (C) parent FIR, no `x_q` | **lag taps = A/B** | **opposite-side tap = A/C** | **parent lags 1,2 = C/B** |
|---|---:|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 | 1.472533 | 1.474437 | 1.471365 | **−0.12%** | +0.07% | **−0.20%** |
| otb_hdsemg_vl | 2.108745 | 2.137520 | 2.076327 | **−1.35%** | **+1.56%** | **−2.86%** |
| capgmyo_dba_s1 | 1.362200 | 1.351382 | 1.347602 | **+0.80%** | **+1.08%** | **−0.28%** |
| cemhsey_s1_d1t1 | 1.950256 | 1.954517 | 1.951742 | **−0.22%** | −0.08% | **−0.14%** |

Two clean facts:

1. **The parent's own lags 1,2 — the "fractional-delay FIR", the actual hypothesis —
   are NEGATIVE on all four real sets** (−0.14% to −2.86%).
2. **The opposite-side neighbour tap `x_q[n-1]` is the whole positive effect**
   (+1.56% OTB, +1.08% CapgMyo, +0.07% Hyser, −0.08% CEMHSEY).

So the CapgMyo win is *not* delay compensation; it is a **second spatial parent on
the far side of the channel**, reachable only because the time-major loop lets the
stage read a higher-index channel at lag 1.

## Attribution

The temporal predictor (order-4 sign-sign LMS) and the entropy back-end (adaptive
Rice) are byte-identical to the promoted best's, and there is no side-info. **100%
of the movement is the cross-channel front-end.** Within that front-end the
isolation above splits it further: parent-lag FIR = negative everywhere;
opposite-side lag-1 neighbour = the only positive term.

**Mechanism.** (a) `x_p[n-1]`, `x_p[n-2]` are *the same channel's* short-lag past.
At 1–2 kS/s HD-sEMG they are strongly collinear with `x_p[n]`, and after the
order-4 temporal LMS has whitened channel c's own past there is essentially no
conditional information left: I(x_c[n]; x_p[n−1], x_p[n−2] | x_p[n]) ≈ 0. Adding
two near-collinear regressors to a stochastic-gradient (sign-sign) filter therefore
adds **misadjustment noise with no MI to pay for it** — the classic
over-parameterisation penalty, worst exactly where ρ(0) is largest and the taps
matter most (OTB, −2.86%). (b) `x_q[n−1]` is a **different channel**: it carries a
genuinely new slice of spatial MI, and lag 1 is merely the causality-legal way to
read a higher-index row. Its payoff is largest where the *zero-lag* pairwise
channel is weakest — CapgMyo (neighbour |corr| ≈ 0.29) gets +1.08% from it against
Hyser's +0.07%.

## Pareto check

- Cost 0.0474 > the best's 0.0394 and > `bpa` 0.0387, `joint2` 0.0366.
- Ratio: below every top registered codec on hyser / otb / cemhsey, **above all of
  them on capgmyo**.
- ⇒ **NOT Pareto-dominated** (no registered codec has ≥ ratio on *all four* real
  sets at ≤ cost). It is a genuine non-dominated **max-ratio corner on CapgMyo**,
  the low-zero-lag-correlation array. Kept registered, **not retired**.

## Sanity gates

- Max real ratio in the run 2.1795× (`acar+bestpartner`, OTB) ≪ the 6× leak ceiling;
  `xstfir`'s own max 2.1087×.
- All 102 rows of `results/cycle_bench.csv` `ok=True` (bit-exact); no FAIL.
- No regression: the leaderboard best reproduces exactly (hyser 1.480384, otb
  2.161938, capgmyo 1.350480, cemhsey 1.955547); search best `lms4s7+x6/b512`
  1.8204/0.027 unchanged.
- Extra edge-shape round-trips (C=1, N=1, cols=1, C∤cols, N=257, C<cols) reported
  bit-exact by the implementer.

## Verifier verdicts

- **Verifier A: PROMOTE**
- **Verifier B: PROMOTE**
- **Unanimous, no split.**

## Decision

**Not promoted** (loses the primary Hyser −0.53% and 3 of 4 real sets; wins only
CapgMyo). **Not retired** (non-dominated CapgMyo max-ratio corner). Registered and
kept. The "port next" headline is unchanged.

**Durable learning (INSIGHTS P1d):** LAG along the *same* parent is a dead axis —
short-lag parent history is collinear with the parent and carries no conditional MI
once order-4 has whitened the coding channel; it only buys gradient misadjustment.
The *opposite-side* neighbour, however, is a real and previously unreachable slice
of spatial MI, and it pays **inversely** to zero-lag neighbour correlation. The
lever to keep from this experiment is **time-major coding order** (it makes
higher-index channels legally readable at lag 1), not the fractional-delay FIR.
