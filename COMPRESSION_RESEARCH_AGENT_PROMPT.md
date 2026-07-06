# Claude Code task — Automated **lossless** compression research loop (RHD2164 neural / HD-EMG)

A self-driving, data-driven search for the best **lossless on-node** compressor for 128-ch RHD2164 data — one that **beats per-channel FLAC / WavPack / mtscomp by exploiting cross-channel spatial correlation**, while staying implementable on an STM32H745 or Spartan-7 at real-time rates. Success is **not** beating Shannon (impossible — independent per-channel noise caps lossless at ~3–3.5×). Success is: **beat per-channel FLAC on real grids, at a fraction of the compute, proven bit-exact.**

Detailed menus live in `compression_spec/`: **`candidates.md`** (codec list), **`datasets.md`** (corpus), **`cost_model.md`** (embedded budget). Read them when the stage calls for them.

## Current state (already built in this repo — reuse, do NOT rebuild)

The 5-stage harness in `COMPRESSION_HARNESS_README.md` is **done and green**. Extend it; never duplicate it. Already present:

- Emulator RTL (`rtl/*.sv`) + `sim/run_sim.sh` (153 transfers, 0 errors), time-varying BRAM playback.
- `host_tools/`: `gen_neural_mem.py` (synthetic simple + neural), `load_wfdb.py` (real PhysioNet data), `bench_lossless.py` (FLAC/WavPack/mtscomp/zstd/LZMA/gzip + embedded), `embedded_codec.py` (delta+Rice, LMS+Rice order-8, `+xchan`), `emu_verify.py`, `verify_compressed.py` — all bit-exact with `--selftest`.
- Real Hyser 128-ch HD-sEMG in `raw_data/` + `sim_data/{ground_truth,real_hyser}.npy`; a working `.venv` (numpy, mtscomp, zstandard); Claude Code here already has PhysioNet network access.

**Starting bar (real Hyser, already measured):** best embedded `LMS+Rice+xchan` = **1.43×**, beating FLAC 0.97×, WavPack 1.33×, zstd 1.44× / mtscomp 1.41×, with **+11% cross-channel gain**. Synthetic neural (corr 0.6) = **2.57×**; xchan gain scales 0 → +20% with `--spatial-corr`.

**This loop's job is the search layer on top:** the agent orchestration (below), a formal `embedded_cost.py` + Pareto ranking, a codec registry, **broader candidates** (JPEG-LS/LOCO-I 2D, range/arithmetic coder, NLMS/higher-order, integer inter-channel decorrelation, better cross-channel topologies), and **more datasets** (CapgMyo 8×16=128 geometry-matched, CEMHSEY 320-ch, a broadband-neural set) — all reusing `bench_lossless.py` + `embedded_codec.py`, not replacing them.

## Non-negotiables (read first — never violate)

1. **Lossless only.** `decode(encode(x)) == x` bit-for-bit, asserted, or the run fails loudly. No lossy / near-lossless / feature-extraction anywhere.
2. **Embedded feasibility is a hard gate.** Nothing counts as a "win" unless `embedded_ok` (see `compression_spec/cost_model.md`). Optimize ratio **subject to** cost; report the **Pareto front**, never ratio alone.
3. **Real data decides.** Synthetic is for sweeps only. Any lossless ratio > ~6× on realistic broadband ⇒ degenerate data or a leak — **stop and report**.
4. **Agents propose, the harness disposes.** No performance number ever comes from an agent's reasoning — only from `bench_lossless.py` + the bit-exact verifier, enforced by a `PostToolUse` hook.
5. **Never touch the emulator SPI / DDR / timing RTL.** Keep `sim/run_sim.sh` green (153 transfers, 0 errors) after any change; paste the output.
6. **Determinism.** Everything reproducible from `--seed`; pin dataset hashes.
7. **Stop at the gates.** Human review after Stage 0 and Stage 2.

## Context to read first

