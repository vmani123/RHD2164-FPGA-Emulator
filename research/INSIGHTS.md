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
- **Refinement (2026-07-22, `LMS4+Rice+acar_sel+bestpartner` — SCALE-SELECTED cascade, KEPT; RETIRES the always-on version).**
  Since the two MI slices are additive only on tight arrays, a per-recording META-GATE on the array channel count `C`
  (`C≤64` → CAR→best-partner cascade, `C≥128` → best-partner only; `C` is header-read → decoder-derivable, zero circularity,
  zero side-info) captures the tight-array corner **without** the large-array loss: it reproduces the always-on cascade's OTB
  ratio **exactly (2.1795×, +0.81% over best-partner)** and equals plain best-partner **exactly on all 3 large arrays**,
  recovering the −0.34/−0.40 pp the always-on cascade *lost* on Hyser/CEMHSEY. It therefore **Pareto-dominates the always-on
  `LMS4+Rice+acar+bestpartner` at equal cost** (≥ ratio everywhere, > on 2 sets) → the always-on cascade is RETIRED (superseded).
  **Theory/implication:** when a lever's sign flips with a *decoder-observable* structural variable (here array size selecting
  whether the global mode is a real eigenvector), the right move is not to average the lever on/off but to GATE it on that
  variable — a zero-side-info meta-decision recovers the best of both regimes. This is the general pattern for every
  geometry-dependent lever here (and the concrete realization of the scale-matched front-end frontier). It is an engineering
  win (clean corner), not a new ratio ceiling: it ties the best on the primary Hyser at higher cost, so it is not a promotion.

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
- **Refinement (2026-07-22, `LMS4+Rice+xchan_jointbp2` — the selection⊕count STACK, KEPT non-dominated, NOT promoted).**
  Fusing the two substitute degrees of freedom — per-block backward SELECT the best *pair* of causal neighbours, then
  predict from it with ONE joint 2-tap co-adaptive sign-LMS — delivered the **highest cross-channel gain of ANY codec on
  the large Hyser array: +12.55%** (vs `joint2`'s fixed-pair +12.26% and best-partner's selected-single +11.31%), and the
  **highest embeddable Hyser ratio measured (1.4969×, +1.12% over the best)**. **So selection and count DO stack — on the
  diffuse-local large array.** But on the tight 64-ch OTB it captured only **+17.91%**, *still below* best-partner's single
  *selected* neighbour (+18.44%): where one diagonal neighbour carries the dominant local mode, a jointly-solved second
  parent adds less MI than the noise its extra adaptive tap injects, and selecting the *pair* does not rescue it. Net: wins
  only the primary Hyser, regresses OTB (−0.45%) and CEMHSEY (−0.17%) → does **not** robustly beat the best (same one-set-win
  disposition as `joint2`). **Theory:** on a tight array the local pairwise MI is essentially rank-1 (one dominant neighbour),
  so a second spatial degree of freedom — whichever way you spend it (count, or a second selected partner) — has almost no
  incremental MI to remove and only adds estimation variance; on a large array the local structure is genuinely rank≥2
  (diffuse across many similar neighbours), so the second jointly-solved parent finds real MI. **Frontier #1 is spent: the
  stack works exactly where array geometry gives the second spatial dimension real MI (large arrays) and is a net loss where
  it does not (tight arrays) — so it raises the large-array ratio ceiling but is not a single codec that beats the best on
  *both* scales at once.** The per-scale winner is now known: single selected partner on tight arrays, jointly-solved best
  pair on large arrays — which is exactly what a *scale-selected spatial front-end* (cf. P1-refinement `acar_sel`) would gate.

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
- **Refinement (2026-07-22, `LMS4rs+Rice+xchan_bestpartner` — regime-switched predictor BANK, RETIRED). Adding
  coefficient *sets* (not order) also fits noise — the temporal residual-entropy lever is spent NEGATIVE.** A bank
  of 3 order-4 sign-LMS predictors, one selected per sample by a backward activity regime (quiescent/normal/burst
  from recent-vs-long-term reconstructed |residual| energy, zero side-info), *lowered* the achieved cross-channel
  gain on **every** real set (OTB +16.49% vs +18.44%, Hyser +10.98% vs +11.31%, CapgMyo +0.59% vs +1.37%, CEMHSEY
  +12.98% vs +13.08%) and lost on all 4 at higher cost (0.0524 vs 0.0394) → conclusively dominated. **Theory, two
  compounding failures:** (1) after order-4 LMS the residual is already near-white, so the high-activity "burst"
  segments the bank meant to exploit are higher-*variance* NOISE, not distinct predictable linear dynamics — there
  is no regime-specific structure for a separate filter to fit, so `H(e|regime) ≈ H(e)`; (2) partitioning the
  samples across 3 banks starves each bank's adaptation to ~1/3 the data, so every bank's taps are estimated from
  fewer points and track *noisier* than the single shared filter — raising coded bits. **This is the predictor-side
  twin of the retired `xctx` failure (P5-extension):** conditioning either the predictor coefficients OR the Rice
  parameter on a backward regime/context buys nothing once the residual is white, and the context-split model cost
  is pure loss. **Do not re-propose activity-regime predictor banks (or any coefficient-set multiplication) as a
  ratio play.** To lower the temporal residual entropy the mechanism must change the predictor's *functional form*
  (genuinely non-linear on real signal structure), not multiply linear coefficient sets.

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

