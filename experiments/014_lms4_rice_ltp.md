# 014 — LMS4+Rice+ltp: gated long-term (MUAP firing-period) residual prediction — RETIRED

- **Cycle:** 13
- **Date:** 2026-07-28
- **Branch:** `compression-cycle-2026-07-28`
- **Candidate:** `LMS4+Rice+ltp` (cost 0.5288, zero side-info, look-ahead 0, `neural_ok` = NO)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis

INSIGHTS frontier #2 said the bottleneck has moved from cross-channel
decorrelation to the **temporal residual's own entropy**. A motor unit fires
quasi-periodically (8–35 pps), so the order-4 LMS residual should contain a
*long-lag* periodic component the short-term predictor structurally cannot see —
the speech-coder long-term-predictor (pitch-tap) idea, transplanted to MUAP firing
periods. After the short-term LMS, subtract one backward-adaptive tap at a long
lag:

```
e'[n] = e[n] - round(g * e[n-T])
```

with `(T, g, gate)` re-derived per block per channel from the **previous
reconstructed residual** (T ∈ [64, 200] ⇒ 32…10 pps at 2 kS/s; g = rounded
integer LS, |g| ≤ 1; gate fires only if normalised autocorrelation ρ(T\*) ≥ 3/8).
Decoder mirrors all three ⇒ zero side-info.

## Implementation

Only `research/registry.py`: `LTP_*`, `_ltp_gate/_ltp_params/_ltp_forward/`
`_ltp_inverse`, `ltp_encode/ltp_decode`, plus the `Codec("LMS4+Rice+ltp", ...)`
registration (family `temporal`). Integer-only (64-bit cross-multiplied ρ²
comparison, no float). Blocks 0/1 run OFF; within a block the tap is applied in
chunks of `LTP_TMIN` so every `e[n−T]` read is strictly before the chunk.
`rtl`/`sim` untouched. Registry self-test: round-trip OK, `emb_ok` OK,
`neural` = `-` (126 > 125 cyc), cost 0.5288 (dominated by the 912 B/ch residual
ring buffer).

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data, from `results/cycle_bench.csv` (all rows bit-exact `ok=True`):

| dataset | `ltp` (0.5288) | `LMS+Rice` temporal-only (0.0523) | vs `LMS+Rice` | best `LMS4+Rice+xchan_bestpartner` (0.0394) | vs best |
|---|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 | 1.331915 | 1.329992 | +0.14% | 1.480384 | **−10.03%** |
| otb_hdsemg_vl | 1.834712 | 1.825352 | +0.51% | 2.161938 | **−15.14%** |
| capgmyo_dba_s1 | 1.333127 | 1.332259 | +0.07% | 1.350480 | **−1.28%** |
| cemhsey_s1_d1t1 | 1.728403 | 1.729317 | **−0.05%** | 1.955547 | **−11.62%** |

## Isolated mechanism measurement (REAL data — the decisive result)

The long-term tap was switched off **in memory** by forcing `registry._ltp_gate`
to `False` (scratch script; the repo was not modified), so the gate-off build is
byte-identical to the gate-on build in every other respect — same order-4 LMS,
same Rice, same block structure. Same real arrays, 15 000 samples:

| dataset | LTP **on** | LTP **off** (gate forced) | **isolated LTP contribution** | gate fired (block-channels) |
|---|---:|---:|---:|---:|
| hyser_1dof_f1_s1 | 1.331915 | 1.332070 | **−0.012%** | 83 / 7296 = **1.1%** |
| otb_hdsemg_vl | 1.834712 | 1.834664 | **+0.003%** | 138 / 3648 = **3.8%** |
| capgmyo_dba_s1 | 1.333127 | 1.333589 | **−0.035%** | 833 / 7296 = **11.4%** |
| cemhsey_s1_d1t1 | 1.728403 | 1.728021 | **+0.022%** | 2126 / 18240 = **11.7%** |

**The long-term tap contributes ZERO on real HD-sEMG — and is *negative* on two of
four sets — despite the gate firing on 1.1–11.7% of block-channels.** It is not a
never-fires artefact: the mechanism engages and buys nothing.

