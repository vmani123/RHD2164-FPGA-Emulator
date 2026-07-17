# 008 — LMS+Rice+xctx: cross-channel context-adaptive Rice parameter

- **Cycle:** 7
- **Date:** 2026-07-16
- **Branch:** `compression-cycle-2026-07-16`
- **Candidate:** `LMS+Rice+xctx`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey

## Hypothesis

Keep the order-8 LMS residual and the Golomb-Rice ENGINE unchanged; only **select the per-sample Rice
k from a backward cross-channel context** (JPEG-LS/LOCO-I context modeling). For channel c with causal
grid parent p, a leaky integrator of |res[p,t]| estimates neighbour spatial energy; its bit-length
buckets that energy (12 log-energy buckets), and per-bucket JPEG-LS (A,N) stats give k. Premise: HD-EMG
MUAP bursts are spatially coherent, so the residual is heteroscedastic and cross-channel
variance-correlated *even after mean decorrelation* → exploit H(e_c | neighbour energy) < H(e_c), the
across-channel heteroscedasticity a per-block k misses. Distinct from the retired `xchan_tans` (P5):
engine stays Rice, only its parameter's context gains cross-channel info.

## Implementation

Only `research/registry.py`: `_xctx_k`/`_xctx_encode_channel`/`_xctx_decode_channel`/`xctx_encode`/
`xctx_decode`, family `entropy-backend`. Root channels fall back to a single context = plain
per-channel JPEG-LS adaptive k. Matched enc/dec pair (decode in channel order, parent residual
reconstructed first), zero side-info, integer-only, look-ahead 0. Note: there is **no cross-channel
subtract** — the neighbour only CONDITIONS the coder. `rtl/`, `sim/` untouched. Self-test: round-trip
OK, emb_ok OK, neural OK, cost 0.095; `registry self-test: ALL round-trips bit-exact`.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

Real data (bit-exact, embedded_ok), from `results/cycle_bench.csv`, cost 0.0947:

| dataset | xctx | plain LMS+Rice (no xchan, 0.0523) | Δ vs LMS+Rice | incumbent LMS+Rice+xchan (0.0572) |
|---|---:|---:|---:|---:|
| otb_hdsemg_vl (64 ch) | 1.7827× | 1.8254× | **−2.34%** | 2.1426× |
| hyser_1dof_f1_s1 (128 ch) | 1.2929× | 1.3300× | **−2.79%** | 1.4738× |
| capgmyo_dba_s1 (128 ch) | 1.2967× | 1.3323× | **−2.67%** | 1.3493× |
| cemhsey_s1_d1t1 (320 ch) | 1.6824× | 1.7293× | **−2.71%** | 1.9551× |

## Attribution

Predictor unchanged; no front-end subtract. The only lever is the cross-channel context k-selection —
and it **LOSES ~2.3–2.8% vs a plain per-block adaptive k on every real set**, at nearly double the cost
(0.095 vs 0.052).

**Mechanism (theory) — two compounding failures:**
1. **The conditional-entropy premise is empty here.** After the order-8 LMS predictor whitens the
   residual, it is near-white and near-geometric with its scale already tracked per channel; the
   neighbour's residual *energy* carries almost no additional information about *this* sample's
   magnitude: H(e_c | neighbour energy) ≈ H(e_c). The across-channel heteroscedasticity the hypothesis
   banked on is largely removed by the temporal predictor, not left in the residual.
2. **Context dilution / model cost.** Splitting the residual into 12 energy buckets × per-context (A,N)
   fragments the sample count; each bucket's k is estimated from far fewer samples → noisier, so the
   *average* coded length rises above a single well-estimated per-channel adaptive k. The
   context-modeling overhead is never amortized against a (near-zero) conditional-entropy gain.

This confirms and **extends P5**: not only is swapping the entropy *engine* a dead lever — even
enriching the Rice *parameter's* context with cross-channel information loses, because the residual's
conditional entropy given neighbour energy ≈ its unconditional entropy on real HD-sEMG after LMS. To
lower coded bits, lower the residual entropy *upstream* (better decorrelation), not in the coder.

## Pareto check

**Conclusively Pareto-dominated even by plain `LMS+Rice`** (no cross-channel front-end at all) on ALL
4 real sets — worse ratio AND far higher cost — and by every registered cross-channel codec. Bottom of
every real table among embeddable codecs. Not on the front.

## Sanity gates

- Max real ratio 1.783× (otb) ≪ 6× ceiling → no leak.
- No FAIL bit-exact rows; round-trip OK, embedded_ok OK, neural_ok OK, cost 0.095.
- No incumbent regression.

## Verification

**Verifier A — PROMOTE. Verifier B — PROMOTE.** Unanimous on correctness/embeddability only, no split.

## Decision

**RETIRED (conclusively Pareto-dominated, below even plain LMS+Rice on all 4 real sets); NOT promoted.**
`retired=True` in `research/registry.py`. Durable learning (P5 extension): the second-order / conditional
cross-channel entropy lever is empty here — after LMS the residual is not cross-channel heteroscedastic
enough to beat a per-channel adaptive k, and context splitting's model cost dominates.
