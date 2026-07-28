# INSIGHTS — distilled, theory-rooted learnings for codec selection

**Read this before every Survey.** This is the *principle-level* knowledge base: what
the harness has **proven on REAL data** about which compression mechanisms work here and
**why**, stated in terms of sound compression theory so it actively guides which
candidate to try next. It complements — does not duplicate — the other records:

- `CYCLE_LOG.md` — one raw row per cycle (append-only ledger of *what* was tried).
- `experiments/NNN_*.md` — the full per-experiment record (commands, outputs, verdicts).
- `SURVEY.md` — forward-looking *proposals* (unverified).
- **`INSIGHTS.md` (this file)** — the backward-looking *distilled truth*: the durable
  lessons, each tied to evidence and to a compression-theory reason, plus the ranked
  open frontier and the dead ends not to re-propose.

**Maintenance (every cycle, by the analyst):** after measurement + verification, append
or refine the relevant principle below with the new evidence. A learning enters this file
only when the harness measured it on **real** data (synthetic is mechanism-illustration
only). State the *theory* for why the result holds, not just the number — a number without
a mechanism does not generalize to the next candidate.

**Latitude (explicit):** candidates need not come from a paper. You may **design a novel
codec** by combining or extending the existing primitives (predictive coding, reversible
integer transforms/lifting, context modeling, Golomb/Rice, ANS) **provided the design is
rooted in sound compression theory** — i.e. you can state *why* it should lower the
residual's entropy or decorrelate the array better, from an information-theoretic or
signal-model argument, before measuring. Novelty is welcome; unprincipled "try X and see"
is not. Every novel design faces the same bars: lossless + bit-exact, `embedded_ok`, and
**real data decides**.

---

## Established principles (proven on real HD-sEMG)

### P1 — Cross-channel spatial decorrelation is the dominant lever, and it is bounded by the array's real neighbour correlation.
- **Evidence:** achieved cross-channel gain +10.8% (Hyser) to +17.4% (OTB) to +13.1%
  (CEMHSEY); **near-zero (+1.3%) on CapgMyo**, whose differential 8×16 array has neighbour
  |corr| ≈ 0.29. Ablation: turning `+xchan` on vs off is worth ~70× every temporal knob.
- **Theory:** a per-channel coder cannot remove redundancy that lives *between* channels
  (shared spatial/common-mode content). The reducible bits ≈ the mutual information between
  a channel and its neighbours, which grows with their correlation. Where neighbour
  correlation is low, that mutual information — and thus the achievable gain — is small
  *by the physics*, not by codec weakness.
- **Implication:** prioritize spatial mechanisms **only where real neighbour correlation is
  high**. On low-correlation arrays the ceiling is real; do not chase spatial gain there.
  CapgMyo is the honest negative control that confirms the harness reports gain only where
  the signal carries it.