Corollary: the codec's entire +0.07…+0.51% edge over `LMS+Rice` is the **order-8 →
order-4 predictor** change (P2), not the LTP stage. Independent corroboration in
`results/cycle_search.csv`: the order-4 temporal-only config `lms4s8/b256` scores
mean 1.5834 on (hyser+otb), against `lms8s8/b256` 1.5777 (= exactly the registry
`LMS+Rice` bench mean of (1.329992 + 1.825352)/2 = 1.57767); `ltp`'s own
(hyser+otb) mean is 1.58331 — i.e. **already inside the order-4 baseline, ±0.006%**.

## Attribution

Spatial front-end: none (this codec has no cross-channel stage — its "xchan gain"
column vs `LMS+Rice` is +0.14/+0.51/+0.07/−0.05%, all attributable to the predictor
order). Entropy back-end: unchanged adaptive Rice. **The only lever is the
temporal predictor, and the isolation above shows the long-term tap is exactly the
part of it that does nothing.**

**Mechanism (why quasi-periodicity is not exploitable here).** A single motor
unit's spike train is quasi-periodic, but a surface electrode integrates **many
asynchronously firing motor units** whose independent, jittered trains superpose.
The sum of ≥ ~5 independent renewal processes with ISI CV ≈ 0.1–0.3 converges to a
near-Poisson point process, whose autocorrelation is flat away from lag 0 — so
ρ(T) for the *observed* residual has no usable peak even though every constituent
train has one. What survives is a low, noise-level ρ; the gate's 3/8 threshold is
then crossed mostly by *estimator noise* over a 128-point window, and the fitted
`g` fits that noise into the current block (which is why the contribution is
slightly **negative** where the gate fires most, CapgMyo 11.4% → −0.035%). This is
the same failure mode as P2 (deeper temporal order fits noise) and P5-ext
(context-splitting raises average length), moved to a long lag: **after order-4 has
whitened the residual, the remaining temporal structure is noise, at every lag —
not just at short ones.**

## Pareto check

Conclusively **Pareto-dominated on ALL FOUR real sets** — worse ratio *and* 13.4×
the cost — by the registered leaderboard best `LMS4+Rice+xchan_bestpartner`
(0.0394), and equally by `bpa` (0.0387), `joint2` (0.0366) and
`acar+bestpartner` (0.0430). Cost 0.5288 is an order of magnitude above every
leaderboard incumbent, driven by the 912 B/ch residual ring (118 KiB at 128 ch);
`neural_ok` = NO (126 > 125 cyc/sample-ch).

## Sanity gates

- Max real ratio in the run 2.1795× ≪ 6× ceiling; `ltp`'s own max 1.8347×.
- All 102 CSV rows `ok=True`; no FAIL bit-exact.
- **Regression note:** `ltp` is the one codec in this run that measures **below a
  registered sibling on a real set without any spatial stage to excuse it** —
  cemhsey 1.728403 < `LMS+Rice` 1.729317 (−0.05%). That is the isolated tap being
  net-harmful, not a harness fault; it is part of the retirement rationale.
- No regression to the recorded best (reproduces exactly).
- Implementer's extra round-trips (N=1, N=200, 513, 1025, C=1, all-zeros, DC,
  int16 extremes, periods at T=64/200/210) reported bit-exact.

## Verifier verdicts

- **Verifier A: PROMOTE**
- **Verifier B: PROMOTE**
- **Unanimous, no split.** (Correctness/embeddability only.)

## Decision

**Not promoted. RETIRED** — `retired=True` + `retired_reason` set on the
`Codec("LMS4+Rice+ltp", ...)` registration in `research/registry.py`. Kept
bit-exact, excluded from the default sweep.

**Durable learning (INSIGHTS P6):** the temporal residual after an order-4 LMS is
white **at all lags**, not merely at short ones — long-lag/periodic prediction
(pitch tap, MUAP firing period) has no MI to remove on surface HD-sEMG because the
electrode superposes many asynchronous motor units into a near-Poisson process.
INSIGHTS frontier #2 ("attack the temporal residual entropy") is now spent
**negative on its long-lag branch**, in addition to the regime/context-switched
branch the sibling PRs already retired: do not re-propose long-term/periodic
temporal prediction here.
