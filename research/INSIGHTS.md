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
- **Refinement (2026-07-25, `LMS4+Rice+xchan_joint2_bpa` — the fusion was BUILT and MEASURED; selection and count
  do NOT stack, they take the MAX). KEPT, non-dominated, not promoted.** Jointly solving the *per-block backward-selected
  best pair* (≤6 pairs of the causal 4-neighbourhood, scored by a closed-form 2×2 Cramer/SSE from one shared block Gram,
  zero side-info) delivered the **highest cross-channel gain ever measured on the primary Hyser array: +12.45%** (vs
  fixed-pair joint2's +12.26% and selected-single best-partner's +11.31%) and the **highest Hyser ratio of any registered
  codec, 1.4956× (+1.03% over the leaderboard best)** — yet it **lost tight OTB (2.1444×, −0.81%; +17.48% xchan gain vs
  best-partner's +18.44%)** and CEMHSEY (−0.21%), for a **−0.090% 4-set mean**. Isolating selection alone (vs the
  fixed-pair joint2 at the same order): **+0.176% hyser, +0.014% capgmyo, −0.245% otb, −0.144% cemhsey.**
- **Theory (why fusion yields the max, not the sum):** both levers approximate the *same* object — the conditional mean
  `E[x_c | causal neighbourhood]`. Once the joint 2-tap solve spans a 2-D subspace of that neighbourhood, a *selected*
  pair and a *fixed* pair usually span nearly the same subspace, so choosing the pair is a **second-order bias reduction**
  whose **estimation variance** is first-order: the argmin runs over 6 candidates scored from a single 256-sample Gram, and
  selection variance grows with candidate-set size while its bias payoff does not. On the large diffuse Hyser array count
  dominates and selection adds a residual +0.19 pp; on the tight OTB array selection would dominate, but the 6-candidate
  *pair* criterion over-fits the block and gives back more than it wins. **Corollary (bias–variance for backward selection):
  widen the candidate set only where the per-block statistic is well-estimated; the 4-candidate single-parent argmin is
  already at the useful limit on 256-sample blocks.**
- **Implication (frontier-closing):** four structurally different spatial mechanisms — selected single parent, fixed
  jointly-solved pair, selected jointly-solved pair, CAR→best-partner cascade — now agree within **±1%** on every real set
  (Hyser 1.48–1.50×, OTB 2.13–2.18×, CEMHSEY 1.951–1.956×, CapgMyo 1.342–1.353×). **The cross-channel front-end has hit a
  shared ceiling; adding spatial degrees of freedom is a spent lever.** Bits now live in the temporal residual's own
  entropy or nowhere reachable by these mechanisms.

