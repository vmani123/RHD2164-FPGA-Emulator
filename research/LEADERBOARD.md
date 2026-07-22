# LEADERBOARD — lossless on-node compression for 128-ch RHD2164 / HD-EMG

Best-per-category, the ratio-vs-cost Pareto front, per-dataset ratios, isolated
cross-channel gain, and **the one codec to port next**. Every number here is
produced by the harness (`research/bench.py` + `research/search.py`), asserted
bit-exact, and gated by `embedded_ok` — never by reasoning (non-negotiables #1,
#2, #4). Maintained per Stage 6 of `../COMPRESSION_RESEARCH_AGENT_PROMPT.md`.

_Updated: 2026-07-22 · branch `compression-cycle-2026-07-22`. **Cycle 12 (2026-07-22) promoted
NOTHING and retired TWO** — three unanimous-PROMOTE-verified candidates (best-pair-selection ⊕ joint
2-tap LMS, regime-switched predictor bank, scale-selected CAR cascade) all landed at ≤ the current best
on real data; the leaderboard best `LMS4+Rice+xchan_bestpartner` is unchanged (see the 2026-07-22 cycle
section below). `LMS4+Rice+xchan_jointbp2` won the primary Hyser (+1.12%, highest embeddable Hyser ratio)
but regressed OTB/CEMHSEY → kept as non-dominated max-Hyser corner, not promoted. `LMS4rs+Rice+xchan_bestpartner`
(regime-switched temporal bank) lost on all 4 real sets at higher cost → **RETIRED**. `LMS4+Rice+acar_sel+bestpartner`
(scale gate) kept the OTB corner without the large-array loss → conclusively supersedes the always-on
`LMS4+Rice+acar+bestpartner` at equal cost → the always-on cascade is **RETIRED**. Prior line — cycle 10
(2026-07-19) promoted/retired nothing; cycle 7 PROMOTED the current best `LMS4+Rice+xchan_bestpartner`
(order-4 predictor under the best-partner front-end). **Nine codecs are now `retired=True` in
`research/registry.py`** (conclusively Pareto-dominated — kept, bit-exact, excluded from the default
`bench.py` sweep and this table's headline rows; `--include-retired` re-checks on demand):
`xchan_adaptive`, `xchan_bestpartner` (order-8), `iklt`, `iklt_adaptive`, `xchan_tans`, `xchan_multiparent`,
`xctx`, `LMS4rs+Rice+xchan_bestpartner` (this cycle), `LMS4+Rice+acar+bestpartner` (this cycle, superseded by
its scale-selected version). The loop targets 2-3 genuinely distinct candidates per cycle; retirement is how
dominated ones stop being re-benchmarked every time without deleting them._

## Headline (REAL data decides — #3)

The physionet/zenodo/figshare hosts are now **reachable in-session**, so the
headline is carried by the **primary real set, Hyser** (PhysioNet HD-sEMG),
alongside the OTB sample and two newly-added real sets (CEMHSEY 320-ch, CapgMyo).
Every set benched at **15 000 samples**; tables `results/06_real_bench.csv`
(Hyser + OTB) and `results/06_real_bench_extra.csv` (CEMHSEY + CapgMyo).

### Primary — real 128-ch Hyser (`hyser_1dof_f1_s1`, PhysioNet, 8×16 @ 2048 Hz)

| codec | ratio | %-of-FLAC | embedded_ok | note |
|---|---:|---:|:--:|---|
| lzma | 1.67× | 171% | ref | **offline**, not embeddable |
| **LMS4+Rice+xchan_bestpartner** | **1.480×** | **151%** | ✅ | **best embeddable — PROMOTED 2026-07-16** (order-4 + best-partner, cost 0.039) |
| LMS+Rice+xchan | 1.474× | 151% | ✅ | prior best (order-8 single-parent, cost 0.057) |
| delta+Rice+xchan | 1.45× | 149% | ✅ | cheapest xchan |
| zstd-19 | 1.44× | 147% | ref | offline |
| mtscomp | 1.41× | 145% | ref | neuro per-channel reference |
| gzip-9 | 1.38× | 141% | ref | offline |
| wavpack | 1.34× | 137% | ref | best embeddable-class per-channel ref |
| LMS+Rice | 1.33× | 136% | ✅ | temporal only |
| flac | 0.98× | 100% | ref | the target to beat (expands here) |

**Embedded `LMS4+Rice+xchan_bestpartner` (1.480×) beats every embeddable-feasible
reference — WavPack 1.34×, mtscomp 1.41×, and even offline zstd-19 (1.44×) — at a
fraction of the compute (cost 0.039), proven bit-exact. Only offline LZMA (1.67×)
is ahead, and it is not portable to the node.** FLAC actually *expands* Hyser
(0.98×), so %-of-FLAC runs high; the honest bar on Hyser is the neuro/audio
references (WavPack, mtscomp), all of which the embedded codec beats.

- **Achieved cross-channel gain: +11.3%** (LMS 1.330× → 1.480×) — the dominant lever.
- Max embeddable ratio 1.480× ≪ the 6× sanity ceiling → honest broadband EMG.

### Real per-dataset summary (best embeddable = `LMS4+Rice+xchan_bestpartner`, cost 0.039)

| dataset | ch | best-emb ratio | %-of-FLAC | vs prior best (LMS+Rice+xchan) | FLAC | best offline ref |
|---|--:|--:|--:|--:|--:|---|
| **hyser_1dof_f1_s1** (primary) | 128 | **1.480×** | 151% | +0.45% (1.474×) | 0.98× | lzma 1.67× |
| otb_hdsemg_vl | 64 | **2.162×** | 176% | +0.90% (2.143×) | 1.23× | wavpack 1.85× (emb-class) |
| cemhsey_s1_d1t1 | 320 | **1.956×** | 167% | +0.02% (1.955×) | 1.17× | lzma 2.06× |
| capgmyo_dba_s1 | 128 | **1.350×** | 137% | +0.09% (1.349×) | 0.98× | wavpack 1.35× |

- Cross-channel gain **tracks real spatial redundancy**, exactly as the mechanism
  predicts: strong on OTB/CEMHSEY/Hyser (neighbour |corr| 0.73–0.79), near-zero on
  CapgMyo (|corr| 0.29 — its heavily band-pass-filtered differential 8×16 array
  carries little inter-electrode redundancy). CapgMyo is the honest **negative
  control**: the harness does *not* manufacture a win where the physics isn't there.
- Every real ratio (best 2.14×) is ≪ the 6× leak ceiling → no degenerate data.
- **Cycle 1 (2026-07-08) — new candidate `LMS+Rice+xchan_adaptive`, NOT promoted as a
  new best (Pareto-dominated).** On real `otb_hdsemg_vl` it measures **2.13×** at cost
  0.065, against the incumbent `LMS+Rice+xchan`'s 2.14× at cost 0.057 — a −0.48% ratio
  give-up **and** higher cost, so per non-negotiable #2 (rank on the Pareto front) it
  does not join the front and `LMS+Rice+xchan` remains the recorded best-ratio
  embeddable codec on every real set, unchanged. It is kept, registered (bit-exact,
  double-verified `embedded_ok`), because it closes a real gap: the incumbent's 2.14×
  depends on a float whole-signal beta computed offline over the whole recording and
  shipped as header side-info — not actually producible on-node — whereas the new
  codec's beta is genuinely causal (look-ahead = 0) and needs **zero side-info** (the
  decoder recomputes it from the previous already-reconstructed block). See
  `experiments/001_lms_rice_xchan_adaptive.md` for the full record, including the
  ranked next hypotheses aimed at recovering the give-up.

## Cycle 2026-07-10 — best-partner cross-channel selection (SURVEY rec #2)

Hyser/CEMHSEY/CapgMyo were network-unreachable this session; `otb_hdsemg_vl` was
the only real dataset reachable, so this cycle's results are OTB-only (Hyser
numbers above are unchanged from the last session that could reach it).

