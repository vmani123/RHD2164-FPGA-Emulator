#!/usr/bin/env python3
"""
load_wfdb.py  -  Feed REAL neural data (PhysioNet WFDB records) into the harness.

Downloads (or reads a local) WFDB format-16 record and converts it into the
emulator's ground truth + .mem segment, so the benchmark and verifiers run on
genuine electrophysiology instead of a synthetic model.

Default source: the Hyser HD-sEMG dataset (256-channel surface EMG @ 2048 Hz,
16-bit) -- broadband multichannel electrophysiology, the same signal class the
RHD2164 records, with a real 8x8 electrode-grid layout.
  https://physionet.org/content/hd-semg/1.0.0/

Usage:
    python3 load_wfdb.py --download                 # fetch default Hyser sample
    python3 load_wfdb.py --record raw_data/hyser    # use a local .dat/.hea pair
"""
import argparse, os, sys, json, urllib.request
import numpy as np
from gen_neural_mem import write_mem, CH_PER_HALF, LSB_UV

DEFAULT_URL = ("https://physionet.org/files/hd-semg/1.0.0/1dof_dataset/"
               "subject01_session1/1dof_raw_finger1_sample1")


def fetch(url_base, dst_base):
    for ext in ('.hea', '.dat'):
        dst = dst_base + ext
        if os.path.exists(dst):
            print(f"  have {dst}")
            continue
        print(f"  downloading {url_base+ext} ...")
        urllib.request.urlretrieve(url_base + ext, dst)
    return dst_base


def read_wfdb16(base):
    """Read a WFDB format-16 record -> (x[C,N] int16, fs, labels)."""
    hea = open(base + '.hea').read().splitlines()
    rec = hea[0].split()
    nsig, fs, nsamp = int(rec[1]), int(rec[2]), int(rec[3])
    labels = [l.split()[-1] for l in hea[1:1 + nsig] if l.strip()]
    fmt = hea[1].split()[1]
    if fmt != '16':
        sys.exit(f"only WFDB format 16 supported here (got {fmt})")
    raw = np.fromfile(base + '.dat', '<i2')
    if raw.size != nsig * nsamp:
        sys.exit(f"size mismatch: {raw.size} != {nsig}*{nsamp}")
    x = raw.reshape(nsamp, nsig).T          # sample-interleaved -> [channels, samples]
    return x.astype(np.int16), fs, labels


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--url', default=DEFAULT_URL, help='WFDB record URL base (no extension)')
    ap.add_argument('--record', default='raw_data/hyser',
                    help='local record base (.dat/.hea) to read/write')
    ap.add_argument('--download', action='store_true', help='fetch --url to --record')
    ap.add_argument('--channels', type=int, default=128)
    ap.add_argument('--chan-offset', type=int, default=0, help='first channel to take')
    ap.add_argument('--zero-mean', action='store_true',
                    help='subtract per-channel DC (RHD-with-DSP-HPF-like)')
    ap.add_argument('--mem-samples', type=int, default=256)
    ap.add_argument('--mem-dir', default='mem')
    ap.add_argument('--out-dir', default='sim_data')
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.record) or '.', exist_ok=True)
    if args.download:
        fetch(args.url, args.record)
    if not os.path.exists(args.record + '.dat'):
        sys.exit(f"no record at {args.record}.dat (use --download)")

    x, fs, labels = read_wfdb16(args.record)
    print(f"record: {x.shape[0]} ch x {x.shape[1]} samples @ {fs} Hz")
    o, C = args.chan_offset, args.channels
    x = x[o:o + C].copy()
    if x.shape[0] < C:
        sys.exit(f"record has < {C} channels")
    if args.zero_mean:
        x = np.clip(x - np.round(x.mean(axis=1, keepdims=True)), -32768, 32767).astype(np.int16)

    os.makedirs(args.out_dir, exist_ok=True)
    gt_path = os.path.join(args.out_dir, 'ground_truth.npy')
    np.save(gt_path, x)
    S = write_mem(args.mem_dir, x, args.mem_samples)

    sat = float(np.mean(np.abs(x) >= 32767))
    params = dict(source='wfdb', url=args.url, channels=C, chan_offset=o, fs=fs,
                  n_samples=int(x.shape[1]), mem_segment_samples=int(S),
                  zero_mean=args.zero_mean, labels=labels[o:o + C])
    with open(os.path.join(args.out_dir, 'params.json'), 'w') as f:
        json.dump(params, f, indent=2)

    print(f"ground truth : {gt_path}  shape={x.shape} int16")
    print(f".mem segment : {S} samples -> {S*CH_PER_HALF} words/file")
    print(f"signal std   : {x.std():.0f} counts (~{x.std()*LSB_UV:.0f} uV)   "
          f"saturated={100*sat:.3f}%   labels[:4]={labels[o:o+4]}")


if __name__ == '__main__':
    main()
