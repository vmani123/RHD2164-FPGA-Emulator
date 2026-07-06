#!/usr/bin/env python3
"""
search.py  -  Hill-climb the lossless-codec design space (Stage 4 of
COMPRESSION_RESEARCH_AGENT_PROMPT.md).

Maximizes compression ratio **subject to `embedded_ok`** over the knobs that
matter for an on-node predictor+Rice codec:

    predictor family : delta | lms | fixed0-3
    lms order        : 4 | 8 | 12 | 16          (taps)
    lms shift        : 6..10                     (fixed-point weight scale)
    cross-channel    : off | grid-neighbour decorrelation
    cross shift      : 6..10                     (beta fixed-point scale)
    rice block       : 128 | 256 | 512           (adaptive-k block)

Every candidate is built ONLY from `embedded_codec.py`'s already-proven,
integer-exact primitives (delta/lms/fixed predictors, cross front-end, adaptive
Golomb-Rice), so it is lossless by construction -- and the search still asserts a
bit-exact round-trip on every evaluation (non-negotiable #1). Numbers come from
measured encoded size, never from reasoning (#4). Ranking uses the ratio-vs-cost
Pareto front, never ratio alone (#2).

Real data decides (#3); real datasets are network-blocked in this container, so
the search runs on the synthetic corpus (spatial-corr 0.3/0.6/0.9) as a
mechanism/infrastructure demonstration. Re-run on Hyser once the host is
reachable: `--datasets hyser_1dof_f1_s1`.

Usage:
    python3 research/search.py                     # hill-climb + ablation + Pareto
    python3 research/search.py --datasets hyser_1dof_f1_s1 --max-samples 15000
    python3 research/search.py --csv results/04_search.csv
"""
import argparse
import csv
import os
import sys

import numpy as np

HOST = os.path.join(os.path.dirname(__file__), "..", "host_tools")
sys.path.insert(0, HOST)
sys.path.insert(0, os.path.dirname(__file__))
import embedded_codec as ec   # noqa: E402
import registry as reg        # noqa: E402  (fixed predictor helpers)
import embedded_cost as ecost  # noqa: E402
import datasets as dsmod      # noqa: E402


# ---------------------------------------------------------------------------
# Parameterized codec: predict (+ optional cross front-end) then adaptive Rice.
# Built from ec primitives so decode is the exact inverse; bit-exact by
# construction and asserted on every eval.
# ---------------------------------------------------------------------------
def _encode(x, cfg):
    C, N = x.shape
    old_block = ec.BLOCK
    ec.BLOCK = cfg["block"]              # rice_encode_1d reads BLOCK at call time
    try:
        if cfg["cross"]:
            parent = ec.grid_parents(C, cfg["cols"])
            betas = ec.cross_betas(x, parent, shift=cfg["cross_shift"])
            xt = ec.cross_forward(x, parent, betas, shift=cfg["cross_shift"])
        else:
            xt = np.asarray(x, np.int64)

        if cfg["pred"] == "delta":
            res = ec.delta_forward(xt)
            body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
        elif cfg["pred"] == "lms":
            res = ec.lms_forward(xt, order=cfg["order"], shift=cfg["shift"])
            body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
        elif cfg["pred"] == "fixed":
            parts = []
            for c in range(C):
                chosen, orders = reg._fixed_choose(xt[c])
                parts.append(bytes([orders.size & 0xFF, (orders.size >> 8) & 0xFF])
                             + orders.tobytes() + ec.rice_encode_1d(chosen))
            body = b"".join(parts)
        else:
            raise ValueError(cfg["pred"])
    finally:
        ec.BLOCK = old_block
    return body, (betas if cfg["cross"] else None)


def _size(x, cfg):
    """Compressed size in bytes (+ tiny side-info), with a bit-exact round-trip
    assert. Returns len(blob). Raises on any non-bit-exact config."""
    C, N = x.shape
    old_block = ec.BLOCK
    ec.BLOCK = cfg["block"]
    try:
        body, betas = _encode(x, cfg)
        # decode + verify
        if cfg["cross"]:
            parent = ec.grid_parents(C, cfg["cols"])
        off = 0
        res = np.empty((C, N), np.int64)
        if cfg["pred"] == "fixed":
            for c in range(C):
                nb = body[off] | (body[off + 1] << 8); off += 2
                orders = np.frombuffer(body, np.uint8, nb, off); off += nb
                arr, off = ec.rice_decode_1d(body, off)
                res[c] = reg._fixed_reconstruct(arr, orders, N)
            xt = res
        else:
            for c in range(C):
                arr, off = ec.rice_decode_1d(body, off)
                res[c] = arr
            xt = (ec.lms_inverse(res, order=cfg["order"], shift=cfg["shift"])
                  if cfg["pred"] == "lms" else ec.delta_inverse(res))
        if cfg["cross"]:
            xt = ec.cross_inverse(xt, parent, betas, shift=cfg["cross_shift"])
        y = xt.astype(np.int16)
        assert np.array_equal(x, y), f"NON-BIT-EXACT config {cfg}"
    finally:
        ec.BLOCK = old_block
    side = (2 * C) if cfg["cross"] else 0        # int16 betas
    return len(body) + side