- **Refinement (2026-07-14, ACAR):** the cross-channel MI decomposes into distinct,
  **non-interchangeable slices**, and array size selects which one dominates. Removing the
  **global rank-1 common-mode** (`LMS+Rice+acar`, array-mean CAR lift) captured **+14.4%** on
  the small, tightly-coupled 64-ch OTB array (near the single-neighbour subtract's +17.4%) but
  collapsed to **+0.8–2.5%** on the larger 128-/320-ch Hyser/CEMHSEY arrays, where the
  single-neighbour beta still got +10.8–13.1%. **Theory:** CAR removes exactly one eigenvector
  (DC-across-array); on small arrays the shared content *is* that global mode, but on large
  arrays it is spatially **local** (high neighbour correlation, low global coherence across
  320 channels), so a single global mean is a mismatched basis and the local-pairwise weight
  captures far more MI. Choose the spatial basis to match the *spatial scale* of the real
  redundancy — global mean for tight arrays, local pairwise for extended ones.
- **Refinement (2026-07-16, `LMS+Rice+xchan_multiparent` — RETIRED).** Extending the spatial support
  from one parent to two (up + left), each with its own backward-adaptive β, the two rank-1 subtracts
  **summed**, captured only **~half** the single-parent gain on real data (otb +8.0% vs +17.4%, hyser
  +5.1% vs +10.8%, cemhsey +8.3% vs +13.1%) and was Pareto-dominated on all 4 sets. **Adding a parent
  HURT.** **Theory:** the two parents are themselves correlated (Cov(up,left) > 0). Each *marginal*
  β_p = ⟨x_c,x_p⟩/⟨x_p,x_p⟩ is correct only when its parent is the sole regressor; summing two
  independent marginal subtracts **double-counts the parents' shared common mode → over-subtracts**,
  injecting more noise than the extra MI removed — the classic *marginal vs multiple* regression gap
  under collinear predictors. **Implication:** richer spatial *topology* pays only via a **joint**
  decorrelation (2×2 normal-equations solve accounting for parent–parent covariance) — which is exactly
  the multi-tap rotation/lifting already a dead end (P3). A *sum of independent rank-1 subtracts* is not
  a valid multi-parent extension. The single most-correlated parent (best-partner) remains the right
  rank-1 lever.
- **Refinement (2026-07-19, `LMS4+Rice+acar+bestpartner` — two-stage cascade, KEPT non-dominated on OTB only).**
  Cascading the two *non-interchangeable* MI slices — global ACAR lift THEN local order-4 best-partner on the CAR
  residual — the CAR stage's **isolated marginal gain over best-partner alone** was **+0.96 pp of cross-channel gain
  on the tight 64-ch OTB array (+0.81% ratio, 2.1795×)**, **exactly 0 on CapgMyo (the CAR gate never fired →
  bit-identical to best-partner)**, and **NEGATIVE on the large arrays (−0.26 pp hyser, −0.23 pp cemhsey)**. **Theory:
  the two slices are additive only where the global common-mode is a real eigenvector.** On tight arrays the
  DC-across-array mode is physical and orthogonal to the local pairwise mode, so cascading captures both. On large
  128-/320-ch arrays the shared content is spatially *local*; once best-partner removes the local slice, the residual's
  array-mean energy is mostly independent noise, so a fired CAR lift subtracts a *mismatched global basis* and injects
  slightly more noise than it removes. **Frontier #1's "capture both slices everywhere" hypothesis is DISPROVEN — the
  slices are additive only on tight arrays.** The cascade is a genuine non-dominated max-ratio corner on OTB (kept) but
  is not a new global best; on the primary Hyser it *loses* (−0.23%) at higher cost. Orthogonal-by-construction was
  necessary (it avoided the multiparent double-count) but not sufficient: two slices being uncorrelated does not make
  the second one *present* — array geometry decides whether the global slice carries any MI at all.

### P1b — A JOINT (co-adaptive) 2-parent solve recovers a second parent's MI where the summed marginal one could not — but *parent selection* and *parent count* are substitutes, not complements, and which wins is set by array geometry.
- **Evidence (2026-07-19, `LMS+Rice+xchan_joint2` — KEPT, cost-dominant, NOT promoted).** One joint backward-adaptive
  sign-sign LMS predicting channel c from BOTH parents (up+left), taps co-adapting against the *shared*
  post-subtraction residual, delivered the **highest cross-channel gain of any codec on the large Hyser array:
  +12.26%** (vs best-partner's +11.31% and single-fixed-parent's +10.8%) — the second parent genuinely adds MI. But on
  the tight 64-ch OTB array the fixed up+left pair got only **+17.77%**, *below* best-partner's *selected* neighbour
  (+18.44%). Net across real data: wins Hyser (+0.85%), loses OTB (−0.57%), ties CapgMyo/CEMHSEY → **dead tie on the
  4-set mean (−0.014%)**, at lower cost (0.0366 < 0.0394) and **zero side-info**.
- **Theory:** the joint gradient sees the *actual* residual after both current taps subtract, so each tap adapts to the
  correlation *remaining* once the other's contribution is out — the stochastic-gradient realization of the 2×2
  multiple-regression solve. This is precisely the *marginal→multiple* fix the retired summed multiparent lacked
  (which is why joint2 **adds** on Hyser where multiparent *lost*), and being asymmetric (residual-only injection,
  parents left clean) it avoids the both-channel corruption that sank iklt_adaptive (P3). **So a joint 2-parent solve
  is the valid multi-parent extension P1/P3 said was needed — and it works.** But two spatial degrees of freedom —
  *which* single parent (best-partner selection) vs *how many* parents (joint count) — are **substitutes**: on large
  arrays with diffuse local structure the extra jointly-solved parent wins; on tight arrays with one dominant diagonal
  neighbour, *selecting* that parent wins. They do not stack into a decisive combined win.
- **Implication:** the joint solve is proven and available (a cheap multiplierless sign-LMS, not a matrix), but on
  its own it only ties the best. The unspent combination is **best-of-N-partner *selection* fused with the joint solve**
  (jointly solve the best *pair*, not a fixed up+left pair) — the one lever that could stack selection AND count.

### P1c — The spatial parent must be chosen by *achieved coded bits* over a *small, physically-motivated* candidate set. Globally optimising a cheap MI surrogate over a large one loses.
- **Evidence (2026-07-28, `LMS4+Rice+xtree` — RETIRED).** A per-block Chow-Liu **maximum-weight
  spanning forest** over ≤12 undirected neighbours per channel (8 grid + 4 axis-distance-2), edge
  weight = |sign-sign cross-correlation| on the previous reconstructed block, coding in the forest's
  topological order (zero side-info), with the *unchanged* shipped integer-LS β and Rice-bits keep/drop
  gate behind it. The apples-to-apples control is `LMS4+Rice+xchan_bestpartner_adaptive` (`bpa`) —
  identical predictor, β estimator, gate, block structure and zero side-info; **only the topology
  criterion differs**. `xtree` lost on **all four** real sets: hyser 1.461730 vs 1.477020 (−1.04%), otb
  2.134552 vs 2.153106 (−0.86%), capgmyo 1.349710 vs 1.352866 (−0.23%), cemhsey 1.945732 vs 1.953948
  (−0.42%), at higher cost (0.0430 vs 0.0387). Isolated cross-channel gain **0.24–1.40 pp lower** on
  every set (+9.91/+16.94/+1.31/+12.51% vs `bpa`'s +11.05/+17.96/+1.55/+12.99%). The mechanism was
  genuinely exercised, not degenerate: **34.8–42.2% of chosen parents have a HIGHER grid index**
  (structurally unreachable by any raster codec here) and **47.7–68.7% lie outside the causal
  4-neighbourhood**.
- **Theory, two compounding failures.** (1) **Wrong objective.** Chow-Liu maximises Σ I(u;v) under a
  Gaussian/arcsine-law proxy, but coded length is set by the residual entropy achieved after a
  *rounded, Q8-quantised, integer-LS* rank-1 subtract — not monotone in |ρ| across edges with
  different variance ratios. `bpa` selects **directly on estimated Rice bits** across all its
  candidates and can fall back to a second-best edge; `xtree` fixes topology from the surrogate first
  and can then only *drop* the edge, making the channel a parentless root. (2) **Winner's curse.**
  Each weight is estimated from 32 samples (256-sample block, 8× subsampled) over ~6C ≈ 768–1920
  candidate edges; the **max** over that many noisy sample correlations is upward-biased and
  systematically prefers long/weak edges — which is exactly what the 47.7–68.7% "outside the
  4-neighbourhood" figure measures.
- **Implication:** the small causal 4-neighbourhood is a **prior**, not a limitation — on a locally
  correlated array (P1) the max-MI parent is already inside it, so global search has no headroom to
  buy and only estimator variance to pay. Select on the true bits criterion, over few candidates. Do
  not re-propose global channel-topology search (MST / clustering / permuted coding order) as a ratio
  play.

### P1d — Cross-channel LAG is a dead freedom along the *same* parent, but reading a *different* (opposite-side) channel at lag 1 is a real, previously unreachable MI slice — and it pays inversely to zero-lag correlation.
- **Evidence (2026-07-28, `LMS4+Rice+xstfir` — KEPT, non-dominated CapgMyo corner).** Replacing the
  scalar zero-lag subtract with a 4-tap spatio-temporal FIR (parent lags 0,1,2 + opposite-side
  neighbour at lag 1, one joint sign-sign LMS, zero side-info) gave hyser 1.472533 (−0.53% vs best),
  otb 2.108745 (−2.46%), cemhsey 1.950256 (−0.27%), **capgmyo 1.362200 (+0.87%, the highest ratio of
  any codec or reference on that set)**. An **in-memory tap ablation on the REAL arrays** (everything
  else byte-identical) splits it exactly:

  | isolated term | hyser | otb | capgmyo | cemhsey |
  |---|---:|---:|---:|---:|
  | parent lags 1,2 (the "fractional-delay FIR") | −0.20% | **−2.86%** | −0.28% | −0.14% |
  | opposite-side neighbour `x_q[n−1]` | +0.07% | **+1.56%** | **+1.08%** | −0.08% |
  | net (all 4 taps vs zero-lag only) | −0.12% | −1.35% | **+0.80%** | −0.22% |

- **Theory.** `x_p[n−1]`, `x_p[n−2]` are the *same channel's* short-lag past. At 1–2 kS/s they are
  strongly collinear with `x_p[n]`, and once the order-4 temporal LMS has whitened channel c's own
  past, I(x_c[n]; x_p[n−1], x_p[n−2] | x_p[n]) ≈ 0 — so the extra taps add **stochastic-gradient
  misadjustment with no MI to pay for it**, and the damage is worst exactly where ρ(0) is largest and
  the taps carry most weight (OTB −2.86%). MUAP *propagation delay* is not the exploitable structure
  it looks like: what propagates is already captured by the zero-lag pairwise weight plus the
  temporal predictor. By contrast `x_q[n−1]` is a **different channel** — a genuinely new spatial
  degree of freedom — and lag 1 is merely the causality-legal way to read a higher-index row, which
  the **time-major** coding order makes available. Its payoff scales *inversely* with ρ(0): CapgMyo
  (neighbour |corr| ≈ 0.29) gets +1.08% from it, Hyser +0.07%.
- **Implication:** the lever worth keeping is **time-major coding order** (it unlocks the far-side
  neighbour, a direction raster order structurally cannot reach), not a delay-compensating FIR. This
  is also the first mechanism that adds gain **where P1 says spatial gain is scarce** — a
  low-ρ(0)-array specialist, complementary to (not competitive with) the high-ρ(0) best-partner
  family. Do not re-propose multi-lag prediction from the *same* parent.

### P2 — Temporal prediction saturates early; deeper prediction *hurts* on real data.
- **Evidence:** order-4 LMS beats order-8 on both Hyser and OTB (order 4→8 *costs* ratio in
  the ablation); the registry default was over-provisioned at order-8.
- **Theory:** after a low-order linear predictor the HD-sEMG residual is already close to
  white — the remaining structure the extra taps could exploit is mostly noise, so a longer
  adaptive filter fits noise (raising, not lowering, coded entropy) while its state and
  per-sample cost grow with order.
- **Implication:** keep the temporal predictor **small** (order ≤4). Spend complexity on the
  spatial front-end and the entropy back-end, not on deeper temporal prediction.
- **Refinement (2026-07-16, `LMS4+Rice+xchan_bestpartner` — PROMOTED, new best).** Dropping the
  predictor order 8→4 *beneath the unchanged best-partner front-end* simultaneously **raised** the
  ratio on all 4 real sets and **cut cost 0.063→0.039** (otb 2.162× vs 2.151×, hyser 1.480× vs 1.478×,
  capgmyo 1.350× vs 1.349×, cemhsey 1.956× vs 1.955×) — a both-axes Pareto win that made the order-8
  bestpartner dominated (RETIRED). Third independent real cycle where order-4 beats order-8 (search
  ablation this cycle: order 4→8 *costs* +0.79% ratio). **P2 is now the reliable way to buy cost back
  under any front-end:** right-size the predictor to order-4 first, then spend the freed budget on the
  spatial lever. The compression *level* is still owned by the cross-channel front-end (+11–18% over
  temporal-only); order-4 owns the cost and a small ratio bonus (order-8's extra taps fit residual
  noise).

### P3 — For the spatial transform, *data-dependent* beats *data-independent*.
- **Evidence:** the fixed 45° integer-KLT front-end (`LMS+Rice+iklt`, cycle 2026-07-13)
  captured only ~+8.8% real cross-channel gain — about **half** the adaptive single-neighbour
  subtract's +18.0% — and was Pareto-dominated (worse ratio, higher cost). Retired.
- **Theory:** the KLT is the optimal linear decorrelator **only when its basis matches the
  true covariance**. A fixed 45° rotation is exactly the KLT for a *stationary, isotropic,
  equal-variance* neighbour pair. Real HD-sEMG covariance is **anisotropic and
  non-stationary**, so a fixed basis is mismatched and leaves cross-channel redundancy
  uncaptured; the adaptive per-neighbour weight (`beta`) tracks the actual, drifting pairwise
  correlation and removes more of it.
- **Implication:** adaptive/data-dependent decorrelation > fixed transforms on this data. A
  multi-tap spatial transform is only worth its cost if its **basis adapts** (data-dependent
  lifting), which is borderline on the embedded budget — a fixed multi-tap basis will not beat
  the adaptive rank-1 subtract here.
- **Refinement (2026-07-14, iklt_adaptive) — even a *data-dependent* multi-tap basis loses,
  and by *more* than the fixed one.** Making the integer-KLT rotation angle backward-adaptive
  (`LMS+Rice+iklt_adaptive`, per-pair/per-block angle from the previous block's covariance)
  captured only **+1.7–3.3%** real cross-channel gain (−0.5% on CapgMyo) — *worse* than the
  retired **fixed** 45° iklt (+8.8% on OTB), and Pareto-dominated on **all 4** real sets.
  **Theory:** two compounding penalties. (1) The angle is estimated from the *previous* block —
  a stale, noisy estimate on non-stationary HD-sEMG. (2) A Givens rotation is energy-preserving
  and mixes **both** channels, so angle-estimation error corrupts *both* outputs, whereas the
  rank-1 subtract injects estimation noise only into the residual channel and leaves the parent
  clean; cascading the noisy rotation over all pairs compounds the error. **The rank-1 adaptive
  subtract is not just a cheaper decorrelator — it is a more *robust* one under causal estimation
  noise.** Multi-tap spatial transforms (fixed OR adaptive) are now a settled dead end here.

### P4 — Embeddability is a hard gate; rank on the ratio-vs-cost Pareto front, and prefer backward-adaptive (zero side-info).
- **Evidence:** `LMS+Rice+xchan_adaptive` (backward-adaptive integer beta) was kept for
  closing the port caveat even though marginally dominated; the incumbent's headline beta is a
  float whole-signal value computed offline — **not producible on-node**. Best-partner adds
  side-info (partner id + beta) for only +0.4% real ratio at +10% cost — a genuine but marginal
  non-dominated corner.
- **Theory:** anything the decoder can recompute from already-reconstructed causal history costs
  **zero side-info** and is streaming-legal; anything derived from the whole recording (offline)
  or shipped in the header trades bits and on-node feasibility for ratio.
- **Implication:** prefer backward-adaptive parameter estimation (decoder mirrors encoder from
  reconstructed data). Treat any offline/whole-signal parameter as a **port caveat**, not a real
  on-node result. Never rank on ratio alone — a higher-cost point must *earn* its place on the
  Pareto front.
- **Refinement (2026-07-19, `LMS4+Rice+xchan_bestpartner_adaptive` — port caveat CLOSED at ~zero ratio cost).** The
  promoted best derives (partner, β) offline over the whole signal and ships a 2×int16/ch header. Replacing that with
  **per-block backward re-selection** from the previous reconstructed raw block (decoder mirrors it → zero side-info,
  look-ahead 0) **held the offline ratio to within −0.08%…−0.41%** on the three high-corr real sets and **beat it by
  +0.18% on CapgMyo**, at slightly lower cost (0.0387 < 0.0394). **Theory:** the best-partner *identity* is stable
  within a recording (neighbour geometry is fixed), so a per-block re-derived choice lands on essentially the offline
  optimum minus a tiny burst-boundary transient (the whole −0.2…−0.4 pp give-up); on the low-corr CapgMyo, per-block
  adaptation even tracks mild non-stationarity *better* than one static choice. **A slowly-varying parameter costs
  ~zero ratio to make backward-adaptive** — so the general rule is stronger than "prefer" backward-adaptive: for a
  parameter that is stable across the recording, offline estimation buys *nothing* over per-block re-derivation, and the
  side-info/look-ahead it costs is pure loss. Frontier #3 (port-caveat closure) is spent **positive**: the promoted
  best now has a fully on-node, streaming-legal, zero-side-info realization at the same ratio.

### P5 — Rice/Golomb is near-optimal for the (near-geometric) residual; the entropy back-end is NOT a lever here (proven negative on real data).
- **Evidence:** every embeddable codec here uses adaptive Golomb-Rice and lands close to the
  offline generic references (lzma/zstd) it should not be able to reach with a per-symbol coder.
  **Direct head-to-head (2026-07-14, `LMS+Rice+xchan_tans`):** a LOCO-ANS-style table-driven
  tANS coder swapped in for Rice on the *identical* LMS+xchan predictor was **1.4–1.8% SMALLER
  at ~2× the cost** (0.109 vs 0.0572) on **all 4** real sets (otb 2.103× vs 2.143×, hyser 1.451×
  vs 1.474×, capgmyo 1.330× vs 1.349×, cemhsey 1.922× vs 1.955×) — a net loss, Pareto-dominated.
- **Theory:** Golomb-Rice is the optimal prefix code for an exactly-geometric (Laplacian-ish)
  residual. Real HD-sEMG residual blocks are near-geometric enough that Rice already sits at the
  entropy floor — there is **no sub-Golomb fraction large enough to amortize** the ANS
  category+mantissa split plus the per-block category-frequency table (side-info). The heavier
  coder pays overhead against a back-end already at the limit, so it loses bits.
- **Implication:** the entropy back-end is a **spent, dead lever** on this data — do not
  re-propose rANS/tANS/arithmetic back-ends as a ratio play. The residual entropy is set by the
  predictor + spatial front-end; to lower coded bits, lower the residual entropy upstream (better
  decorrelation), not the coder. (A back-end swap could still be justified purely for
  *throughput/hardware* reasons, never for ratio.)
- **Extension (2026-07-16, `LMS+Rice+xctx` — RETIRED): even the Rice *parameter's context* is a dead
  lever, not just the engine.** Conditioning the per-sample Rice k on a cross-channel context
  (JPEG-LS/LOCO-I energy buckets driven by the neighbour's residual magnitude) — engine unchanged —
  came in **2.3–2.8% BELOW a plain per-block adaptive k on every real set** at ~2× cost (below even
  plain `LMS+Rice` with no cross-channel front-end at all). **Theory, two compounding failures:** (1)
  after the LMS predictor whitens the residual, H(e_c | neighbour energy) ≈ H(e_c) — the across-channel
  heteroscedasticity the context was meant to exploit is *already removed by the temporal predictor*,
  not left in the residual; (2) splitting into 12 energy buckets fragments the per-context sample count,
  so each k is noisier and average coded length *rises* (context-model cost with no conditional-entropy
  payoff). **The residual's conditional entropy given a cross-channel context ≈ its unconditional
  entropy on this data** — so *any* context-modeling of the Rice parameter (spatial or otherwise) is a
  spent lever too, for the same reason the engine swap is: Rice already sits at the floor and there is
  no sub-Golomb fraction to amortize the model cost.

### P6 — After an order-4 LMS the HD-sEMG residual is white **at ALL lags**, not just short ones: long-lag / periodic (pitch-tap, MUAP-firing-period) prediction has no MI to remove.
- **Evidence (2026-07-28, `LMS4+Rice+ltp` — RETIRED).** A gated long-term tap `e'[n] = e[n] −
  round(g·e[n−T])` after the order-4 short-term LMS, with (T ∈ [64,200] ⇒ 32–10 pps, integer-LS g,
  ρ ≥ 3/8 gate) re-derived per block per channel from the previous reconstructed residual (zero
  side-info). Forcing the gate off **in memory** gives a byte-identical build minus the tap, so the
  tap's contribution on real data is exact: **−0.012% (hyser), +0.003% (otb), −0.035% (capgmyo),
  +0.022% (cemhsey)** — i.e. **zero, and negative on two of four** — *despite the gate firing on
  1.1% / 3.8% / 11.4% / 11.7% of block-channels. The mechanism engaged and bought nothing.* The
  codec's entire +0.07…+0.51% edge over `LMS+Rice` is the order-8→4 predictor change (P2), not the
  LTP stage: its (hyser+otb) mean 1.58331 sits inside the order-4 temporal-only baseline
  `lms4s8/b256` = 1.5834 (`results/cycle_search.csv`) to ±0.006%.
- **Theory.** A *single* motor unit fires quasi-periodically, but a surface electrode integrates many
  **asynchronously firing** motor units. The superposition of ≥ ~5 independent renewal processes with
  ISI CV ≈ 0.1–0.3 converges to a near-Poisson point process, whose autocorrelation is flat away from
  lag 0 — so the *observed* residual has no usable ρ(T) peak even though every constituent train has
  one. Periodicity that exists per-source but not in the mixture is not codeable. What the 3/8 gate
  then crosses is mostly **estimator noise** over a 128-point window, and the fitted g fits that noise
  into the current block — hence the *negative* contribution precisely where the gate fires most
  (CapgMyo, 11.4% → −0.035%).
- **Implication:** this is P2 generalised — *deeper* prediction fits noise at short lags, *longer*
  prediction fits noise at long lags, and P5-ext showed context-splitting fits noise in the
  parameter. **Order-4 LMS + adaptive Rice is at the temporal residual's entropy floor; the temporal
  axis is a spent, proven-negative lever in all three of its branches.** All remaining reducible bits
  live *between* channels. Do not re-propose long-term/periodic temporal prediction, and note that the
  cost side is punishing too: the required residual ring buffer put `ltp` at cost 0.5288 (13.4× the
  best) and failed `neural_ok`.

---

## Open frontier (untried levers, ranked by expected payoff/cost)

_**2026-07-28 (cycle 13 RESULT — refreshed ranking; this list supersedes the one below).** Three
distinct candidates, all unanimous-PROMOTE-verified, **none promoted**, **two RETIRED**. What the
real data spent: **frontier #2 (temporal residual entropy) is now CLOSED NEGATIVE in its last
branch** — long-lag/periodic prediction contributes exactly 0 (`ltp`, P6), joining the
regime/context-switched branch the sibling PRs already retired; the temporal axis is finished.
**Global channel-topology search is closed negative** (`xtree`, P1c) — a lever nobody had ranked, now
settled. And one **genuinely new mechanism was discovered by ablation rather than proposed**:
`xstfir`'s win is not its delay FIR but its **opposite-side neighbour tap at lag 1**, unlocked by
**time-major coding order** (P1d) — the first mechanism to add gain on the *low*-ρ(0) array where P1
says spatial gain is scarce. The live frontier is now narrow and concrete._

1. **Bits-selected far-side second parent under a joint solve** (P1d + P1b + P1c — the fusion of all
   three proven parts). `xstfir` proved the far-side neighbour `x_q[n−1]` carries real, raster-unreachable
   MI (+1.56% OTB, +1.08% CapgMyo isolated) but wasted it by paying −2.86% for the useless parent-lag
   taps and by fixing the far-side channel geometrically. Knob: keep the **time-major** order, drop the
   parent lag taps entirely, and add **one** second parent chosen from {near-side lag-0 candidates,
   far-side lag-1 candidates} by the **`_bp_score` Rice-bits criterion** (P1c — select on bits, over few
   candidates, backward-adaptive → zero side-info), with both taps co-adapting in **one joint sign-sign
   LMS** (P1b — the marginal→multiple fix). Rationale: every measured term that was positive is kept,
   every measured term that was negative is deleted. This is the only combination whose *each component*
   has already been measured positive on real data. **Highest-payoff live lever.** Risk: P1b says
   selection and count are substitutes on high-ρ(0) arrays, so the win may again concentrate on CapgMyo —
   but CapgMyo is exactly where the current best is weakest.
2. **Scale/geometry-selected front-end dispatch** (salvage of P1-refinement + P1d). Three front-ends now
   each own a different array regime: global CAR wins tight arrays (OTB), best-partner wins large
   high-ρ(0) arrays (Hyser/CEMHSEY), the far-side lag-1 tap wins the low-ρ(0) array (CapgMyo). A
   per-recording **decoder-derivable** selector (measured global-vs-local coherence and neighbour ρ(0)
   from the first reconstructed block, zero side-info) that dispatches to the right front-end would take
   the **max** of the three instead of a compromise. Low mechanism risk (all three stages exist and are
   verified); payoff bounded by the per-set margins (+0.9% CapgMyo, +0.8% OTB) but it is the only lever
   that improves **every** set at once. PR #6's `acar_sel+bestpartner` is the 2-way special case and
   already succeeded — generalise it to 3-way including the P1d stage.
3. **Nothing on the temporal axis; nothing on the entropy axis; nothing in global topology.** P2, P5,
   P5-ext, P6 and P1c now close those four completely. Any future candidate must be a *spatial*
   mechanism that either (a) reaches a channel the causal raster cannot, or (b) changes the *selection
   criterion* to achieved bits — those are the only two things that have moved a real number since the
   promoted best.

_Superseded ranking (cycle 10/12 era), retained for history:_

_2026-07-28 (before cycle 13 survey) — **process note: two unmerged sibling PRs already spent every
item below.** `compression-cycle-2026-07-22` (PR #6) and `compression-cycle-2026-07-25` (PR #7) both
branched from the same `compression-wip` commit this branch also starts from, ran independently, and
between them already tried: frontier #1 (best-partner selection fused with joint2, tried TWICE —
PR #6 `xchan_jointbp2` +1.12% Hyser, PR #7 `xchan_joint2_bpa` +1.03% Hyser, both regress OTB/CEMHSEY,
confirms selection+count take the max not the sum); frontier #2 (temporal residual floor via
regime/context-switched predictors, tried TWICE, both RETIRED — PR #6 `xchan_bestpartner` regime bank,
PR #7 K=2 regime bank — conditioning the predictor coefficients fits noise once order-4 has whitened
the residual, same failure as the retired `xctx` moved upstream); frontier #3 (scale-selected cascade,
tried ONCE, PR #6 `acar_sel+bestpartner` — succeeded, Pareto-dominates the always-on cascade, pending
merge). PR #7 additionally retired a fourth mechanism not listed below: residual/innovation-domain
cross-channel subtract (`xres`) — worse-conditioned regressor than the raw-domain pair. **Cycle 13
must find genuinely new mechanisms beyond all four of these**, not variations on them. See `SURVEY.md`'s
2026-07-28 process note for full detail; these two PRs' file edits are not yet merged onto this branch._

_2026-07-19 (cycle 10): **all three of last cycle's ranked frontier levers were spent this cycle**, and
none produced a new best — the leaderboard best `LMS4+Rice+xchan_bestpartner` stands. #1 (two-stage
CAR→best-partner cascade, `acar+bestpartner`) helped **only on the tight OTB array** (+0.81%) and was
neutral/negative on the large arrays — the two MI slices are additive only where the global mode is a real
eigenvector (P1-refinement); non-dominated OTB corner, not a global best. #2/#3 the joint solve
(`joint2`) **works** — recovered the highest Hyser cross-channel gain of any codec (+12.26%) and fixed the
marginal-vs-multiple failure — but only **tied** the best across real data (parent selection vs count are
substitutes, P1b); kept as a cost-dominant zero-side-info entry. The port-caveat closure (`bpa`) **succeeded
as an embeddability lever** (held the offline ratio within ~0.4% at zero side-info, P4-refinement). **The
single-parent rank-1 subtract on an order-4 predictor remains the proven ratio ceiling; three distinct
attempts to exceed it (wider basis, more parents, cheaper estimation) all landed at ≤ the best.** The live
frontier is now the two *unspent combinations* of proven-working parts, plus a not-yet-touched axis._

1. **Best-of-N-partner *selection* fused with the joint 2-parent solve** (P1b + best-partner). The one lever
   this cycle did **not** try: jointly solve the best *pair* of causal neighbours (co-adaptive sign-LMS, as in
   `joint2`) instead of a *fixed* up+left pair, with the pair chosen per channel like best-partner. Rationale:
   joint2 won the large arrays (count) but lost the tight arrays because it used a *fixed* pair where selection
   matters; best-partner won the tight arrays (selection) but is single-parent. Fusing selection AND count is the
   only untried way to *stack* the two substitute degrees of freedom rather than trade them. **Highest-payoff
   live lever.** Risk: on tight arrays the second parent may still add little (joint2 lost OTB), and per-channel
   pair-selection adds cost/side-info — do it backward-adaptively (à la `bpa`) to stay zero-side-info. Measure
   whether selection+count clears the best on *both* array scales at once.
2. **Attack the residual entropy floor itself, not the decorrelator** (P2 + P5). Every spatial lever is now
   within ~1% of a shared ceiling — evidence the bottleneck has moved from *cross-channel decorrelation* to the
   *temporal residual's own entropy*. P5 closed the entropy back-end and Rice-parameter context as dead, but the
   residual's entropy is set upstream by the *predictor*. Untried: a non-linear or piecewise/context-switched
   temporal predictor (still order ≤4 per P2) that lowers the residual variance in the bursty high-activity
   segments where a single linear LMS under-fits. Rationale: the gains are saturating on the spatial axis, so the
   remaining bits live in the temporal residual, not between channels. Medium payoff, genuinely different axis.
3. **Two-stage cascade, but *scale-selected* not always-on** (P1-refinement, salvage of frontier #1). `acar+bestpartner`
   only helped on the tight 64-ch array. A cascade that *chooses* CAR-first vs best-partner-only per recording
   (from the array size / measured global-vs-local coherence, decoder-derivable) would keep the OTB win without the
   large-array loss. Low mechanism risk (both stages exist, verified), low-to-medium payoff (recovers a fraction of
   a percent on tight arrays only). De-prioritized below #1.

## Dead ends (retired — do NOT re-propose as new; a genuinely different variant must say why)

- **Fixed data-independent inter-channel transform** (`LMS+Rice+iklt`): fixed 45° integer-KLT,
  dominated — its isotropy/stationarity assumption is violated by real HD-sEMG (P3).
- **Data-*dependent* multi-tap inter-channel rotation** (`LMS+Rice+iklt_adaptive`, retired
  2026-07-14): backward-adaptive integer-KLT angle, dominated on ALL 4 real sets and *worse* than
  the fixed iklt (only +1.7–3.3% xchan gain, −0.5% on CapgMyo). The backward-estimated angle is
  stale/noisy and an energy-preserving rotation corrupts BOTH channels, so it is less robust than
  the rank-1 subtract (P3-refinement). **Multi-tap spatial transforms — fixed or adaptive — are a
  settled dead end here.**
- **Entropy back-end swap — tANS/rANS vs Rice** (`LMS+Rice+xchan_tans`, retired 2026-07-14):
  1.4–1.8% SMALLER than Rice at ~2× cost on all 4 real sets. Rice is already at the entropy floor
  for the near-geometric residual; the ANS table side-info is a net loss (P5). Do not re-propose
  any entropy-coder swap as a *ratio* play.
- **Per-block single-neighbour adaptive beta alone** (`LMS+Rice+xchan_adaptive`): dominated by the
  incumbent on real OTB (worse ratio AND higher cost); kept only for the zero-side-info port story.
- **Multi-parent summed rank-1 subtract** (`LMS+Rice+xchan_multiparent`, retired 2026-07-16): two
  causal parents (up + left), each its own backward-adaptive β, subtracts summed. Captured only ~half
  the single-parent gain and dominated on all 4 real sets. Summing *marginal* subtracts double-counts
  the correlated parents' shared mode → over-subtracts (P1-refinement, marginal-vs-multiple regression).
  Extending spatial support needs a **joint** solve, not a sum of rank-1 subtracts — and a joint solve
  is the already-dead multi-tap transform. Do not re-propose a summed multi-parent subtract.
- **Cross-channel context-adaptive Rice parameter** (`LMS+Rice+xctx`, retired 2026-07-16): conditioning
  the Rice k on a JPEG-LS-style cross-channel energy context (engine unchanged) came in 2.3–2.8% *below*
  a plain per-block adaptive k — below even plain LMS+Rice with no front-end — at ~2× cost. After LMS,
  H(e_c | neighbour energy) ≈ H(e_c) and context-splitting's model cost dominates (P5-extension). Do not
  re-propose *any* context-modeling of the Rice parameter as a ratio play, spatial or otherwise.
- **Global channel-topology search — Chow-Liu maximum-weight spanning forest** (`LMS4+Rice+xtree`,
  retired 2026-07-28): per-block max-weight directed spanning forest over ≤12 undirected neighbours
  (sign-sign |cross-correlation| weights on the previous reconstructed block) with a permuted
  topological coding order, zero side-info. Lost to the apples-to-apples control `bpa` (identical β,
  gate, predictor; only the topology criterion differs) by −0.23…−1.04% on **all 4** real sets at
  higher cost (0.0430 vs 0.0387), with 0.24–1.40 pp less isolated cross-channel gain — while the
  forest was demonstrably exercised (34.8–42.2% higher-index parents, 47.7–68.7% outside the causal
  4-neighbourhood). **Chow-Liu optimality is optimality for the *modelled* MI, not for the *achieved
  integer-coded* residual, and its weights must be estimated from 32 causal samples over ~6C
  candidates where max-selection is winner's-curse biased** (P1c). Do not re-propose MST /
  clustering / permuted-order channel topology as a ratio play.
- **Long-term (periodic / MUAP-firing-period) temporal prediction** (`LMS4+Rice+ltp`, retired
  2026-07-28): gated backward-adaptive tap at lag T ∈ [64,200] after the order-4 LMS, zero side-info.
  Gate-forced-off isolation on real data: the tap contributes **−0.012% / +0.003% / −0.035% /
  +0.022%** — zero, negative on two of four — *despite firing on 1.1–11.7% of block-channels*; the
  codec's whole edge over `LMS+Rice` is the order-8→4 change (P2). Dominated on all 4 by the
  leaderboard best at **13.4× the cost** (0.5288, `neural_ok` NO, 912 B/ch residual ring).
  **A surface electrode superposes many asynchronous motor units into a near-Poisson process whose
  autocorrelation is flat away from lag 0 — periodicity that exists per-source does not exist in the
  mixture** (P6). With P2 (short lags), P5/P5-ext (entropy engine and parameter context) and now P6
  (long lags), **the temporal axis is completely spent**.
- **Multi-lag prediction from the SAME spatial parent** ("fractional-delay" cross-channel FIR, the
  `x_p[n−1]`, `x_p[n−2]` taps inside `LMS4+Rice+xstfir`): isolated on real data at **−0.20% / −2.86% /
  −0.28% / −0.14%** — negative on all four sets. The parent's short-lag past is collinear with
  `x_p[n]` and conditionally uninformative once order-4 has whitened the coding channel, so the taps
  buy only sign-sign misadjustment (P1d). Do not re-propose delay-compensating cross-channel FIRs.
  (The *codec* is kept — see below — because a different tap inside it does work.)
- **[Kept, NOT retired — non-dominated corner, cycle 13 2026-07-28] Delay-compensated spatio-temporal
  FIR** (`LMS4+Rice+xstfir`, cost 0.0474, zero side-info): **the highest ratio of any codec or
  reference on CapgMyo (1.362200×, +0.87% over the leaderboard best)**, below the best on the other
  three real sets. Its value is *not* the hypothesis it was built for: ablation shows the whole
  positive term is the **opposite-side neighbour tap at lag 1**, legal only because the stage codes
  **time-major** (P1d), and it pays **inversely to zero-lag neighbour correlation** — i.e. it is the
  first mechanism that helps on the low-ρ(0) array where P1 says the pairwise lever is exhausted. Keep
  it as the CapgMyo corner and as the source of open-frontier #1.
- **[Kept, NOT retired — non-dominated corner] Global common-mode CAR** (`LMS+Rice+acar`): a
  low-cost Pareto point on tight arrays (OTB +14.4%, 2.089×/0.0559 — cheaper than the incumbent)
  but not on large arrays where redundancy is local (P1-refinement). Registered, not the best,
  not dominated — a candidate ingredient for the scale-matched two-stage front-end (frontier #3),
  not a dead end.
- **[Kept, NOT retired — non-dominated corners, cycle 10 2026-07-19]** Three candidates, all
  unanimous-PROMOTE-verified, none beat the leaderboard best on real data, none conclusively dominated:
  - **Two-stage CAR→best-partner cascade** (`LMS4+Rice+acar+bestpartner`): non-dominated *max-ratio corner
    on OTB* (2.1795×/0.043 — highest of any registered codec there); loses the primary Hyser (−0.23%) at
    higher cost. The two MI slices are additive only on tight arrays (P1-refinement). Not a dead end (OTB
    corner), not a global best.
  - **Joint 2-parent adaptive sign-LMS** (`LMS+Rice+xchan_joint2`): **cost-dominant** (0.0366, zero
    side-info) — dominates the best on Hyser+CapgMyo, cheaper corner on OTB/CEMHSEY, dead-tie 4-set mean.
    The joint solve is proven to *work* (P1b) but ties, not beats, single best-partner selection.
  - **Backward-adaptive best-partner re-selection** (`LMS4+Rice+xchan_bestpartner_adaptive`): the
    zero-side-info on-node realization of the promoted best (holds ratio within ~0.4%, dominates the best on
    CapgMyo). An embeddability win (P4-refinement), not a ratio play. **This is the codec to port if the
    promoted best's offline-selection port caveat matters.**

## Sanity anchors

- Real embeddable ratios on HD-sEMG live in **~1.3–2.2×**. Any lossless ratio **> ~6×** on
  realistic broadband ⇒ a leak or degenerate data — stop and report.
- **Success is not beating Shannon.** Independent per-channel noise caps lossless at ~3–3.5×.
  Success = beat per-channel FLAC / the embeddable references on real grids at a fraction of the
  compute, proven bit-exact — and, as a stretch, close the gap to offline lzma.