New codec `LMS+Rice+xchan_bestpartner`: best-of-4 causal-neighbour selection
per channel (parent + integer beta sent as side-info) in place of the single
fixed grid-parent. Double-verified (two independent adversarial verifiers,
each re-running the round-trip and re-measuring from scratch): **PROMOTE**
(no split — both agree).

Real `otb_hdsemg_vl` (64ch, 5×13, 15 000 samp — bit-exact, `embedded_ok`):

| codec | ratio | cost | xchan gain (isolated) | note |
|---|---:|---:|---:|---|
| **LMS+Rice+xchan_bestpartner** | **2.15×** | 0.063 | **+18.5%** | non-dominated max-ratio corner; +0.40% over incumbent |
| LMS+Rice+xchan (incumbent) | 2.14× | 0.057 | +18.0% | prior best embeddable |
| delta+Rice+xchan | 2.04× | 0.013 | +16.2% | cheapest xchan |

- **Best-partner adds +0.55 pp of achieved cross-channel gain** (18.0% → 18.5%,
  isolated on real data) at **+10% cost** (0.057 → 0.063). It sits on the
  ratio-vs-cost Pareto front (higher ratio AND higher cost — neither it nor the
  incumbent dominates) but the +0.40% real ratio lift does **not** displace the
  port recommendation below.
- Bit-exact round-trip verified independently (2×, by both verifiers) on the
  real array; ratio 2.15× ≪ 6× ceiling; no regression vs incumbent; only
  `SURVEY.md`/`research/registry.py` touched this cycle (`sim/run_sim.sh` still
  green, 153 transfers / 0 errors).
- **Search refinement:** `research/search.py` on OTB (15 000 samp) reconfirms
  order-4 dominates order-8 and refines the single-parent xchan port pick to
  **`lms4s7+x6/b512` = 2.163×, cost 0.027** (dominates the order-8 start on both
  axes). This remains the **value/port recommendation**; bestpartner is the
  "max ratio if you'll pay the cost" corner, built on order-8 and not yet
  retuned to order-4.
