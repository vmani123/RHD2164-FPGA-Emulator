# 013 — LMS4+Rice+xtree: Chow-Liu maximum-weight spanning-forest channel topology — RETIRED

- **Cycle:** 13
- **Date:** 2026-07-28
- **Branch:** `compression-cycle-2026-07-28`
- **Candidate:** `LMS4+Rice+xtree` (cost 0.0430, zero side-info, look-ahead 0)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis

Every registered spatial front-end picks each channel's parent from the **causal
4-neighbourhood** under a fixed raster coding order — a *greedy, geometrically
constrained* topology. Information theory says the optimal single-parent
(dependence-tree) model is the **Chow-Liu maximum-weight spanning tree** over
pairwise mutual information, with no requirement that edges respect the raster
order. Per block, build a maximum-weight directed spanning **forest** over a
bounded candidate edge set (8 grid neighbours + 4 axis-distance-2 neighbours ≤ 12
per channel), edge weight = |sign-sign cross-correlation counter| over the
**previous reconstructed block** (8× time-subsampled), code channels in the
forest's topological order, predict each from its tree parent with the shipped
adaptive rank-1 subtract. Decoder rebuilds the identical forest from the same
reconstructed block ⇒ **zero side-info**. Prediction: a globally-optimal topology
beats the greedy raster one.

## Implementation

Only `research/registry.py`: `XTREE_*`, `_xtree_edges/_xtree_weights/_xtree_forest/`
`_xtree_block_params/_xtree_forward/_xtree_inverse`, `xtree_encode/xtree_decode`,
plus the `Codec("LMS4+Rice+xtree", ...)` registration. Kruskal + path-halving
union-find, deterministic `(weight, u, v)` tie-break, zero-weight edges dropped,
components rooted at the lowest channel index, DFS visit order = topological coding
order. Edge application is the **unchanged** shipped machinery: rounded integer-LS
`_bp_opt_beta` over the previous block, kept only if it lowers `_bp_score`
(estimated Rice bits), else the edge is dropped and the child becomes a root.
Order-4 sign-sign LMS (P2) + adaptive Rice (P5). `rtl/`, `sim/` untouched.
Registry self-test: round-trip OK, `emb_ok` OK, `neural` OK, cost 0.0430.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data, from `results/cycle_bench.csv` (all rows bit-exact `ok=True`, `embedded=OK`):

| dataset | `xtree` (0.0430) | best `LMS4+Rice+xchan_bestpartner` (0.0394) | vs best | `bpa` (0.0387) | **vs `bpa`** |
|---|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 | 1.461730 | 1.480384 | −1.26% | 1.477020 | **−1.04%** |
| otb_hdsemg_vl | 2.134552 | 2.161938 | −1.27% | 2.153106 | **−0.86%** |
| capgmyo_dba_s1 | 1.349710 | 1.350480 | −0.06% | 1.352866 | **−0.23%** |
| cemhsey_s1_d1t1 | 1.945732 | 1.955547 | −0.50% | 1.953948 | **−0.42%** |

`LMS4+Rice+xchan_bestpartner_adaptive` (`bpa`) is the **exact apples-to-apples
control**: same order-4 LMS, same adaptive Rice, same `_bp_opt_beta` integer-LS
gain, same `_bp_score` Rice-bits keep/drop gate, same previous-block backward
derivation, same zero side-info. **The only difference is the topology criterion**
(greedy min-bits over ≤4 causal neighbours + raster order, vs global max-weight
forest over ≤12 undirected neighbours + topological order). `xtree` is worse on all
four at higher cost.

## Isolated cross-channel gain (real, achieved, vs `LMS+Rice` 0.0523)

| dataset | `xtree` | `bpa` | best |
|---|---:|---:|---:|
| hyser_1dof_f1_s1 | **+9.91%** | +11.05% | +11.31% |
| otb_hdsemg_vl | **+16.94%** | +17.96% | +18.44% |
| capgmyo_dba_s1 | **+1.31%** | +1.55% | +1.37% |
| cemhsey_s1_d1t1 | **+12.51%** | +12.99% | +13.08% |

Global topology optimisation captures **0.24–1.40 pp LESS** achieved cross-channel
gain than greedy causal selection on every real set.

## The mechanism was genuinely exercised (not silently degenerate)

Forest statistics recomputed on the REAL arrays (read-only scratch script calling
`registry._xtree_block_params` directly, 58 blocks per set):