---

## Open frontier (untried levers, ranked by expected payoff/cost)

_2026-07-22 (cycle 12): **all three of last cycle's top frontier levers were spent this cycle**, none produced a
new best — the leaderboard best `LMS4+Rice+xchan_bestpartner` stands. #1 (selection⊕count STACK, `jointbp2`) **works
where geometry gives the second spatial dimension real MI**: highest cross-channel gain of any codec on Hyser
(+12.55%) and highest embeddable Hyser ratio (1.4969×), but still below single best-partner on tight OTB — a
max-Hyser corner, not a codec that beats the best on *both* scales (P1b-refinement). #2 (temporal residual entropy via
regime-switched predictor bank, `LMS4rs`) is spent **NEGATIVE** — coefficient-set multiplication fits noise once the
residual is white (P2-refinement), RETIRED. #3 (scale-selected cascade, `acar_sel`) is spent **POSITIVE as engineering**
— a zero-side-info channel-count gate recovers the OTB corner without the large-array loss and RETIRES the always-on
cascade, but ties the best on the primary (P1-refinement). **Every spatial lever now clusters within ~1% of a shared
ratio ceiling set by array geometry: single selected partner wins tight arrays, jointly-solved best-pair wins large
arrays. No single FIXED spatial front-end beats the best on both scales — but a SCALE-SELECTED one might, now that both
per-scale winners are known.** The live frontier:_

1. **Scale-select the spatial front-end between the two proven per-scale winners** (P1b-refinement ⊕ the `acar_sel`
   gating pattern, P1-refinement). This cycle nailed the per-scale optimum empirically: on tight arrays (`C<=64`) the
   single *selected* best-partner wins; on large arrays (`C>=128`) the jointly-solved best-*pair* (`jointbp2`) wins
   (+12.55% Hyser, the highest measured, +1.12% ratio over the best). Neither fixed front-end beats the best on both
   scales, but `acar_sel` proved a **zero-side-info, header-read channel-count gate** cleanly picks the right regime per
   recording. Gate best-partner (C<=64) vs jointbp2 (C>=128): the tight-array corner stays best-partner's, the
   large-array ratio inherits jointbp2's max-Hyser win — the first construction that could clear the best on the primary
   Hyser *and* hold the tight-array OTB corner. **Highest-payoff live lever, lowest mechanism risk** (both branches AND
   the gate were verified this cycle). Risk: the large-array Hyser win is only +1.12% and costlier (0.0468); measure the
   full 4-set profile of the gated codec directly against the best before claiming a promotion.
2. **Attack the residual entropy floor by changing the predictor's FUNCTIONAL FORM, not its coefficient count** (P2/P5,
   re-scoped after `LMS4rs`). Multiplying linear coefficient sets (regime bank) is now dead — it fits noise once the
   residual is white. The only remaining temporal lever is a genuinely *non-linear* predictor (still order <=4) whose
   residual is *not* a linear function of the history — e.g. a small sign-of-neighbour or gated-magnitude nonlinearity
   capturing higher-order structure a linear LMS provably cannot. Rationale: a linear LMS residual is white *to second
   order*; any remaining compressibility is higher-order. Medium payoff, genuinely different axis, higher mechanism risk.
   Pursue only if #1 does not clear the best.
3. **Backward-adaptive pair-selection guarantee for #1's large-array branch** (P4-refinement, folds into #1). `jointbp2`
   already selects the pair per block at zero side-info; if #1's gated codec is promoted, confirm the large-array branch's
   per-block pair re-selection holds the offline ratio (à la `bpa`) so the whole gated codec is streaming-legal. An
   embeddability guarantee, not a ratio play — low standalone payoff.

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
- **Regime-switched (activity-gated) temporal predictor BANK** (`LMS4rs+Rice+xchan_bestpartner`, retired
  2026-07-22): a 3-way bank of order-4 sign-LMS predictors selected per sample by a backward activity regime.
  *Lowered* achieved xchan gain on ALL 4 real sets (e.g. OTB +16.49% vs +18.44%) and dominated (worse ratio AND
  higher cost 0.0524 vs 0.0394). After order-4 LMS the residual is white, so "burst" segments are variance not
  structure (H(e|regime) ≈ H(e)), and 3 banks starve each other's adaptation → noisier taps (P2-refinement). The
  predictor-side twin of `xctx`. Do not re-propose activity-regime predictor banks or any coefficient-set
  multiplication as a ratio play.
