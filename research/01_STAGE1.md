# Stage 1 — Registry + cost model

Wraps the existing, already-verified codecs behind one uniform interface and adds
the embedded-feasibility cost model that turns every ranking into a ratio-vs-cost
question, not a ratio-alone question. Built per Stage 1 of
`../COMPRESSION_RESEARCH_AGENT_PROMPT.md`, reusing `host_tools/embedded_codec.py`
(never rebuilding it). **Not a human gate** — Stage 2 (corpus) is the next gate.

_Date: 2026-07-06 · branch `compression-wip` · all numbers from `registry.py --selftest`._

---

## Deliverables

- **`research/embedded_cost.py`** — the `embedded_ok` hard gate + a continuous
  `cost` for the Pareto front. Scoring is explicit and reproducible by hand:
  - Targets (STM32H745 Cortex-M7 @ 480 MHz, 128 ch): sEMG budget **1831
    cyc/sample-ch** (2048 Hz), neural budget **125 cyc/sample-ch** (30 kHz),
    **256 KiB** persistent-state SRAM, `cyc/op = 1.2` (documented estimate).
  - `embedded_ok = integer-only AND causal AND look-ahead ≤ 4096 AND encode fits
    the sEMG budget AND state fits SRAM.` `neural_ok` additionally requires the
    tight 125-cyc budget.
  - `cost = enc_cycles/sEMG_budget + sram_bytes/SRAM_limit` (fraction of the roomy
    budget consumed; lower = cheaper).
- **`research/registry.py`** — uniform `encode(x, cols)`/`decode(blob)` + `meta`
  (feasibility inputs) + `cost` (score) for every codec, with a `--selftest` that
  round-trips each on random int16 and prints ratio + cost. Registered:
  - `delta+Rice`, `LMS+Rice`, `delta+Rice+xchan`, `LMS+Rice+xchan` — **wrapped**
    from `embedded_codec.py` (not re-implemented).
  - `fixed0-3+Rice` — **new seeded candidate** from `compression_spec/candidates.md`:
    FLAC's four fixed polynomial predictors (orders 0–3), best-per-block order
    selection, sharing embedded_codec's adaptive Golomb-Rice back-end. Integer-exact
    and causal (samples before t=0 treated as 0 in both directions); one order byte
    per block of side-info; decoder inverts by integrating the p-th finite
    difference block-by-block from the boundary state.

## Result (random int16, seed 0 — Stage 1 "done" criterion)

Full output: `results/01_stage1_registry.txt`.

| codec | ratio | round-trip | embedded_ok | neural_ok | cost |
|---|---:|:--:|:--:|:--:|---:|
| delta+Rice | 2.52× | OK | OK | OK | 0.008 |
| LMS+Rice | 2.73× | OK | OK | OK | 0.052 |
| delta+Rice+xchan | 2.52× | OK | OK | OK | 0.013 |
| LMS+Rice+xchan | 2.72× | OK | OK | OK | 0.057 |
| **fixed0-3+Rice** | **2.72×** | OK | OK | OK | **0.019** |

- **Every codec passes a random-int16 bit-exact round-trip and carries a cost
  score** — the Stage 1 done criterion is met.
- The new **`fixed0-3+Rice`** is an immediately useful Pareto point: it matches
  `LMS+Rice`'s ratio (2.72×) at **~⅓ the compute cost** (0.019 vs 0.057), because
  fixed-difference predictors are far cheaper than an 8-tap adaptive LMS. Whether
  that holds on *real* data (vs. this correlated-noise synthetic) is a Stage 3
  question — this table is the synthetic round-trip gate, not a headline.
- `cost` is dominated by encode compute here; all state fits SRAM with huge margin.

## Enforcement verified

`.claude/hooks/verify_codec.py` (PostToolUse on `Edit|Write|MultiEdit`) re-runs
`registry.py --selftest` on every codec/registry edit. Confirmed **both**
directions with the live `.venv`: a deliberately lossy decode → **exit 2 (block)**;
the corrected codec → **exit 0 (allow)**. Non-negotiable #4 ("agents propose, the
harness disposes") is live for the registry as well as `embedded_codec.py`.

## Notes / honesty

- **`.venv` was gitignored** and lost with the previous container; recreated it
  (`numpy`, `zstandard`) so the verifier hook and headless runner work. FLAC/WavPack
  CLIs and `mtscomp` are **not** installed in this container yet — they matter for
  the Stage 3 reference bar on real data, not for Stage 1's round-trip gate.
- **`+xchan` look-ahead:** the software impl derives each channel's optimal `beta`
  over the whole signal (offline). The cost model scores the *embeddable*
  realization (per-block `beta`, look-ahead = one block); this gap is flagged in
  each xchan codec's `meta.notes` and is a Stage 4 implementation item.
- **Emulator RTL untouched** (non-negotiable #5). `iverilog` is not installed in
  this container, so `sim/run_sim.sh` was not re-run here; no RTL/SPI/DDR/timing
  file changed in Stage 1, so the last green result (153 transfers, 0 errors)
  stands.

## Stage 1 done → Stage 2 (corpus) is the next human gate

Registry + cost model built; every codec round-trips bit-exact with a cost score;
one new candidate seeded; enforcement verified. Next: `research/datasets.py` adds
CapgMyo (8×16, geometry-matched), CEMHSEY (320-ch), and a broadband-neural set
with hashed manifests, then **stop for review**.
