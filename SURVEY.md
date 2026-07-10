# SURVEY — lossless multichannel biosignal compression candidates

Cost-filtered, ranked survey of **lossless** compression methods for the 128-ch
RHD2164 / HD-EMG node (STM32H745 Cortex-M7 and Spartan-7 XC7S25; integer/fixed
only; causal/streaming; must beat per-channel FLAC by exploiting cross-channel
spatial correlation). **This file PROPOSES only** — no measured ratios, no codec
edits (non-negotiable #4). Watch-list methods are never promoted without explicit
human approval. Seeds `compression_spec/candidates.md`.

_Compiled: 2026-07-06 · refreshed 2026-07-10 (cycle 2026-07-10) · web search (US) + candidates.md._

_Cycle log (what's already been tried, so don't re-recommend it):_
- _`LMS+Rice+xchan` (order-8, whole-signal float beta) is the incumbent real best (OTB 2.14×, cost 0.057; Hyser 1.47×)._
- _Cycle 1 (branch `compression-cycle-2026-07-08`, unmerged) implemented **`LMS+Rice+xchan_adaptive`** = backward-adaptive per-block **integer** beta replacing the offline float beta. Verified bit-exact/embeddable but **dominated**: OTB 2.13× at cost 0.065 (−0.48% ratio, +cost) vs the incumbent → NOT promoted. Do not re-implement this identically. Its open follow-ups (untried): pair adaptive-beta with **order-4** LMS (may recover the gap AND cut cost < 0.057 = a real Pareto win); sweep adaptation block size 128/256/512; two-neighbour (up+left) front-end._
- _Untried from prior SURVEY recs: **best-partner / multi-neighbour channel selection** (#2), **rANS residual back-end** (#3), **integer inter-channel lifting** (#4)._

## Verdict key
- **embeddable** — integer, causal, bounded state/look-ahead, fits the sEMG budget
  (≥1831 cyc/sample-ch) and plausibly the 30 kHz neural budget (125 cyc).
- **borderline** — embeddable only after a specific simplification (noted).
- **watch-list** — expected to fail the cost gate today; track, never auto-promote.

## Ranked embeddable contenders (by expected payoff / cost)

| # | method | why it may beat per-channel FLAC | verdict | key cost caveat |
|---|---|---|---|---|
| 1 | **Cross-channel residual decorrelation (MPEG-4 ALS multichannel coding)** | Adaptively weighted subtraction of a *reference channel's residual* from the coding channel's residual — removes shared, temporally-unpredictable spatial content a per-channel predictor cannot. This is exactly the `+xchan` lever, and the literature's cross-correlation-of-residuals estimator matches our achieved +17.5% real gain. | **embeddable** (already our best) | beta/weight must be per-block, not whole-signal (our current port caveat) |
| 2 | **Best-partner / multi-neighbour channel pairing (ALS low-complexity joint coding)** | Instead of a fixed grid parent, pick the most-correlated partner per channel and only subtract when it pays. Choi et al. (Sensors 2014) give the *integer, causal* recipe: gate joint coding by the **normalized residual cross-correlation** DF = \|Σe₁e₂\|/√(Σe₁²·Σe₂²) ≥ ~0.45, and pick the reference channel by **lowest AbsMean residual** (smallest Rice-k) — both are byproducts of prediction, no duplicate entropy pass. Directly attacks the fixed-spanning-tree limit the LEADERBOARD flags; OTB grid neighbour \|corr\| 0.73–0.79 means several near-equal candidates the fixed parent may not pick. | **embeddable** | O(C·k) correlation scan per block over a *bounded* neighbour set (4-neighbourhood, not all C); send partner id (log2 k bits) + 1 gate bit per block as side-info |
| 3 | **Integer inter-channel decorrelation (lifting / matrixing, ALS & Dolby TrueHD/MLP)** | Reversible integer lifting across channels generalises single-neighbour subtraction to a multi-tap spatial transform; fully lossless by construction. | **embeddable** | keep the matrix small + fixed-point; full adaptive matrix is borderline |
| 4 | **FLAC fixed polynomial predictors (ord 0–3), best-per-block + Rice** | Cheapest possible upgrade over order-1 delta; near-LMS ratio at a fraction of the compute. | **embeddable** (implemented: `fixed0-3+Rice`, on the Pareto front) | none material — this is the value pick |
| 5 | **NLMS / leaky / higher-order sign-LMS + Rice (ALS RLS-LMS direction)** | Normalised/leaky adaptation tracks non-stationary EMG better than plain sign-sign LMS; still one-pass. | **embeddable** | our search shows order>4 *hurts* real HD-sEMG — keep order small |
| 6 | **rANS / tANS back-end on residuals (vs. Rice)** | Rice is optimal only for exactly-geometric residuals; a table-driven (t)ANS coder captures the sub-Golomb redundancy on real EMG residual blocks whose histogram deviates from geometric. Static per-block frequency table → deterministic, integer. | **borderline** | tANS is the FPGA-friendly variant (LOCO-ANS: table lookups + renorm, no per-symbol division); needs a per-block freq table (side-info) and reverse-order encode buffer — cost may exceed Rice's for the marginal bits gained; head-to-head vs Rice on the *same* predictor decides |
| 7 | **JPEG-LS / LOCO-I(-ANS) 2D over the electrode-grid × time image** | Median (MED/LOCO) predictor + context modelling + Golomb/ANS exploits 2D spatial structure losslessly; LOCO-ANS (Electronics 2021) is a proven low-complexity HLS/FPGA encoder. | **borderline** | context state per gradient bucket × channel; verify it beats #1/#2 on a 5×13 grid, not just natural images |

## Watch-list (survey only — DO NOT promote without human approval)

| method | note |
|---|---|
| Integer discrete flows (IDF), L3C, learned entropy models | learned lossless; float/GPU, no streaming budget — track for offline baselines only |
| Convolutional autoencoder + lossless residual | near-lossless core + residual; float, disqualified on the FPGA target |
| VAE-DCT / neural context models | research interest; fails integer + latency gates today |

## Recommendations — next codec to implement THIS cycle (embeddable, by expected gain/cost)

Per-block beta is now *done* (cycle 1's `+xchan_adaptive`), so it drops off this
list. The spatial lever still dominates temporal sophistication (~70× on real
Hyser ablation), so the top bet stays spatial — but the *fixed parent*, not the
beta, is now the binding limitation.

1. **★ TOP PICK — Best-partner channel selection (#2).** Untried, and the highest-
   payoff *new* lever. Give each channel a small causal candidate set (its 4-grid
   neighbours), pick the reference by lowest AbsMean residual, and gate the subtract
   per block by the normalized residual-cross-correlation DF (Choi et al.'s integer
   recipe). This is a genuinely new mechanism, not a refinement of a known result;
   it attacks exactly the fixed-spanning-tree limit the LEADERBOARD flags, and on
   OTB's near-isotropic neighbour correlations (0.73–0.79) the fixed parent is
   demonstrably not always the best partner. Reuse the existing `+xchan` subtract;
   only the parent-selection + gate logic is new. Side-info is tiny (log2 4 + 1 bit
   per block). Expected: a few % over the fixed grid neighbour, at bounded cost.
2. **Adaptive-beta + order-4 LMS** (cycle 1 follow-up (a)). The safe Pareto-refinement
   bet: order-4 already beats order-8 on real data, and pairing it with cycle 1's
   side-info-free integer beta may recover the −0.48% give-up **and** push cost
   below the incumbent 0.057 — turning cycle 1's dominated point into a real Pareto
   win that also closes the port caveat. Lower ceiling than #1 (best case ≈ incumbent
   ratio at lower cost), but low-risk and RTL-ready. Not a re-implement of cycle 1:
   the order-4 pairing + block-size sweep (128/256/512) is the new part.
3. **rANS/tANS residual back-end** (#6). Head-to-head vs. Rice on the *same*
   predictor. FPGA-friendly (LOCO-ANS-style tANS: table lookups + renorm, no
   per-symbol division), may recover the last fraction of a bit on non-geometric
   residual blocks — but payoff is uncertain and the per-block freq table + reverse
   encode buffer may cost more than the bits it saves. Worth a bench, not the build.

Order reflects expected ratio gain per unit embedded cost for THIS device on the
real OTB set: a new spatial lever (#1) > a Pareto refinement that also unblocks RTL
(#2) > an uncertain back-end swap (#3). Integer inter-channel lifting (table row 3)
remains a contender for a later cycle if best-partner selection under-delivers.

## Sources

- [Choi et al., A Lossless Multichannel Bio-Signal Compression Based on Low-Complexity Joint Coding Scheme for Portable Medical Devices (Sensors 2014, PMC4208236)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4208236/) — the integer joint-coding **decision factor** (normalized residual cross-correlation, threshold ~0.45) and **AbsMean reference-channel selection** driving rec #1. Reports 20.72% complexity cut vs ALS reference at parity ratio; +11.92% vs single-channel; ECG 3.41, EEG 3.65 (paper-reported, unverified here).
- [Use of the MPEG-4 ALS Architecture and Inter-channel Prediction for Multi-channel ECG Coding](https://www.researchgate.net/publication/261096537_Use_of_the_MPEG-4_ALS_Architecture_and_Inter-channel_Prediction_for_Multi-channel_ECG_Coding) — ALS inter-channel prediction applied to multichannel biosignals; two cross-prediction filter types; ~3% inter- over intra-channel gain (paper-reported, unverified here).
- [LOCO-ANS: An Optimization of JPEG-LS Using an Efficient and Low-Complexity Coder Based on ANS (IEEE Access 2021)](https://ieeexplore.ieee.org/document/9499046/) — tANS back-end replacing Golomb (rec #6/#7 basis).
- [An FPGA-Based LOCO-ANS Implementation Using High-Level Synthesis (Electronics 2021, MDPI)](https://www.mdpi.com/2079-9292/10/23/2934) + [HLS coder source](https://github.com/hpcn-uam/LOCO-ANS-HW-coder) — evidence tANS is FPGA-mappable (table lookups + renorm, no per-symbol division), 40.5 MPix/s Zynq-7020.
- [Low-cost ANS encoder for lossless data compression in FPGAs (2024)](https://www.researchgate.net/publication/379363486_Low-cost_ANS_encoder_for_lossless_data_compression_in_FPGAs) — streaming-rANS with parameters tuned to cut FPGA resource use; supports the rANS-is-embeddable-back-end claim (rec #6).
- [The MPEG-4 Audio Lossless Coding (ALS) Standard — Technology and Applications](https://www.academia.edu/68025436/The_MPEG_4_Audio_Lossless_Coding_ALS_Standard_Technology_and_Applications)
- [A Multichannel Linear Prediction Method for the MPEG-4 ALS Compliant Encoder (IEEE)](https://ieeexplore.ieee.org/document/4393008/)
- [An efficient lossless compression of multichannel time-series signals by MPEG-4 ALS](https://www.researchgate.net/publication/224559434_An_efficient_lossless_compression_of_multichannel_time-series_signals_by_MPEG-4_ALS)
- [An FPGA Implementation of a Lossless Electrocardiogram Compressor based on Prediction and Golomb-Rice Coding](https://www.researchgate.net/publication/239580536_An_FPGA_Implementation_of_a_Lossless_Electrocardiogram_Compressor_based_on_Prediction_and_Golomb-Rice_Coding)
- [An FPGA-Based LOCO-ANS Implementation for Lossless and Near-Lossless Image Compression (MDPI Electronics 2021)](https://www.mdpi.com/2079-9292/10/23/2934)
- [FPGA-based JPEG-LS encoder for onboard real-time lossless image compression](https://www.researchgate.net/publication/279513369_FPGA-based_JPEG-LS_encoder_for_onboard_real-time_lossless_image_compression)
