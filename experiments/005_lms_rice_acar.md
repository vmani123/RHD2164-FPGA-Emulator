# 005 — LMS+Rice+acar: Adaptive Common Average Reference (rank-1 global common-mode)

- **Cycle:** 4
- **Date:** 2026-07-14
- **Branch:** `compression-cycle-2026-07-13`
- **Candidate:** `LMS+Rice+acar`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb/capgmyo/cemhsey

## Hypothesis

A different slice of the cross-channel mutual information (INSIGHTS P1) than the pairwise
subtracts tried so far: remove the **global array common-mode** (array mean per time-slice)
with a reversible-integer S-transform-style lift before the temporal predictor. The root
channel carries the array total S (preserving the array-DC degree of freedom that subtracting
the mean from all channels would lose), every other channel becomes x−floor(S/C). Gated per
block backward-adaptively (fires only when C·Σ(CAR²)/Σ(x²) over the previous reconstructed
block exceeds ~2/C), zero side-info, look-ahead 0, integer-only.

## Implementation

Only `research/registry.py`: `_acar_gate`/`_acar_forward`/`_acar_inverse`/`acar_encode`/
`acar_decode` + one `Codec(...)`, `family="cross-channel"`. Order-8 sign-sign LMS + adaptive
Rice unchanged; only the spatial front-end differs. `rtl/`, `sim/` untouched. Self-test:
`LMS+Rice+acar 2.70x round-trip OK emb_ok OK neural OK cost 0.056`; `registry self-test: ALL
round-trips bit-exact`. Gate confirmed live (fires on 9/10 self-test blocks).

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok), from `results/cycle_bench.csv`:

| dataset | acar | cost | LMS+Rice (xchan off) | iso. CAR gain | incumbent xchan gain | vs incumbent |
|---|---:|---:|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch, 5×13) | 2.0889× | 0.0559 | 1.8254× | **+14.44%** | +17.4% | −2.51% |
| hyser_1dof_f1_s1 (128 ch) | 1.3633× | 0.0559 | 1.3300× | **+2.50%** | +10.8% | −7.50% |
| capgmyo_dba_s1 (128 ch) | 1.3323× | 0.0559 | 1.3323× | **+0.00%** | +1.3% | −1.26% |
| cemhsey_s1_d1t1 (320 ch) | 1.7434× | 0.0559 | 1.7293× | **+0.82%** | +13.1% | −10.83% |

## Attribution

Temporal + back-end unchanged → the only lever is the global common-mode front-end. It
captures the common-mode slice of the spatial mutual information: **+14.4% on the small,
tightly-coupled 64-ch OTB array** (close to the single-neighbour subtract's +17.4%), but
collapses to **+0.8–2.5% on the larger 128/320-ch Hyser and CEMHSEY arrays** where the
single-neighbour subtract still gets +10.8–13.1%. On CapgMyo (negative control) both ~0.

**Mechanism:** CAR removes exactly one eigenvector of the spatial covariance — the rank-1
global DC-across-array component. Where the dominant redundancy *is* that global common mode
(small, tightly-coupled arrays), CAR captures most of it cheaply. On large arrays the shared
content is spatially **local** (high neighbour correlation, low global coherence across 320
channels), so a single global mean is a poor basis and the per-neighbour beta — which adapts
to local pairwise structure — captures far more of the mutual information. Global-rank-1 and
local-pairwise are *different, non-interchangeable* slices of P1's cross-channel MI; array size
selects which dominates.

## Pareto check

**Non-dominated on 2 of 4 real sets, dominated on 2:**
- **otb** — on the Pareto front (2.0889×/0.0559): *cheaper* than the incumbent
  `LMS+Rice+xchan` (0.0572) which has higher ratio; nothing has both ≥ratio and ≤cost.
- **capgmyo** — on the Pareto front (marginally; ~tied with LMS+Rice at slightly higher cost).
- **hyser** — dominated by `delta+Rice+xchan` (1.4516×/0.0127, higher ratio AND far lower cost).
- **cemhsey** — dominated by `delta+Rice+xchan` (1.8824×/0.0127).

Because it is genuinely non-dominated on OTB (and CapgMyo), it is **NOT conclusively
Pareto-dominated** → kept registered (per rule: a non-dominated-but-not-best candidate stays).

## Sanity gates

- Max real ratio 2.089× (acar) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; acar round-trip OK, embedded_ok=OK, neural_ok=OK, cost 0.056.
  Forward/inverse lift verified bit-exact standalone; ON path genuinely exercised (root
  channel inflated to array total on ON blocks).
- No incumbent regression.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous on correctness/embeddability. No split.

## Decision

**KEPT REGISTERED (not retired); NOT promoted.** Passes verification. Does not beat the current
best on the primary real set (Hyser 1.363× < incumbent 1.474×), so not promoted. But it is a
genuinely non-dominated low-cost Pareto corner on OTB (cheaper than the incumbent while
delivering +14.4% common-mode gain), so it is *not* retired — a legitimate front point analogous
to cycle 2's bestpartner. Port recommendation unchanged.
