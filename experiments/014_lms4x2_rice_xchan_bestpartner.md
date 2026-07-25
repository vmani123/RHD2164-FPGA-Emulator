# 014 — LMS4x2+Rice+xchan_bestpartner (`swlms`): regime-switched K=2 order-4 LMS bank

- **Cycle:** 13
- **Date:** 2026-07-25
- **Branch:** `compression-cycle-2026-07-25`
- **Candidate:** `LMS4x2+Rice+xchan_bestpartner`
- **Primary real dataset:** `hyser_1dof_f1_s1` (128 ch, 8×16, 2048 Hz, REAL); also otb / capgmyo / cemhsey
- **Verdict: NOT PROMOTED → RETIRED** (conclusively Pareto-dominated on all 4 real sets)

## Hypothesis (INSIGHTS open-frontier #2 — attack the temporal residual entropy in the *predictor*)

Interference HD-sEMG is an **amplitude-modulated** process: colored EMG gated by a slow activation
envelope, riding on a near-white instrumentation-noise baseline. The MMSE predictor depends on the
*normalized* autocorrelation, which differs between the two regimes (baseline ⇒ optimal taps ≈ 0;
active ⇒ taps ≠ 0). A single adaptive filter converges to a power-weighted compromise dominated by the
high-variance active regime, so it over-predicts at rest and lags at every burst onset. Hold **K=2**
order-4 tap-sets per channel, select the active set from a causally-derived activity state (bit-length
of a fast leaky |e| integrator vs a slow one, with dead-band hysteresis; decoder mirrors it → zero
side-info), predict and update **only** the active set. Rice length ≈ `log2 E|e|`, so lower residual
variance in both regimes is directly fewer bits. Front-end and back-end are the promoted best, verbatim.

