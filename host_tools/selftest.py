#!/usr/bin/env python3
"""
selftest.py  -  CI guard for the host tools. No hardware required.

Generates synthetic v2 frames from the emulator .mem patterns and asserts:
  1. a clean RAW capture -> emu_verify PASS (exit 0)
  2. a capture with an injected bad channel + dropped frame -> emu_verify FAIL (exit 1)
  3. flamegraph + latency_profiler run without error

Run:  python3 selftest.py --mem-dir ../../"NML work"/RHD2164_Emulator/mem
Exits non-zero on any failure (wire into CI).
"""
import os, sys, struct, subprocess, tempfile, argparse, csv

HERE = os.path.dirname(os.path.abspath(__file__))
MAGIC = 0xA55A; HDR = struct.Struct('<HBBIIH'); RAW = 0

def load_mem(p):
    out=[]
    for ln in open(p):
        ln=ln.split('//')[0].strip()
        if ln: out.append(int(ln,16))
    return out

def expected(mem):
    return (load_mem(f'{mem}/chip0_A.mem')+load_mem(f'{mem}/chip0_B.mem')
            +load_mem(f'{mem}/chip1_A.mem')+load_mem(f'{mem}/chip1_B.mem'))

def s16(v): return v if v<0x8000 else v-0x10000

def write_cap(path, exp, n_frames=500, corrupt=False, drop=False):
    n=len(exp)
    with open(path,'wb') as f:
        seq=0
        for fi in range(n_frames):
            if drop and fi in (100,101,250):
                seq+=1; continue
            pay=list(exp)
            if corrupt and fi==300: pay[7]=0xBEEF
            f.write(HDR.pack(MAGIC,RAW,0xFF,seq,fi*16667,n))
            f.write(struct.pack('<%dh'%n,*[s16(v) for v in pay]))
            seq+=1

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mem-dir', required=True)
    a=ap.parse_args()
    exp=expected(a.mem_dir)
    assert len(exp)==128, f'expected 128 channels, got {len(exp)}'
    fails=[]

    with tempfile.TemporaryDirectory() as d:
        clean=f'{d}/clean.bin'; dirty=f'{d}/dirty.bin'
        write_cap(clean, exp)
        write_cap(dirty, exp, corrupt=True, drop=True)

        r=run([sys.executable, f'{HERE}/emu_verify.py', clean, '--mem-dir', a.mem_dir, '--combined'])
        if r.returncode!=0: fails.append('clean capture should PASS but emu_verify returned nonzero\n'+r.stdout)
        else: print('[ok] clean capture -> PASS')

        r=run([sys.executable, f'{HERE}/emu_verify.py', dirty, '--mem-dir', a.mem_dir, '--combined'])
        if r.returncode==0: fails.append('dirty capture should FAIL but emu_verify returned 0')
        else: print('[ok] dirty capture -> FAIL (as expected)')

        # frames.csv for latency tool
        csvp=f'{d}/frames.csv'
        with open(csvp,'w',newline='') as f:
            w=csv.writer(f); w.writerow(['seq','t_sample_stm_cyc','t_esp_rx_us','t_pc_recv_us'])
            for i in range(500): w.writerow([i, i*16667, 1_000_000+i*60, 1_700_000_000_000+i*500])
        r=run([sys.executable, f'{HERE}/latency_profiler.py', csvp, '--stm-mhz','480','--frame-bytes','270'])
        if r.returncode!=0: fails.append('latency_profiler errored\n'+r.stderr)
        else: print('[ok] latency_profiler ran')

        foldp=f'{d}/spans.folded'
        open(foldp,'w').write('sample_isr;unpack 3072\nsample_isr;pack_and_send 350\n')
        r=run([sys.executable, f'{HERE}/flamegraph.py', foldp, '-o', f'{d}/f.svg', '--clock-mhz','480'])
        if r.returncode!=0: fails.append('flamegraph errored\n'+r.stderr)
        else: print('[ok] flamegraph ran')

    if fails:
        print('\nSELFTEST FAILED:'); [print(' -',x) for x in fails]; sys.exit(1)
    print('\nSELFTEST PASSED')

if __name__=='__main__':
    main()