### P1c — The cross-channel rank-1 gain must be estimated in the RAW domain, not the innovation (residual) domain: after a per-channel whitener the two domains nearly coincide, and the whitened regressor is strictly worse-conditioned.
- **Evidence (2026-07-25, `LMS4+Rice+xres` — RETIRED).** Moving the rank-1 subtract from *before* to *after* the
  per-channel order-4 LMS (`ê_c = e_c − (β·e_p >> 8)`, β sign-sign on the residual pair, partner re-selected per block by
  estimated Rice bits of `ê_c` — Choi's DF-on-residuals gate, zero side-info) captured a **live but strictly smaller**
  cross-channel gain on every real set: **+14.70% otb / +10.25% hyser / +1.27% capgmyo / +12.02% cemhsey**, against the
  raw-domain best-partner's **+18.44 / +11.31 / +1.37 / +13.08** (−1.1…−3.7 pp). Ratios **1.4663 hyser / 2.0936 otb /
  1.3492 capgmyo / 1.9371 cemhsey** — below the leaderboard best on all 4 (−0.95%, −3.16%, −0.09%, −0.94%) at higher cost
  (0.0412 > 0.0394) → Pareto-dominated, retired.
- **Theory (two compounding failures).** (1) **Domain collapse / commutation.** Every channel of an HD-sEMG array observes
  the same band-limited process, so the *independently* adapted per-channel order-4 taps converge to near-identical values,
  `w_c ≈ w_p`. Then the innovation is one *shared* LTI whitener applied to both rows, and a linear filter **commutes** with
  a rank-1 subtract: `e_c − β e_p ≈ (1 − W(z))(x_c − β x_p)`. The two domains are essentially the same subspace, so the
  premise that "the raw-power and innovation-power objectives genuinely differ" is **empirically false on this data** —
  there is no extra mutual information waiting in the innovation domain. (2) **Conditioning.** Given (1) there is no gain
  to win, but there is a real loss: `argmin_β Var(e_c − β e_p)` is estimated from the *whitened* pair, whose shared
  low-frequency power — precisely what made the raw regressor well-conditioned — has been stripped by the whitener. The
  estimator's variance rises, and β-estimation noise is injected straight into the coded innovation. The loss is largest
  on OTB (−3.7 pp), the tightest, most strongly common-mode array, i.e. the one with the most low-frequency shared power
  to lose. **General rule: estimate a decorrelation weight in the domain where the correlation you are exploiting has the
  most energy — before whitening, never after.** (Contrast: MPEG-4 ALS's residual-domain joint stereo pays off because
  its per-channel predictors are *high-order and genuinely different*; with matched low-order adaptive taps the mechanism
  has no room.)

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

### P4b — State-indexed *parameter banks* are a dead lever on this data, wherever the parameter lives: HD-sEMG segments are too short relative to an adaptive estimator's convergence time to amortize the split.
- **Evidence (2026-07-25, `LMS4x2+Rice+xchan_bestpartner` "swlms" — RETIRED).** Holding **K=2** order-4 sign-sign LMS
  tap-sets per channel and selecting the active set from a backward-derived activity state (fast vs slow leaky |e|
  integrator compared by bit-length, dead-band hysteresis, zero side-info), with the *front-end and entropy back-end
  byte-identical to the promoted best*, **lost on all 4 real sets**: 1.4763 hyser (−0.27%), 2.1328 otb (−1.35%), 1.3423
  capgmyo (−0.61%), 1.9543 cemhsey (−0.06%); 4-set mean −0.61%; higher cost (0.0549 > 0.0394) → dominated, retired. On the
  near-stationary synthetics it is a **dead tie** (+11 B on sc0.6, −24 B on sc0.9) — the detector works, there is simply
  nothing to detect. Diagnostic by exclusion: the *cross-channel* gain also **fell** (+11.00 / +16.85 / +0.75 / +13.01 vs
  the parent's +11.31 / +18.44 / +1.37 / +13.08) **even though the spatial code is identical** — proof the bank degraded
  the residual the front-end then codes.
- **Theory.** (1) **An adaptive filter is itself a context model**, so splitting it into K state-indexed sets is the same
  *model-cost-without-payoff* failure as `xctx`'s 12 Rice-k buckets (P5-extension), moved from the coder into the
  predictor. A sign-sign LMS has a fixed ±1 step, so its convergence time is measured in *consecutive* samples; a
  regime-switched filter never gets a long run — every switch resumes on a tap vector last updated a regime-dwell ago and
  now stale, and the re-convergence transient costs more than regime-matched taps save. (2) **The premise over-stated the
  regime contrast**: the sign-sign update is *amplitude-invariant* (it uses only signs), so a single filter already absorbs
  a pure amplitude modulation for free — the compromise bias the bank was built to remove was largely absent, while the
  fragmentation cost was fully present. (3) **The loss ordering confirms it**: damage is largest where the residual is
  closest to stationary (otb −1.35%, capgmyo −0.61%) and smallest on the most non-stationary set (cemhsey −0.06%).
- **Implication:** do not re-propose *any* state/context-indexed parameter bank on this data — predictor taps, Rice
  parameter, step size, or block statistics. If a mechanism needs regimes, it must either share statistics across regimes
  (shrinkage toward a pooled estimate) or be non-adaptive per regime (a closed-form estimator with no convergence time) —
  and it must justify why HD-sEMG dwell times exceed the estimator's convergence time, which K=2 with hysteresis did not.

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

---

## Open frontier (untried levers, ranked by expected payoff/cost)

_**2026-07-25 (cycle 13) — the two top-ranked frontier levers are now SPENT, one positive-but-not-decisive and one
negative; a third, brand-new axis was opened and closed the same cycle.** **#1 (selection fused with the joint 2-parent
solve, `xchan_joint2_bpa`) was built and measured: it set a record — highest cross-channel gain ever on the primary
Hyser array (+12.45%) and the highest Hyser ratio of any registered codec (1.4956×, +1.03% over the best) — but lost OTB
(−0.81%) and CEMHSEY (−0.21%) for a −0.090% 4-set mean. Selection and count take the MAX, not the sum (P1b refinement).
KEPT non-dominated, not promoted.** **#2 (attack the residual entropy in the predictor) was spent NEGATIVE via the
regime-switched tap-set bank `swlms`: −0.06…−1.35% on every real set, dominated, RETIRED — state-indexed parameter banks
fragment an adaptive estimator faster than they match its regimes (P4b).** **A new axis, the DOMAIN of the cross-channel
subtract (`xres`, raw vs innovation), was opened and closed negative: −0.95…−3.16%, dominated, RETIRED — after a
per-channel whitener the two domains nearly coincide and the whitened regressor is worse-conditioned (P1c).** Net: the
leaderboard best `LMS4+Rice+xchan_bestpartner` is **unchanged for the fourth consecutive cycle**, and the spatial
front-end is now demonstrably at a shared ceiling — six structurally different mechanisms within ±1% on every real set.
**The live frontier below is deliberately re-ranked away from both the spatial front-end (ceiling, P1b) and from
adaptive-parameter banks (P4b).** The honest reading is that ~1.48× on Hyser / ~2.16× on OTB is close to what this
class of codec can do, and the remaining levers are (a) fixing the ONE unexploited structural property of the array
(non-neighbour / long-range correlation), (b) buying cost rather than ratio, and (c) closing the gap to offline lzma,
which still beats every embeddable codec on hyser (1.67×) and cemhsey (2.06×) by exploiting something none of ours does._

1. **Long-range / clustered partner selection beyond the 4-neighbourhood** (P1 + P1b bias–variance corollary). Every
   spatial mechanism tried so far draws its parent(s) from the **causal 4-neighbourhood** — a hard-coded assumption that
   the array's mutual information is nearest-neighbour. But offline **lzma beats every embeddable codec on hyser
   (1.67× vs 1.48×) and cemhsey (2.06× vs 1.96×)** while being a *pure long-range string matcher with no spatial model
   at all* — strong evidence that real HD-sEMG arrays carry **non-local** redundancy (a motor unit's action potential
   appears on several non-adjacent electrodes along the muscle-fibre direction) that a 4-neighbourhood cannot see.
   Knob: keep the proven single-parent rank-1 backward-adaptive subtract (`bpa`), but let the per-block argmin range over
   a **larger, cheaply-pruned** candidate set (e.g. a per-recording top-M correlated-channel shortlist, M≈8, re-derived
   from the previous reconstructed block). **Highest live payoff** — it is the only untried lever aimed at MI the current
   codecs structurally cannot reach. Risk (named by P1b): selection variance grows with candidate-set size, so the
   shortlist must be *stabilized across blocks* (hysteresis / running score), not re-argmin'd from one 256-sample block.
2. **Buy cost, not ratio: port-grade minimization at the measured ceiling** (P2 + P4). Given the ratio ceiling, the
   remaining engineering value is a codec that holds ~the best ratio at materially lower cost. `search.py` this cycle
   converged to **`lms4s7+x6/b512` at mean 1.8204× / cost 0.0271 / 26 cyc/sample-ch** on the hyser+otb pair — already the
   Pareto front's top point, and the ablation says cross-channel is worth **+14.83%** while order 4→8 is worth only
   +0.79%, shift +0.16%, block +0.15%. Knob: re-run the search sweep *including* the `bpa` front-end and block 512/1024,
   and record the cheapest configuration within 0.5% of the best ratio as the port pick. Low mechanism risk, immediate
   port value, no new codec required.
3. **Understand *what lzma is finding* before proposing anything else spatial** (diagnostic, not a codec). lzma wins
   hyser by +13% and cemhsey by +5% over every embeddable codec while losing badly on otb (1.58× vs 2.18×) and capgmyo
   (1.17× vs 1.35×). That split is a *measurable fingerprint* of the redundancy class our codecs miss. Knob: an analysis
   pass (no registry change) measuring, on hyser/cemhsey, (a) the channel-pair correlation matrix beyond the
   4-neighbourhood, (b) the residual's repeat-match statistics at lzma's window scale, (c) whether the win is temporal
   long-range (quantization plateaus / DC steps) rather than spatial at all. Cheap, and it decides whether frontier #1 is
   aimed at a real structure or at a mirage. **Do this before spending another cycle on spatial mechanisms.**

_(Superseded ranking from 2026-07-19, kept for provenance:)_

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
- **Residual-domain (innovation) cross-channel subtract** (`LMS4+Rice+xres`, retired 2026-07-25): moving the rank-1
  subtract from before to after the per-channel order-4 LMS. Captured +10.3…+14.7% real cross-channel gain — live, but
  **1.1–3.7 pp below** the identical subtract in the raw domain — and lost on all 4 real sets at higher cost
  (0.0412 > 0.0394). With matched low-order per-channel predictors the whitener **commutes** with the rank-1 subtract, so
  the two domains are the same subspace (no new MI), while the whitened pair is a strictly worse-conditioned regressor for
  β (P1c). Do not re-propose residual-domain / ALS-style joint-stereo-on-residuals unless the per-channel predictors are
  first made genuinely heterogeneous — and P2 says they should not be.
- **State-indexed parameter banks — regime-switched predictor** (`LMS4x2+Rice+xchan_bestpartner` "swlms", retired
  2026-07-25): K=2 order-4 tap-sets per channel selected by a backward activity state, front-end and back-end identical to
  the promoted best. Lost on **all 4** real sets (−0.06…−1.35%) at higher cost (0.0549), and even the *spatial* gain fell
  though the spatial code was unchanged. An adaptive filter is already a context model; splitting it halves each set's
  adaptation runs and a sign-sign LMS re-converges after every switch, and the sign-only update already absorbs amplitude
  modulation for free (P4b). **Do not re-propose any state/context-indexed parameter bank — predictor taps, Rice k, step
  size, or block statistics.** This closes the "attack the residual entropy in the predictor" frontier lever negative.
- **[Kept, NOT retired — non-dominated corner, cycle 13 2026-07-25] Selected-pair joint 2-parent solve**
  (`LMS4+Rice+xchan_joint2_bpa`): the fusion of best-partner *selection* with the joint 2-parent *count*, zero side-info,
  look-ahead 0. Sets the record on the primary array — **highest cross-channel gain ever measured on Hyser (+12.45%)** and
  the **highest Hyser ratio of any registered codec (1.4956×, +1.03% over the leaderboard best)** — and also leads on
  CapgMyo, but loses OTB (−0.81%) and CEMHSEY (−0.21%) for a −0.090% 4-set mean, at the highest cost of the family
  (0.0500). Not a dead end (Hyser/CapgMyo corner), not a global best. Its lesson is the frontier-closing one: selection and
  count take the **max**, not the sum (P1b refinement), and the spatial front-end is at a shared ceiling.
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
