#!/usr/bin/env python3
"""
registry.py  -  Uniform codec registry for the lossless-compression search
(Stage 1 of COMPRESSION_RESEARCH_AGENT_PROMPT.md).

Every candidate the search ranks is registered here behind ONE interface:

    codec.encode(x, cols=16) -> bytes
    codec.decode(blob)       -> int16 [C, N]   (bit-exact inverse)
    codec.meta               -> embedded_cost.CodecMeta   (feasibility inputs)
    codec.cost               -> embedded_cost.CostScore   (embedded_ok + Pareto cost)

It **wraps** the existing, already-verified codecs in `host_tools/embedded_codec.py`
(delta+Rice, LMS+Rice, and the +xchan cross-channel front-end) -- it does NOT
re-implement them -- and **seeds** the first new candidate from
`compression_spec/candidates.md`: FLAC's four fixed polynomial predictors with
pick-best-per-block order selection, sharing embedded_codec's adaptive Golomb-Rice
back-end.

Run `python research/registry.py --selftest` to round-trip every registered codec
on random int16 and print ratio + cost. This is the command the PostToolUse
verifier hook runs, so a broken/lossy codec here blocks the loop.
"""
import argparse
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host_tools"))
import embedded_codec as ec  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
import embedded_cost as cost  # noqa: E402
from embedded_cost import CodecMeta  # noqa: E402


# ===========================================================================
# NEW seeded candidate: fixed polynomial predictors (orders 0-3) + Rice.
# ---------------------------------------------------------------------------
# FLAC's "fixed" subframe predictors are integer differences of order 0..3:
#     p=0: pred = 0                       (res = x)
#     p=1: pred = x[t-1]                  (1st difference)
#     p=2: pred = 2x[t-1] - x[t-2]        (2nd difference)
#     p=3: pred = 3x[t-1] - 3x[t-2] + x[t-3]   (3rd difference)
# res[t] = x[t] - pred. All integer-exact and causal (samples before t=0 are
# treated as 0, identically in encode and decode). We pick the best order PER
# BLOCK (same BLOCK as the Rice coder) by estimated coded length, and store one
# order byte per block as tiny side-info. The candidate residuals for every order
# depend only on x (not on which order neighbouring blocks chose), so selection
# is free to switch per block and the decoder can still invert sequentially.
# ===========================================================================
FIXED_MAGIC = 0x4658  # 'FX'
FBLOCK = ec.BLOCK      # reuse the Rice block size so order/k blocks align


def _fixed_residuals(xc):
    """All four fixed-predictor residual streams for a 1-D channel (int64)."""
    xc = xc.astype(np.int64)
    x1 = np.concatenate(([0], xc[:-1]))
    x2 = np.concatenate(([0, 0], xc[:-2]))
    x3 = np.concatenate(([0, 0, 0], xc[:-3]))
    r = np.empty((4, xc.size), np.int64)
    r[0] = xc
    r[1] = xc - x1
    r[2] = xc - (2 * x1 - x2)
    r[3] = xc - (3 * x1 - 3 * x2 + x3)
    return r


def _block_bits(res_block):
    """Estimated Rice-coded length (bits) of a residual block, at its best k."""
    u = ec.zigzag(res_block)
    k = ec._best_k(u)
    return int((u >> np.uint64(k)).sum()) + res_block.size * (1 + k)


def _fixed_choose(xc):
    """Return (chosen residual 1-D, per-block order uint8) for one channel."""
    r = _fixed_residuals(xc)
    n = xc.size
    nblocks = (n + FBLOCK - 1) // FBLOCK
    orders = np.zeros(nblocks, np.uint8)
    chosen = np.empty(n, np.int64)
    for b in range(nblocks):
        s, e = b * FBLOCK, min((b + 1) * FBLOCK, n)
        costs = [_block_bits(r[p, s:e]) for p in range(4)]
        p = int(np.argmin(costs))
        orders[b] = p
        chosen[s:e] = r[p, s:e]
    return chosen, orders


def _diff_at(hist, j):
    """D^j x evaluated at the sample just before a block, from the last
    reconstructed samples hist = [x[t-1], x[t-2], x[t-3]] (0 for indices < 0)."""
    if j == 0:
        return hist[0]
    if j == 1:
        return hist[0] - hist[1]
    return hist[0] - 2 * hist[1] + hist[2]  # j == 2


