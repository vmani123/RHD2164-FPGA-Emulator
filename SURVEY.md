# SURVEY — lossless multichannel biosignal compression candidates

Cost-filtered, ranked survey of **lossless** compression methods for the 128-ch
RHD2164 / HD-EMG node (STM32H745 Cortex-M7 and Spartan-7 XC7S25; integer/fixed
only; causal/streaming; must beat per-channel FLAC by exploiting cross-channel
spatial correlation). **This file PROPOSES only** — no measured ratios, no codec
edits (non-negotiable #4). Watch-list methods are never promoted without explicit
human approval. Seeds `compression_spec/candidates.md`.

_Compiled: 2026-07-06 · refreshed 2026-07-10 · refreshed 2026-07-13 (cycle 2026-07-13) · web search (US) + candidates.md._

_Cycle log (what's already been tried, so don't re-recommend it):_
- _`LMS+Rice+xchan` (order-8, whole-signal float beta) is the incumbent real best (OTB 2.14×, cost 0.057; Hyser 1.47×)._
- _Cycle 1 (branch `compression-cycle-2026-07-08`, merged PR #1) implemented **`LMS+Rice+xchan_adaptive`** = backward-adaptive per-block **integer** beta replacing the offline float beta. Verified bit-exact/embeddable but **dominated**: OTB 2.13× at cost 0.065 (−0.48% ratio, +cost) vs the incumbent → **RETIRED 2026-07-13** (conclusively Pareto-dominated; `registry.py retired=True`). Do not re-implement this identically. Its open follow-ups (untried): pair adaptive-beta with **order-4** LMS; sweep adaptation block size 128/256/512; two-neighbour (up+left) front-end._
- _Cycle 2 (branch `compression-cycle-2026-07-10`, merged PR #2) implemented **`LMS+Rice+xchan_bestpartner`** — the prior SURVEY's TOP PICK #2 (per-channel best-partner selection via lowest-AbsMean reference + normalized-residual-cross-correlation gate). Verified/registered as a **non-dominated** Pareto point (OTB 2.15×, cost 0.063 — higher ratio AND higher cost than the incumbent) but did **not** displace the port pick. So best-partner selection is now DONE — do not re-propose it as new. Its open follow-ups (untried): widen the candidate set beyond the 4-neighbourhood (channel-clustering reference selection); multi-neighbour (2-tap) joint subtract._
- _Cycle 3 (this cycle, 2026-07-13, SURVEY-only): with best-partner (pairwise) and adaptive-beta both spent, the untried spatial frontier is **multi-tap** decorrelation — a reversible integer inter-channel transform (Integer-KLT / lifting) and a rank-1 common-mode front-end (Adaptive CAR). Both are genuinely distinct mechanisms from single-neighbour subtraction; see the refreshed table + recs below._
- _Cycle 3 RESULT (branch `compression-cycle-2026-07-13`) implemented **`LMS+Rice+iklt`** = the TOP PICK, a **fixed** data-independent 45° integer-KLT (3-step lifting) multi-tap front-end. Verified bit-exact/embeddable but **verifier split (A REJECT, B PROMOTE) → held for human review, not promoted**, and **conclusively Pareto-dominated → RETIRED 2026-07-13**: OTB 2.07× at cost 0.068 vs incumbent 2.24×/0.057 (worse ratio AND higher cost; also dominated by bestpartner 2.25×/0.063 and delta+Rice+xchan 2.19×/0.013). Root cause measured: the **fixed** 45° basis captures only **+8.8%** real cross-channel gain vs single-neighbour subtract's **+18.0%** — the isotropic-model rotation is mismatched to real anisotropic HD-sEMG covariance. Do not re-propose a **fixed data-independent** inter-channel transform; the spatial gain lives in a **data-dependent** weight. See `experiments/002_lms_rice_iklt.md`._
- _**Next hypotheses for the next cycle (ranked by expected payoff):**_
  - _**(1, highest) Adaptive/data-dependent 2×2 lifting rotation.** Knob to change: replace the fixed θ=45° in the integer-KLT with a **per-block backward-adaptive integer angle** (quantized θ chosen from the previous reconstructed block's neighbour covariance, decoder recomputes it → zero side-info), keeping the exact same reversible 3-lift structure. Why the data says so: iklt's fixed basis got only +8.8% vs the data-dependent single-neighbour's +18.0% on the identical predictor/back-end — the entire gap is basis mismatch, and adapting the one angle is the minimal lever that closes it while staying multiplierless and lossless. This is NOT the retired fixed-iklt (fixed→adaptive angle) nor the retired scalar adaptive-beta (rotation vs rank-1 subtract)._
  - _**(2) rANS/tANS residual back-end vs Rice (entropy axis, untouched all 3 cycles).** Knob to change: swap the adaptive Golomb-Rice coder for a table-driven tANS (LOCO-ANS style) on the SAME `LMS+Rice+xchan` predictor+front-end. Why: every cycle so far moved only the cross-channel front-end; the entropy back-end has never been tested, and Rice is optimal only for exactly-geometric residuals — real EMG residual histograms deviate sub-Golomb. Head-to-head on the same predictor isolates the back-end's marginal bits._
  - _**(3) Adaptive CAR (rank-1 common-mode) front-end, the untried spatial axis.** Knob to change: subtract a reversible-integer running array-mean (S-transform lift) per time-slice before LMS, gated per block. Why: this removes the **shared common-mode** (drive/power-line/reference drift) that pairwise subtraction structurally cannot cancel — a different spatial axis than iklt's neighbour rotations — at near-free O(1)/sample-ch cost. It is the cheapest remaining spatial lever and was the survey's rec #3b fallback, still unmeasured._

## Verdict key
- **embeddable** — integer, causal, bounded state/look-ahead, fits the sEMG budget
  (≥1831 cyc/sample-ch) and plausibly the 30 kHz neural budget (125 cyc).
- **borderline** — embeddable only after a specific simplification (noted).
- **watch-list** — expected to fail the cost gate today; track, never auto-promote.

## Ranked embeddable contenders (by expected payoff / cost)

| # | method | why it may beat per-channel FLAC | verdict | key cost caveat |
|---|---|---|---|---|
| 1 | **Cross-channel residual decorrelation (MPEG-4 ALS multichannel coding)** | Adaptively weighted subtraction of a *reference channel's residual* from the coding channel's residual — removes shared, temporally-unpredictable spatial content a per-channel predictor cannot. This is exactly the `+xchan` lever, and the literature's cross-correlation-of-residuals estimator matches our achieved +17.5% real gain. | **embeddable** (already our best) | beta/weight must be per-block, not whole-signal (our current port caveat) |
| 2 | **Best-partner / multi-neighbour channel pairing (ALS low-complexity joint coding)** — **IMPLEMENTED cycle 2 (`LMS+Rice+xchan_bestpartner`, non-dominated).** DONE, not a new proposal. | Instead of a fixed grid parent, pick the most-correlated partner per channel and only subtract when it pays. Choi et al. (Sensors 2014) give the *integer, causal* recipe: gate joint coding by the **normalized residual cross-correlation** DF = \|Σe₁e₂\|/√(Σe₁²·Σe₂²) ≥ ~0.45, and pick the reference channel by **lowest AbsMean residual** (smallest Rice-k). | **embeddable — shipped** | open follow-up only: widen candidate set beyond 4-neighbourhood (channel-clustering) / 2-tap joint |
| 3 | **★ Reversible integer inter-channel transform — Integer-KLT / lifting (ALS multichannel, Dolby TrueHD/MLP, IntSKLT)** | Generalises *single-neighbour* subtraction (which bestpartner already spent) to a **multi-tap spatial transform**: decorrelate each time-slice across the whole array with a reversible integer transform, then run the existing per-channel LMS+Rice temporally. Integer-KLT (Srinivasan et al.) makes the KLT reversible+integer via **ladder / matrix-factorization (lifting) steps** — lossless by construction. Captures the *joint* covariance across many electrodes, not just one partner, which is exactly the spatial lever pairwise subtraction leaves on the table on a near-isotropic HD grid. | **borderline** | keep the transform **small and FIXED** (offline-trained integer basis shipped as constants, or a slowly-adapted one) — per-block eigendecomposition is disqualifying. 2024 *data-independent* multiplierless integer-KLT approximations (arXiv 2410.09227) show the fixed-basis version is adds+shifts only, FPGA-mapped. Verify a fixed 5×13-grid basis beats bestpartner before adaptivity |
| 3b | **Adaptive Common Average Reference (ACAR) — rank-1 global common-mode front-end** | Subtract a (weighted) **mean across the array** from every channel before temporal prediction. Removes the shared common-mode component (movement/EMG drive, power-line, reference drift) that a pairwise / spanning-tree predictor structurally *cannot* fully cancel — a different spatial axis from best-partner, and the cheapest new lever by far (one running cross-channel sum per time sample). ACAR (Vaisman/Farina, MBEC 2014) adapts the referencing weights to the signal. Made lossless via a reversible integer S-transform-style lift: code the array total as a virtual channel, residuals as channel−round(CAR). | **borderline** | needs a reversible integer formulation (lift, not float mean); ~O(C) adds/sample for the shared sum (amortized O(1)/sample-ch) + one subtract — near-free. Gate per block so it can't hurt low-common-mode segments |
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
| Compressive on-chip AP recording (IEEE TBME 11183845 / bioRxiv 2025.09.26.678698) | **LOSSY** — requantise + mutual-information selective sampling; ~1098× paper-reported (unverified here). **DISQUALIFIED** by non-negotiable #1 (lossless-only); listed for the record / offline baseline only |
| TSCom-Bench + chained lightweight neural predictors (arXiv 2509.21002 / 2604.15472) | learned-lossless time-series predictors; float/GPU, no streaming budget — watch-list only |
| GPU adaptive lossless FP framework (arXiv 2511.04140) | offline/GPU floating-point pipeline; no integer streaming budget — watch-list only |
| Predictability-aware multichannel time-series (arXiv 2506.00614) | lossy + neural + non-causal; disqualified by lossless-only + causal gates, tracked for record |

**Reference-only (not a contender):** streaming floating-point time-series compressors
(Elf / SElf / Chimp / Gorilla, surveyed in arXiv 2510.07015) are XOR-of-float oriented and a
poor fit for int16 biosignals, where Rice/Golomb already dominates. Useful as a baseline bar,
not as a candidate on this target.

## Recommendations — next codec to implement THIS cycle (embeddable, by expected gain/cost)

Both cheap spatial levers are now spent: per-block beta (cycle 1, retired) and
*pairwise* best-partner selection (cycle 2, shipped non-dominated). The spatial
lever still dominates temporal sophistication (~70× on real Hyser ablation), so the
top bet stays spatial — but the binding limitation is now that every shipped
front-end subtracts **one** reference channel. The untried frontier is **multi-tap**
spatial decorrelation.

1. **★ TOP PICK — Reversible integer inter-channel transform (Integer-KLT / lifting, table #3).**
   The highest-ceiling *new* lever and a genuinely distinct mechanism: replace the
   single-neighbour subtract with a **fixed, offline-trained, multiplierless integer
   transform** across the electrode array (ladder/lifting factorization → lossless by
   construction), then keep the existing per-channel LMS+Rice temporal back-end. It
   exploits the *joint* array covariance — the isotropic 0.73–0.79 neighbour
   correlations mean several partners carry shared content one subtraction can't
   remove, exactly what a multi-tap transform captures. Ship the basis as constants
   (or slowly-adapted) to dodge per-block eigendecomposition; the 2024 data-independent
   integer-KLT approximations (arXiv 2410.09227) confirm the fixed-basis form is
   adds+shifts only and FPGA-mappable. Verdict **borderline→embeddable** once the
   basis is fixed. Not a re-proposal of cycle 2: bestpartner is rank-1 single-tap
   selection; this is a fixed multi-tap transform. Risk: a fixed basis may under-fit
   non-stationary EMG — so gate/bench a small (grid-local, e.g. 4–8-tap) transform
   against bestpartner first.
2. **Adaptive Common Average Reference (ACAR) rank-1 front-end (table #3b).** The
   cheapest new spatial lever and a safe complement/precursor to #1: subtract the
   array's (weighted) common-mode before temporal prediction, via a reversible
   integer S-transform lift (code the total as a virtual channel). It removes the
   global shared component a spanning-tree/pairwise predictor cannot cancel, at
   ~O(1)/sample-ch amortized cost. Lower ceiling than #1 (rank-1, not full transform)
   but near-zero cost and RTL-trivial — a real Pareto win if HD-EMG common-mode is
   material. Gate per block so it can't hurt low-common-mode segments. Distinct from
   both cycle-1 beta and cycle-2 bestpartner (global mean, not a selected neighbour).
3. **rANS/tANS residual back-end (table #6).** Head-to-head vs. Rice on the *same*
   predictor — the one untried *entropy-axis* lever (orthogonal to #1/#2's spatial
   front-ends, so composable with either). FPGA-friendly (LOCO-ANS-style tANS: table
   lookups + renorm, no per-symbol division); may recover the last fraction of a bit
   on non-geometric residual blocks, but payoff is uncertain and the per-block freq
   table + reverse-encode buffer may cost more than the bits saved. Worth a bench,
   not the headline build.

Order reflects expected ratio gain per unit embedded cost for THIS device on the
real OTB set: a new *multi-tap* spatial lever with the highest ceiling (#1) > a
near-free rank-1 spatial complement (#2) > an uncertain, orthogonal entropy swap
(#3). #2 is also a low-risk fallback if #1's fixed basis under-delivers on
non-stationary EMG.

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
- [Channel-clustering reference selection for multichannel EEG compression (ScienceDirect S1746809416301252)](https://www.sciencedirect.com/science/article/abs/pii/S1746809416301252)
- [Srinivasan et al., Integer sub-optimal Karhunen–Loeve Transform for multi-channel lossless EEG compression (IEEE, doc 7071329)](https://ieeexplore.ieee.org/document/7071329) — the reversible **integer KLT via ladder/matrix factorization** basis for TOP PICK #1 (multi-tap inter-channel decorrelation). Reports ~3% compression-ratio degradation vs full KLT while cutting computational complexity >60% (paper-reported, unverified here).
- [Fast Data-independent KLT Approximations Based on Integer Functions (arXiv 2410.09227, 2024)](https://arxiv.org/pdf/2410.09227) — 2024 **multiplierless, data-independent (fixed-basis, no eigendecomposition) integer-KLT** approximations, FPGA-evaluated; embeddability evidence for #1's fixed-basis path. NB: paper targets *lossy* image coding — cited for the transform's complexity/hardware profile, not a lossless ratio.
- [Vaisman, Jordanic & Farina, Adaptive common average filtering for myocontrol applications (Medical & Biological Engineering & Computing 2014)](https://link.springer.com/article/10.1007/s11517-014-1215-1) — signal-adaptive common-average referencing (ACAR) for HD-EMG, basis for rank-1 front-end #2 (paper reports myocontrol/SNR benefit, not a compression ratio).
- [Adaptive Spatial Filtering of High-Density EMG for Reducing Noise and Artefacts in Myoelectric Control (ResearchGate 341365971)](https://www.researchgate.net/publication/341365971_Adaptive_Spatial_Filtering_of_High-Density_EMG_for_Reducing_the_Influence_of_Noise_and_Artefacts_in_Myoelectric_Control) — corroborates a large shared common-mode component across HD-EMG grids that global referencing removes (motivates #2's spatial lever).
- [Efficient sequential compression of multi-channel biomedical signals (arXiv 1605.04418)](https://arxiv.org/abs/1605.04418) — streaming multivariate-RLS lossless coder exploiting spatial + temporal redundancy; reports beating prior state-of-the-art lossless ratios on EEG/ECG (paper-reported, unverified here) — supporting evidence that a multi-channel (multi-tap) predictor beats per-channel.
- [Integer sub-optimal KLT for multi-channel lossless EEG compression (ResearchGate 253197278)](https://www.researchgate.net/publication/253197278_Integer_sub-optimal_Karhunen-Loeve_Transform_for_multi-channel_lossless_EEG_compression) — mirror of the IntSKLT source above.
- [TSCom-Bench: benchmarking learned lossless time-series compression (arXiv 2509.21002)](https://arxiv.org/abs/2509.21002)
- [Chained lightweight neural predictors for learned-lossless time-series (arXiv 2604.15472)](https://arxiv.org/abs/2604.15472)
- [Survey of streaming floating-point time-series compressors — Elf/SElf/Chimp/Gorilla (arXiv 2510.07015)](https://arxiv.org/abs/2510.07015)
- [GPU adaptive lossless floating-point compression framework (arXiv 2511.04140)](https://arxiv.org/abs/2511.04140)
- [Predictability-aware multichannel time-series compression (arXiv 2506.00614)](https://arxiv.org/abs/2506.00614)
- [Compressive on-chip action-potential recording (IEEE TBME 11183845)](https://ieeexplore.ieee.org/document/11183845) · [bioRxiv 2025.09.26.678698](https://www.biorxiv.org/content/10.1101/2025.09.26.678698)