# ---------------------------------------------------------------------------
# Cost of a config (embedded_cost.CodecMeta from the knobs)
# ---------------------------------------------------------------------------
def _meta(cfg):
    rice_ops = 9                          # zigzag + adaptive-Rice pack, per sample-ch
    if cfg["pred"] == "lms":
        M = cfg["order"]
        pred_ops = 2 * M + 2               # M macs + M sign-updates + shift
        pred_state = 2 * M * 2             # weights + history, int16 each
    elif cfg["pred"] == "fixed":
        pred_ops, pred_state = 13, 8       # 4 diffs + per-block argmin; 3 past samples
    else:                                   # delta
        pred_ops, pred_state = 1, 6
    x_ops = 3 if cfg["cross"] else 0
    x_state = 6 if cfg["cross"] else 0
    look = cfg["block"] if cfg["cross"] else 0
    return ecost.CodecMeta(
        integer_only=True, enc_ops=rice_ops + pred_ops + x_ops,
        dec_ops=rice_ops + pred_ops + x_ops,
        state_bytes_per_ch=4 + pred_state + x_state, causal=True,
        lookahead_samples=look, block_size=cfg["block"])


def _label(cfg):
    p = cfg["pred"]
    if p == "lms":
        base = f"lms{cfg['order']}s{cfg['shift']}"
    else:
        base = p
    x = f"+x{cfg['cross_shift']}" if cfg["cross"] else ""
    return f"{base}{x}/b{cfg['block']}"


# ---------------------------------------------------------------------------
# Evaluation over the corpus
# ---------------------------------------------------------------------------
def _mean_ratio(datasets_xy, cfg):
    """Mean compression ratio across the loaded corpus (all bit-exact)."""
    rs = []
    for name, x, cols in datasets_xy:
        c = dict(cfg, cols=cols)
        rs.append(x.nbytes / _size(x, c))
    return float(np.mean(rs))


AXES = {
    "pred":        ["delta", "lms", "fixed"],
    "order":       [4, 8, 12, 16],
    "shift":       [6, 7, 8, 9, 10],
    "cross":       [False, True],
    "cross_shift": [6, 7, 8, 9, 10],
    "block":       [128, 256, 512],
}


def _neighbors(cfg):
    """One-axis-at-a-time neighbours (order/shift only matter for lms; cross_shift
    only when cross is on)."""
    out = []
    for axis, vals in AXES.items():
        if axis in ("order", "shift") and cfg["pred"] != "lms":
            continue
        if axis == "cross_shift" and not cfg["cross"]:
            continue
        for v in vals:
            if cfg[axis] == v:
                continue
            nb = dict(cfg, **{axis: v})
            out.append(nb)
    return out


def hill_climb(datasets_xy, start, log, evaluated):
    cur = dict(start)
    cur_r = _cached(datasets_xy, cur, evaluated)
    log.append(f"start {_label(cur)}: ratio {cur_r:.4f}  emb_ok="
               f"{ecost.score(_meta(cur)).embedded_ok}")
    step = 0
    while True:
        step += 1
        best, best_r = None, cur_r
        for nb in _neighbors(cur):
            if not ecost.score(_meta(nb)).embedded_ok:
                continue                          # hard gate: never leave feasible set
            r = _cached(datasets_xy, nb, evaluated)
            if r > best_r + 1e-6:
                best, best_r = nb, r
        if best is None:
            break
        log.append(f"step {step}: {_label(cur)} -> {_label(best)}  "
                   f"ratio {cur_r:.4f} -> {best_r:.4f}  (+{100*(best_r/cur_r-1):.2f}%)")
        cur, cur_r = best, best_r
    log.append(f"converged at {_label(cur)}: ratio {cur_r:.4f}")
    return cur, cur_r


def _cached(datasets_xy, cfg, evaluated):
    key = (_label(cfg),)
    if key in evaluated:
        return evaluated[key][0]
    r = _mean_ratio(datasets_xy, cfg)
    cost = ecost.score(_meta(cfg))
    evaluated[key] = (r, cost, dict(cfg))
    return r


