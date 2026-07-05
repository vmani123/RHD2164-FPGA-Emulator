#!/usr/bin/env python3
"""
gen_neural_mem.py  -  Time-varying data generator for the RHD2164 emulator rig.

Produces two things from one signal model:
  1. sim_data/ground_truth.npy   -  FULL-length int16 [channels, samples], the
     benchmark reference (lives only on the PC).
  2. mem/{chip0_A,chip0_B,chip1_A,chip1_B}.mem  -  a SHORT representative segment
     the FPGA plays out of BRAM (sample-major: addr = sample*32 + channel).

Two playback modes (--mode):
  * simple  : deterministic per-channel sinusoids. No neural model, no RNG in the
              signal itself -> trivially reproducible, easy to eyeball/verify.
  * neural  : per-channel independent Gaussian noise floor (the incompressible
              cap on lossless ratio) + Poisson spike/MUAP events that spread over
              physically-neighbouring electrodes with distance falloff and an
              optional propagation delay across the grid. This spatial structure
              is what a cross-channel predictor can exploit to beat FLAC.

Signal scaling is anchored to the RHD2164 datasheet: 16-bit ADC, 0.195 uV/LSB
referred to input, +/-5 mV range, 2.4 uVrms typical noise (~= 12 ADC counts).
Values are 16-bit two's-complement, zero-mean (matches RHD with twoscomp=1).

The physical electrode grid is a HOST-SIDE modelling choice (the chip itself is
just 64 unipolar inputs with no geometry). Default 8x16 for 128 channels; the
combined channel index g maps to grid (row, col) = (g // cols, g % cols).

Channel/.mem layout matches emu_verify.py / hdemg_frame.h combined order:
    g 0..31   -> chip0_A      g 32..63  -> chip0_B
    g 64..95  -> chip1_A      g 96..127 -> chip1_B
    within a half, channel c (0..31) lives at mem word (sample*32 + c).

Usage:
    python3 gen_neural_mem.py --mode neural --seconds 1.0 --spatial-corr 0.7 --report
    python3 gen_neural_mem.py --mode simple --seconds 0.5
    python3 gen_neural_mem.py --load recording.npy      # play a real [C,N] int16 array
"""
import argparse, json, os, sys
import numpy as np

LSB_UV = 0.195   # datasheet: ADC step referred to amplifier input (uV/LSB)
CH_PER_HALF = 32
HALVES = [("chip0_A", 0), ("chip0_B", 32), ("chip1_A", 64), ("chip1_B", 96)]


# ---------------------------------------------------------------------------
# Grid geometry
# ---------------------------------------------------------------------------
def parse_grid(s, channels):
    r, c = (int(x) for x in s.lower().split("x"))
    if r * c < channels:
        sys.exit(f"grid {r}x{c}={r*c} too small for {channels} channels")
    return r, c


def channel_rowcol(channels, cols):
    """Combined channel index -> (row, col), row-major."""
    idx = np.arange(channels)
    return idx // cols, idx % cols


# ---------------------------------------------------------------------------
# Simple mode: deterministic per-channel sinusoids (no RNG, no neural model)
# ---------------------------------------------------------------------------
def gen_simple(channels, n, fs, amp_counts=800.0):
    t = np.arange(n) / fs
    sig = np.zeros((channels, n), dtype=np.float64)
    for ch in range(channels):
        # two channel-dependent tones, a few Hz..a few hundred Hz, distinct phase
        f1 = 5.0 + 2.0 * ch
        f2 = 13.0 + 1.0 * ch
        ph = (ch % 16) * (np.pi / 8.0)
        sig[ch] = amp_counts * (0.7 * np.sin(2 * np.pi * f1 * t + ph)
                                + 0.3 * np.sin(2 * np.pi * f2 * t))
    return sig


# ---------------------------------------------------------------------------
# Neural mode
# ---------------------------------------------------------------------------
def spike_templates(fs):
    """A small library of normalized (peak |amp| = 1) extracellular spike shapes."""
    ms = fs / 1000.0
    tmpls = []
    # biphasic: sharp negative trough then slower positive rebound (classic AP)
    L = int(2.0 * ms)
    x = np.linspace(-3, 5, L)
    bi = -np.exp(-((x + 0.5) ** 2)) + 0.45 * np.exp(-((x - 1.5) ** 2) / 2.0)
    tmpls.append(bi / np.max(np.abs(bi)))
    # triphasic (MUAP-like)
    L = int(2.5 * ms)
    x = np.linspace(-3, 6, L)
    tri = (0.4 * np.exp(-((x + 1.0) ** 2))
           - np.exp(-((x) ** 2))
           + 0.5 * np.exp(-((x - 1.8) ** 2) / 1.5))
    tmpls.append(tri / np.max(np.abs(tri)))
    # narrow biphasic (fast unit)
    L = int(1.2 * ms)
    x = np.linspace(-3, 4, L)
    nar = -np.exp(-((x) ** 2) * 1.5) + 0.4 * np.exp(-((x - 1.2) ** 2))
    tmpls.append(nar / np.max(np.abs(nar)))
    return tmpls


