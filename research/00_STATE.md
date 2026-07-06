# Stage 0 — Orient: current state of the lossless-compression search

Records the harness inventory, the **current ratio bar** from a fresh
`bench_lossless.py` run, what exists vs. what this research loop adds, and the
assumptions the search rests on. Written per Stage 0 of
`COMPRESSION_RESEARCH_AGENT_PROMPT.md`. **This is a human-review gate — stop here.**

_Date: 2026-07-06 · branch `main` · all numbers reproduced on this machine._

---

## 1. The current bar (fresh run, REAL data)

Command (ground truth — not reasoning):

```
PYTHONPATH=host_tools ./.venv/bin/python host_tools/bench_lossless.py \
    --gt sim_data/ground_truth.npy --cols 16 --bench-samples 15000
```

`sim_data/ground_truth.npy` currently holds the **real Hyser** HD-sEMG sample
(128 ch × 51 200 @ 2048 Hz, PhysioNet `1dof_raw_finger1_sample1`; benched on the
first 15 000 samples = 3.84 MB raw). Full output saved to
`results/00_stage0_real_hyser.txt`.

| codec               | ratio | comp KB | enc MB/s | dec MB/s | lossless |
|---------------------|------:|--------:|---------:|---------:|:--------:|
| flac                | 0.97× |  3852.5 |      3.0 |      3.3 | OK |
| wavpack             | 1.33× |  2824.9 |      2.9 |      inf | OK |
| mtscomp             | 1.41× |  2651.1 |     25.4 |    158.3 | OK |
| zstd-19             | 1.44× |  2603.9 |      8.8 |    416.7 | OK |
| lzma                | 1.67× |  2247.3 |      5.5 |     35.6 | OK |
| gzip-9              | 1.38× |  2721.7 |     37.5 |    226.5 | OK |
| delta+Rice          | 1.31× |  2864.9 |      6.0 |      1.9 | OK |
| LMS+Rice            | 1.28× |  2925.5 |      4.9 |      1.7 | OK |
| **LMS+Rice +xchan** | **1.42×** | 2641.7 | 5.2 | 1.8 | OK |
| delta+Rice +xchan   | 1.41× |  2662.0 |      6.4 |      2.0 | OK |

- **Best embeddable today: `LMS+Rice +xchan` = 1.42×** (README quoted 1.43× — the
  0.01× drift is run/cap noise; treat **1.42×** as the live bar).
- **Cross-channel gain (LMS): +10.7%** (1.28× → 1.42×) — the headline lever.
- `LMS+Rice` reaches **132% of FLAC's ratio** (FLAC *expands* this high-gain EMG).
- Every codec round-trips **bit-exact** (all `OK`). Max ratio 1.67× ≪ the 6×
  sanity ceiling — data is honest broadband EMG, not degenerate.
- **Honest caveat:** the best generic reference here is `lzma` (1.67×) and `zstd-19`
  (1.44×) — both **not embeddable**. The embedded contender's job is to beat the
  *embeddable/per-channel* references (FLAC 0.97×, WavPack 1.33×, mtscomp 1.41×) at
  a fraction of the compute, which it does. It does **not** yet beat offline lzma —
  that gap is a target for the search, not a claim of victory.

Synthetic reference (sweeps only, never a headline): `LMS+Rice +xchan` = **2.57×**
at `--spatial-corr 0.6`; xchan gain scales 0 → +20% as spatial correlation rises.