| dataset | edges chosen | roots | parents with **HIGHER** grid index | parents **outside** the causal 4-neighbourhood | negative β |
|---|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 | 7341 | 83 (1.1%) | **42.2%** | **51.2%** | 0.5% |
| otb_hdsemg_vl | 3654 | 58 (1.6%) | **34.8%** | **57.6%** | 0.1% |
| capgmyo_dba_s1 | 6633 | 791 (10.7%) | **36.2%** | **47.7%** | **13.0%** |
| cemhsey_s1_d1t1 | 18178 | 382 (2.1%) | **38.2%** | **68.7%** | 0.3% |

A third to a half of the parents are structurally unreachable by any raster codec
here, and half to two-thirds of the edges are outside the 4-neighbourhood. The
extra freedom was used — and it lost anyway. (CapgMyo's 13% negative β confirms the
|·| weight does select anti-correlated differential pairs, as designed.)

## Attribution

Predictor and back-end are identical to `bpa`; the topology criterion is the only
lever. It loses for two compounding, theory-level reasons:

1. **Wrong objective.** Chow-Liu maximises Σ I(u;v) under a Gaussian/arcsine-law
   proxy. The quantity that actually sets coded bits is the **achieved residual
   Rice length** after a *rounded, Q8-quantised, integer-LS* rank-1 subtract, which
   is not monotone in |ρ| across edges with different variance ratios. `bpa`
   selects **directly on `_bp_score`** (estimated Rice bits) over all its
   candidates; `xtree` fixes the topology from the coarse surrogate first and only
   then applies `_bp_score` as a keep/drop gate — so when the max-weight edge's β
   fails to pay, the channel becomes a **root** and codes with *no* parent instead
   of falling back to the second-best edge. Optimising the wrong objective globally
   is worse than optimising the right one greedily.
2. **Winner's-curse in the weight estimator.** Each weight is a sign-sign counter
   over 32 samples (256-sample block, 8× subsampled) across ~6C ≈ 768–1920
   candidate edges. Taking the **max** over that many noisy, upward-biased sample
   correlations systematically prefers long/weak edges whose true ρ is small —
   which is exactly what the 47.7–68.7% "outside the 4-neighbourhood" figure shows.
   The *small*, physically-motivated candidate set is a feature, not a limitation:
   it is a prior that suppresses selection bias.

Underneath both: on a locally-correlated electrode array the max-MI parent is
**already inside** the causal 4-neighbourhood (P1 — the redundancy is local), so a
global search has no headroom to buy, only estimator noise to pay.

## Pareto check

Conclusively **Pareto-dominated on ALL FOUR real sets** — worse ratio *and* higher
cost — by three already-registered codecs:

| dominator | cost | dominates `xtree` (0.0430) on |
|---|---:|---|
| `LMS4+Rice+xchan_bestpartner_adaptive` | 0.0387 | hyser, otb, capgmyo, cemhsey |
| `LMS4+Rice+xchan_bestpartner` (best) | 0.0394 | hyser, otb, capgmyo, cemhsey |
| `LMS+Rice+xchan_joint2` | 0.0366 | hyser, otb, capgmyo, cemhsey |

## Sanity gates

- Max real ratio in the run 2.1795× ≪ 6× ceiling; `xtree`'s own max 2.1346×.
- All 102 CSV rows `ok=True`; no FAIL bit-exact.
- No regression to the recorded best (reproduces exactly).
- Extra round-trips reported bit-exact by the implementer for cols ∈ {4,8,16} ×
  shapes {(32,1200),(16,900),(20,513),(12,257),(8,255)} and a differential array.

## Verifier verdicts

- **Verifier A: PROMOTE**
- **Verifier B: PROMOTE**
- **Unanimous, no split.** (Verification is correctness/embeddability only — it
  never was a ratio judgement.)

## Decision

**Not promoted. RETIRED** — `retired=True` + `retired_reason` set on the
`Codec("LMS4+Rice+xtree", ...)` registration in `research/registry.py`. Kept
bit-exact, excluded from the default sweep; `--include-retired` re-checks it.

**Durable learning (INSIGHTS P1c):** *global* optimisation of a **cheap surrogate**
objective over a **large** candidate set loses to *greedy* optimisation of the
**true coding-bits** objective over a **small, physically-motivated** one. Chow-Liu
optimality is optimality for the *modelled MI*, not for the *achieved integer-coded
residual*, and its edge weights must be estimated from a short causal window where
max-selection over many candidates is dominated by winner's-curse bias. Do not
re-propose global channel-topology search (MST/clustering/permuted coding order) as
a ratio play on locally-correlated arrays.
