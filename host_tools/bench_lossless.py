#!/usr/bin/env python3
"""
bench_lossless.py  -  Lossless compression benchmark for the HD-EMG stream.

Sets the ratio bar (FLAC / WavPack / mtscomp / zstd / LZMA / gzip) and trials the
hardware-implementable candidates (delta+Rice, sign-LMS+Rice, and a cross-channel
grid-neighbour front-end). Every codec must round-trip bit-exact or it is failed.

Input: sim_data/ground_truth.npy  [channels, samples] int16  (from gen_neural_mem.py)

Usage:
    python3 bench_lossless.py --gt sim_data/ground_truth.npy
    python3 bench_lossless.py --sweep spatial-corr --values 0,0.3,0.6,0.9 --csv sweep.csv
"""
import argparse, gzip, lzma, os, struct, subprocess, sys, tempfile, time, wave, json
import numpy as np
import embedded_codec as ec

RAW_DTYPE = np.int16


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def result(name, raw_bytes, comp_bytes, enc_s, dec_s, lossless):
    mb = raw_bytes / 1e6
    return dict(name=name, ratio=raw_bytes / comp_bytes, comp=comp_bytes,
                enc=mb / enc_s if enc_s else float('inf'),
                dec=mb / dec_s if dec_s else float('inf'), ok=lossless)


# ---- general-purpose byte codecs (whole array, channel-contiguous) ----
def bench_generic(name, x, comp_fn, decomp_fn):
    raw = x.tobytes()
    t = time.perf_counter(); c = comp_fn(raw); enc = time.perf_counter() - t
    t = time.perf_counter(); d = decomp_fn(c); dec = time.perf_counter() - t
    return result(name, len(raw), len(c), enc, dec, d == raw)


def bench_gzip(x):
    return bench_generic('gzip-9', x, lambda b: gzip.compress(b, 9), gzip.decompress)


def bench_lzma(x):
    return bench_generic('lzma', x, lambda b: lzma.compress(b, preset=6), lzma.decompress)


def bench_zstd(x):
    try:
        import zstandard as zstd
    except ImportError:
        return None
    cctx = zstd.ZstdCompressor(level=19)
    dctx = zstd.ZstdDecompressor()
    return bench_generic('zstd-19', x, cctx.compress, dctx.decompress)


# ---- audio codecs via CLI, per-channel mono 16-bit ----
def _write_wav(path, ch, fs=30000):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs)
        w.writeframes(ch.astype('<i2').tobytes())


