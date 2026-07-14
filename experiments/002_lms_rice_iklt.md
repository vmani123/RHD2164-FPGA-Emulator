# 002 — LMS+Rice+iklt: fixed reversible integer-KLT multi-tap cross-channel front-end

- **Cycle:** 3
- **Date:** 2026-07-13
- **Branch:** `compression-cycle-2026-07-13`
- **Candidate:** `LMS+Rice+iklt`
- **Primary real dataset:** `otb_hdsemg_vl` (64 ch, 5×13 grid, 2048 Hz, REAL)

## Hypothesis

Replace single-neighbour subtraction with a fixed, data-independent, multiplierless
reversible integer inter-channel transform (integer-KLT via lifting) that decorrelates
each time-slice across the electrode array losslessly, then run the existing per-channel
LMS+Rice temporally. For two equal-variance channels with covariance `[[1,r],[r,1]]` the
KLT is exactly the 45° rotation (sum/difference) for any correlation `r`, so a fixed 45°
butterfly is the true KLT of a stationary isotropic neighbour pair and needs no training.
Rotations are realized as three integer lifting shears (Hao-Shi / Srinivasan IntSKLT,
coeffs at shift=12: P=−1697, U=2896) and cascaded over a fixed grid-neighbour schedule
(all horizontal adjacent pairs, then all vertical), making each transformed channel a
reversible integer mixture across a neighbourhood — genuinely multi-tap, distinct from the
rank-1 single-neighbour subtract of `+xchan`/`bestpartner`. Temporal look-ahead = 0 (the
transform lives inside a single time-slice); no persistent per-channel transform state.

## Implementation

Only `research/registry.py` was touched (added `_rmul`/`_rot_forward`/`_rot_inverse`/
`_iklt_pairs`/`_iklt_forward`/`_iklt_inverse`, `iklt_encode`/`iklt_decode`, cost constants
`_IKLT_OPS=24`/`_IKLT_NOTE`, and one `_register` entry). The order-8 sign-sign LMS temporal
predictor and the adaptive Golomb-Rice entropy back-end are byte-identical to the incumbent
`LMS+Rice+xchan`; the ONLY change is the cross-channel front-end. `rtl/` and `sim/` untouched.

Self-test command + output (proves bit-exactness of every registered codec incl. iklt):

```
$ PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest
registry self-test on random int16 [32 x 2500], 8 codecs (2 retired, excluded from the default sweep)

codec                 ratio  round-trip  emb_ok  neural    cost  status
-----------------------------------------------------------------------
delta+Rice            2.52x          OK      OK      OK   0.008
LMS+Rice              2.73x          OK      OK      OK   0.052
delta+Rice+xchan      2.52x          OK      OK      OK   0.013
LMS+Rice+xchan        2.72x          OK      OK      OK   0.057
LMS+Rice+xchan_adaptive  2.72x       OK      OK      OK   0.065  RETIRED
LMS+Rice+xchan_bestpartner  2.72x    OK      OK      OK   0.063
LMS+Rice+iklt         2.71x          OK      OK      OK   0.068  RETIRED
fixed0-3+Rice         2.72x          OK      OK      OK   0.019

registry self-test: ALL round-trips bit-exact
```
(The RETIRED status on iklt is post-decision — see Outcome. It was OK/not-RETIRED at
measure time.)

## Measurement

Command: `PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets otb_hdsemg_vl synth_sc0.6 synth_sc0.9 --csv results/cycle_bench.csv`

Real `otb_hdsemg_vl` (64 ch × 8000 samp @ 2048 Hz, grid 5×13 — bit-exact, embedded_ok),
from `results/cycle_bench.csv`:

| codec | ratio | cost | note |
|---|---:|---:|---|
| LMS+Rice+xchan_bestpartner | 2.2518× | 0.063 | max-ratio corner |
| **LMS+Rice+xchan** | **2.2413×** | **0.057** | incumbent best embeddable |
| delta+Rice+xchan | 2.1942× | 0.013 | cheapest xchan |
| **LMS+Rice+iklt** | **2.0669×** | **0.068** | **candidate under review** |
| wavpack | 1.9074× | ref | |
| LMS+Rice (temporal only) | 1.8998× | 0.052 | xchan-off baseline |

Synthetic mechanism check (same run):

| dataset | iklt | LMS+Rice | iklt gain | single-neighbour xchan gain |
|---|---:|---:|---:|---:|
| synth_sc0.6 | 2.4912× | 2.3516× | **+5.94%** | +9.4% |
| synth_sc0.9 | 2.3498× | 2.1276× | **+10.44%** | +19.1% |

