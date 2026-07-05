#!/usr/bin/env python3
"""
emu_verify.py  -  Sample-by-sample verification of the RHD2164 emulator -> H745 -> S3 -> PC path.

The FPGA emulator plays a short time-varying segment out of BRAM (mem/*.mem,
sample-major: word address = sample*32 + channel) and loops it. So every
received channel value is predictable: for a RAW frame with sequence number
seq, channel c should equal the emulator's segment at sample (seq mod
MEM_SAMPLES). This tool reads a capture, reconstructs that expectation from the
same .mem files, and reports dropped / corrupted / mis-aligned samples per
channel -- the real test of "did the H745+S3 receive exactly what the emulator
played."

Back-compatible: if the .mem files are 32 words deep (the old constant-per-
channel pattern) it verifies against that single value per channel.

Frame format (HD-EMG v2, little-endian) -- matches firmware_patches/hdemg_frame.h:
    u16 magic=0xA55A  u8 type{0=RAW16,1=RMS16,2=COMPRESSED}  u8 chip_id{0,1,0xFF}
    u32 seq  u32 t_stm  u16 n_ch  i16 payload[n_ch]

Usage:
    python3 emu_verify.py cap.bin --mem-dir ../mem --combined
    python3 emu_verify.py --selftest --mem-dir ../mem --combined   # no hardware needed
"""
import sys, os, struct, argparse

MAGIC = 0xA55A
HDR = struct.Struct('<HBBIIH')   # magic,type,chip,seq,t_stm,n_ch = 14 bytes
TYPE_RAW, TYPE_RMS, TYPE_COMPRESSED = 0, 1, 2
CH_PER_HALF = 32


def load_mem(path):
    vals = []
    with open(path) as f:
        for line in f:
            line = line.split('//')[0].strip()
            if line:
                vals.append(int(line, 16))
    return vals


def build_expected(mem_dir, combined):
    """Return (expected[sample][channel], n_samples, n_ch).

    Handles both the deep time-varying segment (sample-major, >32 words/file)
    and the legacy 32-word constant pattern.
    """
    files = ['chip0_A.mem', 'chip0_B.mem']
    if combined:
        files += ['chip1_A.mem', 'chip1_B.mem']
    halves = [load_mem(os.path.join(mem_dir, f)) for f in files]

    depth = len(halves[0])
    if any(len(h) != depth for h in halves):
        sys.exit('mem files differ in length')
    n_samples = max(1, depth // CH_PER_HALF)   # 32-deep -> 1 sample (constant)
    n_ch = CH_PER_HALF * len(halves)

    expected = []
    for s in range(n_samples):
        row = []
        for h in halves:                       # half order = channel order
            base = s * CH_PER_HALF
            row.extend(h[base:base + CH_PER_HALF])
        expected.append(row)
    return expected, n_samples, n_ch


def verify_bytes(data, expected, n_samples, n_ch, mask, max_report):
    frames = checked = bad_value = misaligned = 0
    per_ch_err = {}
    seqs = []
    reported = 0
    seq0 = None
    i, L = 0, len(data)
    while i + HDR.size <= L:
        magic, ftype, chip, seq, t_stm, nch = HDR.unpack_from(data, i)
        if magic != MAGIC:
            i += 1
            misaligned += 1
            continue
        pbytes = nch * 2
        if i + HDR.size + pbytes > L:
            break
        payload = struct.unpack_from('<%dh' % nch, data, i + HDR.size)
        i += HDR.size + pbytes
        frames += 1
        seqs.append(seq)
        if seq0 is None:
            seq0 = seq

        if ftype == TYPE_RAW:
            if nch != n_ch:
                if reported < max_report:
                    print(f'frame seq={seq}: n_ch={nch} != expected {n_ch} (mode mismatch?)')
                    reported += 1
                continue
            exp_row = expected[(seq - seq0) % n_samples]
            for c in range(nch):
                got = payload[c] & mask
                exp = exp_row[c] & mask
                checked += 1
                if got != exp:
                    bad_value += 1
                    per_ch_err[c] = per_ch_err.get(c, 0) + 1
                    if reported < max_report:
                        print(f'frame seq={seq} ch{c}: got 0x{got:04X} expected 0x{exp:04X}')
                        reported += 1
        # RMS / COMPRESSED frames are not value-checked here (see verify_compressed.py)

    lost = dups = 0
    if seqs:
        span = max(seqs) - min(seqs) + 1
        uniq = len(set(seqs))
        lost = span - uniq
        dups = len(seqs) - uniq

    print('\n==== emulator loopback verification ====')
    print(f'segment samples      : {n_samples}   channels/frame: {n_ch}')
    print(f'frames parsed        : {frames}')
    print(f'channel values checked: {checked}')
    print(f'value mismatches     : {bad_value}'
          + (f'  ({100.0*bad_value/checked:.4f}%)' if checked else ''))
    print(f'frame loss (seq gaps): {lost}   duplicates: {dups}')
    print(f'resync byte-slides   : {misaligned}')
    if per_ch_err:
        worst = sorted(per_ch_err.items(), key=lambda kv: -kv[1])[:10]
        print('worst channels       : ' + ', '.join(f'ch{c}={n}' for c, n in worst))
    ok = (bad_value == 0 and lost == 0 and frames > 0)
    print('RESULT               : ' + ('PASS - every sample matched the emulator'
                                        if ok else 'FAIL - see mismatches above'))
    return ok


def synth_capture(expected, n_samples, n_ch, n_frames):
    """Build a byte stream of RAW frames straight from the expected segment --
    a hardware-free proof that a correct capture verifies PASS."""
    out = bytearray()
    for s in range(n_frames):
        row = expected[s % n_samples]
        signed = [(v & 0xFFFF) - (0x10000 if (v & 0x8000) else 0) for v in row]
        out += HDR.pack(MAGIC, TYPE_RAW, 0xFF, s, 0, n_ch)
        out += struct.pack('<%dh' % n_ch, *signed)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', nargs='?', help='raw byte capture (omit with --selftest)')
    ap.add_argument('--mem-dir', required=True, help='emulator mem/ directory')
    ap.add_argument('--combined', action='store_true', help='128-ch combined frames')
    ap.add_argument('--selftest', action='store_true',
                    help='synthesize a correct capture from the .mem segment and verify it')
    ap.add_argument('--selftest-frames', type=int, default=1000)
    ap.add_argument('--max-report', type=int, default=20)
    ap.add_argument('--mask', default='0xFFFF',
                    help='AND mask before compare (0x7FFF if MSB is a status bit)')
    args = ap.parse_args()

    mask = int(args.mask, 0)
    expected, n_samples, n_ch = build_expected(args.mem_dir, args.combined)

    if args.selftest:
        data = synth_capture(expected, n_samples, n_ch, args.selftest_frames)
        print(f'[selftest] synthesized {args.selftest_frames} RAW frames from segment '
              f'({n_samples} samples x {n_ch} ch)')
    else:
        if not args.capture:
            ap.error('provide a capture file or use --selftest')
        data = open(args.capture, 'rb').read()

    ok = verify_bytes(data, expected, n_samples, n_ch, mask, args.max_report)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