def gen_neural(channels, n, fs, rng, grid, noise_rms, firing_rate_hz,
               spatial_corr, spike_amp_counts, prop_velocity):
    rows, cols = grid
    gr, gc = channel_rowcol(channels, cols)

    # 1) independent per-channel Gaussian noise floor (the incompressible cap)
    sig = rng.normal(0.0, noise_rms, size=(channels, n))

    # 2) spatial spread parameters
    #    sigma controls how many neighbours see an event; spatial_corr in [0,1]
    #    scales it from "only the source channel" (0) to "broad cluster" (1).
    sigma = 1e-6 + spatial_corr * 3.0          # grid units
    tmpls = spike_templates(fs)

    # total events over the whole array ~ Poisson(rate * duration)
    duration = n / fs
    n_events = rng.poisson(firing_rate_hz * duration)

    for _ in range(n_events):
        r0 = rng.uniform(0, rows - 1)
        c0 = rng.uniform(0, cols - 1)
        t0 = rng.integers(0, n)
        tmpl = tmpls[rng.integers(0, len(tmpls))]
        amp = spike_amp_counts * rng.uniform(0.6, 1.4)   # amplitude jitter
        L = tmpl.size

        # distance from source to every channel on the grid
        d = np.hypot(gr - r0, gc - c0)
        gain = np.exp(-(d ** 2) / (2 * sigma ** 2))       # amplitude falloff
        # propagation delay (samples): 0 => synchronous
        if prop_velocity > 0:
            delay = np.round(d / prop_velocity * fs).astype(int)
        else:
            delay = np.zeros(channels, dtype=int)

        active = np.where(gain > 0.02)[0]                 # skip negligible channels
        for ch in active:
            s = t0 + delay[ch]
            e = s + L
            if s >= n:
                continue
            seg = tmpl[: max(0, min(L, n - s))]
            sig[ch, s:s + seg.size] += amp * gain[ch] * seg

    # 3) shared spatially-correlated field: dense BROADBAND common-mode that
    #    appears on physically-neighbouring channels at once. It is temporally
    #    white (so a per-channel temporal predictor like FLAC/LMS cannot remove
    #    it) but spatially shared -- exactly what a cross-channel predictor (or
    #    common-average reference) removes losslessly. This, not an LFP, is the
    #    honest lever for beating per-channel codecs; its strength scales with
    #    --spatial-corr (0 => none). The independent noise floor in (1) still
    #    caps the achievable ratio.
    n_field = 5
    field_amp = spatial_corr * 2.5 * noise_rms          # exceeds noise core at high corr
    broad_sigma = 2.0 + 4.0 * spatial_corr              # broad spatial spread
    for _ in range(n_field):
        common = rng.normal(0.0, field_amp, n)          # broadband (temporally white)
        r0, c0 = rng.uniform(0, rows - 1), rng.uniform(0, cols - 1)
        d = np.hypot(gr - r0, gc - c0)
        spatial = np.exp(-(d ** 2) / (2 * broad_sigma ** 2))
        sig += spatial[:, None] * common[None, :]

    return sig


# ---------------------------------------------------------------------------
# Quantize + I/O
# ---------------------------------------------------------------------------
def to_int16(sig):
    return np.clip(np.round(sig), -32768, 32767).astype(np.int16)


def write_mem(mem_dir, gt, mem_samples):
    os.makedirs(mem_dir, exist_ok=True)
    S = min(mem_samples, gt.shape[1])
    for name, base in HALVES:
        path = os.path.join(mem_dir, name + ".mem")
        with open(path, "w") as f:
            f.write(f"// {name}: sample-major, addr = sample*32 + channel; "
                    f"{S} samples x 32 ch = {S*32} words (16-bit two's-complement)\n")
            for s in range(S):
                for c in range(CH_PER_HALF):
                    v = int(gt[base + c, s]) & 0xFFFF
                    f.write("%04X\n" % v)
    return S