- **Port caveat unchanged in kind:** partner+beta derived offline over the
  whole signal; the embeddable realization is per-block (lookahead = 256) —
  same caveat class as the incumbent's offline whole-signal beta.
- **Not promoted as the new headline/port pick** — the real-data win is
  marginal (+0.40% ratio for +10% cost); it is registered as a Pareto-front
  entry, not a replacement for the port recommendation.
- **Next hypothesis (highest ranked payoff):** rebuild best-partner on the
  order-4 predictor (`lms4s7...`, search-proven cheaper AND higher ratio) —
  expected to dominate the incumbent on both axes instead of only extending
  the ratio ceiling at extra cost.

OTB Pareto corners (real, 15 000 samp, embedded_ok only):

| config | ratio | cost | neural_ok | character |
|---|---:|---:|:--:|---|
| delta+x6/b512 | 2.04× | 0.016 | ✅ | cheapest embeddable with xchan |
| fixed+x6/b512 | 2.13× | 0.025 | ✅ | fixed-predictor xchan |
| lms4s7+x6/b512 | 2.163× | 0.027 | ✅ | **best value / port pick** |
| LMS+Rice+xchan_bestpartner | 2.15× | 0.063 | ✅ | max-ratio corner (best-of-4 neighbour) |

Cycle 1 (`compression-cycle-2026-07-08`, unmerged) previously tried a
backward-adaptive per-block beta (`LMS+Rice+xchan_adaptive`, 2.13×/cost 0.065
on OTB) — dominated by the incumbent, not promoted. This cycle's best-partner
selection is a different mechanism (which neighbour, not how the beta adapts)
and is the first candidate this research loop has produced that is genuinely
non-dominated vs. the incumbent on real data.

## Cycle 2026-07-13 — fixed integer-KLT multi-tap front-end (SURVEY rec #3) — RETIRED

Hyser/CEMHSEY/CapgMyo were network-unreachable this session; `otb_hdsemg_vl` was
the only real dataset reachable, so this cycle's results are OTB-only (Hyser
headline numbers above are unchanged from the last session that could reach it).
This cycle also uses an **8 000-sample** OTB window (`results/cycle_bench.csv`),
so its incumbent measures 2.24× here vs 2.14× at 15 000 samp above — compare
candidates only *within* the same run.

New codec `LMS+Rice+iklt`: a fixed, data-independent, multiplierless reversible
integer inter-channel transform (integer-KLT via 3-step lifting/Givens rotations
at θ=45°) as a **multi-tap** spatial front-end ahead of the unchanged per-channel
LMS+Rice. **Verifier split — A REJECT, B PROMOTE → held for human review, NOT
promoted.** It is also **conclusively Pareto-dominated on real data and RETIRED**
(`retired=True` in `research/registry.py`; kept bit-exact, excluded from the
default sweep, `--include-retired` re-checks it).

Real `otb_hdsemg_vl` (64 ch, 5×13, 8 000 samp — bit-exact, `embedded_ok`):

| codec | ratio | cost | note |
|---|---:|---:|---|
| LMS+Rice+xchan_bestpartner | 2.25× | 0.063 | max-ratio corner (dominates iklt) |
| **LMS+Rice+xchan** (incumbent) | **2.24×** | **0.057** | best embeddable — **dominates iklt** |
| delta+Rice+xchan | 2.19× | 0.013 | cheapest xchan (dominates iklt) |
| LMS+Rice+iklt | **2.07×** | **0.068** | **candidate — highest cost, 4th ratio** |
| LMS+Rice (xchan-off baseline) | 1.90× | 0.052 | temporal only |

- **Attribution:** temporal predictor and Rice back-end are byte-identical to the
  incumbent, so the only lever changed is the cross-channel front-end. The fixed
  integer-KLT captures only **+8.8%** achieved cross-channel gain on real data
  (iklt 2.07× / LMS+Rice 1.90×) — **roughly half** the single-neighbour subtract's
  **+18.0%**. It lands −7.78% below the incumbent (2.07× vs 2.24×).
- **Mechanism:** a fixed 45° butterfly is the exact KLT only under the equal-variance
  *isotropic* model. Real HD-sEMG inter-electrode covariance is anisotropic and
  non-stationary, so the fixed rotation is mismatched while the incumbent's
  data-dependent single-neighbour beta tracks the actual pairwise correlation. The
  synthetic sweep confirms the pattern at every correlation (iklt +5.9%/+10.4% vs
  xchan +9.4%/+19.1% at sc0.6/sc0.9).
- **Pareto:** dominated on real data by three registered codecs (LMS+Rice+xchan
  2.24×/0.057, bestpartner 2.25×/0.063, delta+Rice+xchan 2.19×/0.013 — each higher
  ratio AND lower cost). Not on the front → RETIRED.
