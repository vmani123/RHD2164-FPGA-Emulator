#!/usr/bin/env python3
"""
pc_receiver.py  -  TCP/UDP sink for the HD-EMG v2 frame stream.

Replaces the old simple_server.py: instead of treating the stream as raw bytes,
it parses hdemg_frame.h frames, timestamps each on arrival, and writes:
    - cap.bin     : raw bytes (feed to emu_verify.py)
    - frames.csv  : seq,t_sample_stm_cyc,t_esp_rx_us,t_pc_recv_us (feed to latency_profiler.py)

It works for both RAW and RMS frames (it just records; the analyzers interpret).
Live throughput / loss is printed ~1 Hz so you can see the link health.

Usage:
    python3 pc_receiver.py --host 0.0.0.0 --port 3333                 # TCP (matches current ESP)
    python3 pc_receiver.py --host 0.0.0.0 --port 3333 --udp           # UDP
    python3 pc_receiver.py ... --t-esp-offset 8                       # byte offset of t_esp if ESP stamps it
"""
import socket, struct, argparse, time, sys

MAGIC = 0xA55A
HDR = struct.Struct('<HBBIIH')   # magic,type,chip,seq,t_stm,n_ch
TYPE = {0: 'RAW', 1: 'RMS'}

def run(args):
    cap = open(args.cap, 'wb')
    csvf = open(args.csv, 'w')
    csvf.write('seq,t_sample_stm_cyc,t_esp_rx_us,t_pc_recv_us\n')

    if args.udp:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((args.host, args.port))
        print(f'UDP listening on {args.host}:{args.port}')
        conn = None
    else:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((args.host, args.port)); srv.listen(1)
        print(f'TCP listening on {args.host}:{args.port}')
        conn, addr = srv.accept()
        print(f'client {addr}')

    buf = bytearray()
    frames = bytes_total = 0
    last_seq = None
    lost = 0
    t0 = time.perf_counter(); t_report = t0
    try:
        while True:
            if args.udp:
                chunk, _ = s.recvfrom(65535)
            else:
                chunk = conn.recv(65536)
                if not chunk:
                    print('client disconnected'); break
            now_us = time.perf_counter() * 1e6
            cap.write(chunk)
            bytes_total += len(chunk)
            buf += chunk

            # parse as many whole frames as present
            i = 0
            while True:
                if len(buf) - i < HDR.size:
                    break
                if struct.unpack_from('<H', buf, i)[0] != MAGIC:
                    i += 1; continue          # resync
                magic, ftype, chip, seq, t_stm, n_ch = HDR.unpack_from(buf, i)
                need = HDR.size + n_ch * 2
                if len(buf) - i < need:
                    break
                t_esp = ''
                if args.t_esp_offset is not None and args.t_esp_offset + 4 <= n_ch*2 + HDR.size:
                    t_esp = struct.unpack_from('<I', buf, i + args.t_esp_offset)[0]
                csvf.write(f'{seq},{t_stm},{t_esp},{now_us:.1f}\n')
                if last_seq is not None:
                    gap = (seq - last_seq - 1) & 0xFFFFFFFF
                    if 0 < gap < 1000000:
                        lost += gap
                last_seq = seq
                frames += 1
                i += need
            del buf[:i]

            now = time.perf_counter()
            if now - t_report >= 1.0:
                dt = now - t_report
                mbps = bytes_total * 8 / (now - t0) / 1e6
                print(f'[{now-t0:6.1f}s] frames={frames} lost={lost} '
                      f'{mbps:5.2f} Mbit/s avg  last_type={TYPE.get(ftype,"?")} n_ch={n_ch}')
                t_report = now
    except KeyboardInterrupt:
        pass
    finally:
        cap.close(); csvf.close()
        print(f'\nwrote {args.cap} and {args.csv}: {frames} frames, {bytes_total} bytes, {lost} lost')

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=3333)
    ap.add_argument('--udp', action='store_true')
    ap.add_argument('--cap', default='cap.bin')
    ap.add_argument('--csv', default='frames.csv')
    ap.add_argument('--t-esp-offset', type=int, default=None,
                    help='byte offset within frame where ESP wrote its esp_timer us (if enabled)')
    run(ap.parse_args())