def spatial_report(gt, grid, channels):
    """Print the spatial ceiling: how much of a channel's variance a physical
    neighbour explains (max gain available to a 1-neighbour cross predictor)."""
    rows, cols = grid
    gr, gc = channel_rowcol(channels, cols)
    x = gt.astype(np.float64)
    x = x - x.mean(axis=1, keepdims=True)
    std = x.std(axis=1) + 1e-12

    corrs, r2s = [], []
    for ch in range(channels):
        # 4-neighbours on the grid
        nb = []
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            r, c = gr[ch] + dr, gc[ch] + dc
            if 0 <= r < rows and 0 <= c < cols:
                g = r * cols + c
                if g < channels:
                    nb.append(g)
        if not nb:
            continue
        cc = [float(np.dot(x[ch], x[g]) / (len(x[ch]) * std[ch] * std[g])) for g in nb]
        corrs.append(np.mean(np.abs(cc)))
        r2s.append(max(c * c for c in cc))     # best single neighbour R^2

    print("\n==== spatial correlation report ====")
    print(f"mean |corr| to grid neighbours : {np.mean(corrs):.4f}")
    print(f"mean best-neighbour R^2        : {np.mean(r2s):.4f}"
          "   <- spatial ceiling (max variance a 1-neighbour predictor removes)")
    print(f"  interpret: ~0 => no cross-channel gain; higher => more to exploit.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["simple", "neural"], default="neural")
    ap.add_argument("--channels", type=int, default=128)
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--fs", type=float, default=30000.0)
    ap.add_argument("--firing-rate-hz", type=float, default=50.0,
                    help="Poisson event rate over the whole array (neural mode)")
    ap.add_argument("--noise-rms", type=float, default=12.0,
                    help="per-channel Gaussian noise RMS in ADC counts (~12 = 2.4 uV)")
    ap.add_argument("--spike-amp", type=float, default=350.0,
                    help="nominal spike peak amplitude in ADC counts")
    ap.add_argument("--spatial-corr", type=float, default=0.7,
                    help="0..1 spatial spread of events across the grid")
    ap.add_argument("--prop-velocity", type=float, default=0.0,
                    help="propagation speed in grid-units/sec (0 = synchronous)")
    ap.add_argument("--grid", default="8x16", help="physical grid RxC (e.g. 8x16)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--load", default=None, help="play a real [C,N] int16 .npy instead of synth")
    ap.add_argument("--mem-samples", type=int, default=256,
                    help="length of the FPGA .mem segment in samples")
    ap.add_argument("--mem-dir", default="mem")
    ap.add_argument("--out-dir", default="sim_data")
    ap.add_argument("--report", action="store_true", help="print spatial correlation ceiling")
    args = ap.parse_args()

    grid = parse_grid(args.grid, args.channels)
    rng = np.random.default_rng(args.seed)
    n = int(round(args.seconds * args.fs))

    if args.load:
        arr = np.load(args.load)
        if arr.shape[0] != args.channels and arr.shape[1] == args.channels:
            arr = arr.T
        gt = arr.astype(np.int16)
        n = gt.shape[1]
        print(f"loaded {args.load} -> {gt.shape} int16")
    elif args.mode == "simple":
        gt = to_int16(gen_simple(args.channels, n, args.fs))
    else:
        sig = gen_neural(args.channels, n, args.fs, rng, grid,
                         args.noise_rms, args.firing_rate_hz, args.spatial_corr,
                         args.spike_amp, args.prop_velocity)
        gt = to_int16(sig)

    # outputs
    os.makedirs(args.out_dir, exist_ok=True)
    gt_path = os.path.join(args.out_dir, "ground_truth.npy")
    np.save(gt_path, gt)
    S = write_mem(args.mem_dir, gt, args.mem_samples)

    brams = 4 * -(-S * CH_PER_HALF // 2048)   # ceil, 36Kb BRAM = 2048x16
    params = {k: getattr(args, k) for k in
              ("mode", "channels", "seconds", "fs", "firing_rate_hz", "noise_rms",
               "spike_amp", "spatial_corr", "prop_velocity", "grid", "seed",
               "mem_samples")}
    params.update({"n_samples": int(n), "mem_segment_samples": int(S),
                   "mem_words_per_file": int(S * CH_PER_HALF),
                   "est_bram36k_used": int(brams), "bram36k_available_xc7s25": 45})
    with open(os.path.join(args.out_dir, "params.json"), "w") as f:
        json.dump(params, f, indent=2)

    print(f"mode={args.mode}  channels={args.channels}  fs={args.fs:.0f}  "
          f"samples={n} ({args.seconds}s)")
    print(f"ground truth : {gt_path}  shape={gt.shape} int16")
    print(f".mem segment : {S} samples -> {S*CH_PER_HALF} words/file  "
          f"(~{brams}/45 BRAM36k on XC7S25)")
    rms = gt.astype(np.float64).std()
    print(f"signal std   : {rms:.1f} counts (~{rms*LSB_UV:.2f} uV)   "
          f"peak |v| = {np.abs(gt).max()}")
    if args.report:
        spatial_report(gt, grid, args.channels)


if __name__ == "__main__":
    main()
