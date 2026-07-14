# SURVEY — lossless multichannel biosignal compression candidates

Cost-filtered, ranked survey of **lossless** compression methods for the 128-ch
RHD2164 / HD-EMG node (STM32H745 Cortex-M7 and Spartan-7 XC7S25; integer/fixed
only; causal/streaming; must beat per-channel FLAC by exploiting cross-channel
spatial correlation). **This file PROPOSES only** — no measured ratios, no codec
edits (non-negotiable #4). Watch-list methods are never promoted without explicit
human approval. Seeds `compression_spec/candidates.md`.

_Compiled: 2026-07-06 · refreshed 2026-07-10 · refreshed 2026-07-13 (cycle 2026-07-13) · refreshed 2026-07-14 (cycle 4, SURVEY-only) · web search (US) + candidates.md + INSIGHTS.md._

_Cycle log (what's already been tried, so don't re-recommend it):_
- _`LMS+Rice+xchan` (order-8, whole-signal float beta) is the incumbent real best (OTB 2.14×, cost 0.057; Hyser 1.47×)._
- _Cycle 1 (branch `compression-cycle-2026-07-08`, merged PR #1) implemented **`LMS+Rice+xchan_adaptive`** = backward-adaptive per-block **integer** beta replacing the offline float beta. Verified bit-exact/embeddable but **dominated**: OTB 2.13× at cost 0.065 (−0.48% ratio, +cost) vs the incumbent → **RETIRED 2026-07-13** (conclusively Pareto-dominated; `registry.py retired=True`). Do not re-implement this identically. Its open follow-ups (untried): pair adaptive-beta with **order-4** LMS; sweep adaptation block size 128/256/512; two-neighbour (up+left) front-end._
- _Cycle 2 (branch `compression-cycle-2026-07-10`, merged PR #2) implemented **`LMS+Rice+xchan_bestpartner`** — the prior SURVEY's TOP PICK #2 (per-channel best-partner selection via lowest-AbsMean reference + normalized-residual-cross-correlation gate). Verified/registered as a **non-dominated** Pareto point (OTB 2.15×, cost 0.063 — higher ratio AND higher cost than the incumbent) but did **not** displace the port pick. So best-partner selection is now DONE — do not re-propose it as new. Its open follow-ups (untried): widen the candidate set beyond the 4-neighbourhood (channel-clustering reference selection); multi-neighbour (2-tap) joint subtract._
- _Cycle 3 (this cycle, 2026-07-13, SURVEY-only): with best-partner (pairwise) and adaptive-beta both spent, the untried spatial frontier is **multi-tap** decorrelation — a reversible integer inter-channel transform (Integer-KLT / lifting) and a rank-1 common-mode front-end (Adaptive CAR). Both are genuinely distinct mechanisms from single-neighbour subtraction; see the refreshed table + recs below._
- _Cycle 3 RESULT (branch `compression-cycle-2026-07-13`) implemented **`LMS+Rice+iklt`** = the TOP PICK, a **fixed** data-independent 45° integer-KLT (3-step lifting) multi-tap front-end. Verified bit-exact/embeddable but **verifier split (A REJECT, B PROMOTE) → held for human review, not promoted**, and **conclusively Pareto-dominated → RETIRED 2026-07-13**: OTB 2.07× at cost 0.068 vs incumbent 2.24×/0.057 (worse ratio AND higher cost; also dominated by bestpartner 2.25×/0.063 and delta+Rice+xchan 2.19×/0.013). Root cause measured: the **fixed** 45° basis captures only **+8.8%** real cross-channel gain vs single-neighbour subtract's **+18.0%** — the isotropic-model rotation is mismatched to real anisotropic HD-sEMG covariance. Do not re-propose a **fixed data-independent** inter-channel transform; the spatial gain lives in a **data-dependent** weight. See `experiments/002_lms_rice_iklt.md`._
- _Cycle 4 (this cycle, 2026-07-14, SURVEY-only): checked the retired ledger (`registry.py --selftest`: `LMS+Rice+xchan_adaptive` and `LMS+Rice+iklt` are RETIRED; `bestpartner` is DONE/non-dominated). Slate this cycle is the three genuinely-distinct mechanisms below, one per axis — a **data-dependent** transform basis (spatial), an **entropy back-end** swap, and a **rank-1 common-mode** front-end (a distinct spatial axis) — all aligned with INSIGHTS' open frontier (data-dependent > data-independent, P3; entropy axis never tested, P5) and steering clear of the two dead ends (fixed data-independent inter-channel transform; scalar adaptive-beta alone)._
- _**This cycle's ranked slate (proposals only — see the recs section for full rationale):**_
  - _**(1, highest) Adaptive/data-dependent 2×2 integer-lifting rotation** (`LMS+Rice+xrot_adaptive`). Knob: replace the retired iklt's fixed θ=45° with a **per-block backward-adaptive quantized integer angle** (θ chosen from the previous **reconstructed** block's neighbour covariance, decoder recomputes → zero side-info), keeping the exact reversible 3-lift shear structure, cascaded over grid neighbour pairs. Why the data says so: iklt's fixed basis got only +8.8% vs the data-dependent single-neighbour's +18.0% on the identical predictor/back-end (experiment 002) — the entire gap is basis mismatch (P3), and adapting the one angle is the minimal multiplierless lever that closes it losslessly. Distinct from the retired **fixed** iklt (data-independent→data-dependent, the axis P3 names decisive) AND from the retired scalar **adaptive-beta** (energy-preserving orthogonal rotation of BOTH channels, cascaded multi-tap, vs an asymmetric rank-1 subtract of one)._
  - _**(2) tANS/rANS residual back-end vs Rice** (the entropy axis, untouched all 4 cycles). Knob: swap the adaptive Golomb-Rice coder for a table-driven tANS (LOCO-ANS style) on the SAME `LMS+Rice+xchan` predictor+front-end. Why: every cycle so far moved only the cross-channel front-end; Rice is optimal only for exactly-geometric residuals and real EMG residual histograms deviate sub-Golomb (P5). Orthogonal to the front-end, so composable with either spatial pick; head-to-head on the same predictor isolates the back-end's marginal bits._
  - _**(3) Adaptive CAR (rank-1 common-mode) front-end**, the cheapest untried spatial axis. Knob: subtract a reversible-integer running array-mean (S-transform lift) per time-slice before LMS, gated per block. Why: removes the **shared common-mode** (drive/power-line/reference drift) that pairwise subtraction structurally cannot cancel — a different slice of the cross-channel mutual information (P1) than neighbour rotations — at near-free O(1)/sample-ch amortized cost. Survey rec #3b, still unmeasured._
- _**Cycle 4 RESULT (branch `compression-cycle-2026-07-13`, measured on all 4 real sets at 15 000 samp, `results/cycle_bench.csv`):** all three double-verified PROMOTE (no splits); **none promoted** (none beats the incumbent on real data); **two RETIRED**. **(1) `LMS+Rice+iklt_adaptive`** — the data-dependent rotation captured only **+1.7–3.3%** real xchan gain (−0.5% CapgMyo), *worse* than the retired fixed iklt; dominated on all 4 → RETIRED. Root cause: the backward-estimated angle is stale/noisy and an energy-preserving rotation corrupts BOTH channels (less robust than the rank-1 subtract). **Multi-tap spatial transforms — fixed or adaptive — are now a settled dead end; do not re-propose.** **(2) `LMS+Rice+xchan_tans`** — tANS was **1.4–1.8% SMALLER than Rice at ~2× cost** on every real set (Rice already at the entropy floor for the near-geometric residual; per-block table = net loss) → RETIRED. **The entropy back-end is a proven-negative, spent lever; do not re-propose any entropy-coder swap as a ratio play.** **(3) `LMS+Rice+acar`** — captured **+14.4%** on the tight 64-ch OTB array but only +0.8–2.5% on the larger 128/320-ch arrays (global mean is a poor basis where redundancy is local); genuinely non-dominated on OTB/CapgMyo (2.089×/0.0559, cheaper than incumbent) → KEPT registered, not promoted, not retired._
- _**Cycle 5 ranked next hypotheses (proposals only, from the refreshed INSIGHTS frontier — both prior open-frontier levers are now spent, so every live lever is a variation on the adaptive rank-1 subtract):**_
  - _**(1, highest payoff/cost) Best-partner rebuilt on the order-4 predictor.** Knob: predictor order 8→4 under the shipped `LMS+Rice+xchan_bestpartner` front-end (`lms4s7+x7/b512`, cycle_search.csv 2.321×/0.027 — order-4 beats order-8 at ~half cost, P2). Cheaper AND better predictor under the one mechanism that works; could dominate the incumbent on BOTH axes with no new mechanism risk. Carry-over from cycle 2, now the top live lever._
  - _**(2) Multi-parent backward-adaptive rank-1 subtract.** Knob: single grid-parent → a small set of causal neighbours (up + left), each its own backward-adaptive integer beta, summed. Stays rank-per-parent (robust under estimation noise — unlike the rotation that corrupts both channels) and zero side-info; targets the residual *local* spatial MI one parent leaves, largest where neighbour |corr| is high (OTB/CEMHSEY/Hyser). Gate hard on per-parent cost._
  - _**(3) Scale-matched two-stage front-end: global CAR then local pairwise.** Knob: backward-gated ACAR lift followed by the adaptive neighbour subtract on the CAR residual — capture the *two different* MI slices (global common-mode + local pairwise) where both exist. Highest ceiling, but measure whether the slices are additive or already redundant; on large arrays CAR may add ~nothing (won't clear its gate)._

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
| 3 | **★ Data-DEPENDENT adaptive 2×2 integer-lifting rotation (backward-adaptive Givens angle)** — TOP PICK this cycle | Keeps the retired iklt's reversible 3-lift shear butterfly (Srinivasan IntSKLT / lifting-of-Givens: shift+add, no float mult) but makes the rotation angle θ **backward-adaptive per block** — quantized θ derived from the previous **reconstructed** block's neighbour covariance, decoder recomputes it (zero side-info). Cascaded over grid neighbour pairs → genuinely multi-tap. The KLT is the optimal linear decorrelator only when its basis matches the covariance (P3); the retired *fixed* 45° basis captured only +8.8% of real cross-channel gain vs the data-dependent single-neighbour's +18.0% (exp 002) precisely because real HD-sEMG covariance is anisotropic/non-stationary. Adapting the single angle is the minimal lever that closes that measured gap. | **borderline → embeddable** | angle from 2 covariance accumulators/pair on a short reconstructed window + a **quantized-θ → lift-coeff lookup** (no per-block eigendecomposition, no float); shift+add lifts. NOT the retired fixed iklt (fixed→adaptive) nor the retired scalar adaptive-beta (orthogonal rotation of both channels vs asymmetric rank-1 subtract). Verify the per-block covariance accumulate stays inside budget |
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

All three cheap/fixed spatial levers are now spent or dead: per-block beta (cycle 1,
retired), *pairwise* best-partner selection (cycle 2, shipped non-dominated), and the
**fixed** data-independent integer-KLT (cycle 3, retired — its isotropic 45° basis
captured only +8.8% real gain vs the data-dependent single-neighbour's +18.0%). The
spatial lever still dominates temporal sophistication (~70× on real Hyser ablation),
and cycle 3 pinpointed *where* the remaining spatial gain lives: in a **data-dependent
weight/basis**, not a wider fixed one (P3). This cycle's slate spans three distinct
axes — a data-dependent transform basis, the never-tested entropy back-end, and a
distinct rank-1 spatial axis.

1. **★ TOP PICK — Data-DEPENDENT adaptive integer-lifting rotation (backward-adaptive Givens angle, table #3).**
   The direct, principled answer to cycle 3's negative result. Keep the retired iklt's
   reversible 3-lift shear butterfly (multiplierless, lossless by construction) but make
   the rotation **angle backward-adaptive per block**: quantize θ from the previous
   *reconstructed* block's neighbour covariance, decoder recomputes it → **zero side-info**
   (P4). The KLT decorrelates optimally only when its basis matches the covariance; the
   fixed 45° basis was mismatched to anisotropic non-stationary HD-sEMG, which is the whole
   measured +8.8%-vs-+18.0% gap (exp 002). Adapting the single angle is the minimal lever
   that closes it while staying shift+add. Distinct from the retired fixed iklt
   (data-independent → data-dependent) AND from the retired scalar adaptive-beta
   (energy-preserving orthogonal rotation of *both* channels, cascaded multi-tap, vs an
   asymmetric rank-1 subtract of *one*). Verdict **borderline → embeddable**: cost is the
   iklt lifts + a cheap per-block covariance accumulate + a quantized-θ→coeff lookup (no
   eigendecomposition, no float). Risk to watch: the per-block covariance estimate must stay
   inside the neural 125-cyc budget; gate the rotation per block so it can't hurt
   low-correlation segments.
2. **tANS/rANS residual back-end vs Rice (table #6) — the never-tested entropy axis.**
   Head-to-head vs. Rice on the *same* `LMS+Rice+xchan` predictor+front-end — the one lever
   untouched across all four cycles, and orthogonal to the spatial front-end so composable
   with #1 or the incumbent. Rice is optimal only for exactly-geometric residuals; real EMG
   residual blocks deviate sub-Golomb, and a table-driven tANS approaches the true block
   entropy (P5). FPGA-friendly (LOCO-ANS-style: table lookups + renorm, no per-symbol
   division). Payoff is expected small and uncertain — the per-block freq table (side-info) +
   reverse-encode buffer may cost more than the bits saved — but it is the only way to test
   the back-end's marginal fraction, and a bench on the same predictor isolates it cleanly.
3. **Adaptive Common Average Reference (ACAR) rank-1 common-mode front-end (table #3b).** The
   cheapest new spatial lever and a distinct spatial axis: subtract the array's (weighted)
   common-mode before temporal prediction via a reversible integer S-transform lift (code the
   array total as a virtual channel). It removes the **shared common-mode** (movement drive /
   power-line / reference drift) that a pairwise or rotation-based front-end structurally
   cannot fully cancel — a different slice of the cross-channel mutual information (P1) — at
   ~O(1)/sample-ch amortized cost, RTL-trivial. Lower ceiling than #1 (rank-1, not a full
   transform) but near-zero cost → a real Pareto win if HD-EMG common-mode is material, and a
   low-risk fallback if #1's adaptive basis under-delivers. Gate per block. Distinct from
   cycle-1 beta and cycle-2 bestpartner (global mean, not a selected neighbour) and from #1
   (rank-1 global vs pairwise rotation).

Order reflects expected ratio gain per unit embedded cost for THIS device on the
real OTB set: the highest-ceiling *new* spatial lever that directly closes the
measured iklt basis-mismatch gap (#1) > the never-tested, composable entropy swap
with a small but real ceiling (#2) > a near-free rank-1 spatial complement / fallback
(#3). All three are genuinely distinct in mechanism, not parameter variants.

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
- [Fast Data-independent KLT Approximations Based on Integer Functions (arXiv 2410.09227, 2024)](https://arxiv.org/pdf/2410.09227) — 2024 **multiplierless, data-independent (fixed-basis, no eigendecomposition) integer-KLT** approximations, FPGA-evaluated. Cited now as the *retired* fixed-basis path's hardware profile (cycle 3) — the fixed basis is exactly what exp 002 showed underfits; this cycle's TOP PICK #1 keeps the lifting hardware but makes the **basis data-dependent**. NB: paper targets *lossy* image coding.
- [Lossless and Near-Lossless Audio Compression Using Integer-Reversible Modulated Lapped Transforms (RGate 221578983)](https://www.researchgate.net/publication/221578983_Lossless_and_Near-Lossless_Audio_Compression_Using_Integer-Reversible_Modulated_Lapped_Transforms) — precedent that an **integer-reversible transform realized as lifted Givens rotations** (shift+add, no float) feeding a **backward-adaptive Golomb-Rice/RLGR** coder is a working lossless pipeline; supports the reversibility + zero-side-info construction of TOP PICK #1's adaptive-angle rotation.
- [Lossy-to-Lossless Hyperspectral Image Compression Based on Multiplierless Reversible Integer TDLT/KLT (IEEE 5075592)](https://ieeexplore.ieee.org/document/5075592/) — reversible integer KLT via lifting for cross-channel (spectral) decorrelation; corroborates that a multi-tap reversible integer inter-channel transform is lossless-by-construction and multiplierless (TOP PICK #1 mechanism).
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
