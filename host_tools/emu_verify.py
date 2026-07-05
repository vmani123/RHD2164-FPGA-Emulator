#!/usr/bin/env python3
"""
emu_verify.py  -  Sample-by-sample verification of the RHD2164 emulator -> H745 -> S3 -> PC path.

The FPGA emulator sources every channel from BRAM initialized by mem/*.mem, so
each channel returns a *known, distinct* 16-bit value:

    chip0:  ch c (0..31)  -> 0x1000 | c          ch c+32 (32..63) -> 0x2000 | c
    chip1:  ch c (0..31)  -> 0x3000 | c          ch c+32 (32..63) -> 0x4000 | c

So when the firmware streams RAW frames, every received channel value is
predictable. This tool reads a raw capture, checks each channel against the
expected pattern, and reports dropped / corrupted / mis-aligned samples per
channel -- the real test of "did the H745+S3 receive everything the emulator
sent."

Frame format (HD-EMG v2, little-endian) -- matches firmware_patches/hdemg_frame.h:
    u16 magic   = 0xA55A
    u8  type    : 0 = RAW16, 1 = RMS16
    u8  chip_id : 0, 1, or 0xFF = combined (chip0 A/B then chip1 A/B)
    u32 seq     : monotonic frame counter
    u32 t_stm   : DWT CYCCNT latched at the sample period
    u16 n_ch    : channels in payload
    i16 payload[n_ch]

Usage:
    python3 emu_verify.py received_data.bin --mem-dir ../../NML\\ work/RHD2164_Emulator/mem
    python3 emu_verify.py received_data.bin --mem-dir <dir> --combined   # 128-ch combined frames
"""
import sys, os, struct, argparse

MAGIC = 0xA55A
HDR = struct.Struct('<HBBIIH')   # magic,type,chip,seq,t_stm,n_ch  = 14 bytes
TYPE_RAW, TYPE_RMS = 0, 1

def load_mem(path):
    vals = []
    with open(path) as f:
        for line in f:
            line = line.split('//')[0].strip()
            if line:
                vals.append(int(line, 16))
    return vals

def build_expected(mem_dir, combined):
    """Return expected[channel_index] for a frame's payload order."""
    a0 = load_mem(os.path.join(mem_dir, 'chip0_A.mem'))
    b0 = load_mem(os.path.join(mem_dir, 'chip0_B.mem'))
    if combined:
        a1 = load_mem(os.path.join(mem_dir, 'chip1_A.mem'))
        b1 = load_mem(os.path.join(mem_dir, 'chip1_B.mem'))
        # firmware combined order: chip0 A(0..31), chip0 B(0..31), chip1 A, chip1 B
        return a0 + b0 + a1 + b1
    # single chip0: A(0..31) then B(0..31)
    return a0 + b0

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', help='raw byte capture from the PC receiver (e.g. received_data.bin)')
    ap.add_argument('--mem-dir', required=True, help='emulator mem/ directory')
    ap.add_argument('--combined', action='store_true', help='128-ch combined frames (both chips)')
    ap.add_argument('--max-report', type=int, default=20, help='max mismatch lines to print')
    ap.add_argument('--mask', default='0xFFFF',
                    help='AND mask applied before compare (use 0x7FFF if MSB is a status bit)')
    args = ap.parse_args()

    mask = int(args.mask, 0)
    expected = build_expected(args.mem_dir, args.combined)
    n_expected = len(expected)
    data = open(args.capture, 'rb').read()

    # resync to magic
    frames = 0
    checked = 0
    bad_value = 0
    misaligned = 0
    per_ch_err = {}
    seqs = []
    reported = 0
    i = 0
    L = len(data)
    while i + HDR.size <= L:
        magic, ftype, chip, seq, t_stm, n_ch = HDR.unpack_from(data, i)
        if magic != MAGIC:
            i += 1                      # slide until we find a frame boundary
            misaligned += 1
            continue
        payload_bytes = n_ch * 2
        if i + HDR.size + payload_bytes > L:
            break
        payload = struct.unpack_from('<%dh' % n_ch, data, i + HDR.size)
        i += HDR.size + payload_bytes
        frames += 1
        seqs.append(seq)

        if ftype == TYPE_RAW:
            if n_ch != n_expected:
                if reported < args.max_report:
                    print(f'frame seq={seq}: n_ch={n_ch} != expected {n_expected} (mode mismatch?)')
                    reported += 1
                continue
            for c in range(n_ch):
                got = payload[c] & mask
                exp = expected[c] & mask
                checked += 1
                if got != exp:
                    bad_value += 1
                    per_ch_err[c] = per_ch_err.get(c, 0) + 1
                    if reported < args.max_report:
                        print(f'frame seq={seq} ch{c}: got 0x{got:04X} expected 0x{exp:04X}')
                        reported += 1
        # RMS frames are not value-checked here (no closed-form expected), only counted

    # loss from seq
    lost = dups = 0
    if seqs:
        lo, hi = min(seqs), max(seqs)
        span = hi - lo + 1
        uniq = len(set(seqs))
        lost = span - uniq
        dups = len(seqs) - uniq

    print('\n==== emulator loopback verification ====')
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
    sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