**Emulator still green** (non-negotiable #5): `./sim/run_sim.sh` →
`0 error(s) over 153 checked transfers · ALL CHECKS PASSED` (exit 0).

---

## 2. What already exists (reuse — do NOT rebuild)

- **Emulator RTL** (`rtl/*.sv`) + `sim/run_sim.sh` — 153 transfers, 0 errors,
  time-varying BRAM playback. **Off-limits** (SPI/DDR/timing).
- **`host_tools/`**
  - `gen_neural_mem.py` — synthetic generator (simple + neural), the **only**
    source with a `--spatial-corr` knob (sweeps only).
  - `load_wfdb.py` — real PhysioNet WFDB loader (Hyser present; PhysioNet reachable).
  - `bench_lossless.py` — the benchmark: FLAC/WavPack/mtscomp/zstd/LZMA/gzip
    reference bar + the embedded candidates, with bit-exact asserts, sweeps, CSV.
  - `embedded_codec.py` — the embeddable codecs: `delta+Rice` (order-1 DPCM +
    adaptive Golomb-Rice), `LMS+Rice` (sign-sign LMS order-8), and the `+xchan`
    cross-channel front-end (subtract a grid neighbour × optimal per-channel int16
    gain). Integer-exact, streaming-legal. `python embedded_codec.py` self-tests.
  - `emu_verify.py` (RAW path), `verify_compressed.py` (COMPRESSED path) — both
    bit-exact with `--selftest`.
- **Data:** real Hyser in `raw_data/` + `sim_data/{ground_truth,real_hyser}.npy`
  (both = Hyser now); `sim_data/params.json` (grid labels ED/EP 8×8×2, fs 2048).
- **`.venv`** (numpy, mtscomp, zstandard); FLAC/WavPack CLIs present.
- Wire format `firmware_patches/hdemg_frame.h` (adds `type=2 COMPRESSED`).

**Data contract:** `int16` two's-complement, zero-mean, `[channels, samples]`;
combined 128-ch order chip0_A/B, chip1_A/B; default grid 8×16.

---

## 3. What THIS loop adds (the search layer on top)

Per the prompt's Stages 1–6 — all **reusing** `bench_lossless.py` +
`embedded_codec.py`, never replacing them:

- **Stage 1** — `research/registry.py` (uniform `encode`/`decode` + metadata
  wrapping the existing codecs) + `research/embedded_cost.py` (the `embedded_ok`
  hard gate + continuous cost for the Pareto front); seed candidates from
  `compression_spec/candidates.md`.
- **Stage 2** — `research/datasets.py`: add CapgMyo (8×16, geometry-matched),
  CEMHSEY (320-ch), a broadband-neural set; normalized + grid maps + hashed
  manifest; report the per-set spatial-correlation ceiling. **(Gate.)**
- **Stage 3** — extend `bench_lossless.py`: per codec × dataset (+ sweeps) →
  ratio, enc/dec MB/s, `embedded_ok`, bit-exact assert, %-of-FLAC, xchan gain → CSV.
- **Stage 4** — `research/search.py` hill-climbs the design space (predictor
  family/order, k-window, Rice vs. range, channel-pairing topology, transform,
  block size) maximizing ratio s.t. `embedded_ok`; ablations + Pareto front.
- **Stage 5** — `research/survey.py` → `SURVEY.md` (cost-filtered ranked
  candidates; proposes only).
- **Stage 6** — `research/LEADERBOARD.md` (best per category, Pareto, per-dataset
  ratios, xchan gain, the one codec to port next).

**New candidate codecs to try** (from `candidates.md`): FLAC's 4 fixed polynomial
predictors (pick-best-per-block), NLMS / higher-order & leaky LMS + Rice,
range/arithmetic coder on residuals (vs. Rice head-to-head), JPEG-LS/LOCO-I 2D over
the grid×time image, and richer cross-channel topologies (multi-neighbour /
best-partner selection, integer inter-channel decorrelation à la MPEG-4 ALS / MLP).

---

## 4. Agent wiring built in Stage 0 (ready, not yet run)

- `.claude/agents/{orchestrator,surveyor,implementer,analyst,verifier}.md` — the
  think→code→measure→analyze→verify loop; orchestrator/analyst on Opus, surveyor on
  Haiku (cheap web/read), implementer/verifier on Sonnet.
- `.claude/hooks/verify_codec.py` + a **PostToolUse** hook (`Edit|Write|MultiEdit`)
  in `.claude/settings.local.json` — re-runs the bit-exact self-test on every edit
  to `embedded_codec.py` / `research/registry.py` and **blocks** (exit 2) on any
  non-bit-exact round-trip. This enforces non-negotiable #4 ("agents propose, the
  harness disposes"). Verified working in both directions: OK edit → allow; broken
  codec → block with feedback.
- `run_research.sh` — headless driver (`claude -p --agents orchestrator
  --max-turns N`); respects the Stage 0 / Stage 2 human gates.

---

## 5. Assumptions & guardrails

1. **Lossless only** — `decode(encode(x)) == x` bit-for-bit, or fail loudly.
2. **Embedded feasibility is a hard gate** — nothing is a "win" unless
   `embedded_ok`; rank on the ratio-vs-cost **Pareto front**, never ratio alone.
3. **Real data decides.** Synthetic is for sweeps only. Any lossless ratio > ~6× on
   realistic broadband ⇒ leak/degenerate → stop and report.
4. **No number from an agent's reasoning** — only from `bench_lossless.py` + the
   bit-exact verifiers, enforced by the PostToolUse hook.
5. **Never touch emulator SPI/DDR/timing RTL**; keep `sim/run_sim.sh` green.
6. **Determinism** — reproducible from `--seed`; pin dataset hashes (Stage 2).
7. **Success ≠ beating Shannon.** Independent per-channel noise caps lossless at
   ~3–3.5×. Success = beat per-channel FLAC on real grids at a fraction of the
   compute, proven bit-exact — and, as a stretch, close the gap to offline lzma.
8. Current `ground_truth.npy` == the Hyser sample; Stage 2 will add a manifest so
   the corpus is hashed and reproducible rather than a single overwritten file.

---

## Stage 0 done → STOP for review

Harness inventoried, current bar reproduced on real data, agent wiring +
enforcement hook built and tested, emulator still green. **Awaiting human review
before Stage 1 (registry + cost model).**
