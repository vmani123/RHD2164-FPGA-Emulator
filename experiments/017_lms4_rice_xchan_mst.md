# 017 — LMS4+Rice+xchan_mst: Chow–Liu maximum-MI spanning-tree parent assignment

- **Cycle:** 13
- **Date:** 2026-08-01
- **Branch:** `compression-cycle-2026-08-01`
- **Candidate:** `LMS4+Rice+xchan_mst` (a.k.a. `mst`)
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis (SURVEY cycle-16 rec #3 — the untried GRAPH axis)

Every cross-channel codec so far picks each channel's parent from a **raster-causal** 4-neighbour set, so
the dependency structure is fixed by scan order, not by the data: channel 0 of every row has only its
far-edge "left" neighbour, and a channel whose strongest correlate has a *higher* index can never use it.
Chow–Liu says the best first-order dependency structure over a set of variables is the **maximum-weight
spanning tree of the pairwise mutual-information graph** — and coding in a topological order of that tree
makes it realizable. Replacing the raster forest with a per-block Prim MST over the full channel graph,
rebuilt from the previous reconstructed block (decoder mirrors it → zero side-info), should capture the MI
the raster restriction throws away, at no extra per-sample cost.

## Implementation

`research/registry.py` only: `_mst_topology` (bounded candidate graph — the full 8-neighbourhood plus 2
long-range probes per channel, ~6 undirected edges/channel, a pure function of array shape so both sides
build it identically), `_mst_edge_weights` (symmetric Chow–Liu weight = estimated Rice **bit saving** in both
orientations summed — the harness's operational stand-in for 2·I(X_u;X_v), reusing the shipped integer-LS β
and Rice-length metric), `_mst_build_block` (max-weight spanning **forest** by Prim with fully deterministic
tie-breaks; edges of weight ≤ 0 are never taken and a non-paying orientation is demoted to no-parent, so no
individual channel is made worse; returns Prim's admission order as a root-first topological coding order),
`_mst_forward`/`_mst_inverse` (**block-outer / channel-inner** nesting — this is what legalizes a
non-raster whole-array parent map, since block *i−1* is complete for all channels on both sides when block
*i* is processed), `mst_encode`/`mst_decode`. Exactly ONE asymmetric rank-1 subtract per channel is kept —
the entire lever is spent on the dependency **graph** and the coding **order**. Back-end unchanged: order-4
sign-sign LMS (P2) + adaptive Rice (P5). Look-ahead 0, zero side-info.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact `ok=True`, `embedded_ok=OK`, `neural_ok=−`, cost 0.106), from `results/cycle_bench.csv`:

| dataset | **mst** | best `bestpartner` (0.0394) | **vs best** | matched zero-side-info twin `bpa` (0.0387) | **graph+order lever, isolated** |
|---|---:|---:|---:|---:|---:|
| otb_hdsemg_vl | **2.172663** | 2.161938 | **+0.496%** | 2.153106 | **+0.908%** |
| **hyser_1dof_f1_s1** | **1.483075** | 1.480384 | **+0.182%** | 1.477020 | **+0.410%** |
| capgmyo_dba_s1 | **1.352902** | 1.350480 | **+0.179%** | 1.352866 | +0.003% |
| cemhsey_s1_d1t1 | **1.957450** | 1.955547 | **+0.097%** | 1.953948 | **+0.179%** |
| 4-set mean | **1.741522** | 1.737087 | **+0.255%** | 1.734235 | **+0.420%** |

**This is the first candidate since cycle 7 to strictly beat the leaderboard best on ALL FOUR real
datasets.** `bestpartner_adaptive` is the correct matched twin for isolating the lever (identical backward
per-block selection, identical zero side-info, identical rank-1 apply, identical predictor and back-end —
differing only in that its parent must come from the 4 raster-causal neighbours in scan order), so the
right-hand column is the graph+order lever alone.

## Attribution

Isolated cross-channel gain on REAL data (vs `LMS4+Rice` temporal-only, measured this cycle at otb 1.834664,
hyser 1.332070, capgmyo 1.333589, cemhsey 1.728021):

| dataset | **mst** | `bestpartner` | `bpa` (D=0 twin) |
|---|---:|---:|---:|
| otb_hdsemg_vl | **+18.42%** | +17.84% | +17.36% |
| hyser_1dof_f1_s1 | **+11.34%** | +11.13% | +10.88% |
| capgmyo_dba_s1 | +1.45% | +1.27% | +1.45% |
| cemhsey_s1_d1t1 | **+13.28%** | +13.17% | +13.07% |

Everything moved is **cross-channel front-end** — the temporal predictor and the entropy back-end are
byte-identical to `bestpartner`/`bpa`, and the per-sample apply is the same single multiply-shift-subtract.

**The mechanism demonstrably engages, hard.** Probe of the shipped `_mst_build_block` over every block of
every real set:

| dataset | parent index p > g (impossible for any raster codec) | parent outside the 4-candidate raster set | roots (no parent) |
|---|---:|---:|---:|
| otb_hdsemg_vl | 1585/3712 = **42.7%** | 1886/3712 = **50.8%** | 58 = 1.6% |
| hyser_1dof_f1_s1 | 4533/7424 = **61.1%** | 4755/7424 = **64.0%** | 59 = 0.8% |
| capgmyo_dba_s1 | 2853/7424 = **38.4%** | 3491/7424 = **47.0%** | 91 = 1.2% |
| cemhsey_s1_d1t1 | 9084/18560 = **48.9%** | 12536/18560 = **67.5%** | 223 = 1.2% |

So **47–68% of channels are predicted from a parent the raster codecs structurally could not use**, and
38–61% from a *higher-indexed* channel that only the topological coding order makes legal. The lever is
genuinely exercised — this is not a codec that quietly degenerates to its incumbent.

**And yet the win is only +0.10% … +0.50%.** That is the durable result. Theory: total redundancy removed by
a first-order dependency tree is `Σ_g I(X_g ; X_pa(g))`. Swapping two-thirds of the parents for their true
MI-argmax raises that sum by well under 1% because HD-sEMG spatial correlation is **smooth and locally
isotropic** — the MI-vs-distance curve is flat across the immediate neighbourhood, so the best *raster*
neighbour is almost always within a fraction of a bit of the global argmax. Changing *which* neighbour you
subtract is therefore nearly free in both directions. Confirmation from the other end of the scale: on
CapgMyo, where the neighbourhood carries essentially no MI at all (+1.45% total), the graph lever is worth
**+0.003%** — you cannot re-route your way to information that isn't there.

**The gain that does appear is concentrated exactly where the raster restriction is a real constraint:** OTB
(+0.91%) is the 5×13 array with the largest fraction of edge channels (column-0 channels whose raster "left"
neighbour is the far-edge electrode — a spurious parent the MST simply never selects), and CEMHSEY (+0.18%)
is the 5×64 strip where the same edge pathology recurs 64 times. The MST's first-order benefit is largely
**pathology repair at array boundaries**, not new physics in the interior.

## Pareto check

Cost **0.106** vs the best's 0.0394 — **2.69×** for a **+0.255%** mean-ratio gain. Automated domination scan
over all `embedded_ok` codecs on the 4 real sets: nothing dominates it (it is the max-ratio point on
CEMHSEY), and it dominates nothing (it is the most expensive `embedded_ok` codec registered). It is a
legitimate, non-dominated **max-ratio corner**.

The sobering comparison is not to the best but to `acar_sel+bestpartner`, already registered at cost
**0.043**: 4-set mean **1.741488** vs mst's **1.741522** — a **+0.002% dead tie at 2.5× the cost** (acar_sel
even beats mst on OTB, 2.179540 vs 2.172663). So on the mean-ratio-vs-cost front mst buys essentially
nothing over an existing, far cheaper codec; its claim rests entirely on being the only codec that is
strictly *above the best on every individual set*.