def _fixed_reconstruct(res, orders, n):
    """Invert _fixed_choose for one channel: res (1-D) + per-block orders -> x."""
    x = np.empty(n, np.int64)
    for b in range(len(orders)):
        s, e = b * FBLOCK, min((b + 1) * FBLOCK, n)
        p = int(orders[b])
        hist = [x[s - 1] if s - 1 >= 0 else 0,
                x[s - 2] if s - 2 >= 0 else 0,
                x[s - 3] if s - 3 >= 0 else 0]
        a = res[s:e].astype(np.int64)
        # res is the p-th finite difference of x; integrate p times, each level
        # seeded by that difference's value at the block boundary.
        for j in range(p - 1, -1, -1):
            a = _diff_at(hist, j) + np.cumsum(a)
        x[s:e] = a
    return x


def fixed_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    body = []
    for c in range(C):
        chosen, orders = _fixed_choose(x[c])
        nblocks = orders.size
        body.append(struct.pack("<I", nblocks) + orders.tobytes()
                    + ec.rice_encode_1d(chosen))
    hdr = struct.pack("<HII", FIXED_MAGIC, C, N)
    return hdr + b"".join(body)


def fixed_decode(buf):
    magic, C, N = struct.unpack_from("<HII", buf, 0)
    assert magic == FIXED_MAGIC, "bad fixed-codec magic"
    off = 10
    out = np.empty((C, N), np.int64)
    for c in range(C):
        (nblocks,) = struct.unpack_from("<I", buf, off); off += 4
        orders = np.frombuffer(buf, np.uint8, nblocks, off); off += nblocks
        res, off = ec.rice_decode_1d(buf, off)
        out[c] = _fixed_reconstruct(res, orders, N)
    return out.astype(np.int16)


# ===========================================================================
# NEW candidate: best-partner cross-channel selection + LMS + Rice.
# ---------------------------------------------------------------------------
# The incumbent +xchan front-end subtracts a SINGLE fixed grid parent (left, or
# up for the first column) with an optimal integer gain. On a near-isotropic
# electrode grid the fixed parent is demonstrably not always the best-correlated
# neighbour (LEADERBOARD flags this), so here we let each channel CHOOSE its
# partner from a bounded set of causally-available neighbours -- all with grid
# index < g, so the decoder can reconstruct in channel order:
#     left   = g-1        (col > 0)
#     up     = g-cols     (row > 0)
#     up-left= g-cols-1   (row > 0 and col > 0)
#     up-right=g-cols+1   (row > 0 and col < cols-1)
# For each candidate we derive the optimal integer gain beta (rounded integer
# least-squares, no float in the transform) and estimate the Rice-coded length of
# the resulting cross-residual; we keep the partner (or NONE) with the fewest
# estimated bits. The chosen (parent, beta) pair per channel is tiny explicit
# side-info (two int16 / channel) carried in the format, so encoder and decoder
# use identical, causally-available data. Everything downstream (LMS temporal
# predictor + adaptive Rice) is reused verbatim from embedded_codec.
# ===========================================================================
BP_MAGIC = 0x5042   # 'BP'
BP_SHIFT = ec.CROSS_SHIFT   # same fixed-point gain scale as the incumbent xchan


def _bp_candidates(g, cols, C):
    """Causally-available grid neighbours of channel g (all index < g)."""
    r, c = divmod(g, cols)
    cands = []
    if c > 0:
        cands.append(g - 1)               # left
    if r > 0:
        cands.append(g - cols)            # up
    if r > 0 and c > 0:
        cands.append(g - cols - 1)        # up-left
    if r > 0 and c < cols - 1:
        cands.append(g - cols + 1)        # up-right
    return cands


