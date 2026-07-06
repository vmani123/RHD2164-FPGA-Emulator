#!/usr/bin/env python3
"""
bench.py  -  Registry-driven benchmark over codec x dataset (Stage 3 of
COMPRESSION_RESEARCH_AGENT_PROMPT.md).

For every dataset in `research/datasets.py` and every codec in
`research/registry.py`, measure ratio, encode/decode MB/s, `embedded_ok`, the
Pareto cost, %-of-FLAC, and the achieved cross-channel gain, asserting a bit-exact
round-trip on every embedded codec. The FLAC/WavPack/mtscomp/zstd/LZMA/gzip
reference bar is reused from `host_tools/bench_lossless.py` (not re-implemented).
Emits a tidy CSV + a per-dataset table.

Non-negotiables enforced here:
  * bit-exact: every registry codec must round-trip or the run fails loudly (#1).
  * sanity gate: any lossless ratio > 6x on a REAL dataset => degenerate/leak,
    stop and report (#3). (Synthetic can legitimately exceed it at high corr.)
  * embedded_ok is carried per row so ranking never uses ratio alone (#2).

Usage:
    python3 research/bench.py                       # synthetic corpus, CSV
    python3 research/bench.py --datasets synth_sc0.6 hyser_1dof_f1_s1
    python3 research/bench.py --csv results/03_bench.csv --max-samples 8000
"""
import argparse
import csv
import os
import sys
import time

import numpy as np

HOST = os.path.join(os.path.dirname(__file__), "..", "host_tools")
sys.path.insert(0, HOST)
sys.path.insert(0, os.path.dirname(__file__))
import bench_lossless as bl   # noqa: E402  reference-bar functions
import registry as reg        # noqa: E402
import datasets as dsmod      # noqa: E402

SANITY_MAX = 6.0  # lossless ratio above this on REAL data => stop and report


def _ref_rows(x):
    """Run the reference bar (whichever CLIs/pkgs are present) on [C,N]."""
    rows = []
    for fn in (bl.bench_flac, bl.bench_wavpack, bl.bench_mtscomp,
               bl.bench_zstd, bl.bench_lzma, bl.bench_gzip):
        try:
            r = fn(x)
        except Exception as e:
            r = None
            print(f"  (reference {fn.__name__} skipped: {e})")
        if r:
            r["embedded"] = "ref"       # references are the bar, not contenders
            r["cost"] = None
            rows.append(r)
    return rows


def _codec_rows(x, cols):
    """Run every registry codec on [C,N] with bit-exact assert."""
    rows = []
    for c in reg.list_codecs():
        t = time.perf_counter(); blob = c.encode(x, cols=cols)
        enc = time.perf_counter() - t
        t = time.perf_counter(); y = c.decode(blob)
        dec = time.perf_counter() - t
        ok = np.array_equal(x, y)
        assert ok, f"NON-BIT-EXACT round-trip for {c.name} (non-negotiable #1)"
        mb = x.nbytes / 1e6
        rows.append(dict(name=c.name, ratio=x.nbytes / len(blob), comp=len(blob),
                         enc=mb / enc if enc else float("inf"),
                         dec=mb / dec if dec else float("inf"), ok=ok,
                         embedded=("OK" if c.cost.embedded_ok else "no"),
                         neural=("OK" if c.cost.neural_ok else "-"),
                         cost=round(c.cost.cost, 4), family=c.family))
    return rows


def _xchan_gain(rows):
    """Achieved cross-channel gain per predictor family (xchan vs its sibling)."""
    by = {r["name"]: r for r in rows}
    out = {}
    for base, xc in (("LMS+Rice", "LMS+Rice+xchan"),
                     ("delta+Rice", "delta+Rice+xchan")):
        if base in by and xc in by:
            out[xc] = 100 * (by[xc]["ratio"] / by[base]["ratio"] - 1)
    return out


def run(datasets, max_samples, csv_path):
    all_rows = []
    for ds in datasets:
        if not ds.available():
            print(f"\n### {ds.name}: pending (unavailable here) -- skipped")
            continue
        x, grid = ds.load(max_samples=max_samples)
        cols = grid[1]
        is_real = ds.kind != "synthetic"
        print(f"\n### {ds.name}  [{x.shape[0]} ch x {x.shape[1]} samp @ {ds.fs:.0f} Hz, "
              f"grid {grid}, {'REAL' if is_real else 'synthetic'}]  "
              f"{x.nbytes/1e6:.2f} MB raw")

        rows = _ref_rows(x) + _codec_rows(x, cols)
        flac = next((r for r in rows if r["name"] == "flac"), None)
        gains = _xchan_gain(rows)

        print(f"  {'codec':<20}{'ratio':>7}{'MB/s enc':>9}{'%FLAC':>7}"
              f"{'emb':>5}{'cost':>7}")
        print("  " + "-" * 55)
        for r in sorted(rows, key=lambda r: -r["ratio"]):
            pct = 100 * r["ratio"] / flac["ratio"] if flac else float("nan")
            r["pct_of_flac"] = round(pct, 1) if flac else None
            r["xchan_gain"] = round(gains.get(r["name"], float("nan")), 1) \
                if r["name"] in gains else None
            emb = r.get("embedded", "ref")
            costs = f"{r['cost']:.3f}" if r.get("cost") is not None else "  -  "
            print(f"  {r['name']:<20}{r['ratio']:>6.2f}x{r['enc']:>9.1f}"
                  f"{pct:>6.0f}%{emb:>5}{costs:>7}")
            all_rows.append(dict(dataset=ds.name, real=is_real, **{
                k: r.get(k) for k in ("name", "family", "ratio", "comp", "enc",
                                      "dec", "ok", "embedded", "neural", "cost",
                                      "pct_of_flac", "xchan_gain")}))

        if gains:
            print("  xchan gain: " + ", ".join(f"{k} {v:+.1f}%" for k, v in gains.items()))

        # sanity gate (#3): only meaningful on REAL data
        best = max(r["ratio"] for r in rows)
        if is_real and best > SANITY_MAX:
            print(f"\n*** SANITY GATE: {ds.name} best ratio {best:.1f}x > {SANITY_MAX}x "
                  f"on REAL data -- likely a leak/degenerate. STOP and inspect. ***")

    if csv_path and all_rows:
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader(); w.writerows(all_rows)
        print(f"\nwrote {csv_path}  ({len(all_rows)} rows)")
    return all_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", nargs="*", default=None,
                    help="dataset names to run (default: all available)")
    ap.add_argument("--max-samples", type=int, default=8000)
    ap.add_argument("--csv", default="results/03_bench.csv")
    args = ap.parse_args()

    sets = dsmod.corpus()
    if args.datasets:
        sets = [d for d in sets if d.name in args.datasets]
    run(sets, args.max_samples, args.csv)


if __name__ == "__main__":
    main()
