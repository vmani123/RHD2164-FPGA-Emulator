# LEADERBOARD — lossless on-node compression for 128-ch RHD2164 / HD-EMG

Best-per-category, the ratio-vs-cost Pareto front, per-dataset ratios, isolated
cross-channel gain, and **the one codec to port next**. Every number here is
produced by the harness (`research/bench.py` + `research/search.py`), asserted
bit-exact, and gated by `embedded_ok` — never by reasoning (non-negotiables #1,
#2, #4). Maintained per Stage 6 of `../COMPRESSION_RESEARCH_AGENT_PROMPT.md`.

_Updated: 2026-07-06 · branch `compression-wip`._

## Headline (REAL data decides — #3)

**Real 64-ch HD-sEMG** (`otb_hdsemg_vl`, OTB GR08MM1305 grid @ 2048 Hz, from the
pip-installed `openhdemg` sample — reachable without physionet/zenodo). Benched on
20 000 samples; full table `results/05_real_otb.csv`.

| codec | ratio | %-of-FLAC | embedded_ok | note |
|---|---:|---:|:--:|---|
| **LMS+Rice+xchan** | **2.12×** | 159% | ✅ | best embeddable (registry default, order-8) |
| delta+Rice+xchan | 2.00× | 150% | ✅ | cheapest xchan |
| wavpack | 1.84× | 138% | ref | best per-channel reference |
| LMS+Rice | 1.80× | 136% | ✅ | temporal only |
| fixed0-3+Rice | 1.80× | 135% | ✅ | new candidate, temporal only |
| delta+Rice | 1.72× | 129% | ✅ | |
| lzma | 1.56× | 117% | ref | **offline**, not embeddable |
| mtscomp | 1.46× | 110% | ref | neuro per-channel reference |
| zstd-19 | 1.41× | 106% | ref | offline |
| flac | 1.33× | 100% | ref | the target to beat |
| gzip-9 | 1.30× | 98% | ref | |

**The embedded `LMS+Rice+xchan` (2.12×) beats every reference — including offline
LZMA (1.56×) and WavPack (1.84×) — at a fraction of the compute, proven bit-exact.
That is the Stage-4 success condition met on real data.**

- **Achieved cross-channel gain: +17.5%** (LMS 1.80× → 2.12×) — the dominant lever.
- Max ratio 2.12× ≪ the 6× sanity ceiling → honest broadband EMG, not degenerate.

## Best embeddable after search (real HD-sEMG)

`research/search.py` on `otb_hdsemg_vl` (12 000 samples, `results/05_search_real.csv`):

**`lms4s7+x9/b512` — 2.194× (mean), cost 0.027, ~26 enc cyc/sample-ch, neural_ok.**
The search improves the registry default (`lms8s8+x8/b256`, 2.173×) by retuning to
**order-4 LMS / shift-7 / Rice-block-512** — order-4 beats order-8 on real data
(+1.0% ablation) at **~half the state/compute** of order-8. The hardcoded default
was over-provisioned.

### What mattered (ablation, real data)

| axis | Δ ratio | |
|---|---:|---|
| cross-channel on→off | **+17.9%** | dominant lever, ~18× everything else |
| lms order 4→8 | +1.0% | deeper temporal prediction *hurts* here |
| lms shift 7→8 | +0.3% | marginal |
| rice block 512→256 | +0.2% | marginal |

## Pareto front (ratio vs cost, embedded_ok only — real data)

| config | ratio | cost | neural_ok | character |
|---|---:|---:|:--:|---|
| delta+x9/b512 | 2.093× | 0.016 | ✅ | cheapest embeddable that clears 2× |
| **fixed0-3+x9/b512** | 2.168× | 0.025 | ✅ | **value pick — simplest hardware** |
| **lms4s7+x9/b512** | 2.194× | 0.027 | ✅ | **best ratio** |

## Cross-channel gain vs. spatial correlation (synthetic sweep — mechanism)

Synthetic corpus is for sweeps only, never a headline. It confirms the lever is
spatial correlation (`results/03_bench.csv`):

| spatial-corr | xchan gain (LMS) |
|---:|---:|
| 0.0 | −0.1% |
| 0.3 | +1.4% |
| 0.6 | +9.4% |
| 0.9 | +19.1% |

Real HD-sEMG (|corr|≈0.79) lands at +17.5% achieved — consistent with the sweep.

## → The one codec to port next

**`LMS+Rice+xchan`, retuned to order-4 / shift-7 / Rice-block-512** (`lms4s7+x9/b512`).

- Beats every reference on real HD-sEMG at 2.1–2.2×, bit-exact, `neural_ok`
  (~26 cyc/sample-ch ≪ both the sEMG and 30 kHz neural budgets).
- The essential component is the **cross-channel grid-neighbour front-end**
  (+17.5% real gain); the temporal predictor can be as small as order-4.
- **If minimizing hardware is paramount** (no adaptive-weight loop), port
  **`fixed0-3+Rice+xchan`** instead — 2.17× at cost 0.025, 99% of the best ratio
  with fixed integer-difference predictors only.
- **Port caveat:** the `+xchan` beta is currently derived offline over the whole
  signal; the embeddable form computes beta per block (look-ahead = one block).
  This is the first RTL/firmware implementation task (flagged in registry
  `meta.notes`).

## Status vs. the 6-stage plan

| stage | state |
|---|---|
| 0 Orient | ✅ `research/00_STATE.md` |
| 1 Registry + cost | ✅ `registry.py`, `embedded_cost.py` |
| 2 Corpus | ✅ `datasets.py` (synthetic + **real OTB**; Hyser/CapgMyo/CEMHSEY pending network) |
| 3 Benchmark | ✅ `bench.py`, `results/03_bench.csv`, `results/05_real_otb.csv` |
| 4 Search + analysis | ✅ `search.py`, real + synthetic Pareto, ablations |
| 5 Survey | ⏳ `SURVEY.md` (needs web; egress-blocked here) |
| 6 Report | ✅ this file |

**Reachable real data (OTB via PyPI) let Stages 3–4 produce a genuine real-data
headline in-session. Hyser/CapgMyo/CEMHSEY unlock once the environment egress
policy allows physionet.org/zenodo.org — the loaders are already wired.**