def bench_audio_cli(name, exe, x, enc_args, dec_args, verify_ch=4):
    if not _which(exe):
        return None
    C = x.shape[0]
    total = 0
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as td:
        comp_paths = []
        for c in range(C):
            wavp = os.path.join(td, f'{c}.wav')
            outp = os.path.join(td, f'{c}.{name}')
            _write_wav(wavp, x[c])
            subprocess.run([exe] + enc_args + ['-o', outp, wavp],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            total += os.path.getsize(outp)
            comp_paths.append((wavp, outp))
        enc = time.perf_counter() - t0
        # verify losslessness on a subset by decoding back
        ok = True
        t1 = time.perf_counter()
        for c in range(min(verify_ch, C)):
            wavp, outp = comp_paths[c]
            decp = os.path.join(td, f'{c}.dec.wav')
            subprocess.run([exe] + dec_args + ['-o', decp, outp],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with wave.open(decp, 'rb') as w:
                got = np.frombuffer(w.readframes(w.getnframes()), '<i2')
            if not np.array_equal(got, x[c]):
                ok = False
        dec = (time.perf_counter() - t1) * (C / max(1, min(verify_ch, C)))  # extrapolate
    return result(name, x.nbytes, total, enc, dec, ok)


def bench_flac(x):
    return bench_audio_cli('flac', 'flac', x, ['-8', '-s', '-f'], ['-d', '-s', '-f'])


def bench_wavpack(x):
    if not _which('wavpack'):
        return None
    # wavpack/wvunpack use separate binaries
    C = x.shape[0]; total = 0
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for c in range(C):
            wavp = os.path.join(td, f'{c}.wav'); outp = os.path.join(td, f'{c}.wv')
            _write_wav(wavp, x[c])
            subprocess.run(['wavpack', '-h', '-y', '-q', wavp, '-o', outp],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            total += os.path.getsize(outp); paths.append((wavp, outp))
        enc = time.perf_counter() - t0
        ok = True
        if _which('wvunpack'):
            for c in range(min(4, C)):
                wavp, outp = paths[c]; decp = os.path.join(td, f'{c}.dec.wav')
                subprocess.run(['wvunpack', '-y', '-q', outp, '-o', decp],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with wave.open(decp, 'rb') as w:
                    got = np.frombuffer(w.readframes(w.getnframes()), '<i2')
                if not np.array_equal(got, x[c]):
                    ok = False
    return result('wavpack', x.nbytes, total, enc, 0.0, ok)


def bench_mtscomp(x):
    try:
        from mtscomp import compress as mc_compress, decompress as mc_decompress
    except Exception:
        return None
    fs = 30000
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, 'data.bin')
        x.T.astype('<i2').tofile(raw)          # mtscomp wants [n_samples, n_channels]
        cbin = os.path.join(td, 'data.cbin'); chf = os.path.join(td, 'data.ch')
        t = time.perf_counter()
        try:
            mc_compress(raw, cbin, chf, sample_rate=fs, n_channels=x.shape[0],
                        dtype=RAW_DTYPE, check_after_compress=False)
        except Exception as e:
            print(f'  (mtscomp skipped: {e})'); return None
        enc = time.perf_counter() - t
        comp = os.path.getsize(cbin) + os.path.getsize(chf)
        t = time.perf_counter()
        arr = mc_decompress(cbin, chf)
        got = arr[:] if hasattr(arr, '__getitem__') else arr
        dec = time.perf_counter() - t
        ok = np.array_equal(np.asarray(got).astype(RAW_DTYPE), x.T.astype(RAW_DTYPE))
    return result('mtscomp', x.nbytes, comp, enc, dec, ok)


# ---- embedded candidates ----
def bench_embedded(name, x, predictor, cross, cols):
    t = time.perf_counter(); blob = ec.encode(x, predictor=predictor, cross=cross, cols=cols)
    enc = time.perf_counter() - t
    t = time.perf_counter(); y = ec.decode(blob); dec = time.perf_counter() - t
    return result(name, x.nbytes, len(blob), enc, dec, np.array_equal(x, y))


def _which(exe):
    from shutil import which
    return which(exe) is not None


# ---------------------------------------------------------------------------
def run_suite(x, cols):
    rows = []
    for fn in (bench_flac, bench_wavpack, bench_mtscomp, bench_zstd, bench_lzma, bench_gzip):
        r = fn(x)
        if r:
            rows.append(r)
    rows.append(bench_embedded('delta+Rice',        x, ec.PRED_DELTA, False, cols))
    rows.append(bench_embedded('LMS+Rice',          x, ec.PRED_LMS,   False, cols))
    rows.append(bench_embedded('LMS+Rice +xchan',   x, ec.PRED_LMS,   True,  cols))
    rows.append(bench_embedded('delta+Rice +xchan', x, ec.PRED_DELTA, True,  cols))
    return rows


def print_table(rows):
    print(f"\n{'codec':<20}{'ratio':>8}{'comp KB':>10}{'enc MB/s':>10}{'dec MB/s':>10}{'lossless':>10}")
    print('-' * 68)
    for r in rows:
        print(f"{r['name']:<20}{r['ratio']:>7.2f}x{r['comp']/1024:>10.1f}"
              f"{r['enc']:>10.1f}{r['dec']:>10.1f}{'OK' if r['ok'] else 'FAIL!':>10}")
    # analysis
    by = {r['name']: r for r in rows}
    flac = by.get('flac')
    lms = by.get('LMS+Rice'); lmsx = by.get('LMS+Rice +xchan')
    print()
    if flac and lms:
        print(f"LMS+Rice reaches {100*lms['ratio']/flac['ratio']:.0f}% of FLAC's ratio")
    if lms and lmsx:
        gain = 100 * (lmsx['ratio'] / lms['ratio'] - 1)
        print(f"cross-channel gain (LMS): {gain:+.1f}%  "
              f"({lms['ratio']:.2f}x -> {lmsx['ratio']:.2f}x)")
    if any(not r['ok'] for r in rows):
        print("\n!!! a codec failed bit-exact round-trip -- see FAIL above")
    hi = max(r['ratio'] for r in rows)
    if hi > 50:
        print(f"\n*** SANITY: max ratio {hi:.1f}x > 50x -- data looks unrealistic (too "
              f"compressible). Check the generator before trusting these numbers. ***")


def load_gt(path, cap):
    x = np.load(path)
    if x.dtype != RAW_DTYPE:
        x = x.astype(RAW_DTYPE)
    if cap and x.shape[1] > cap:
        x = x[:, :cap]
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--gt', default='sim_data/ground_truth.npy')
    ap.add_argument('--cols', type=int, default=16, help='grid columns (for cross-channel)')
    ap.add_argument('--bench-samples', type=int, default=15000,
                    help='cap samples for speed (0 = all)')
    ap.add_argument('--sweep', choices=['spatial-corr', 'firing-rate-hz', 'noise-rms'])
    ap.add_argument('--values', default='0,0.3,0.6,0.9')
    ap.add_argument('--csv', default=None)
    args = ap.parse_args()

    if not args.sweep:
        x = load_gt(args.gt, args.bench_samples)
        print(f"benchmark on {x.shape[0]} ch x {x.shape[1]} samples "
              f"({x.nbytes/1e6:.2f} MB raw)")
        rows = run_suite(x, args.cols)
        print_table(rows)
        return

    # sweep: regenerate ground truth per value, re-run, collect CSV
    vals = [float(v) for v in args.values.split(',')]
    csv_rows = []
    for v in vals:
        gen = [sys.executable, os.path.join(os.path.dirname(__file__), 'gen_neural_mem.py'),
               '--mode', 'neural', '--seconds', '0.5', '--seed', '1',
               f'--{args.sweep}', str(v)]
        subprocess.run(gen, check=True, stdout=subprocess.DEVNULL)
        x = load_gt(args.gt, args.bench_samples)
        rows = run_suite(x, args.cols)
        print(f"\n===== {args.sweep} = {v} =====")
        print_table(rows)
        for r in rows:
            csv_rows.append(dict(sweep=args.sweep, value=v, **{k: r[k] for k in
                            ('name', 'ratio', 'comp', 'enc', 'dec', 'ok')}))
    if args.csv:
        import csv
        with open(args.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader(); w.writerows(csv_rows)
        print(f"\nwrote {args.csv} ({len(csv_rows)} rows)")


if __name__ == '__main__':
    main()