def _bp_opt_beta(xg, xp, shift):
    """Rounded integer least-squares gain beta ~ <xg,xp>/<xp,xp> * (1<<shift).
    Integer-only (rounded division), clamped to int16 side-info range."""
    denom = int((xp * xp).sum())
    if denom <= 0:
        return 0
    num = int((xg * xp).sum()) << shift
    if num >= 0:
        b = (num + denom // 2) // denom
    else:
        b = -(((-num) + denom // 2) // denom)
    return max(-32768, min(32767, b))


def _bp_score(res1d):
    """Estimated Rice-coded length (bits) of a residual channel at its best k."""
    u = ec.zigzag(np.asarray(res1d, np.int64))
    k = ec._best_k(u)
    return int((u >> np.uint64(k)).sum()) + int(u.size) * (1 + k)


def _bp_select(x, cols):
    """Per-channel best-partner selection. Returns (xt, parents, betas) where
    xt[g] is the cross-decorrelated channel and parents/betas are int64 side-info
    (parent = -1 means the channel is coded as-is)."""
    C, N = x.shape
    x = x.astype(np.int64)
    parents = np.full(C, -1, np.int64)
    betas = np.zeros(C, np.int64)
    xt = x.copy()
    for g in range(C):
        best_bits = _bp_score(x[g])       # option: no cross-channel subtract
        best_p, best_b, best_y = -1, 0, x[g]
        for p in _bp_candidates(g, cols, C):
            b = _bp_opt_beta(x[g], x[p], BP_SHIFT)
            if b == 0:
                continue
            y = x[g] - ((b * x[p]) >> BP_SHIFT)
            bits = _bp_score(y)
            if bits < best_bits:
                best_bits, best_p, best_b, best_y = bits, p, b, y
        parents[g] = best_p
        betas[g] = best_b
        xt[g] = best_y
    return xt, parents, betas


def _bp_inverse(xt, parents, betas):
    """Invert the best-partner front-end. parents[g] < g so the parent channel is
    already reconstructed when we reach g."""
    C, N = xt.shape
    x = xt.astype(np.int64).copy()
    for g in range(C):
        p = int(parents[g])
        if p >= 0:
            x[g] = xt[g] + ((int(betas[g]) * x[p]) >> BP_SHIFT)
    return x


def bestpartner_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    xt, parents, betas = _bp_select(x, cols)
    res = ec.lms_forward(xt)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", BP_MAGIC, cols, C, N)
    side = parents.astype("<i2").tobytes() + betas.astype("<i2").tobytes()
    return hdr + side + body


def bestpartner_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == BP_MAGIC, "bad best-partner codec magic"
    off = 12
    parents = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    betas = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    xt = ec.lms_inverse(res)
    x = _bp_inverse(xt, parents, betas)
    return x.astype(np.int16)


# ===========================================================================
# Uniform codec objects + the registry
# ===========================================================================
class Codec:
    def __init__(self, name, encode, decode, meta, family="", desc=""):
        self.name = name
        self.encode = encode
        self.decode = decode
        self.meta = meta
        self.family = family
        self.desc = desc
        self.cost = cost.score(meta)


def _wrap_embedded(predictor, cross):
    """Adapter: embedded_codec.encode/decode with fixed predictor+cross flags."""
    def enc(x, cols=16):
        return ec.encode(np.asarray(x, np.int64), predictor=predictor,
                         cross=cross, cols=cols)
    return enc, ec.decode


# --- op counts per sample-channel for the cost model (see cost_model.md) ---
# delta: 1 sub + zigzag(2) + rice pack(~6)          ~ 9
# LMS-8: 8 mac + 1 shift + 8-tap sign update(~16) + hist shift(8) + rice(~9) ~ 50
# xchan front-end adds: 1 mul + 1 shift + 1 sub      ~ 3   (per sample-channel)
# fixed:  4 candidate diffs(~12) + per-block argmin(amortised ~1) + rice(~9) ~ 22
_DELTA_OPS = 9
_LMS_OPS = 50
_XCHAN_OPS = 3
_FIXED_OPS = 22

# state bytes/ch: rice-k + small history. LMS keeps order-8 weights+history
# (16 x int16 = 32) + k; delta/fixed keep <=3 past samples + k.
_RICE_STATE = 4
_LMS_STATE = 40
_FIXED_STATE = 10
# xchan (block-adaptive realization): one int16 beta + the parent's current
# sample; the software impl computes beta over the whole array (offline) but the
# embeddable realization computes it per block -> bounded look-ahead = block.
_XCHAN_STATE = 6
_XCHAN_NOTE = ("software impl derives per-channel beta over the whole signal; "
               "embeddable realization computes beta per block (look-ahead=block)")

REGISTRY = {}


def _register(c):
    REGISTRY[c.name] = c
    return c


# existing, already-verified codecs (wrapped, not rebuilt)
_e, _d = _wrap_embedded(ec.PRED_DELTA, False)
_register(Codec("delta+Rice", _e, _d, CodecMeta(
    integer_only=True, enc_ops=_DELTA_OPS, dec_ops=_DELTA_OPS,
    state_bytes_per_ch=_RICE_STATE, causal=True, lookahead_samples=0,
    block_size=ec.BLOCK), family="temporal", desc="order-1 DPCM + adaptive Rice"))

_e, _d = _wrap_embedded(ec.PRED_LMS, False)
_register(Codec("LMS+Rice", _e, _d, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS, dec_ops=_LMS_OPS,
    state_bytes_per_ch=_LMS_STATE, causal=True, lookahead_samples=0,
    block_size=ec.BLOCK), family="temporal", desc="sign-sign LMS order-8 + Rice"))

_e, _d = _wrap_embedded(ec.PRED_DELTA, True)
_register(Codec("delta+Rice+xchan", _e, _d, CodecMeta(
    integer_only=True, enc_ops=_DELTA_OPS + _XCHAN_OPS, dec_ops=_DELTA_OPS + _XCHAN_OPS,
    state_bytes_per_ch=_RICE_STATE + _XCHAN_STATE, causal=True,
    lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_XCHAN_NOTE),
    family="cross-channel", desc="delta + grid-neighbour decorrelation"))

_e, _d = _wrap_embedded(ec.PRED_LMS, True)
_register(Codec("LMS+Rice+xchan", _e, _d, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _XCHAN_OPS, dec_ops=_LMS_OPS + _XCHAN_OPS,
    state_bytes_per_ch=_LMS_STATE + _XCHAN_STATE, causal=True,
    lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_XCHAN_NOTE),
    family="cross-channel", desc="LMS + grid-neighbour decorrelation (current best)"))

# NEW candidate: best-partner cross-channel selection (this cycle).
# Encode adds, on top of LMS+xchan, a per-channel scan over <=4 causal-neighbour
# candidates (each ~2 MACs/sample to accumulate <xg,xp> and <xp,xp>) to pick the
# best partner -> ~8 extra enc ops/sample-ch; the decoder does NOT search (it
# reads the chosen parent+beta side-info), so its op count matches plain xchan.
# State adds one parent-id byte/ch beyond the incumbent xchan state.
_BP_SELECT_OPS = 8
_BP_STATE = _XCHAN_STATE + 1
_BP_NOTE = ("per-channel best-partner: encoder scans <=4 causal grid neighbours "
            "(left/up/up-left/up-right, all idx<g) and picks the min-Rice-bits "
            "partner + integer gain; chosen (parent,beta) carried as 2xint16/ch "
            "side-info. Selection derived offline over the whole signal (like the "
            "incumbent xchan beta); embeddable realization selects per block "
            "(look-ahead=block). Decoder is search-free.")
_register(Codec("LMS+Rice+xchan_bestpartner", bestpartner_encode, bestpartner_decode,
    CodecMeta(
        integer_only=True, enc_ops=_LMS_OPS + _XCHAN_OPS + _BP_SELECT_OPS,
        dec_ops=_LMS_OPS + _XCHAN_OPS,
        state_bytes_per_ch=_LMS_STATE + _BP_STATE, causal=True,
        lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_BP_NOTE),
    family="cross-channel",
    desc="LMS + best-of-4 causal-neighbour cross-channel selection + Rice"))

# NEW seeded candidate
_register(Codec("fixed0-3+Rice", fixed_encode, fixed_decode, CodecMeta(
    integer_only=True, enc_ops=_FIXED_OPS, dec_ops=_FIXED_OPS,
    state_bytes_per_ch=_FIXED_STATE, causal=True, lookahead_samples=ec.BLOCK,
    block_size=ec.BLOCK), family="temporal",
    desc="FLAC fixed predictors ord 0-3, best-per-block + Rice"))


def list_codecs():
    return list(REGISTRY.values())


# ===========================================================================
def _selftest():
    rng = np.random.default_rng(0)
    # A realistic-ish int16 field: correlated noise floor + spikes + a shared
    # common-mode, on an 8x16 grid, so cross-channel codecs are exercised too.
    C, N, cols = 32, 2500, 8
    base = rng.normal(0, 12, (C, N))
    common = rng.normal(0, 6, N)                       # shared common-mode
    x = (base + 0.5 * common).round().astype(np.int16)
    x[5, 800:820] += 500                               # a spike burst
    x[6, 800:820] += 300

    print(f"registry self-test on random int16 [{C} x {N}], {len(REGISTRY)} codecs\n")
    print(f"{'codec':<20}{'ratio':>7}{'round-trip':>12}{'emb_ok':>8}"
          f"{'neural':>8}{'cost':>8}")
    print("-" * 63)
    all_ok = True
    for c in list_codecs():
        blob = c.encode(x, cols=cols)
        y = c.decode(blob)
        ok = np.array_equal(x, y)
        all_ok &= ok
        ratio = x.nbytes / len(blob)
        print(f"{c.name:<20}{ratio:>6.2f}x{('OK' if ok else 'FAIL!'):>12}"
              f"{('OK' if c.cost.embedded_ok else 'no'):>8}"
              f"{('OK' if c.cost.neural_ok else '-'):>8}{c.cost.cost:>8.3f}")
        assert ok, f"round-trip mismatch for {c.name}"
    assert all_ok
    print("\nregistry self-test: ALL round-trips bit-exact")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    # default action is the self-test (the verifier hook invokes with --selftest)
    _selftest()