`COMPRESSION_HARNESS_README.md` (what's built + the ratio bar), `host_tools/{bench_lossless,embedded_codec,gen_neural_mem,load_wfdb,emu_verify,verify_compressed}.py`, `firmware_patches/hdemg_frame.h`, `docs/SPEC.md`.

**Data contract:** `int16` two's-complement, zero-mean, `[channels, samples]`. Combined 128-ch order: chip0_A(0–31), chip0_B(32–63), chip1_A(64–95), chip1_B(96–127). Frame (LE): `u16 magic=0xA55A, u8 type(0=RAW16,1=RMS16,2=COMPRESSED), u8 chip_id, u32 seq, u32 t_stm, u16 n_ch, i16 payload[n_ch]`. Default grid 8×16 for 128 ch (real datasets carry their own).

## Agent orchestration loop (how it runs)

An **orchestrator agent** runs a think → code → measure → analyze → iterate loop; deterministic tools are ground truth. Subagents live in `.claude/agents/*.md`, spawn via the Agent tool, run in isolated contexts, and parallelize. Drive it headless (`claude -p`) or via the Claude Agent SDK; interactive for debugging.

**Cycle:** read state (`LEADERBOARD.md`, latest CSV, last ablations) → form **one** testable hypothesis → `implementer` adds/edits **exactly one** codec in `embedded_codec.py`/the registry (must pass its round-trip self-test) → run `bench_lossless.py` (tool) → `analyst` attributes the change and updates the leaderboard → keep/revert, log a replayable experiment record, iterate or gate.

**Subagents (`.claude/agents/`):** `surveyor` (papers → cost-filtered candidates → `SURVEY.md`; cheap model, web/read only) · `implementer` (one codec + self-test; code tools, no web) · `analyst` (CSV → "what mattered" + next hypotheses; read-only, stronger model) · `verifier` (independent bit-exact + cost audit before any leaderboard promotion). Orchestrator runs a stronger model; workers route cheaper where mechanical. **Terminate** on `max_turns` or "no ratio gain over the last N iterations *and* Pareto front unchanged." Never promote a watch-list method without approval.

## Stages (goal → done)

- **Stage 0 — Orient.** Inventory the existing harness (above); write `research/00_STATE.md` recording the current ratio bar from a fresh `bench_lossless.py` run, what exists vs. what the search adds, and assumptions. **Stop for review.**
- **Stage 1 — Registry + cost model.** Wrap the existing codecs behind `research/registry.py` (uniform `encode`/`decode` + metadata) and add `embedded_cost.py`; seed new candidates from `candidates.md`. *Done:* every codec passes a random-int16 round-trip; each has a cost score.
- **Stage 2 — Corpus.** Extend `load_wfdb.py`/add `research/datasets.py` per `datasets.md` (Hyser already present; add CapgMyo, CEMHSEY, a neural set), normalized + grid maps + manifest; report the spatial-correlation ceiling per set. **Stop for review.**
- **Stage 3 — Benchmark.** Extend `bench_lossless.py`: per codec × dataset (+ sweeps) report ratio, encode/decode MB/s, `embedded_ok`, bit-exact assert, %-of-FLAC, cross-channel gain → CSV + table. *Done:* all codecs round-trip on real data; cross-channel gain positive where correlation is high; sanity gate holds.
- **Stage 4 — Search + analysis.** `research/search.py` hill-climbs the design space (predictor family/order, k-window, Rice vs. range, channel-pairing topology, transform, block size) maximizing ratio s.t. `embedded_ok`; ablations attribute gain; emit Pareto front + "what mattered"; propose next candidates (watch-list gated). *Done:* ranked leaderboard on real data beating the 1.43× / 2.57× bar (or an honest account of why not); best **embeddable** codec named, cross-channel gain isolated.
- **Stage 5 — Survey.** `research/survey.py` → `SURVEY.md`: cost-filtered ranked candidates with embeddability verdicts. Proposes only.
- **Stage 6 — Report.** Maintain `research/LEADERBOARD.md`: best per category, Pareto front, per-dataset ratios, cross-channel gain, and the one codec to port to firmware/RTL next.

## Deliverables

- `research/{embedded_cost,registry,datasets,search,survey}.py`; `bench_lossless.py` / `embedded_codec.py` extensions.
- Agent wiring: `.claude/agents/{orchestrator,surveyor,implementer,analyst,verifier}.md`, the `PostToolUse` verifier hook, an optional headless runner.
- `research/00_STATE.md`, `SURVEY.md`, `LEADERBOARD.md`, an `experiments/` log, `results/*.csv`, and a short `research/README.md`.
- All codecs bit-exact; nothing ranked a win unless `embedded_ok`; every headline number reproduced on **real** (not synthetic) data; `sim/run_sim.sh` still green.

**Start with Stage 0 and stop for review.**