- **Always-on two-stage CAR→best-partner cascade** (`LMS4+Rice+acar+bestpartner`, retired 2026-07-22 —
  SUPERSEDED): its OTB max-ratio corner (2.1795×) is reproduced *exactly* by its scale-selected version
  `LMS4+Rice+acar_sel+bestpartner` at equal cost 0.043, which additionally avoids the always-on cascade's
  −0.34/−0.40 pp large-array regression (Hyser/CEMHSEY) — Pareto-dominated at equal cost. The mechanism is not dead
  (the *scale-gated* form is kept, below); the always-on form is simply obsolete. Always GATE a geometry-dependent
  lever on a decoder-observable structural variable rather than run it unconditionally (P1-refinement).
- **[Kept, NOT retired — non-dominated corner] Global common-mode CAR** (`LMS+Rice+acar`): a
  low-cost Pareto point on tight arrays (OTB +14.4%, 2.089×/0.0559 — cheaper than the incumbent)
  but not on large arrays where redundancy is local (P1-refinement). Registered, not the best,
  not dominated — a candidate ingredient for the scale-matched two-stage front-end (frontier #3),
  not a dead end.
- **[Kept, NOT retired — non-dominated corners, cycle 10 2026-07-19]** Three candidates, all
  unanimous-PROMOTE-verified, none beat the leaderboard best on real data, none conclusively dominated:
  - **Two-stage CAR→best-partner cascade** (`LMS4+Rice+acar+bestpartner`): was the non-dominated OTB max-ratio
    corner (2.1795×/0.043) — **but RETIRED 2026-07-22, superseded by its scale-selected version `acar_sel`** (same
    OTB corner at equal cost, without the large-array regression). See Dead ends above.
  - **Joint 2-parent adaptive sign-LMS** (`LMS+Rice+xchan_joint2`): **cost-dominant** (0.0366, zero
    side-info) — dominates the best on Hyser+CapgMyo, cheaper corner on OTB/CEMHSEY, dead-tie 4-set mean.
    The joint solve is proven to *work* (P1b) but ties, not beats, single best-partner selection.
  - **Backward-adaptive best-partner re-selection** (`LMS4+Rice+xchan_bestpartner_adaptive`): the
    zero-side-info on-node realization of the promoted best (holds ratio within ~0.4%, dominates the best on
    CapgMyo). An embeddability win (P4-refinement), not a ratio play. **This is the codec to port if the
    promoted best's offline-selection port caveat matters.**
- **[Kept, NOT retired — non-dominated corners, cycle 12 2026-07-22]** Two candidates, unanimous-PROMOTE-verified,
  neither beats the leaderboard best on real data, neither conclusively dominated:
  - **Best-pair-selection ⊕ joint 2-tap sign-LMS** (`LMS4+Rice+xchan_jointbp2`, cost 0.0468): the **max-Hyser
    corner** — highest embeddable Hyser ratio (1.4969×, +1.12%) and highest cross-channel gain of any codec on Hyser
    (+12.55%). The selection⊕count stack works on large arrays but stays below single best-partner on tight OTB
    (P1b-refinement) → wins only the primary, regresses OTB/CEMHSEY, not a robust beat. Non-dominated (nothing beats
    its Hyser ratio) → kept. **The large-array branch of the frontier #1 scale-gated codec.**
  - **Scale-selected CAR cascade** (`LMS4+Rice+acar_sel+bestpartner`, cost 0.043): the non-dominated OTB max-ratio
    corner (2.1795×) with NO large-array regression (= best exactly on the 3 large arrays); ties the best on the
    primary at higher cost → not promoted. Proves the zero-side-info channel-count gate pattern (P1-refinement).
    Its existence RETIRED the always-on cascade.

## Sanity anchors

- Real embeddable ratios on HD-sEMG live in **~1.3–2.2×**. Any lossless ratio **> ~6×** on
  realistic broadband ⇒ a leak or degenerate data — stop and report.
- **Success is not beating Shannon.** Independent per-channel noise caps lossless at ~3–3.5×.
  Success = beat per-channel FLAC / the embeddable references on real grids at a fraction of the
  compute, proven bit-exact — and, as a stretch, close the gap to offline lzma.
