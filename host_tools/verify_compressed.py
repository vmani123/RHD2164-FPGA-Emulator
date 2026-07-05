#!/usr/bin/env python3
"""
verify_compressed.py  -  Prove the COMPRESSED path is lossless end-to-end.

A COMPRESSED frame (type=2, see firmware_patches/hdemg_frame.h) carries a block
of N sample periods for all channels, encoded by the reference embedded codec
(embedded_codec.py). This tool decodes each frame and asserts bit-exact equality
with sim_data/ground_truth.npy, reusing the seq field for block indexing and
loss detection.

Because the node will eventually run the same predictor+Rice math in firmware,
"decode here == ground truth" is the contract the firmware must meet.

Modes:
  --selftest : pure-software loopback (ground_truth -> encode -> frame -> parse
               -> decode -> compare) for delta+Rice and LMS+Rice. No hardware.
  <capture>  : verify a real capture of COMPRESSED frames against ground truth.

Usage:
    python3 verify_compressed.py --selftest --gt sim_data/ground_truth.npy
    python3 verify_compressed.py cap.bin --gt sim_data/ground_truth.npy
"""
import argparse, struct, sys
import numpy as np
import embedded_codec as ec

MAGIC = 0xA55A
HDR = struct.Struct('<HBBIIH')          # magic,type,chip,seq,t_stm,n_ch
TYPE_COMPRESSED = 2


def pack_frame(seq, blob, n_ch):
    return (HDR.pack(MAGIC, TYPE_COMPRESSED, 0xFF, seq, 0, n_ch)
            + struct.pack('<I', len(blob)) + blob)


def build_capture(gt, predictor, cross, cols, block):
    """Encode ground truth into a stream of COMPRESSED frames (one per block)."""
    C, N = gt.shape
    out = bytearray()
    for b, s in enumerate(range(0, N, block)):
        chunk = gt[:, s:s + block]
        blob = ec.encode(chunk, predictor=predictor, cross=cross, cols=cols)
        out += pack_frame(b, blob, C)
    return bytes(out)


def verify(data, gt, cols, max_report=20):
    C, N = gt.shape
    i, L = 0, len(data)
    frames = checked = bad = misaligned = 0
    reported = 0
    seqs = []
    while i + HDR.size <= L:
        magic, ftype, chip, seq, t_stm, n_ch = HDR.unpack_from(data, i)
        if magic != MAGIC:
            i += 1
            misaligned += 1
            continue
        if ftype != TYPE_COMPRESSED:
            # skip other frame types (RAW/RMS) using their sizing
            i += HDR.size + n_ch * 2
            continue
        (blob_len,) = struct.unpack_from('<I', data, i + HDR.size)
        start = i + HDR.size + 4
        if start + blob_len > L:
            break
        blob = data[start:start + blob_len]
        i = start + blob_len
        frames += 1
        seqs.append(seq)

        block = ec.decode(blob)                    # [C, n]
        n = block.shape[1]
        s0 = seq * n                               # block index -> sample offset
        exp = gt[:, s0:s0 + n]
        if block.shape != exp.shape:
            if reported < max_report:
                print(f'frame seq={seq}: block {block.shape} vs gt {exp.shape} (range?)')
                reported += 1
            continue
        mism = np.argwhere(block != exp)
        checked += block.size
        if mism.size:
            bad += len(mism)
            for (c, t) in mism[:max_report - reported]:
                print(f'  seq={seq} ch{c} sample{s0+t}: got {block[c,t]} exp {exp[c,t]}')
                reported += 1

    lost = dups = 0
    if seqs:
        lost = (max(seqs) - min(seqs) + 1) - len(set(seqs))
        dups = len(seqs) - len(set(seqs))

    print('\n==== compressed-path verification ====')
    print(f'compressed frames    : {frames}')
    print(f'samples checked      : {checked}')
    print(f'sample mismatches    : {bad}')
    print(f'frame loss (seq gaps): {lost}   duplicates: {dups}')
    print(f'resync byte-slides   : {misaligned}')
    ok = (bad == 0 and lost == 0 and frames > 0)
    print('RESULT               : ' + ('PASS - decompressed stream is bit-exact'
                                        if ok else 'FAIL - see mismatches above'))
    return ok


def selftest(gt, cols, block):
    all_ok = True
    for name, pred in (('delta+Rice', ec.PRED_DELTA), ('LMS+Rice', ec.PRED_LMS)):
        for cross in (False, True):
            cap = build_capture(gt, pred, cross, cols, block)
            print(f'\n--- {name}{" +xchan" if cross else ""}  '
                  f'({len(cap)} bytes, {gt.nbytes/len(cap):.2f}x) ---')
            ok = verify(cap, gt, cols)
            all_ok = all_ok and ok
    return all_ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', nargs='?', help='capture of COMPRESSED frames')
    ap.add_argument('--gt', default='sim_data/ground_truth.npy')
    ap.add_argument('--cols', type=int, default=16)
    ap.add_argument('--block', type=int, default=1000, help='samples per compressed frame')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--max-samples', type=int, default=8000, help='cap gt length (0=all)')
    args = ap.parse_args()

    gt = np.load(args.gt)
    if gt.dtype != np.int16:
        gt = gt.astype(np.int16)
    if args.max_samples and gt.shape[1] > args.max_samples:
        gt = gt[:, :args.max_samples]

    if args.selftest:
        ok = selftest(gt, args.cols, args.block)
    else:
        if not args.capture:
            ap.error('provide a capture or use --selftest')
        ok = verify(open(args.capture, 'rb').read(), gt, args.cols)
    print('\n' + ('ALL COMPRESSED CHECKS PASSED' if ok else 'THERE WERE FAILURES'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
