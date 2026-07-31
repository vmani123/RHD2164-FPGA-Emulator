# 017 — LMS4+Rice+xchan_scalesel: scale-gated spatial front-end (best-partner C≤64 / jointbp2 C≥128)

- **Cycle:** 16
- **Date:** 2026-07-31
- **Branch:** `compression-cycle-2026-07-31`
- **Candidate:** `LMS4+Rice+xchan_scalesel`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (INSIGHTS open-frontier #1, verbatim — the scale-gated spatial front-end)

Cycle 13 measured the per-scale optimum: on the tight 64-ch OTB array the single *selected* best-partner wins
(+18.44% xchan gain > `jointbp2`'s +17.91%), while on the large 128-ch primary Hyser the jointly-solved
*selected best pair* wins (+12.55%, the highest of any codec, 1.4969× = +1.12% over the best). No **fixed**
front-end wins both scales (P1b-refinement). `acar_sel` proved that a zero-side-info, header-read
channel-count gate reproduces both regimes exactly (P1-refinement). Gating best-partner (C≤64) against
jointbp2 (C≥128) should therefore be the first construction that clears the best on the primary Hyser *and*
holds the tight-array OTB corner.

## Implementation

`research/registry.py` only (`_scalesel_use_pair`, `scalesel_encode/decode`, `SCALESEL_MAGIC 0x4753`,
threshold `SCALESEL_MAX_CH=64`). A pure meta-gate over two already-verified primitives reused verbatim:
`_bp_select`/`_bp_inverse` (identical to `LMS4+Rice+xchan_bestpartner`) for C≤64, `_jbp2_forward/_inverse`
(identical to `LMS4+Rice+xchan_jointbp2`) for C≥128. The gate is an integer compare on the header-read `C`,
decidable before any payload parsing → zero side-info, zero circularity, O(1)/recording; it also tells the
decoder which payload layout follows. Cost 0.0472 (worst-case branch). Self-test OK; round-trips verified at
C = 1, 2, 64, 65, 128, 129, 320 and N=257, plus an all-zero array — all bit-exact. Payload after the 12-byte
header is **byte-identical** to `lms4bp_encode` at C=32 and to `jbp2_encode` at C=128, proving each branch is
the verified primitive.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (`results/cycle_bench.csv`, all `ok=True`, `embedded=OK`, `neural=OK`), cost 0.0472:

| dataset | C | branch | scalesel | best `bestpartner` (0.0394) | vs best | `jointbp2` (0.0468) | `acar_sel` (0.043) |
|---|--:|---|---:|---:|---:|---:|---:|
| **hyser_1dof_f1_s1** (primary) | 128 | jointbp2 | **1.496924** | 1.480384 | **+1.117%** | 1.496924 | 1.480384 |
| otb_hdsemg_vl | 64 | best-partner | 2.161938 | 2.161938 | ±0.000% | 2.152244 | **2.179540** |
| capgmyo_dba_s1 | 128 | jointbp2 | 1.350287 | 1.350480 | −0.014% | 1.350287 | 1.350480 |
| cemhsey_s1_d1t1 | 320 | jointbp2 | 1.952260 | 1.955547 | **−0.168%** | 1.952260 | 1.955547 |

Each branch inherits its primitive's ratio **exactly** (scalesel = `bestpartner` on OTB to 6 decimals,
= `jointbp2` on all three large arrays to 6 decimals) — the gate is verified end-to-end on real data.
4-set mean 1.740352 vs the best's 1.737087 (+0.188%) and vs `acar_sel`'s 1.741488 (−0.065%).
Isolated cross-channel gain (vs `LMS+Rice`): hyser **+12.55%**, otb **+18.44%**, capgmyo +1.35%,
cemhsey +12.89% — i.e. it picks up the max-Hyser gain *and* the max-tight-array gain in one codec.

## Attribution

Nothing new in the predictor (order-4 sign-LMS) or the back-end (adaptive Rice); the lever is **which verified
spatial front-end runs**, selected by array scale. The +1.117% on Hyser is `jointbp2`'s rank≥2 local structure
gain (selection ⊕ count stacking where the second spatial DOF has real MI); the ±0.000% on OTB is
best-partner's rank-1 win preserved exactly, i.e. the gate removes `jointbp2`'s −0.448% OTB regression
entirely. The two residual losses are inherited, not introduced: CapgMyo −0.014% and CEMHSEY −0.168% are
exactly `jointbp2`'s deficits on those arrays. **The gate mechanism works perfectly; what it cannot do is
beat a per-scale winner that does not exist — the C≥128 branch is `jointbp2`, and `jointbp2` is not the
per-scale winner on the two large arrays that are not Hyser.** The cycle-13 "large arrays → jointbp2"
generalization was drawn from Hyser alone; CEMHSEY (320 ch) and CapgMyo (128 ch) contradict it, so a gate on
raw channel count is the wrong structural variable there.

## Cross-channel gain, isolated, on real data

**+12.55% hyser (the highest of any codec), +18.44% otb (the highest of any non-CAR codec), +1.35% capgmyo,
+12.89% cemhsey.** Versus the best: +1.24 pp, ±0 pp, −0.02 pp, −0.19 pp.

## Pareto check

Cost 0.0472. **Non-dominated:** it ties the highest embeddable Hyser ratio measured (1.496924, shared with
`jointbp2`) while also holding OTB at the best's 2.161938 — no registered codec has ≥ ratio on both of those
at ≤ 0.0472 (`jointbp2` is cheaper at 0.0468 but −0.448% on OTB; `acar_sel` is cheaper at 0.043 but −1.10%
on Hyser). It does **not** dominate `jointbp2` (strictly cheaper) → `jointbp2` stays registered. It does not
Pareto-dominate the leaderboard best either: higher cost (0.0472 > 0.0394) *and* two small ratio regressions.

## Sanity gates

- Max real ratio in the run 2.179540 ≪ 6× ceiling; scalesel's own max 2.161938. No leak.
- No FAIL bit-exact rows (all 120 `ok=True`); `embedded=OK`, `neural=OK` on every row.
- No regression to the recorded best (`bestpartner` reproduces its leaderboard ratios exactly).

## Verifier verdicts

- **Verifier A: PROMOTE**
- **Verifier B: PROMOTE**
- Unanimous, no split.

## Decision

**NOT promoted.** It wins the primary Hyser (+1.117%) and exactly ties the best on OTB, but it **regresses on
CapgMyo (−0.014%) and CEMHSEY (−0.168%) at +20% cost (0.0472 vs 0.0394)** — not a Pareto win and not a beat
of the current best across real data. This is the same disposition cycle 12 applied to `acar_sel`, which had
*zero* regressions and still was not promoted for tying the primary at higher cost; consistency requires no
promotion here. **NOT retired** — genuinely non-dominated (max-Hyser *and* best-tying-OTB in one codec).
Kept registered as the best-of-both-corners front-end. Distilled learning: INSIGHTS P1-refinement
(gate on the *structurally correct* variable — channel count is a proxy for spatial rank, and it is a
**leaky** proxy: CEMHSEY's 5×64 strip and CapgMyo's decorrelated array are "large" by C but not rank≥2 local).
