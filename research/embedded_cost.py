#!/usr/bin/env python3
"""
embedded_cost.py  -  The embedded-feasibility cost model (non-negotiable #2).

Ratio never decides alone. Every codec in `research/registry.py` carries a small
metadata record describing its *embeddable realization* (integer-only? causal?
ops/sample-channel? persistent state? look-ahead?). This module turns that record
into:

  * `embedded_ok`  - a boolean **hard gate**. A codec that fails it can never be
                     ranked a "win", no matter how good its ratio.
  * `cost`         - a continuous score (fraction-of-budget consumed) used to
                     place the codec on the ratio-vs-cost **Pareto front**.

The scoring formula is intentionally explicit and documented here so a reviewer
can reproduce any codec's score by hand from its metadata. See
`compression_spec/cost_model.md` for the rationale.

Run `python research/embedded_cost.py` to print the model's targets and a worked
example.
"""
from dataclasses import dataclass, field, asdict
import math

# ---------------------------------------------------------------------------
# Targets (compression_spec/cost_model.md)
# ---------------------------------------------------------------------------
CPU_HZ = 480e6          # STM32H745 Cortex-M7 core clock
N_CH = 128              # the device's channel count (2x RHD2164)

# Two operating regimes the node must serve. cyc/sample-channel = the compute
# budget the encoder has per (sample, channel) pair before it can't keep up.
REGIMES = {
    "semg":   dict(fs=2048,  n_ch=N_CH),   # HD-sEMG  ->  ~1831 cyc/sample-ch (roomy)
    "neural": dict(fs=30000, n_ch=N_CH),   # neural   ->  ~125  cyc/sample-ch (tight)
}

# On-chip SRAM the codec's *persistent* state may occupy. The STM32H745 has
# 512 KiB AXI SRAM + more tightly-coupled; budget half of AXI to leave room for
# framing/DMA buffers and the app.
SRAM_LIMIT = 256 * 1024  # bytes

# Look-ahead beyond this many samples is treated as "needs the whole recording"
# -> not streaming -> disqualified. One block of a few hundred samples is fine;
# a whole-file second pass is not.
MAX_LOOKAHEAD = 4096     # samples

# Cortex-M7 is dual-issue and single-cycle for most integer ALU/MAC ops. We do
# not model pipeline effects; a flat cycles-per-op with a small overhead factor
# is enough to separate "clearly fits" from "clearly doesn't". This is an
# ESTIMATE, deliberately conservative (>1), and is the one knob a reviewer can
# argue with.
CYC_PER_OP = 1.2


def cyc_budget(fs, n_ch):
    """Cycles available per sample-channel at (fs, n_ch) on the M7."""
    return CPU_HZ / (fs * n_ch)


SEMG_BUDGET = cyc_budget(**REGIMES["semg"])      # ~1831
NEURAL_BUDGET = cyc_budget(**REGIMES["neural"])  # ~125


# ---------------------------------------------------------------------------
# Per-codec metadata (filled in by the registry) and the score it produces
# ---------------------------------------------------------------------------
@dataclass
class CodecMeta:
    """The embeddability inputs a codec must declare. Numbers are per
    sample-channel unless noted; describe the *streaming/embeddable* realization,
    not an offline software convenience (note any gap in `notes`)."""
    integer_only: bool           # int/fixed-point only? float => FPGA-disqualified
    enc_ops: float               # ALU/MAC ops per sample-ch, ENCODE (runs on-node)
    dec_ops: float               # ALU/MAC ops per sample-ch, DECODE (off-node, recorded)
    state_bytes_per_ch: int      # persistent state bytes per channel
    causal: bool                 # predictor uses only past samples?
    lookahead_samples: float     # extra future samples needed (0 = pure streaming)
    block_size: int              # bounded working block (samples)
    notes: str = ""