- **Sanity:** max real ratio 2.25× ≪ 6× ceiling (no leak); no FAIL bit-exact rows
  (all `ok=True`, iklt round-trip OK, `neural_ok`); incumbent unchanged (no
  regression to the recorded best).
- **Negative result, kept honest:** a fixed *data-independent* multi-tap transform
  does not beat *adaptive* single-neighbour subtraction on real anisotropic HD-sEMG.
  The spatial gain lives in the data-dependent pairwise weight, not in a wider fixed
  basis. Port recommendation below is **unchanged**. Full record:
  `experiments/002_lms_rice_iklt.md`.

## Cycle 2026-07-14 — three distinct front-ends: adaptive rotation, tANS back-end, global CAR

All four real sets reachable and benched at 15 000 samples (`results/cycle_bench.csv`);
incumbent numbers reproduce the headline exactly (Hyser `LMS+Rice+xchan` 1.474×, OTB 2.143×).
Three genuinely distinct candidates, **all double-verified PROMOTE (no splits)**, **none
promoted** (none beats the current best on real data), **two retired**:

| candidate | mechanism | real result (Hyser / OTB / CEMHSEY / CapgMyo) | cost | verdict |
|---|---|---|---:|---|
| `LMS+Rice+iklt_adaptive` | data-dependent adaptive integer-KLT rotation (multi-tap) | 1.352× / 1.885× / 1.761× / 1.326× | 0.083 | **RETIRED** — dominated on all 4 |
| `LMS+Rice+xchan_tans` | tANS entropy back-end vs Rice (same front-end) | 1.451× / 2.103× / 1.922× / 1.330× | 0.109 | **RETIRED** — dominated on all 4 |
| `LMS+Rice+acar` | global common-average-reference lift (rank-1 common-mode) | 1.363× / 2.089× / 1.743× / 1.332× | 0.056 | **kept** — non-dominated corner on OTB/CapgMyo |

