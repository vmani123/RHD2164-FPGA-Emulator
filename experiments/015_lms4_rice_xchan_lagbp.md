# 015 — LMS4+Rice+xchan_lagbp: lag-aligned (propagation-compensated) best-partner spatial predictor

- **Cycle:** 16
- **Date:** 2026-07-31
- **Branch:** `compression-cycle-2026-07-31`
- **Candidate:** `LMS4+Rice+xchan_lagbp`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (SURVEY cycle-16 pick #1 — a genuinely new spatial degree of freedom: TIME ALIGNMENT)

All 20 previously-registered codecs apply their spatial operator at **lag zero** (`x_c[n] − (β·x_p[n])>>s`).
But the pairwise cross-covariance of an HD-sEMG array is a *function of lag*, `R_cp(τ)`, peaking at the MUAP
propagation delay `τ* = IED/CV` (≈2–3.3 ms ≈ 4–7 samples at 2048 Hz for 10 mm pitch, CV 3–5 m/s). For an
along-fibre neighbour pair `I(x_c[n]; x_p[n−τ*]) ≫ I(x_c[n]; x_p[n])`, so every codec so far has been
implicitly restricted to *transverse* (τ≈0) neighbours and the whole **propagating slice** of the
cross-channel mutual information has been unavailable. Searching (partner, lag) jointly and predicting with
one joint 2-tap co-adaptive sign-LMS on `{x_p[n], x_p[n−d*]}` should recover it; `d*=0` degenerates exactly
to the shipped best-partner, so the construction is a strict superset of the promoted best.

## Implementation

`research/registry.py` only (`_lag_window`, `_lagbp_select_block`, `_lagbp_forward/_inverse`,
`lagbp_encode/decode`, `LAGBP_D=4`). Per channel per block (i>0), a backward search over the previous
already-reconstructed RAW block scans the ≤4 causal grid neighbours × integer lags d∈{−4..+4} (36 options +
the no-parent option), scored by estimated Rice bits of `x_c[n] − (β(p,d)·x_p[n−d])>>s` (`_bp_opt_beta` /
`_bp_score` reused). The min-bits `(p*,d*)` drives ONE joint 2-tap sign-sign LMS on the SAME parent at two
lags. Zero side-info (decoder mirrors search and taps from bit-identical reconstructed history). Back-end
unchanged: order-4 sign-sign LMS (P2) + adaptive Rice (P5). Declared `lookahead_samples=4` (only the *parent*
row is read at a shifted index; channel c's own future is never touched). Cost 0.0677.

Registry self-test (first try, no fix loop):
`PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest` → `ALL round-trips bit-exact`,
`LMS4+Rice+xchan_lagbp  2.72x  OK  OK  OK  0.068`. Extra shapes (16,1000,4), (8,257,8), (9,301,3), (4,60,2),
(32,600,16) all bit-exact. Instrumented synthetic propagating field: lag histogram over 210 channel-blocks
`{d=0: 168, d=4: 42}` → the new DOF fires and is non-degenerate.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (`results/cycle_bench.csv`, all rows `ok=True`, `embedded=OK`, `neural=OK`), cost 0.0677:

| dataset | lagbp | best `bestpartner` (0.0394) | vs best | xchan gain (vs `LMS+Rice`) | best's xchan gain |
|---|---:|---:|---:|---:|---:|
| **hyser_1dof_f1_s1** (primary) | 1.478705 | 1.480384 | **−0.113%** | +11.18% | +11.31% |
| otb_hdsemg_vl | 2.124018 | 2.161938 | **−1.754%** | +16.36% | +18.44% |
| capgmyo_dba_s1 | **1.362255** | 1.350480 | **+0.872%** | **+2.25%** | +1.37% |
| cemhsey_s1_d1t1 | 1.955829 | 1.955547 | +0.014% | +13.10% | +13.08% |

`LMS+Rice` (temporal-only) baselines used for the isolated gain: hyser 1.329992, otb 1.825352,
capgmyo 1.332259, cemhsey 1.729317. 4-set mean 1.730202 vs the best's 1.737087 (−0.40%).
Synthetic (mechanism only, no verdict): sc0.6 2.587835, sc0.9 2.546504 — both below best-partner.

## Attribution

Predictor (order-4 sign-LMS) and back-end (adaptive Rice) are byte-identical to the promoted best, so **every
delta is the cross-channel front-end**, and within it the only new knob is the *lag axis* (selection and count
are held at the best's setting: one selected parent, second tap on the same parent).

- **CapgMyo (+0.872% ratio, +0.88 pp isolated xchan gain — the highest CapgMyo ratio any codec has ever
  measured here, above `bestpartner_adaptive`'s 1.352866 and WavPack's 1.35).** CapgMyo is the array whose
  *lag-0* neighbour correlation is near-zero (|corr| ≈ 0.29 — the harness's negative control). That is exactly
  the regime the hypothesis predicts a lag lever pays: when `R_cp(0)` is small, the pairwise MI has not
  vanished, it has moved to `τ ≠ 0`. Nearly doubling the achieved cross-channel gain (+1.37% → +2.25%) on the
  one array where lag-0 spatial coding has always failed is a mechanism-consistent, not incidental, result.
- **OTB (−1.754%, xchan gain +16.36% vs +18.44%).** The tight 64-ch array has one dominant *transverse*
  neighbour with a large `R(0)`. Here the lag search is pure model-selection cost: the argmin over 36 (p,d)
  options is fit on the previous block, so a spuriously-better lag in that block is carried into this one, and
  the second tap adds adaptive-estimation variance where there is no delayed MI to remove. Same failure shape
  P1b recorded for a second *channel* on tight arrays — a second spatial DOF, spent on lag instead of count.
- **Hyser (−0.113%) / CEMHSEY (+0.014%)** — essentially neutral: on the large arrays the diffuse local
  structure gives the lag axis a little to find but not enough to pay for the selector's variance.

## Cross-channel gain, isolated, on real data

Achieved (not ceiling), vs the temporal-only `LMS+Rice` in the same run: **+11.18% hyser, +16.36% otb,
+2.25% capgmyo, +13.10% cemhsey**. Versus the promoted best on the same axis: −0.13 pp, −2.08 pp,
**+0.88 pp**, +0.02 pp.

## Pareto check

Cost 0.0677 — the highest of any registered codec. It is **non-dominated**: its CapgMyo ratio (1.362255) is
the highest measured by *any* codec in `results/cycle_bench.csv`, and its CEMHSEY ratio is marginally the
highest embeddable (1.955829 > 1.955547), so no registered codec has ≥ ratio on all real sets at ≤ cost.
It is *not* a promotion candidate: it loses the primary Hyser and OTB at 72% higher cost than the best.

## Sanity gates

- Max real ratio anywhere in the run 2.179540 (`acar_sel`/OTB) ≪ the 6× leak ceiling; lagbp's own max is
  2.124018. No degenerate data.
- No FAIL bit-exact rows: every row in `results/cycle_bench.csv` has `ok=True`; `embedded=OK`, `neural=OK`.
- No regression to the recorded best: `LMS4+Rice+xchan_bestpartner` reproduces its leaderboard ratios exactly
  (1.480384 / 2.161938 / 1.350480 / 1.955547).

## Verifier verdicts

- **Verifier A: PROMOTE**
- **Verifier B: PROMOTE**
- Unanimous, no split.

## Decision

**NOT promoted** (loses the primary Hyser −0.113% and OTB −1.754%; the promotion rule requires beating the
current best on real data — a single-set win on the negative-control array is not that). **NOT retired** —
genuinely non-dominated (highest CapgMyo and CEMHSEY ratios measured). **Kept registered** as the
*low-lag-0-correlation (CapgMyo) corner* and as the proof that the lag axis carries real, previously
unavailable MI. See `research/INSIGHTS.md` P1c for the distilled principle.
