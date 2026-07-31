# 016 — LMS4+Rice+rxchan_bp: residual-domain cross-channel prediction (MPEG-4 ALS order-swap) — RETIRED

- **Cycle:** 16
- **Date:** 2026-07-31
- **Branch:** `compression-cycle-2026-07-31`
- **Candidate:** `LMS4+Rice+rxchan_bp`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (SURVEY cycle-16 pick #2 — move the spatial operator's DOMAIN, ~cost-neutral)

The shipped `+xchan` family subtracts in the **raw** domain and, worse, *selects* (partner, β) by scoring the
raw decorrelated channel — while the bits actually paid are the entropy of the **post-LMS residual**. Raw
energy is dominated by low-frequency shared power the temporal predictor removes anyway, so the estimator
optimizes a mis-weighted proxy. Running the order-4 LMS on each unmodified raw channel and coding
`e_c − (β·e_p)>>s`, with (partner, β) chosen by the Rice bits of that *actually-coded* quantity, makes the
selection objective and the rate the same functional. This is literally the MPEG-4 ALS multichannel tool and
Choi et al.'s residual-cross-correlation recipe; the shipped codec is a raw-domain simplification of its own
cited source, never tested against it. Not an algebraic rearrangement: each channel carries its own adaptive
filter, so `P_c(x_c − βx_p) ≠ P_c(x_c) − βP_p(x_p)` whenever `P_c ≠ P_p`.

## Implementation

`research/registry.py` only (`_rxbp_select_block`, `_rxbp_forward/_inverse`, `rxbp_encode/decode`,
`RXBP_MAGIC 0x5852`). Per-block backward re-derivation of (partner, β) in the residual domain over the same
≤4 causal neighbours, asymmetric (the parent's own coded stream `e_p` is read clean), zero side-info,
look-ahead 0. `enc_ops = dec_ops = _LMS4_OPS + _XCHAN_OPS + _LMS4BPA_SELECT` → **cost 0.0387, exactly equal to
`LMS4+Rice+xchan_bestpartner_adaptive`** (the raw-domain codec with the *same* per-block zero-side-info
selection) — a clean equal-cost head-to-head of the two domains. Self-test passed first run; extra shapes
(8,257,4), (16,1000,16), (5,63,2), (64,600,8), (3,300,1), (12,2049,3) and an int16-saturation case all
bit-exact.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (`results/cycle_bench.csv`, all `ok=True`, `embedded=OK`, `neural=OK`), cost 0.0387:

| dataset | rxchan_bp | raw-domain twin `bpa` (0.0387) | vs twin | best `bestpartner` (0.0394) | vs best | xchan gain |
|---|---:|---:|---:|---:|---:|---:|
| **hyser_1dof_f1_s1** (primary) | 1.468615 | 1.477020 | −0.57% | 1.480384 | **−0.795%** | +10.42% |
| otb_hdsemg_vl | 2.097478 | 2.153106 | −2.58% | 2.161938 | **−2.982%** | +14.91% |
| capgmyo_dba_s1 | 1.350009 | 1.352866 | −0.21% | 1.350480 | −0.035% | +1.33% |
| cemhsey_s1_d1t1 | 1.940736 | 1.953948 | −0.68% | 1.955547 | **−0.757%** | +12.23% |

4-set mean 1.714209 vs the best's 1.737087 (−1.32%). Synthetic (mechanism only): sc0.6 2.579006,
sc0.9 2.528825 — below best-partner on both.

## Attribution

The predictor (order-4 sign-LMS), the back-end (adaptive Rice), the candidate neighbourhood, the integer-LS
β and the Rice-bits scoring functional are all identical to `bestpartner_adaptive`; **the only difference is
that the subtract and its estimator act on `e` instead of `x`.** So the entire −0.57…−2.58% is attributable
to the domain swap, at exactly equal cost.

**Theory for the loss (the hypothesis's own stated risk, resolved in the opposite direction).** The
hypothesis assumed the two operators nearly commute and the gain would come from the better-aligned
objective. What real data shows is that they do *not* commute, and non-commutation **destroys** MI rather than
exposing it: `e_c = P_c(x_c)` and `e_p = P_p(x_p)` are produced by two *independently adapted* whitening
filters. Whitening is exactly the operation that removes the predictable, i.e. *shared*, low-frequency
structure — the same structure the spatial subtract lives on. Once each channel has been whitened by its own
`P`, what remains is close to the innovation process of each channel, and two innovation processes are far
less correlated than the two raw signals (their coherence is what survives `P_c(ω)P_p*(ω)S_cp(ω)` — a
high-pass-weighted remnant of `S_cp`). Aligning the estimator with the rate objective cannot compensate,
because by then there is much less cross-channel MI left to estimate. **Order matters: decorrelate across
channels FIRST (while the shared source is still present), whiten in time SECOND.** The isolated
cross-channel gain confirms it directly: +14.91% OTB vs the raw-domain +18.44%, +10.42% Hyser vs +11.31%,
+12.23% CEMHSEY vs +13.08% — the residual-domain subtract recovers ~80–85% of the raw-domain spatial gain
everywhere.

## Cross-channel gain, isolated, on real data

**+10.42% hyser, +14.91% otb, +1.33% capgmyo, +12.23% cemhsey** (vs `LMS+Rice` in the same run) — strictly
below the raw-domain best-partner's +11.31% / +18.44% / +1.37% / +13.08% on every set.

## Pareto check

Cost 0.0387 equals `LMS4+Rice+xchan_bestpartner_adaptive`'s 0.0387 exactly (same op accounting), and `bpa` is
**strictly higher on all four real sets**. That is textbook Pareto domination (no objective on which
rxchan_bp is better) — the same standard under which the always-on `acar+bestpartner` was retired at equal
cost in cycle 12. It is additionally below the leaderboard best on all four sets.

## Sanity gates

- Max real ratio in the run 2.179540 ≪ 6× ceiling; rxchan_bp's own max 2.097478. No leak.
- No FAIL bit-exact rows anywhere (`ok=True` for all 120 rows); `embedded=OK`, `neural=OK`.
- No regression to the recorded best (`bestpartner` reproduces 1.480384 / 2.161938 / 1.350480 / 1.955547).

## Verifier verdicts

- **Verifier A: PROMOTE**
- **Verifier B: PROMOTE**
- Unanimous, no split (verification covers correctness/embeddability, not ratio).

## Decision

**NOT promoted** (loses on all 4 real sets). **RETIRED** — `retired=True` + `retired_reason` set on its
`Codec(...)` registration in `research/registry.py`; conclusively Pareto-dominated by
`LMS4+Rice+xchan_bestpartner_adaptive` at equal cost 0.0387 on all four real sets. Kept bit-exact and
re-checkable with `--include-retired`. The durable learning (operator ORDER: spatial-before-temporal) is
recorded as INSIGHTS **P6**.
