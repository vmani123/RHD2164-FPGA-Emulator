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

### P2 — Temporal prediction saturates early; deeper prediction *hurts* on real data.
- **Evidence:** order-4 LMS beats order-8 on both Hyser and OTB (order 4→8 *costs* ratio in
  the ablation); the registry default was over-provisioned at order-8.
- **Theory:** after a low-order linear predictor the HD-sEMG residual is already close to
  white — the remaining structure the extra taps could exploit is mostly noise, so a longer
  adaptive filter fits noise (raising, not lowering, coded entropy) while its state and
  per-sample cost grow with order.
- **Implication:** keep the temporal predictor **small** (order ≤4). Spend complexity on the
  spatial front-end and the entropy back-end, not on deeper temporal prediction.

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

---

## Open frontier (untried levers, ranked by expected payoff/cost)

_Frontier #1 (entropy back-end) and #2 (data-dependent multi-tap transform) from the prior
cycle were both SPENT this cycle with **negative** real-data results — moved to Dead ends
(P5, P3). Every remaining live lever is a variation on the **adaptive rank-1 spatial subtract**,
which every cycle keeps confirming as the mechanism that works._

1. **Best-partner selection rebuilt on the order-4 predictor** (P2 + P4). Cycle 2's best-partner
   used order-8; on the search-proven cheaper order-4 (`lms4s7+x7/b512`, cycle_search.csv 2.321×
   / 0.027) it might dominate the incumbent on *both* axes instead of only extending the ratio
   ceiling at extra cost. **Now the highest-payoff live lever** — a cheaper, better predictor
   under the proven front-end, no new mechanism risk.
2. **Multi-parent backward-adaptive rank-1 subtract** (P1 + P3-refinement + P4). Extend the
   single grid-parent to a *small* set of causal neighbours (e.g. up + left), each with its own
   backward-adaptive integer beta, summed. Stays rank-per-parent (robust under estimation noise,
   unlike the rotation that corrupts both channels) and zero side-info. Targets the residual
   *local* spatial MI that one parent leaves — largest where neighbour |corr| is high
   (OTB/CEMHSEY/Hyser). Gate hard on cost (each parent adds state + ops).
3. **Scale-matched two-stage spatial front-end: global CAR *then* local pairwise** (P1-refinement).
   ACAR and the single-neighbour subtract capture *different* MI slices (global common-mode vs
   local pairwise); on tight arrays CAR wins, on large arrays pairwise wins. A backward-gated CAR
   lift followed by the adaptive neighbour subtract on the CAR residual could capture *both*
   slices where both exist. Risk: on large arrays CAR adds ~nothing (may not clear its gate) —
   measure whether the two slices are additive or already redundant with each other.

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
- **[Kept, NOT retired — non-dominated corner] Global common-mode CAR** (`LMS+Rice+acar`): a
  low-cost Pareto point on tight arrays (OTB +14.4%, 2.089×/0.0559 — cheaper than the incumbent)
  but not on large arrays where redundancy is local (P1-refinement). Registered, not the best,
  not dominated — a candidate ingredient for the scale-matched two-stage front-end (frontier #3),
  not a dead end.

## Sanity anchors

- Real embeddable ratios on HD-sEMG live in **~1.3–2.2×**. Any lossless ratio **> ~6×** on
  realistic broadband ⇒ a leak or degenerate data — stop and report.
- **Success is not beating Shannon.** Independent per-channel noise caps lossless at ~3–3.5×.
  Success = beat per-channel FLAC / the embeddable references on real grids at a fraction of the
  compute, proven bit-exact — and, as a stretch, close the gap to offline lzma.