@dataclass
class CostScore:
    embedded_ok: bool
    neural_ok: bool              # also fits the tight 30 kS/s neural budget?
    cost: float                  # continuous, lower = cheaper (fraction of budget)
    enc_cycles: float            # estimated encode cycles / sample-ch
    dec_cycles: float
    sram_bytes: int              # persistent state across all channels
    reasons: list = field(default_factory=list)   # why the gate failed (if it did)

    def as_row(self):
        d = asdict(self)
        d["reasons"] = "; ".join(self.reasons)
        return d


def score(meta: CodecMeta, n_ch: int = N_CH) -> CostScore:
    """Turn a CodecMeta into an embedded_ok gate + a continuous Pareto cost.

    embedded_ok  = integer-only AND causal AND bounded look-ahead
                   AND encode fits the sEMG budget AND state fits SRAM.
    neural_ok    = embedded_ok AND encode also fits the tight neural budget.
    cost         = enc_cycles/SEMG_BUDGET + sram_bytes/SRAM_LIMIT
                   (dimensionless "fraction of the roomy budget consumed";
                   ties broken toward less compute and less memory).
    """
    enc_cycles = meta.enc_ops * CYC_PER_OP
    dec_cycles = meta.dec_ops * CYC_PER_OP
    sram_bytes = meta.state_bytes_per_ch * n_ch

    reasons = []
    if not meta.integer_only:
        reasons.append("uses float (FPGA-disqualified)")
    if not meta.causal:
        reasons.append("non-causal predictor")
    if meta.lookahead_samples > MAX_LOOKAHEAD:
        reasons.append(f"look-ahead {meta.lookahead_samples} > {MAX_LOOKAHEAD} "
                       f"(needs whole recording)")
    if enc_cycles > SEMG_BUDGET:
        reasons.append(f"encode {enc_cycles:.0f} cyc > sEMG budget "
                       f"{SEMG_BUDGET:.0f} cyc/sample-ch")
    if sram_bytes > SRAM_LIMIT:
        reasons.append(f"state {sram_bytes} B > SRAM budget {SRAM_LIMIT} B")

    embedded_ok = not reasons
    neural_ok = embedded_ok and enc_cycles <= NEURAL_BUDGET

    cost = enc_cycles / SEMG_BUDGET + sram_bytes / SRAM_LIMIT
    return CostScore(embedded_ok, neural_ok, cost, enc_cycles, dec_cycles,
                     sram_bytes, reasons)


def _demo():
    print("Embedded cost model  (STM32H745 Cortex-M7 @ 480 MHz, 128 ch)")
    print(f"  sEMG   budget = {SEMG_BUDGET:7.0f} cyc/sample-ch  (2048 Hz)")
    print(f"  neural budget = {NEURAL_BUDGET:7.0f} cyc/sample-ch  (30 kHz)")
    print(f"  SRAM budget   = {SRAM_LIMIT//1024} KiB persistent state")
    print(f"  cyc/op        = {CYC_PER_OP}  (estimate)\n")

    examples = {
        "LMS+Rice (order-8)": CodecMeta(
            integer_only=True, enc_ops=60, dec_ops=60, state_bytes_per_ch=40,
            causal=True, lookahead_samples=0, block_size=256),
        "float LPC (illustrative fail)": CodecMeta(
            integer_only=False, enc_ops=120, dec_ops=120, state_bytes_per_ch=64,
            causal=True, lookahead_samples=0, block_size=4096),
        "offline 2-pass (illustrative fail)": CodecMeta(
            integer_only=True, enc_ops=30, dec_ops=30, state_bytes_per_ch=16,
            causal=True, lookahead_samples=math.inf, block_size=0),
    }
    hdr = f"{'codec':<34}{'emb_ok':>7}{'neural':>7}{'cost':>8}{'enc_cyc':>9}"
    print(hdr); print("-" * len(hdr))
    for name, m in examples.items():
        s = score(m)
        print(f"{name:<34}{('OK' if s.embedded_ok else 'no'):>7}"
              f"{('OK' if s.neural_ok else '-'):>7}{s.cost:>8.3f}{s.enc_cycles:>9.0f}"
              + (f"   <- {'; '.join(s.reasons)}" if s.reasons else ""))


if __name__ == "__main__":
    _demo()
