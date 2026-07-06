# Lossless Compression Test Harness

Software + emulator foundation for real-time **lossless** compression of the
128-channel RHD2164 stream (RHD2164 emulator → STM32H745 → ESP32 → PC). It makes
the emulated data realistic, sets the ratio bar against the offline standards
(FLAC / WavPack / mtscomp), trials hardware-implementable candidates, and proves
the compressed path is bit-exact — all before any RTL/firmware compressor exists.

**Lossless only.** Every codec must round-trip bit-exact or the run fails loudly.

## Pipeline

```
gen_neural_mem.py ──► mem/*.mem (short segment, FPGA plays it)  ──► emulator ─┐
      │                                                                        │
      └──► sim_data/ground_truth.npy (full length, PC only) ──► bench_lossless.py
                                                              └► verify_compressed.py
```

The emulator plays a **time-varying** segment from BRAM (sample-major, advanced
by the STM32's `CONVERT(0)` sweep) and loops it. The full-length ground truth
lives only on the PC for benchmarking. `sim/run_sim.sh` stays green (153
transfers, 0 errors) with the new time-varying RTL.

## Run the whole loop

```bash
# 0. one-time: python deps (numpy, mtscomp, zstandard) + flac/wavpack CLIs
python3 -m venv .venv && ./.venv/bin/pip install numpy mtscomp zstandard tqdm
#    brew install flac wavpack        (or apt-get)

PY=./.venv/bin/python
export PYTHONPATH=host_tools

# 1a. REAL neural data (recommended): Hyser 256-ch HD-sEMG from PhysioNet
$PY host_tools/load_wfdb.py --download --channels 128 --record raw_data/hyser
# 1b. or synthetic: realistic neural model / simple deterministic stream
$PY host_tools/gen_neural_mem.py --mode neural --seconds 1.0 --spatial-corr 0.6 --report
$PY host_tools/gen_neural_mem.py --mode simple --seconds 1.0        # sinusoids, no neural model

# 2. emulator self-check (RTL) — must stay green
./sim/run_sim.sh

# 3. RAW path is bit-exact (hardware-free)
$PY host_tools/emu_verify.py --selftest --mem-dir mem --combined

# 4. benchmark the codecs (sets the bar, trials candidates)
$PY host_tools/bench_lossless.py --gt sim_data/ground_truth.npy --cols 16
$PY host_tools/bench_lossless.py --sweep spatial-corr --values 0,0.3,0.6,0.9 --csv sweep.csv

# 5. COMPRESSED path is bit-exact end-to-end (hardware-free)
$PY host_tools/verify_compressed.py --selftest --gt sim_data/ground_truth.npy
```

## Data generator (`gen_neural_mem.py`)

Two modes: `simple` (deterministic per-channel sinusoids) and `neural`
(independent Gaussian noise floor + Poisson spike/MUAP events with spatial
falloff and propagation + a **shared broadband common-mode** field). Signal
scaling follows the RHD2164 datasheet (0.195 µV/LSB, 2.4 µVrms ≈ 12 counts).
`--report` prints the spatial correlation ceiling (rises with `--spatial-corr`).

Key knobs: `--mode --seconds --fs --firing-rate-hz --noise-rms --spatial-corr
--prop-velocity --grid RxC --spike-amp --seed --mem-samples`.

## Codecs

**Reference bar:** FLAC, WavPack (per-channel), mtscomp, zstd, LZMA, gzip.
**Embedded candidates** (`embedded_codec.py`, hardware-portable, bit-exact):
- `delta+Rice` — order-1 DPCM + adaptive Golomb-Rice
- `LMS+Rice` — backward-adaptive sign-sign LMS (order 8) + adaptive Rice
- `+xchan` — optional cross-channel front-end: subtract a physical grid neighbour
  with an **optimal per-channel gain** (tiny int16 side-info), before the
  temporal predictor. This is the lever to beat per-channel codecs.

## Results

**REAL HD-sEMG** (Hyser `1dof_raw_finger1_sample1`, 128 ch, 15000 samples).
Neighbour correlation is high (mean |corr| 0.90, R² 0.92) as real surface EMG is.
Raw high-gain EMG is genuinely hard to compress losslessly, so ratios are modest
and honest:

| codec            | real HD-sEMG | synthetic neural (corr 0.6) |
|------------------|-------------:|----------------------------:|
| FLAC             | 0.97× | 1.42× |
| WavPack          | 1.33× | 2.34× |
| mtscomp          | 1.41× | 1.71× |
| zstd-19          | 1.44× | 2.00× |
| LZMA             | 1.67× | 2.16× |
| gzip-9           | 1.38× | 1.80× |
| delta+Rice       | 1.31× | 2.19× |
| **LMS+Rice**     | 1.28× | 2.34× |
| **LMS+Rice +xchan** | **1.43×** | **2.57×** |

On real data the embedded `LMS+Rice+xchan` (1.43×) beats FLAC (0.97× — it slightly
*expands* this data), WavPack (1.33×), zstd/mtscomp, with **+11% cross-channel
gain**. Every codec round-trips bit-exact.

**Synthetic cross-channel gain vs `--spatial-corr`:** 0.0 → −0.1%, 0.3 → +1.5%,
0.6 → +10%, 0.9 → +20% — lets you probe the mechanism under a controlled knob.

## Honest notes

- **Ratios are realistic (~1.4–2.6×), not inflated.** The independent per-channel
  noise floor caps the ratio; the embedded Rice coder sits within ~0.2 bits of
  the empirical entropy.
- **FLAC underperforms here** because this data is broadband-noise-dominated and
  FLAC's LPC targets correlated audio. On data with more temporal structure FLAC
  is more competitive. WavPack is the fairest per-channel reference (2.34×).
- **Cross-channel only helps losslessly when a *shared, temporally-unpredictable*
  component exists** (common-mode) — a per-channel temporal predictor already
  removes shared *low-frequency* (LFP) content. Variance-R² overstates the
  lossless ceiling on spiky data; the benchmark reports the *achieved* gain.
- **FPGA playback is BRAM-limited** to a short looped segment (~256 samples ≈
  16/45 BRAM36k on XC7S25); the full signal is PC-side only.

## Files

```
host_tools/load_wfdb.py          load REAL data (PhysioNet WFDB, e.g. Hyser HD-sEMG)
host_tools/gen_neural_mem.py     synthetic data generator (simple + neural)
host_tools/embedded_codec.py     delta/LMS + Rice + cross-channel (bit-exact)
host_tools/bench_lossless.py     benchmark + sweeps + CSV
host_tools/emu_verify.py         RAW path verifier (+ --selftest)
host_tools/verify_compressed.py  COMPRESSED path verifier (+ --selftest)
firmware_patches/hdemg_frame.h   wire format (adds type=2 COMPRESSED)
sim_data/ground_truth.npy        full-length int16 [channels, samples]
mem/*.mem                        FPGA playback segment (sample-major)
```