`neural_ok = −`: 155 cyc/sample-ch encode vs the tight 125-cyc 30 kS/s neural budget. `embedded_ok` holds at
~8% of the roomy 2 kS/s sEMG budget it targets. The cost is entirely the **per-block scan** (100 of 129 enc
ops/sample-ch are edge scoring + Prim); the per-sample apply is identical to best-partner's.

## Sanity gates

- Max real ratio 2.1727× for this codec, 2.1795× across the sweep ≪ the 6× broadband ceiling → no leak.
- All 120 rows in `results/cycle_bench.csv` have `ok=True` → no FAIL bit-exact.
- No incumbent regression: every registered codec reproduces its promoted value exactly.
- Extra robustness (implementation phase, bit-exact): C not a multiple of cols (17×777 cols=4; 7×1000
  cols=3), C=1, C=2, N < one block (32×100), N=257, int16 extremes.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous, no split.

## Decision

**PROMOTED — new leaderboard best-ratio embeddable on real data.** It satisfies the promotion rule in full:
it strictly beats the current best `LMS4+Rice+xchan_bestpartner` on **all four** real datasets (+0.50% /
+0.18% / +0.18% / +0.10%, mean +0.255%), it is bit-exact and `embedded_ok`, and both verifiers returned
PROMOTE with no split. Not retired; nothing it dominates is retired either.

**Scope of the promotion, stated loudly.** It is the new best *ratio* point, **not** the new port pick:
- cost 0.106 = **2.69×** the best's, for **+0.255%** — a poor Pareto trade;
- `neural_ok` fails (155 > 125 cyc/sample-ch), so it cannot run at the RHD2164's native 30 kS/s;
- the cheaper `acar_sel+bestpartner` (0.043) matches its 4-set mean to +0.002%.

The **"one codec to port next" headline is unchanged** (`lms4s7+x6/b512`, cost 0.027, `neural_ok=OK`,
reconfirmed by this cycle's search at mean 1.820× on hyser+otb). The scientific value of this result is the
*negative* half: two-thirds of parents re-routed to their true MI-argmax buys under half a percent, which
retires "wrong parent" as an explanation for the remaining bits (see INSIGHTS P1c).