Search command: `PYTHONPATH=host_tools ./.venv/bin/python research/search.py --datasets otb_hdsemg_vl --csv results/cycle_search.csv`
(the search sweeps the single-parent `+xchan` family, not iklt; it reconfirms the
port pick `lms4s7+x7/b512 = 2.321×`, cost 0.027, order-4 cross beats order-8 by
+19.03% cross-on ablation — orthogonal to this candidate.)

## Attribution

- **Temporal predictor and entropy back-end are unchanged** (order-8 sign-sign LMS +
  adaptive Rice, byte-identical to the incumbent). Baseline `LMS+Rice` = 1.8998× on real
  OTB. So neither the predictor nor the back-end moved the ratio this cycle.
- **The only lever changed is the cross-channel front-end**, and the fixed integer-KLT
  underperforms: iklt/`LMS+Rice` = 2.0669/1.8998 = **+8.80%** achieved cross-channel gain
  on real data — roughly **half** the single-neighbour subtract's **+18.0%** (harness-
  reported for `LMS+Rice+xchan`). iklt lands **−7.78%** below the incumbent (2.0669 vs
  2.2413).
- **Mechanism:** a fixed 45° butterfly is the exact KLT only under the equal-variance
  isotropic model. On real, non-stationary, anisotropic HD-sEMG the true inter-electrode
  covariance is not isotropic, so the fixed rotation is mismatched, whereas the incumbent's
  data-dependent single-neighbour beta tracks the actual pairwise correlation. The synthetic
  sweep confirms the pattern: at every spatial correlation the multi-tap fixed transform
  captures ~half of what adaptive single-neighbour subtraction does (5.94% vs 9.4% at
  sc0.6; 10.44% vs 19.1% at sc0.9). The implementer's own caveat anticipated exactly this.

## Pareto check

iklt (2.0669× / cost 0.068) is **conclusively Pareto-dominated on real data** — it has
the highest cost of any embeddable codec in the run and only the 4th ratio. Dominated by:
- `LMS+Rice+xchan` — 2.2413× / 0.057 (higher ratio AND lower cost)
- `LMS+Rice+xchan_bestpartner` — 2.2518× / 0.063 (higher ratio AND lower cost)
- `delta+Rice+xchan` — 2.1942× / 0.013 (higher ratio AND far lower cost)

## Sanity gates

- Max real ratio in the run is 2.2518× ≪ the ~6× leak/degenerate ceiling → no leak.
- No FAIL bit-exact rows: every row in `results/cycle_bench.csv` has `ok=True`; iklt
  round-trip OK, `embedded_ok=OK`, `neural_ok=OK`, cost 0.068 (both verifiers reproduced).
- iklt (2.0669×) is below the incumbent measured in the same run (2.2413×) — a candidate
  regression, but it does **not** regress the incumbent itself, which is unchanged.

## Verification

**Verifier A — REJECT.** Reran the round-trip from scratch (all 8 codecs bit-exact,
iklt cost 0.068), reran `bench.py` on real `otb_hdsemg_vl` and reproduced iklt = 2.07×
(vs incumbent 2.24×), hand-verified the cost gate against `cost_model.md`
(enc 88.8 cyc/sample-ch < both the sEMG 1831 and neural 125 budgets; sram 5120 B ≪ 256 KiB).
Sanity: 2.25× ≪ 6× ceiling. Verdict REJECT because the codec is dominated.

**Verifier B — PROMOTE.** Independent from-scratch round-trip (all bit-exact),
independent `bench.py` on real data reproduced iklt = 2.07×, hand-checked the same cost
gate (all gates PASS, cost 0.068 matches). No gate failed. Verdict PROMOTE on
correctness/embeddability.

**Combined outcome: verifier split (A REJECT, B PROMOTE) → NOT promoted, held for human
review.** Promotion additionally fails on its own terms: 2.07× does not beat the current
best (2.24×), so even a double-PROMOTE would not have promoted it.

## Outcome

**RETIRED (2026-07-13).** Conclusively Pareto-dominated on real `otb_hdsemg_vl` by three
registered codecs (primary dominator `LMS+Rice+xchan`, 2.24×/0.057 vs iklt 2.07×/0.068 —
worse ratio AND higher cost). Set `retired=True` + one-line `retired_reason` on the
`Codec(...)` registration in `research/registry.py`; kept registered and bit-exact for
reproducibility, excluded from the default `bench.py`/leaderboard sweep (`--include-retired`
re-checks on demand). Not promoted; the port recommendation is unchanged. The negative
result is informative: a fixed data-independent multi-tap transform does not beat adaptive
single-neighbour subtraction on real anisotropic HD-sEMG — the spatial gain lives in the
data-dependent pairwise weight, not in a wider fixed basis.
