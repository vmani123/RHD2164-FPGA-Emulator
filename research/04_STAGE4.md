# Stage 4 — Search + analysis

`research/search.py` hill-climbs the codec design space maximizing ratio subject
to `embedded_ok`, with ablations and a ratio-vs-cost Pareto front. Every candidate
is built only from `embedded_codec.py`'s proven integer-exact primitives, so it is
lossless by construction **and** bit-exact-asserted on every evaluation (#1).

**Real data decides (#3).** Real datasets are network-blocked in this container
(physionet/zenodo 403), so the search ran on the synthetic corpus
(spatial-corr 0.6 + 0.9) — an **infrastructure/mechanism demonstration, not a
headline**. Re-run on Hyser once the egress policy allows it:
`./.venv/bin/python research/search.py --datasets hyser_1dof_f1_s1 --max-samples 15000`.

_Date: 2026-07-06 · branch `compression-wip` · synthetic corpus._

## Search result (synthetic sc0.6+0.9, 60 configs evaluated)

```
start   lms8s8 +x8 /b256 : 2.5477x  (current registry default)
converged lms4s10+x7 /b512 : 2.5596x
```

**Best embeddable: `lms4s10+x7/b512` — mean ratio 2.560x, cost 0.027, ~26 enc
cyc/sample-ch, neural_ok.** The search found **order-4 LMS is as good as order-8
here at ~half the cost** (0.027 vs 0.057), block 512 a marginal win — i.e. the
hardcoded defaults (order 8, block 256) were slightly over-provisioned. This is a
cost win at equal ratio, exactly what the Pareto objective rewards.

### What mattered (ablation from best → cheap baseline)

| axis reverted | Δ ratio | note |
|---|---:|---|
| cross-channel True → False | **+14.0%** | the dominant lever, by far |
| rice block 512 → 256 | +0.23% | marginal |
| lms shift 10 → 8 | +0.09% | marginal |
| lms order 4 → 8 | +0.02% | order beyond 4 buys ~nothing here |

Cross-channel decorrelation is ~60× more important than every temporal-predictor
knob combined. This is the honest mechanism story: on data with a shared,
temporally-unpredictable component, the lever is *spatial*, not deeper temporal
prediction.

### Pareto front (ratio vs cost, embedded_ok only)

| config | ratio | cost | neural_ok |
|---|---:|---:|:--:|
| delta+x8/b512 | 2.370× | 0.016 | OK |
| fixed+x7/b512 | 2.547× | 0.025 | OK |
| **lms4s10+x7/b512** | **2.560×** | 0.027 | OK |

`fixed0-3+x7/b512` is the value pick — 99.5% of the best ratio at lower cost and
the simplest hardware (fixed integer differences, no adaptive weights). Full grid
in `results/04_search.csv`.

## Caveats

- Synthetic numbers only; the order-4-vs-8 and block-512 findings must be
  re-confirmed on Hyser before porting (real EMG has different temporal structure).
- The hill-climb is greedy/one-axis; it can miss interacting optima. It is a
  screening tool, not a global optimizer — good enough to rank the design axes,
  which is the Stage 4 goal.
- Cross-channel here still uses the offline global-beta software realization; the
  embeddable per-block-beta form is a Stage-4/port implementation item (flagged in
  the registry `meta.notes`).