# ---------------------------------------------------------------------------
def pareto_front(evaluated):
    """Configs not dominated on (ratio up, cost down), embedded_ok only."""
    pts = [(lab, r, cost.cost, cfg) for (lab,), (r, cost, cfg) in evaluated.items()
           if cost.embedded_ok]
    front = []
    for lab, r, c, cfg in pts:
        dominated = any((r2 >= r and c2 <= c and (r2 > r or c2 < c))
                        for _, r2, c2, _ in pts)
        if not dominated:
            front.append((lab, r, c, cfg))
    return sorted(front, key=lambda t: t[2])   # by cost ascending


def ablate(datasets_xy, best, evaluated):
    """From the best config, revert each axis toward the cheap baseline and report
    the ratio it costs -- 'what mattered'."""
    baseline = dict(pred="lms", order=8, shift=8, cross=False, cross_shift=8, block=256)
    best_r = _cached(datasets_xy, best, evaluated)
    out = []
    for axis in ("cross", "order", "shift", "block", "pred"):
        if best.get(axis) == baseline.get(axis):
            continue
        rev = dict(best, **{axis: baseline[axis]})
        if not ecost.score(_meta(rev)).embedded_ok:
            continue
        r = _cached(datasets_xy, rev, evaluated)
        out.append((axis, best[axis], baseline[axis], best_r - r,
                    100 * (best_r / r - 1)))
    return sorted(out, key=lambda t: -t[3])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", nargs="*",
                    default=["synth_sc0.3", "synth_sc0.6", "synth_sc0.9"])
    ap.add_argument("--max-samples", type=int, default=6000)
    ap.add_argument("--csv", default="results/04_search.csv")
    args = ap.parse_args()

    sets = {d.name: d for d in dsmod.corpus()}
    datasets_xy = []
    for name in args.datasets:
        ds = sets.get(name)
        if ds is None or not ds.available():
            print(f"skip {name}: unavailable here")
            continue
        x, grid = ds.load(max_samples=args.max_samples)
        datasets_xy.append((name, x, grid[1]))
    if not datasets_xy:
        sys.exit("no datasets available to search")
    real = any(dsmod.corpus() and d.kind != "synthetic"
               for d in sets.values() if d.name in args.datasets and d.available())
    print(f"searching on {[n for n, _, _ in datasets_xy]}  "
          f"({'REAL' if real else 'SYNTHETIC — infrastructure demo, not a headline'})\n")

    evaluated = {}
    log = []
    start = dict(pred="lms", order=8, shift=8, cross=True, cross_shift=8, block=256)
    best, best_r = hill_climb(datasets_xy, start, log, evaluated)
    print("\n".join("  " + l for l in log))

    print(f"\n=== BEST embeddable codec: {_label(best)}  mean ratio {best_r:.3f}x ===")
    bc = ecost.score(_meta(best))
    print(f"    embedded_ok={bc.embedded_ok} neural_ok={bc.neural_ok} "
          f"cost={bc.cost:.3f} enc={bc.enc_cycles:.0f} cyc/sample-ch")

    print("\n=== what mattered (ablation from best) ===")
    for axis, was, to, dr, pct in ablate(datasets_xy, best, evaluated):
        print(f"    {axis:<12} {str(was):>6} -> {str(to):<6}  costs {dr:+.4f}x "
              f"ratio ({pct:+.2f}%)")

    front = pareto_front(evaluated)
    print(f"\n=== Pareto front (ratio vs cost, embedded_ok only, {len(evaluated)} "
          f"configs evaluated) ===")
    print(f"    {'config':<20}{'ratio':>8}{'cost':>8}{'neural':>8}")
    for lab, r, c, cfg in front:
        n = "OK" if ecost.score(_meta(cfg)).neural_ok else "-"
        print(f"    {lab:<20}{r:>7.3f}x{c:>8.3f}{n:>8}")

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["config", "mean_ratio", "cost", "embedded_ok", "neural_ok",
                        "on_pareto"])
            fl = {lab for lab, _, _, _ in front}
            for (lab,), (r, cost, cfg) in sorted(evaluated.items(),
                                                 key=lambda kv: -kv[1][0]):
                w.writerow([lab, round(r, 4), round(cost.cost, 4),
                            cost.embedded_ok, cost.neural_ok, lab in fl])
        print(f"\nwrote {args.csv} ({len(evaluated)} configs)")


if __name__ == "__main__":
    main()