**Pre-registered risk (implementer's note + in-code):** *"context fragmentation — each tap-set adapts on
only ~1/K of the samples, the mechanism that sank xctx's 12 buckets — hence K=2 plus hysteresis."*

## Implementation

`research/registry.py` only. `_swlms_bitlen` (6 fixed shift/compares = priority encoder / CLZ),
`_swlms_next_state` (two multiplierless leaky integrators `af += |e| − (af>>4)`, `aslow += |e| − (aslow>>8)`,
compared by bit-length with a HOLD dead-band → amplitude-scale-invariant), `_swlms_forward/_swlms_inverse`
(matched pair; `w` shape (K, C, order); sample history shared across sets — it is the signal, not a regime
parameter). Front-end = `_bp_select`/`_bp_inverse` best-of-4 causal neighbour with the same 2×int16/ch
(partner, β) side-info as the promoted best; back-end = unchanged adaptive per-block Golomb-Rice (P5).
Per-sample predict+update op count unchanged (only one set is touched); P2 respected (order stays 4).

Cost: enc_ops 48, dec_ops 40, 48 state bytes/ch, look-ahead 256 (inherited from the best-partner
front-end), block 256, `embedded_ok` OK, `neural_ok` OK, **cost 0.0549**.

## Measurement

Command:
`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv`

REAL data, from `results/cycle_bench.csv` (all rows `ok=True`, `embedded=OK`, `neural=OK`). The
single-filter parent is byte-identical apart from the bank, so this is a clean A/B of the regime switch:

| dataset | `swlms` K=2 (0.0549) | single-filter `LMS4+Rice+xchan_bestpartner` (0.0394) | vs parent | `swlms` bytes | parent bytes |
|---|---:|---:|---:|---:|---:|
| hyser_1dof_f1_s1 (primary) | 1.476340 | 1.480384 | **−0.273%** | 2 601 026 | 2 593 921 |
| otb_hdsemg_vl | 2.132833 | 2.161938 | **−1.346%** | 900 211 | 888 092 |
| capgmyo_dba_s1 | 1.342310 | 1.350480 | **−0.605%** | 2 860 741 | 2 843 433 |
| cemhsey_s1_d1t1 | 1.954323 | 1.955547 | **−0.063%** | 4 912 188 | 4 909 112 |
| **4-set mean** | **1.72651** | **1.73709** | **−0.609%** | — | — |

Synthetic (near-stationary, no amplitude modulation to switch on — the expected null):
synth_sc0.6 2.5960013 vs parent 2.5959820 (**+11 bytes**, 1 479 198 vs 1 479 209);
synth_sc0.9 2.5616788 vs 2.5617198 (**−24 bytes**). A dead tie, i.e. the switch is neutral where the
signal is stationary — the detector is not broken, it simply has nothing to detect.

## Attribution

The cross-channel front-end (best-partner + integer β side-info) and the entropy back-end (adaptive
per-block Rice) are **byte-identical to the parent**; the only difference is one vs two order-4 tap-sets
and the regime state machine. So **100% of the −0.06…−1.35% is attributable to the temporal predictor**,
and specifically to the *bank*, not to order (order is 4 in both), not to the coder (P5), not to spatial
structure. The cross-channel gain confirms this by exclusion: `swlms` posts +11.00% hyser / +16.85% otb /
+0.75% capgmyo / +13.01% cemhsey against the parent's +11.31 / +18.44 / +1.37 / +13.08 — i.e. the *spatial*
gain went **down** even though the spatial code is identical, because a noisier temporal residual is a
worse substrate for the same rank-1 subtract. The regime switch degraded the input to the front-end.

## Cross-channel gain (isolated, REAL, vs temporal-only `LMS+Rice`)

+11.00% hyser, +16.85% otb, +0.75% capgmyo, +13.01% cemhsey — below the parent on every set (largest
gap −1.59 pp on OTB, −0.62 pp on CapgMyo).

## Pareto check

Dominated: **worse ratio on all 4 real sets AND higher cost** (0.0549 > 0.0394) than the already-registered
`LMS4+Rice+xchan_bestpartner`, whose front-end and back-end it copies verbatim. Also dominated by the
cheaper zero-side-info `bpa` (0.0387) on all 4. No axis on which it corners: the synthetic tie is not a win,
its look-ahead (256) and side-info are the parent's, and it carries the highest state-per-channel (48 B) of
any embeddable codec in the sweep.

## Sanity gates

- Bit-exact: `ok=True` on all 6 datasets. ✅
- `embedded_ok` = OK, `neural_ok` = OK. ✅
- Max real ratio 2.1328× ≪ 6× leak ceiling. ✅
- Regression: yes, on all 4 real sets vs its own single-filter parent — that is the finding.

## Verifier verdicts

- **Verifier A: PROMOTE.**
- **Verifier B: PROMOTE.**
- **Unanimous, no split.** (Correctness/embeddability only; both verifiers re-ran the round-trip and the
  cost audit. Neither verifier's remit is ratio.)

## Decision

**NOT PROMOTED** (loses to the best on all 4 real sets).
**RETIRED** — `retired=True` + `retired_reason` set on the `Codec("LMS4x2+Rice+xchan_bestpartner", …)`
registration in `research/registry.py`.

## Mechanism / why it lost (the durable lesson → INSIGHTS P2 refinement, P6)

The pre-registered context-fragmentation risk materialized, and the theory is the same one that sank
`xctx`'s 12 Rice-k buckets — **model cost without conditional-entropy payoff**, here in the *predictor*
rather than the *coder*:

1. **An adaptive filter is itself a context model.** Splitting into K=2 state-indexed tap-sets halves the
   effective adaptation sample count per set. A sign-sign LMS has a fixed ±1 step, so its convergence time
   is set by how many *consecutive* samples it sees — and a regime-switched filter never sees a long run:
   each switch drops it back onto a tap vector that was last updated one regime-dwell ago and has since
   gone stale. The re-convergence transient after every switch costs more bits than the regime-matched taps
   save. With ~21 switches/channel measured on the implementer's synthetic (and real HD-sEMG bursting far
   more irregularly), the transient fraction is not small.
2. **The premise over-states the regime contrast.** The bank pays off only if the *normalized*
   autocorrelation genuinely differs between regimes. On real HD-sEMG the "rest" segments are not white
   instrumentation noise — they contain low-level motor-unit activity with the *same* spectral shape at
   lower amplitude. Because the sign-sign update is **amplitude-invariant** (it uses only signs), a single
   filter already handles a pure amplitude modulation for free: `sign` normalizes out the envelope. The
   compromise-bias the hypothesis wanted to remove was therefore largely **not present** in a sign-sign
   LMS to begin with, while the fragmentation cost was fully present.
3. **The loss ordering confirms it.** The damage is largest on OTB (−1.35%) and CapgMyo (−0.61%) — the two
   sets whose residual is closest to white/stationary, where a switch can only ever fragment — and smallest
   on CEMHSEY (−0.06%), the most strongly non-stationary set. Regime switching helps exactly where the
   hypothesis needs it and hurts everywhere else, and the everywhere-else dominates.

**Generalization: state-indexed *parameter banks* are a dead lever on this data, whether the parameter is
the entropy coder's (xctx) or the predictor's (swlms), because HD-sEMG segments are too short relative to
an adaptive estimator's convergence time to amortize the split.**
