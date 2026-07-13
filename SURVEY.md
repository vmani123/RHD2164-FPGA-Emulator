# SURVEY — lossless multichannel biosignal compression candidates

Cost-filtered, ranked survey of **lossless** compression methods for the 128-ch
RHD2164 / HD-EMG node (STM32H745 Cortex-M7 and Spartan-7 XC7S25; integer/fixed
only; causal/streaming; must beat per-channel FLAC by exploiting cross-channel
spatial correlation). **This file PROPOSES only** — no measured ratios, no codec
edits (non-negotiable #4). Watch-list methods are never promoted without explicit
human approval. Seeds `compression_spec/candidates.md`.

_Compiled: 2026-07-08 · web search (US) + candidates.md._

_2026 literature scan (this compile): no NEW embeddable lossless multichannel-biosignal
codec has surfaced since the 2026-07-06 compile that supersedes the flagged next step.
Recent activity is either lossy or float/neural (see watch-list / reference-bar / disqualified),
so the top implement-now pick is unchanged — now scoped as a new registry codec (per-block beta)._

## Verdict key
- **embeddable** — integer, causal, bounded state/look-ahead, fits the sEMG budget
  (≥1831 cyc/sample-ch) and plausibly the 30 kHz neural budget (125 cyc).
- **borderline** — embeddable only after a specific simplification (noted).
- **watch-list** — expected to fail the cost gate today; track, never auto-promote.

## Ranked embeddable contenders (by expected payoff / cost)

| # | method | why it may beat per-channel FLAC | verdict | key cost caveat |
|---|---|---|---|---|
| 1 | **Cross-channel residual decorrelation (MPEG-4 ALS multichannel coding)** | Adaptively weighted subtraction of a *reference channel's residual* from the coding channel's residual — removes shared, temporally-unpredictable spatial content a per-channel predictor cannot. This is exactly the `+xchan` lever, and the literature's cross-correlation-of-residuals estimator matches our achieved +17.5% real gain. | **embeddable** (already our best) | beta/weight must be per-block, not whole-signal (our current port caveat) |
| 2 | **Best-partner / multi-neighbour channel pairing** | Instead of a fixed grid neighbour, pick the most-correlated partner (or a small set) per channel — captures anisotropic muscle/propagation structure a fixed spanning tree misses. Generalises to per-cluster centroid references per the EEG channel-clustering reference-selection literature (ScienceDirect S1746809416301252), which reinforces its priority. | **embeddable** | O(C·k) correlation scan per block; send partner id (log2 C bits) side-info |
| 3 | **Integer inter-channel decorrelation (lifting / matrixing, ALS & Dolby TrueHD/MLP)** | Reversible integer lifting across channels generalises single-neighbour subtraction to a multi-tap spatial transform; fully lossless by construction. | **embeddable** | keep the matrix small + fixed-point; full adaptive matrix is borderline |
| 4 | **FLAC fixed polynomial predictors (ord 0–3), best-per-block + Rice** | Cheapest possible upgrade over order-1 delta; near-LMS ratio at a fraction of the compute. | **embeddable** (implemented: `fixed0-3+Rice`, on the Pareto front) | none material — this is the value pick |
| 5 | **NLMS / leaky / higher-order sign-LMS + Rice (ALS RLS-LMS direction)** | Normalised/leaky adaptation tracks non-stationary EMG better than plain sign-sign LMS; still one-pass. | **embeddable** | our search shows order>4 *hurts* real HD-sEMG — keep order small |
| 6 | **Range / arithmetic / rANS back-end on residuals (vs. Rice)** | Sub-Golomb redundancy on low-entropy residual blocks; JPEG-LS-style context modelling. | **borderline** | range/arith division & renorm is costlier than Rice; rANS is the FPGA-friendlier variant |
| 7 | **JPEG-LS / LOCO-I 2D over the electrode-grid × time image** | Median (MED/LOCO) predictor + context modelling + Golomb exploits 2D spatial structure losslessly; proven low-complexity with mature FPGA encoders. | **borderline** | context state per gradient bucket × channel; verify it beats #1 on grids, not just images |

## Watch-list (survey only — DO NOT promote without human approval)

| method | note |
|---|---|
| Integer discrete flows (IDF), L3C, learned entropy models | learned lossless; float/GPU, no streaming budget — track for offline baselines only |
| Convolutional autoencoder + lossless residual | near-lossless core + residual; float, disqualified on the FPGA target |
| VAE-DCT / neural context models | research interest; fails integer + latency gates today |
| Compressive on-chip AP recording (IEEE TBME 11183845 / bioRxiv 2025.09.26.678698) | **LOSSY** — requantise + mutual-information selective sampling; ~1098× paper-reported (unverified here). **DISQUALIFIED** by non-negotiable #1 (lossless-only); listed for the record / offline baseline only |
| TSCom-Bench + chained lightweight neural predictors (arXiv 2509.21002 / 2604.15472) | learned-lossless time-series predictors; float/GPU, no streaming budget — watch-list only |
| GPU adaptive lossless FP framework (arXiv 2511.04140) | offline/GPU floating-point pipeline; no integer streaming budget — watch-list only |
| Predictability-aware multichannel time-series (arXiv 2506.00614) | lossy + neural + non-causal; disqualified by lossless-only + causal gates, tracked for record |

**Reference-only (not a contender):** streaming floating-point time-series compressors
(Elf / SElf / Chimp / Gorilla, surveyed in arXiv 2510.07015) are XOR-of-float oriented and a
poor fit for int16 biosignals, where Rice/Golomb already dominates. Useful as a baseline bar,
not as a candidate on this target.

## Recommendations — next codecs to implement (embeddable, by expected gain/cost)

1. **Per-block beta for `+xchan`** (this cycle's top implement-now pick) — replace the
   whole-signal cross-channel gain (`cross_betas()` derives beta once over the entire
   signal and ships it once) with a **per-block** estimate, as a **NEW registry codec**
   (e.g. `lms4s8+x6/b512-pb`) rather than an in-place edit of the winning family. Two
   streaming-legal variants to bench: **(a) forward-adaptive** — beta from the current
   B-sample block, quantised to int16 and shipped as per-block side-info (one-block
   look-ahead); **(b) backward-adaptive** — beta derived from the previous block's already-
   reconstructed samples, so it carries **zero side-info** and the decoder recomputes it
   identically (fully causal, ALS-style). This keeps the dominant cross-channel spatial
   lever (which FLAC lacks) while making it non-stationarity-tracking and streaming-legal;
   per-block / backward-adaptive weight estimation is precisely the MPEG-4 ALS joint-coding
   approach, so it should retain the spatial gain rather than lose it. Block size B trades
   tracking vs. overhead and MUST be chosen by `research/bench.py` + `research/search.py`;
   treat no ratio as decided until the harness reports `embedded_ok` and a Pareto point,
   and assert a bit-exact round-trip (the backward-adaptive form requires encode/decode to
   mirror the same integer statistics exactly). Nothing embeddable superseded this pick.
2. **Best-partner channel selection** (#2) — one correlation scan per block picks
   the parent; small side-info. Directly attacks the fixed-spanning-tree limitation
   the LEADERBOARD flags. Expected to add a few % over the fixed grid neighbour.
3. **rANS residual back-end** (#6) — head-to-head vs. Rice on the same predictor;
   FPGA-friendly, may recover the last fraction of a bit per residual.
4. **Integer inter-channel lifting** (#3, small fixed matrix) — generalise
   single-neighbour subtraction to a 2–3-tap spatial lift; test whether the extra
   taps beat best-partner selection at acceptable cost.

Order reflects expected ratio gain per unit embedded cost for THIS device: the
spatial lever (#1→#2→#4) dominates temporal-predictor sophistication, matching
both the ALS literature and our own ablation (cross-channel ≈ 18× any temporal
knob on real HD-sEMG).

## Sources

- [A Lossless Multichannel Bio-Signal Compression Based on Low-Complexity Joint Coding Scheme for Portable Medical Devices (PMC4208236)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4208236/)
- [The MPEG-4 Audio Lossless Coding (ALS) Standard — Technology and Applications](https://www.academia.edu/68025436/The_MPEG_4_Audio_Lossless_Coding_ALS_Standard_Technology_and_Applications)
- [A Multichannel Linear Prediction Method for the MPEG-4 ALS Compliant Encoder (IEEE)](https://ieeexplore.ieee.org/document/4393008/)
- [An efficient lossless compression of multichannel time-series signals by MPEG-4 ALS](https://www.researchgate.net/publication/224559434_An_efficient_lossless_compression_of_multichannel_time-series_signals_by_MPEG-4_ALS)
- [An FPGA Implementation of a Lossless Electrocardiogram Compressor based on Prediction and Golomb-Rice Coding](https://www.researchgate.net/publication/239580536_An_FPGA_Implementation_of_a_Lossless_Electrocardiogram_Compressor_based_on_Prediction_and_Golomb-Rice_Coding)
- [An FPGA-Based LOCO-ANS Implementation for Lossless and Near-Lossless Image Compression (MDPI Electronics 2021)](https://www.mdpi.com/2079-9292/10/23/2934)
- [FPGA-based JPEG-LS encoder for onboard real-time lossless image compression](https://www.researchgate.net/publication/279513369_FPGA-based_JPEG-LS_encoder_for_onboard_real-time_lossless_image_compression)
- [Channel-clustering reference selection for multichannel EEG compression (ScienceDirect S1746809416301252)](https://www.sciencedirect.com/science/article/abs/pii/S1746809416301252)
- [TSCom-Bench: benchmarking learned lossless time-series compression (arXiv 2509.21002)](https://arxiv.org/abs/2509.21002)
- [Chained lightweight neural predictors for learned-lossless time-series (arXiv 2604.15472)](https://arxiv.org/abs/2604.15472)
- [Survey of streaming floating-point time-series compressors — Elf/SElf/Chimp/Gorilla (arXiv 2510.07015)](https://arxiv.org/abs/2510.07015)
- [GPU adaptive lossless floating-point compression framework (arXiv 2511.04140)](https://arxiv.org/abs/2511.04140)
- [Predictability-aware multichannel time-series compression (arXiv 2506.00614)](https://arxiv.org/abs/2506.00614)
- [Compressive on-chip action-potential recording (IEEE TBME 11183845)](https://ieeexplore.ieee.org/document/11183845) · [bioRxiv 2025.09.26.678698](https://www.biorxiv.org/content/10.1101/2025.09.26.678698)