- **`iklt_adaptive` (open-frontier #2, data-dependent multi-tap).** Isolated cross-channel gain
  only **+1.7–3.3%** on real data (−0.5% CapgMyo) vs the single-neighbour subtract's +10.8–17.4%
  — *worse* than the retired fixed iklt. A backward-estimated rotation angle is stale/noisy on
  non-stationary HD-sEMG and, being energy-preserving, corrupts *both* channels; the rank-1
  subtract is the more robust decorrelator. Conclusively Pareto-dominated by `LMS+Rice+xchan`
  (higher ratio AND lower cost) on all 4 real sets → RETIRED.
- **`xchan_tans` (open-frontier #1, entropy back-end — the axis never tested in 4 cycles).**
  With the front-end held identical, tANS is **1.4–1.8% SMALLER than Rice at ~2× cost** on every
  real set. Real HD-sEMG residuals are near-geometric, so Rice is already at the entropy floor and
  the per-block ANS table is a net loss (confirms P5). Conclusively dominated → RETIRED. **The
  entropy back-end is now a proven-negative, spent lever.**
- **`acar` (global common-mode, a new MI slice).** Captures **+14.4%** on the small tightly-coupled
  64-ch OTB array (near the +17.4% single-neighbour gain) but collapses to +0.8–2.5% on the larger
  128-/320-ch arrays where redundancy is *local*. Genuinely **non-dominated on OTB** (2.089×/0.0559,
  *cheaper* than the incumbent) and CapgMyo → kept registered as a low-cost Pareto corner; dominated
  by `delta+Rice+xchan` on Hyser/CEMHSEY. Not best → not promoted, not retired.
- **Sanity:** max real ratio in the run 2.151× ≪ 6× ceiling; every row bit-exact (`ok=True`), all
  `embedded_ok`/`neural_ok`; incumbent unchanged (no regression). Only `research/registry.py`
  (retire flags), report files, and `SURVEY.md` touched; `rtl/`/`sim/` untouched.
- **Port recommendation unchanged.** Two open-frontier levers are now spent; the live frontier
  collapses onto variations of the adaptive rank-1 subtract (best-partner on order-4 first).

## Cycle 2026-07-16 — order-4 best-partner (PROMOTED), multi-parent, cross-channel context Rice

All four real sets reachable, benched at 15 000 samples (`results/cycle_bench.csv`); incumbent
reproduces the headline (`LMS+Rice+xchan` Hyser 1.474×, OTB 2.143×). Three distinct candidates, **all
double-verified PROMOTE (no splits)**; **one promoted (new best), two retired**:

| candidate | mechanism / axis | real (Hyser / OTB / CEMHSEY / CapgMyo) | cost | verdict |
|---|---|---|---:|---|
| `LMS4+Rice+xchan_bestpartner` | order-4 predictor under best-partner front-end (temporal) | **1.480× / 2.162× / 1.956× / 1.350×** | **0.039** | **PROMOTED — new best on all 4** |
| `LMS+Rice+xchan_multiparent` | two-parent (up+left) summed rank-1 subtract (spatial topology) | 1.398× / 1.971× / 1.872× / 1.347× | 0.078 | **RETIRED** — dominated on all 4 |
| `LMS+Rice+xctx` | cross-channel context-adaptive Rice k (conditional entropy) | 1.293× / 1.783× / 1.682× / 1.297× | 0.095 | **RETIRED** — below even plain LMS+Rice on all 4 |

- **`LMS4+Rice+xchan_bestpartner` (PROMOTED).** Same best-partner spatial front-end as cycle-2's
  codec, predictor order 8→4 (P2). **Dominates on BOTH axes** the prior incumbent `LMS+Rice+xchan`
  (higher ratio, cost 0.039 < 0.057) *and* the order-8 `LMS+Rice+xchan_bestpartner` (higher ratio,
  0.039 < 0.063 → order-8 bestpartner now RETIRED) on all 4 real sets. Beats the current best on real
  data AND unanimous PROMOTE → promotion rule satisfied. Achieved cross-channel gain vs temporal-only:
  +18.4% OTB, +13.1% CEMHSEY, +11.3% Hyser, +1.37% CapgMyo (search-isolated cross on→off +14.83%).
- **`LMS+Rice+xchan_multiparent` (RETIRED).** A second summed parent captured only **~half** the
  single-parent gain (OTB +8.0% vs +17.4%) — the up/left parents are mutually correlated, so summing
  two *marginal* β subtracts over-subtracts their shared mode (marginal-vs-multiple regression). Needs
  a joint 2×2 solve — the already-dead multi-tap transform. Dominated on all 4 (worse ratio AND higher
  cost 0.078 > 0.057). See `experiments/007_lms_rice_xchan_multiparent.md`.
- **`LMS+Rice+xctx` (RETIRED).** Conditioning the Rice k on a cross-channel energy context lost
  **2.3–2.8% vs a plain per-block adaptive k** on every real set — below even plain `LMS+Rice`. After
  LMS whitening the residual is not cross-channel heteroscedastic (H(e_c|neighbour energy) ≈ H(e_c)),
  and the 12-bucket context split's model cost dominates — extends P5. See
  `experiments/008_lms_rice_xctx.md`.
- **Sanity:** max real ratio 2.162× (OTB) ≪ 6× ceiling; every row bit-exact (`ok=True`), all
  `embedded_ok`/`neural_ok`; incumbent unchanged (no regression). Only `research/registry.py` (retire
  flags + the promoted registration was added its cycle), report files, and `SURVEY.md` touched;
  `rtl/`/`sim/` untouched.

## Cycle 2026-07-19 — two-stage cascade, joint 2-parent, best-partner re-selection — NONE promoted, NONE retired

All four real sets reachable, benched at 15 000 samples (`results/cycle_bench.csv`); the current best
`LMS4+Rice+xchan_bestpartner` reproduces its headline exactly (Hyser 1.4804×, OTB 2.1619×). Three distinct
candidates, **all double-verified PROMOTE (no splits)**; **none beats the current best on real data → none
promoted; all three non-dominated → none retired.** The leaderboard best is unchanged.

| candidate | mechanism / axis | real (Hyser / OTB / CapgMyo / CEMHSEY) | cost | side-info | verdict |
|---|---|---|---:|---|---|
| `LMS4+Rice+acar+bestpartner` | two-stage cascade: global CAR → local best-partner (spatial, scales) | 1.4770× / **2.1795×** / 1.3505× / 1.9515× | 0.043 | 2×int16/ch | **kept** — non-dominated OTB max-ratio corner |
| `LMS+Rice+xchan_joint2` | joint asymmetric 2-parent adaptive sign-LMS (spatial, joint count) | **1.4930×** / 2.1497× / 1.3504× / 1.9543× | **0.0366** | **zero** | **kept** — cost-dominant, dead-tie 4-set mean |
| `LMS4+Rice+xchan_bestpartner_adaptive` | backward-adaptive best-partner re-selection (embeddability) | 1.4770× / 2.1531× / **1.3529×** / 1.9539× | 0.0387 | **zero** | **kept** — port-caveat closure, holds ratio ±0.4% |

- **`acar+bestpartner` (frontier #1, spent — partial).** Isolated CAR-stage marginal gain over best-partner:
  **+0.96 pp on the tight 64-ch OTB array (+0.81% ratio)**, **0 on CapgMyo (gate never fired, bit-identical)**,
  **−0.26/−0.23 pp on the large 128-/320-ch Hyser/CEMHSEY arrays**. The two MI slices (global common-mode +
  local pairwise) are additive **only where the global mode is a real eigenvector** (tight arrays); on large
  arrays the fired CAR lift over-subtracts a mismatched global basis after best-partner took the local slice.
  Non-dominated OTB max-ratio corner (2.1795×) → kept; loses the primary Hyser (−0.23%) at higher cost → not
  promoted. **Frontier #1's "capture both slices everywhere" hope is disproven.**
- **`joint2` (frontier #2/#3, spent positive — but ties).** One joint backward-adaptive sign-sign LMS on both
  parents (up+left), taps co-adapting against the shared residual — the multiplierless 2×2 multiple-regression
  solve. It **recovers the highest cross-channel gain of any codec on Hyser (+12.26%** vs best-partner +11.31%,
  single-fixed +10.8%): the joint solve genuinely adds a second parent's MI where the retired *summed* multiparent
  over-subtracted (marginal→multiple fix), asymmetric injection keeps parents clean (avoids the iklt_adaptive
  both-channel corruption). But on the tight OTB array the *fixed* up+left pair (+17.77%) is below best-partner's
  *selected* neighbour (+18.44%). Net: wins Hyser (+0.85%), loses OTB (−0.57%), **dead-tie on the 4-set mean
  (−0.014%)** at lower cost (0.0366) and zero side-info. **Parent selection and parent count are substitutes,
  not complements** — the joint solve works but only ties. Cost-dominant non-dominated entry → kept; not a robust
  real-data beat → not promoted.
- **`bpa` (frontier #3, spent positive — embeddability).** Per-block backward re-selection of (partner, β) from
  the previous reconstructed block **holds the offline best-partner ratio within −0.08%…−0.41%** on the high-corr
  sets and **beats it +0.18% on CapgMyo**, at **zero side-info + look-ahead 0** (drops the 2×int16/ch header and
  the whole-signal look-ahead), cost 0.0387. The best-partner identity is stable within a recording, so per-block
  re-derivation lands on the offline optimum minus a tiny burst-boundary transient. **The promoted best's last
  port caveat is closed at ~zero ratio cost.** Dominates the best on CapgMyo → kept; loses the primary → not
  promoted (never a ratio play).
- **Sanity:** max real ratio 2.1795× (acar+bestpartner, OTB) ≪ 6× ceiling; every row bit-exact (`ok=True`), all
  `embedded_ok`/`neural_ok`; no incumbent/best regression. Only report files + `SURVEY.md` touched this cycle —
  **no retire flags set** (all three non-dominated), `research/registry.py` codec logic untouched; `rtl/`/`sim/`
  untouched.
- **Net:** the single-parent rank-1 subtract on order-4 (`LMS4+Rice+xchan_bestpartner`) remains the proven ratio
  ceiling; three distinct attempts to exceed it (wider basis, more parents, cheaper estimation) all landed at ≤ the
  best. **Port takeaway:** if the promoted best's offline-selection port caveat matters, port
  `LMS4+Rice+xchan_bestpartner_adaptive` (same ratio, zero side-info, fully on-node).

## Cycle 2026-07-22 — best-pair⊕joint, regime-switched bank, scale-selected cascade — NONE promoted, TWO retired

All four real sets reachable, benched at 15 000 samples (`results/cycle_bench.csv`); the current best
`LMS4+Rice+xchan_bestpartner` reproduces its headline exactly (Hyser 1.4804×, OTB 2.1619×). Three distinct
candidates, **all double-verified PROMOTE (no splits)**; **none beats the current best on real data → none
promoted; two conclusively Pareto-dominated → two retired.** The leaderboard best is unchanged.

| candidate | mechanism / axis | real (Hyser / OTB / CapgMyo / CEMHSEY) | cost | verdict |
|---|---|---|---:|---|
| `LMS4+Rice+xchan_jointbp2` | best-PAIR selection ⊕ joint 2-tap adaptive sign-LMS (spatial: selection+count STACK) | **1.4969×** / 2.1522× / 1.3503× / 1.9523× | 0.0468 | **kept** — non-dominated max-Hyser corner |
| `LMS4rs+Rice+xchan_bestpartner` | regime-switched (activity-gated) order-4 predictor bank (temporal residual entropy) | 1.4760× / 2.1264× / 1.3401× / 1.9538× | 0.0524 | **RETIRED** — dominated on all 4 |
| `LMS4+Rice+acar_sel+bestpartner` | scale-selected CAR→best-partner cascade (per-recording meta-gate on channel count) | 1.4804× / **2.1795×** / 1.3505× / 1.9555× | 0.043 | **kept** — non-dominated OTB corner; supersedes always-on cascade |

- **`jointbp2` (frontier #1, the one untried STACK — spent, works but not a global best).** Selecting the best
  *pair* of causal neighbours (backward, zero side-info) then predicting from it with one joint 2-tap sign-sign
  LMS. It **recovers the highest cross-channel gain of any codec on Hyser (+12.55%** vs temporal `LMS+Rice`,
  above `joint2`'s +12.26% and best-partner's +11.31%): selection+count genuinely *stack* on the diffuse-local
  large array, exactly as frontier #1 predicted. **But on the tight 64-ch OTB it is +17.91%, still below
  best-partner's single selected neighbour (+18.44%)** — a jointly-solved second parent adds less than one
  dominant diagonal neighbour, and selecting the pair does not rescue it. Net: wins primary Hyser (+1.12%, the
  highest embeddable Hyser ratio measured), loses OTB (−0.45%) and CEMHSEY (−0.17%), ≈neutral CapgMyo → does not
  robustly beat the best. Highest-ratio-on-Hyser → non-dominated → kept; not promoted (one-set win, two-set
  regressions, same disposition as `joint2`).
- **`LMS4rs` (frontier #2, temporal residual entropy — spent NEGATIVE → RETIRED).** A 3-way activity-regime bank
  of order-4 sign-LMS predictors *lowered* the achieved cross-channel gain on **every** real set (OTB +16.49% vs
  +18.44%, Hyser +10.98% vs +11.31%, CapgMyo +0.59% vs +1.37%, CEMHSEY +12.98% vs +13.08%) and lost on all 4 at
  higher cost (0.0524 > 0.0394). After order-4 LMS the residual is near-white (P2); "burst" segments are
  higher-*variance* noise not distinct linear dynamics, and splitting into 3 banks fragments each bank's
  adaptation to ~⅓ the samples → noisier taps → higher coded bits. `H(e|regime)` did **not** drop below `H(e)`.
  Conclusively dominated → RETIRED. The predictor-side analogue of the retired `xctx` context-modeling failure.
- **`acar_sel` (frontier #3, scale-selected cascade — spent POSITIVE as engineering → RETIRES the always-on
  version).** A per-recording meta-gate on array channel count `C` (header-read, zero circularity, zero
  side-info): `C≤64` → full CAR→best-partner cascade, `C≥128` → best-partner only. It reproduces the always-on
  cascade's OTB max-ratio corner **exactly (2.1795×, +0.81% over best-partner)** while equalling the plain best
  **exactly on all 3 large arrays** — recovering the −0.34/−0.40 pp large-array ratio the always-on cascade had
  *lost*. It **Pareto-dominates `LMS4+Rice+acar+bestpartner` at equal cost 0.043** (≥ ratio everywhere, > on
  Hyser+CEMHSEY) → that always-on codec is **RETIRED (superseded)**. Versus the leaderboard best, acar_sel weakly
  dominates on ratio (≥ all, > OTB) but at higher cost (0.043 > 0.0394) and ties the primary Hyser → not a Pareto
  win, not promoted; kept as the non-dominated OTB corner.
- **Sanity:** max real ratio 2.1795× (acar_sel/OTB) ≪ 6× ceiling; every row bit-exact (`ok=True`), all
  `embedded_ok`/`neural_ok`; no incumbent/best regression (best reproduces its registered ratios exactly). Only
  `research/registry.py` (two retire flags — codec encode/decode logic untouched), report files, and `SURVEY.md`
  touched; `rtl/`/`sim/` untouched.
- **Net:** the single-parent rank-1 subtract on order-4 (`LMS4+Rice+xchan_bestpartner`) remains the proven ratio
  ceiling. Frontier #1 (selection⊕count stack) is now spent — it works on large arrays but does not clear the best
  on tight arrays; frontier #2 (temporal residual entropy via regime switching) is spent negative; frontier #3
  (scale-selected cascade) is spent positive as an engineering result (clean OTB corner) but yields no new best.
  Headline/port pick unchanged.

## Best embeddable after search (real Hyser)

`research/search.py` on `hyser_1dof_f1_s1` (15 000 samples, `results/06_search_hyser.csv`):

**`lms4s8+x6/b512` — 1.478× (mean), cost 0.027, ~26 enc cyc/sample-ch, neural_ok.**
The hill-climb improves the registry default (`lms8s8+x8/b256`, 1.474×) by retuning
to **order-4 LMS / cross-shift-6 / Rice-block-512** — order-4 again beats order-8 on
real data (the default is over-provisioned), at **~half the state/compute**.

### What mattered (ablation from best, real Hyser)

| axis | Δ ratio | |
|---|---:|---|
| cross-channel on→off | **+10.8%** | dominant lever, ~70× everything else |
| lms order 4→8 | +0.15% | deeper temporal prediction *hurts* here |
| rice block 512→256 | +0.13% | marginal |

## Pareto front (ratio vs cost, embedded_ok only — real Hyser)

| config | ratio | cost | neural_ok | character |
|---|---:|---:|:--:|---|
| delta+x8/b512 | 1.453× | 0.016 | ✅ | cheapest embeddable with xchan |
| **lms4s8+x6/b512** | **1.478×** | 0.027 | ✅ | **best ratio** |

## Cross-channel gain vs. spatial correlation (synthetic sweep — mechanism)

Synthetic corpus is for sweeps only, never a headline. It confirms the lever is
spatial correlation (`results/03_bench.csv`):

| spatial-corr | xchan gain (LMS) |
|---:|---:|
| 0.0 | −0.1% |
| 0.3 | +1.4% |
| 0.6 | +9.4% |
| 0.9 | +19.1% |

Real HD-sEMG lands at +10.8% (Hyser) to +17.4% (OTB) achieved — same lever, scaled
by each set's real neighbour correlation, consistent with the sweep.

## → The one codec to port next

**`LMS+Rice+xchan`, retuned to order-4 / cross-shift-6 / Rice-block-512** (`lms4s8+x6/b512`).

- Best embeddable on every real HD-sEMG set (Hyser 1.47×, OTB 2.14×, CEMHSEY 1.96×,
  CapgMyo 1.35×), bit-exact, `neural_ok` (~26 cyc/sample-ch ≪ both the sEMG and
  30 kHz neural budgets). It beats every embeddable-feasible reference on all four.
- The essential component is the **cross-channel grid-neighbour front-end**
  (+10.8% to +17.4% real gain where redundancy exists); the temporal predictor can
  be as small as order-4. Two independent real sets now agree that order-4 beats the
  order-8 default — port the smaller predictor.
- **If minimizing hardware is paramount** (no adaptive-weight loop), port
  **`delta+Rice+xchan`** instead — Hyser 1.45× at cost 0.016 (98% of the best ratio)
  with fixed integer-difference predictors only.
- **Port caveat:** the `+xchan` beta is currently derived offline over the whole
  signal; the embeddable form computes beta per block (look-ahead = one block).
  This is the first RTL/firmware implementation task (flagged in registry
  `meta.notes`).
- **2026-07-16 update (all 4 real sets) — new PROMOTED best:**
  `LMS4+Rice+xchan_bestpartner` (best-of-4 causal-neighbour selection on an
  order-4 predictor) is now the **max-ratio best embeddable on every real set**
  (Hyser 1.480×, OTB 2.162×, CEMHSEY 1.956×, CapgMyo 1.350×) at **cost 0.039** —
  it Pareto-*dominates* both the prior incumbent `LMS+Rice+xchan` (0.057) and the
  retired order-8 `LMS+Rice+xchan_bestpartner` (0.063) on both axes. **The
  value/minimal-hardware port pick stays `lms4s8+x6/b512`** (single-parent
  order-4, cost 0.027, **zero partner side-info**): it is essentially tied on
  ratio (hyser+otb mean 1.8204 vs the promoted codec's 1.8212, +0.04%) without
  the best-partner id+β side-info. Port the promoted codec only if that side-info
  is acceptable for the marginal ratio; otherwise the single-parent order-4 is
  the simpler, near-identical choice. The best-partner selection carries the same
  offline whole-signal port caveat as before (embeddable form re-selects per
  block).
- **2026-07-19 update (cycle 10) — the promoted best's port caveat is now CLOSED.**
  `LMS4+Rice+xchan_bestpartner_adaptive` re-selects (partner, β) per block from the
  previous reconstructed block (decoder mirrors it → **zero side-info, look-ahead 0**)
  and **holds the promoted best's ratio within ~0.4%** on the high-corr sets (Hyser
  1.4770× vs 1.4804×, OTB 2.1531× vs 2.1619×) while *beating* it on CapgMyo (1.3529×
  vs 1.3505×), at cost 0.0387. If the offline whole-signal partner/β selection is not
  acceptable on-node, **port this codec** — it is the fully streaming-legal realization
  of the promoted best at essentially the same ratio. The `LMS+Rice+xchan_joint2` joint
  2-parent predictor (cost 0.0366, zero side-info) is an even cheaper near-tie that *wins*
  the primary Hyser (1.4930×) but regresses on OTB/CEMHSEY — a strong value alternative,
  not a strict ratio win. **The headline best-ratio codec stays `LMS4+Rice+xchan_bestpartner`.**

## Status vs. the 6-stage plan

| stage | state |
|---|---|
| 0 Orient | ✅ `research/00_STATE.md` |
| 1 Registry + cost | ✅ `registry.py`, `embedded_cost.py` |
| 2 Corpus | ✅ `datasets.py` (synthetic + **real Hyser, OTB, CEMHSEY-320, CapgMyo**) |
| 3 Benchmark | ✅ `bench.py`, `results/06_real_bench.csv`, `results/06_real_bench_extra.csv` |
| 4 Search + analysis | ✅ `search.py` on real Hyser, Pareto + ablations (`results/06_search_hyser.csv`) |
| 5 Survey | ✅ `SURVEY.md` (web reachable; ranked candidates + next-codec recs) |
| 6 Report | ✅ this file |

**All four real HD-sEMG sets are now reachable and benched in-session: Hyser via
PhysioNet (WFDB), OTB via PyPI, CEMHSEY via a range-extracted single trial from a
19 GB Zenodo zip, and CapgMyo via the figshare mirror (`zju-capg.org` does not
resolve here). Hyser is the primary headline; CapgMyo serves as the negative
control that confirms the harness only reports cross-channel gain where the real
signal carries it.**
