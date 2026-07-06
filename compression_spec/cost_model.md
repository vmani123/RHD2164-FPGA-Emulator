# Embedded cost model — reference

Referenced by `../COMPRESSION_RESEARCH_AGENT_PROMPT.md` (non-negotiable #2, Stage 1). Implemented as `research/embedded_cost.py`. Its job: assign every codec a portability score so ranking reflects **what can actually ship on-node**. Ratio alone never decides — the search maximizes ratio **subject to `embedded_ok`**, and reports the **Pareto front (ratio vs. cost)**. (The existing `embedded_codec.py` candidates are the reference point for "clearly shippable.")

## Record / estimate per codec

- **Arithmetic class** — integer / fixed-point only? Float is heavily penalized and **disqualified for the FPGA target**.
- **Ops per sample-channel** — adds, muls, shifts, compares, table lookups. Report **encode and decode separately** (only encode runs on-node).
- **State memory** — bytes of persistent state **× n_channels** (predictor history, adaptation coefficients, Rice-k, contexts). Must fit STM32H745 SRAM and be plausible in Spartan-7 BRAM.
- **Latency / look-ahead** — causal? bounded block size? A codec needing the whole recording is **disqualified** for streaming.

## Targets to score against

- **STM32H745** (Cortex-M7 @ 480 MHz) — budget = cycles per sample-channel available:
  - sEMG **2 kS/s × 128 ch** ≈ **1875 cyc/sample-ch** (roomy).
  - neural **30 kS/s × 128 ch** ≈ **125 cyc/sample-ch** (tight — flag anything that can't fit).
- **Arty S7-25** (Spartan-7 XC7S25) — rough LUT / DSP / BRAM feasibility; note whether the predictor maps to DSP slices. (Playback BRAM is already ~16/45 BRAM36k for the 256-sample loop, per the harness README — leave headroom.)

## Output

- `embedded_ok` — a boolean **hard gate** (fails → cannot be ranked a win).
- a continuous **cost** used for the Pareto front.

Document the scoring formula in `embedded_cost.py` so a reviewer can reproduce any codec's score.
