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
# NEW candidate: backward-adaptive per-block cross-channel beta.
# ---------------------------------------------------------------------------
# The existing +xchan front-end (embedded_codec.cross_betas/forward/inverse)
# derives ONE gain `beta` per channel over the WHOLE recording (a float
# least-squares ratio) and ships it as header side-info -- i.e. it needs the
# whole signal offline to pick beta. This variant makes the gain (i) track
# non-stationarity and (ii) removes the look-ahead AND the side-info:
#
#   * beta for block i is (re)estimated from the PREVIOUS block's ALREADY-
#     reconstructed samples of the channel and its grid parent, using
#     integer-only fixed-point arithmetic (two dot-products + one rounded
#     integer divide per block-channel). Because the reconstruction is lossless,
#     the parent/child samples the decoder has after block i-1 are bit-identical
#     to the encoder's, so the decoder recomputes the SAME beta causally and NO
#     beta is transmitted.
#   * Block 0 bootstraps with beta = 0 (no prior block exists yet), so the first
#     block is coded as if xchan were off; the gain then adapts each block.
#
# Everything downstream (grid parent tree, order-8 sign-sign LMS, adaptive
# Golomb-Rice) is identical to the current best codec "LMS+Rice+xchan"; only the
# gain-estimation is swapped from whole-signal side-info to backward-adaptive.
# ===========================================================================
XADAPT_MAGIC = 0x5841        # 'XA'
XADAPT_BLOCK = ec.BLOCK      # cross-channel adaptation block (samples); tunable
XADAPT_SHIFT = ec.CROSS_SHIFT  # fixed-point scale for beta (matches the family)


def _int_beta(num, den, shift=XADAPT_SHIFT):
    """Integer-only fixed-point gain ~= round(num/den * 2**shift) for den > 0,
    clamped to int16. No float anywhere; deterministic and therefore identical
    on encode and decode. `den` is a sum of squares so it is always >= 0."""
    d = int(den)
    if d <= 0:
        return 0
    numer = int(num) << shift
    if numer >= 0:
        b = (numer + d // 2) // d          # symmetric round-half-up
    else:
        b = -(((-numer) + d // 2) // d)
    if b > 32767:
        return 32767
    if b < -32768:
        return -32768
    return b


def _beta_from_block(xc_blk, xp_blk, shift=XADAPT_SHIFT):
    """Backward-adaptive gain from ONE already-reconstructed block: the
    least-squares ratio <x_c, x_p> / <x_p, x_p>, integer/fixed-point. Identical
    call on both sides guarantees the same beta bit-for-bit."""
    xc = xc_blk.astype(np.int64)
    xp = xp_blk.astype(np.int64)
    num = int((xc * xp).sum())
    den = int((xp * xp).sum())
    return _int_beta(num, den, shift)


def _xadapt_forward(x, parent, B=XADAPT_BLOCK, shift=XADAPT_SHIFT):
    """Cross-channel decorrelation with backward-adaptive per-block gain.
    y[c,t] = x[c,t] - ((beta[c,block(t)] * x[parent,t]) >> shift), beta derived
    from the previous block. Operates on the RAW signal, which the decoder
    reconstructs bit-exactly, so the betas match."""
    x = x.astype(np.int64)
    C, N = x.shape
    y = x.copy()
    nblocks = (N + B - 1) // B
    for c in range(C):
        p = parent[c]
        if p < 0:                          # root channel: no parent to subtract
            continue
        beta = 0                           # block-0 bootstrap
        for i in range(nblocks):
            s, e = i * B, min((i + 1) * B, N)
            if i > 0:
                beta = _beta_from_block(x[c, (i - 1) * B:i * B],
                                        x[p, (i - 1) * B:i * B], shift)
            y[c, s:e] = x[c, s:e] - ((beta * x[p, s:e]) >> shift)
    return y


def _xadapt_inverse(y, parent, B=XADAPT_BLOCK, shift=XADAPT_SHIFT):
    """Invert _xadapt_forward. parent[c] < c so the parent channel is fully
    reconstructed before c; within a channel, block i's beta is recomputed from
    the already-reconstructed block i-1 -- exactly mirroring the encoder."""
    y = y.astype(np.int64)
    C, N = y.shape
    x = y.copy()                           # root channels already correct
    nblocks = (N + B - 1) // B
    for c in range(C):
        p = parent[c]
        if p < 0:
            continue
        beta = 0
        for i in range(nblocks):
            s, e = i * B, min((i + 1) * B, N)
            if i > 0:
                beta = _beta_from_block(x[c, (i - 1) * B:i * B],
                                        x[p, (i - 1) * B:i * B], shift)
            x[c, s:e] = y[c, s:e] + ((beta * x[p, s:e]) >> shift)
    return x


def xadapt_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    parent = ec.grid_parents(C, cols)
    y = _xadapt_forward(x, parent)
    res = ec.lms_forward(y)                # order-8 sign-sign LMS (same as family)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", XADAPT_MAGIC, cols, C, N)  # NO beta side-info
    return hdr + body


def xadapt_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == XADAPT_MAGIC, "bad xadapt magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res)
    x = _xadapt_inverse(y, ec.grid_parents(C, cols))
    return x.astype(np.int16)


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
# NEW candidate: fixed reversible integer inter-channel transform (integer-KLT
# via lifting) + per-channel LMS + Rice.
# ---------------------------------------------------------------------------
# Every shipped front-end so far subtracts exactly ONE reference channel (the
# grid parent in +xchan; a selected best partner in +xchan_bestpartner) -- a
# rank-1, single-tap operation. On a near-isotropic HD-EMG grid (neighbour
# correlations ~0.73-0.79) several partners carry shared content one subtraction
# cannot remove. This front-end is the untried MULTI-TAP spatial lever: a fixed,
# offline/data-independent, MULTIPLIERLESS reversible INTEGER transform that
# decorrelates each time-slice across the electrode array, applied BEFORE the
# existing per-channel LMS+Rice temporal back-end.
#
# Reversible integer KLT via lifting (Hao & Shi; Srinivasan et al. IntSKLT):
# any orthogonal transform factors into Givens rotations, and each rotation is
# realised losslessly by THREE integer "lifting" (shear) steps with rounded
# fixed-point coefficients -- reversible by construction (each step adds a
# rounded integer function of the current integer state; the inverse subtracts
# the identical value). No eigendecomposition, no per-block basis, NO side-info.
#
# Data-independent FIXED basis: for two equal-variance channels with covariance
# [[1, r],[r, 1]], the KLT is EXACTLY the +/-45 degree rotation (sum/difference)
# for ANY correlation r -- so a fixed 45-degree rotation is the true KLT of a
# stationary isotropic neighbour pair, needing no training data. We cascade
# these fixed rotations over a fixed grid-neighbour schedule (all horizontal
# adjacent pairs, then all vertical adjacent pairs, in channel order). The
# cascade makes each transformed channel a reversible integer mixture across a
# whole neighbourhood -- genuinely multi-tap, distinct from the rank-1 subtracts.
#
# The transform is applied WITHIN each time-slice (columns are independent), so
# temporal look-ahead is ZERO -- better than the +xchan variants, which need a
# block to estimate beta. The decoder applies the inverse rotations in reverse
# schedule order. Coefficients are global constants (no per-channel state).
# ===========================================================================
IKLT_MAGIC = 0x4B54          # 'KT' (integer-KLT)
IKLT_SHIFT = 12              # fixed-point scale for the lifting coefficients
# 45-degree rotation lifting coefficients (Hao-Shi 3-step factorization):
#   shear P = (cos t - 1)/sin t,  update U = sin t,  at t = 45 deg.
IKLT_P = int(round((0.7071067811865476 - 1.0) / 0.7071067811865476 * (1 << IKLT_SHIFT)))  # -1697
IKLT_U = int(round(0.7071067811865476 * (1 << IKLT_SHIFT)))                                # 2896


def _rmul(coef, v, shift=IKLT_SHIFT):
    """Rounded fixed-point product round(coef * v / 2**shift), integer-only and
    symmetric about zero, vectorized over an int64 array v. Identical on encode
    and decode (same function, same operands) -> the lifting steps cancel
    exactly. `>>` on a non-negative int64 is a floor; we negate for v*coef < 0 so
    rounding is symmetric rather than toward -inf."""
    p = coef * v.astype(np.int64)
    half = np.int64(1 << (shift - 1))
    pos = (p + half) >> np.int64(shift)
    neg = -(((-p) + half) >> np.int64(shift))
    return np.where(p >= 0, pos, neg)


def _rot_forward(a, b, P=IKLT_P, U=IKLT_U, shift=IKLT_SHIFT):
    """One reversible integer Givens rotation of two channel rows (each 1-D over
    time), as three lifting steps. In-place-safe: returns new arrays."""
    a = a + _rmul(P, b, shift)
    b = b + _rmul(U, a, shift)
    a = a + _rmul(P, b, shift)
    return a, b


def _rot_inverse(a, b, P=IKLT_P, U=IKLT_U, shift=IKLT_SHIFT):
    """Exact inverse of _rot_forward: undo the three lifting steps in reverse."""
    a = a - _rmul(P, b, shift)
    b = b - _rmul(U, a, shift)
    a = a - _rmul(P, b, shift)
    return a, b


def _iklt_pairs(C, cols):
    """Fixed grid-neighbour rotation schedule: all horizontal adjacent pairs in
    channel order, then all vertical adjacent pairs. Deterministic from (C, cols)
    so encode and decode build the identical list."""
    pairs = []
    for g in range(C):
        r, c = divmod(g, cols)
        if c > 0:
            pairs.append((g - 1, g))       # horizontal neighbour pair
    for g in range(C):
        r, c = divmod(g, cols)
        if r > 0:
            pairs.append((g - cols, g))    # vertical neighbour pair
    return pairs


def _iklt_forward(x, cols):
    """Apply the fixed reversible integer inter-channel transform per time-slice
    (vectorized over time). Returns the transformed [C, N] int64 array."""
    C = x.shape[0]
    y = x.astype(np.int64).copy()
    for a, b in _iklt_pairs(C, cols):
        y[a], y[b] = _rot_forward(y[a], y[b])
    return y


def _iklt_inverse(y, cols):
    """Invert _iklt_forward by applying the inverse rotations in REVERSE order."""
    C = y.shape[0]
    x = y.astype(np.int64).copy()
    for a, b in reversed(_iklt_pairs(C, cols)):
        x[a], x[b] = _rot_inverse(x[a], x[b])
    return x


def iklt_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _iklt_forward(x, cols)
    res = ec.lms_forward(y)                 # order-8 sign-sign LMS (same as family)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", IKLT_MAGIC, cols, C, N)   # NO transform side-info
    return hdr + body


def iklt_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == IKLT_MAGIC, "bad integer-KLT codec magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res)
    x = _iklt_inverse(y, cols)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: DATA-DEPENDENT adaptive integer-lifting rotation cascade
# (backward-adaptive Givens angle) + per-channel LMS + Rice.
# ---------------------------------------------------------------------------
# This is the retired fixed integer-KLT (`LMS+Rice+iklt`) with its ONE fatal
# assumption removed. The retired variant rotated every grid-neighbour pair by a
# FIXED 45 degrees -- the exact KLT only of a *stationary, isotropic,
# equal-variance* pair. INSIGHTS P3 MEASURED that this fixed basis captured only
# ~+8.8% real cross-channel gain vs the data-dependent single-neighbour subtract's
# +18.0%, because real HD-sEMG covariance is anisotropic and non-stationary: the
# whole gap is basis mismatch. Here the rotation ANGLE becomes data-dependent and
# backward-adaptive, closing that gap while staying multiplierless and lossless.
#
# Mechanism (keeps the reversible 3-lift shear butterfly of the iklt verbatim --
# multiplierless, lossless by construction for ANY integer lift coefficients):
#   * Per grid-neighbour pair (a,b) and per time-block i, choose a QUANTIZED
#     Givens angle theta from the pair's 2x2 covariance [[saa,sab],[sab,sbb]]
#     accumulated over the PREVIOUS block (i-1) of the already-reconstructed RAW
#     channels. The chosen angle is the tabulated theta that minimizes the
#     post-rotation off-diagonal |s'_ab| = |0.5(sbb-saa)sin2t + sab cos2t| -- i.e.
#     the discrete argmin of the true 2x2 decorrelating rotation, evaluated with
#     an integer sin/cos table (no atan, no float, no eigendecomposition).
#   * theta[block i] depends only on RAW block i-1, which the decoder reconstructs
#     bit-exactly before it reaches block i (it inverts the cascade block-by-block
#     in time order), so the decoder recomputes the SAME angle -> ZERO side-info,
#     fully causal, look-ahead 0 (backward-adaptive, INSIGHTS P4). Block 0
#     bootstraps to the identity angle (theta=0), so it is coded as if the spatial
#     transform were off; the basis then adapts each block.
#   * The angles are applied as a CASCADE over the fixed grid-neighbour schedule
#     (all horizontal adjacent pairs, then all vertical) -- reusing _iklt_pairs --
#     so each transformed channel becomes a reversible integer mixture across a
#     whole neighbourhood: genuinely MULTI-TAP, distinct from the rank-1
#     single-neighbour subtract of +xchan/xadapt/bestpartner.
#
# Distinct from BOTH retired mechanisms on the axis INSIGHTS P3 names decisive:
#   - vs `LMS+Rice+iklt` (retired): data-INDEPENDENT fixed 45deg basis -> here
#     data-DEPENDENT per-pair/per-block angle (the exact lever P3 says is decisive).
#   - vs `LMS+Rice+xchan_adaptive` (retired): an asymmetric rank-1 subtract of ONE
#     channel with a scalar beta -> here an energy-preserving ORTHOGONAL rotation of
#     BOTH channels, cascaded to a multi-tap transform.
# Behind it: the identical order-8 sign-sign LMS + adaptive Rice back-end as the
# whole family (unchanged, so the ONLY variable vs the retired iklt is the basis).
#
# Bases: Srinivasan et al. IntSKLT (reversible-integer KLT via ladder/lifting,
# IEEE 7071329); RGate (lifted-Givens integer-reversible transform + backward-
# adaptive Golomb-Rice, 221578983); reversible integer TDLT/KLT cross-channel
# decorrelation (IEEE 5075592). Paper-reported context; unverified here.
# ===========================================================================
ITSKLT_MAGIC = 0x4954         # 'IT'  (Integer adaptive Transform)
ITSKLT_SHIFT = IKLT_SHIFT     # lift-coefficient fixed point (as the retired iklt)
ITSKLT_TRIG_SHIFT = 14        # sin/cos fixed point used only for angle SELECTION
ITSKLT_BLOCK = ec.BLOCK       # backward-adaptation block (aligns with Rice block)


def _itsklt_build_table():
    """Build the quantized-angle lifting/selection tables. Float is used HERE, at
    import, to precompute integer constants only (exactly like IKLT_P/IKLT_U
    above) -- the encode/decode PATH that follows uses these integer tables and no
    float. Returns per-angle lift coeffs (P,U at ITSKLT_SHIFT) and selection
    trig (sin2t,cos2t at ITSKLT_TRIG_SHIFT), plus the identity-angle index."""
    degs = np.arange(-60, 61, 4)              # 31 angles incl. 0 (identity)
    P = np.empty(degs.size, np.int64)
    U = np.empty(degs.size, np.int64)
    SIN2 = np.empty(degs.size, np.int64)
    COS2 = np.empty(degs.size, np.int64)
    for j, d in enumerate(degs):
        t = float(np.deg2rad(float(d)))
        s, c = float(np.sin(t)), float(np.cos(t))
        # 3-step lifting factorization of R(theta): P = (cos t - 1)/sin t =
        # -tan(t/2), U = sin t (Hao-Shi / IntSKLT). theta=45deg reproduces IKLT.
        P[j] = 0 if abs(s) < 1e-12 else int(round((c - 1.0) / s * (1 << ITSKLT_SHIFT)))
        U[j] = int(round(s * (1 << ITSKLT_SHIFT)))
        SIN2[j] = int(round(float(np.sin(2.0 * t)) * (1 << ITSKLT_TRIG_SHIFT)))
        COS2[j] = int(round(float(np.cos(2.0 * t)) * (1 << ITSKLT_TRIG_SHIFT)))
    zero_idx = int(np.flatnonzero(degs == 0)[0])
    return P, U, SIN2, COS2, zero_idx


_ITSKLT_P, _ITSKLT_U, _ITSKLT_SIN2, _ITSKLT_COS2, _ITSKLT_ZERO = _itsklt_build_table()


def _itsklt_angle(saa, sbb, sab):
    """Integer-only backward angle selection for the 2x2 covariance
    [[saa,sab],[sab,sbb]]: return the index of the tabulated Givens angle that
    minimizes the post-rotation off-diagonal covariance. Since
    s'_ab = 0.5(sbb-saa)sin2t + sab cos2t, we minimize |(sbb-saa)sin2t + 2 sab
    cos2t| (a common positive scale 2 dropped). All operands are integers, so the
    argmin is deterministic and identical on encode and decode. (saa,sbb,sab are
    sums over <=256 int16 products -> |.| < 3e11; times the <=2^14 trig entries and
    a factor 2 stays < 1e16, comfortably inside int64.)"""
    f = (int(sbb) - int(saa)) * _ITSKLT_SIN2 + (2 * int(sab)) * _ITSKLT_COS2
    return int(np.argmin(np.abs(f)))


def _itsklt_block_angles(xprev, pairs):
    """Angle index for every schedule pair from the previous RAW block xprev
    ([C, B] int64). Covariance per pair is over the ORIGINAL channels (not the
    partially-rotated state), so the decoder -- which reconstructs the raw
    previous block exactly -- derives identical angles."""
    idx = {}
    for (a, b) in pairs:
        xa = xprev[a]
        xb = xprev[b]
        saa = int((xa * xa).sum())
        sbb = int((xb * xb).sum())
        sab = int((xa * xb).sum())
        idx[(a, b)] = _itsklt_angle(saa, sbb, sab)
    return idx


def _itsklt_forward(x, cols, B=ITSKLT_BLOCK):
    """Apply the backward-adaptive integer rotation cascade, per time-block.
    Angles for block i come from RAW block i-1 (block 0 -> identity). Returns the
    transformed [C, N] int64 array."""
    C, N = x.shape
    x = x.astype(np.int64)
    y = x.copy()
    pairs = _iklt_pairs(C, cols)
    nblocks = (N + B - 1) // B
    for i in range(nblocks):
        s, e = i * B, min((i + 1) * B, N)
        if i == 0:
            idx = {pr: _ITSKLT_ZERO for pr in pairs}          # identity bootstrap
        else:
            idx = _itsklt_block_angles(x[:, (i - 1) * B:i * B], pairs)
        for (a, b) in pairs:
            j = idx[(a, b)]
            y[a, s:e], y[b, s:e] = _rot_forward(
                y[a, s:e], y[b, s:e], _ITSKLT_P[j], _ITSKLT_U[j], ITSKLT_SHIFT)
    return y


def _itsklt_inverse(y, cols, B=ITSKLT_BLOCK):
    """Invert _itsklt_forward. We rebuild the RAW signal block-by-block in time
    order; before block i is inverted, block i-1's raw samples are already in x, so
    the same per-pair angles are recomputed and the cascade is undone in REVERSE
    pair order."""
    C, N = y.shape
    y = y.astype(np.int64)
    x = y.copy()
    pairs = _iklt_pairs(C, cols)
    nblocks = (N + B - 1) // B
    for i in range(nblocks):
        s, e = i * B, min((i + 1) * B, N)
        if i == 0:
            idx = {pr: _ITSKLT_ZERO for pr in pairs}
        else:
            idx = _itsklt_block_angles(x[:, (i - 1) * B:i * B], pairs)
        for (a, b) in reversed(pairs):
            j = idx[(a, b)]
            x[a, s:e], x[b, s:e] = _rot_inverse(
                x[a, s:e], x[b, s:e], _ITSKLT_P[j], _ITSKLT_U[j], ITSKLT_SHIFT)
    return x


def itsklt_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _itsklt_forward(x, cols)
    res = ec.lms_forward(y)                  # order-8 sign-sign LMS (same as family)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", ITSKLT_MAGIC, cols, C, N)  # NO transform side-info
    return hdr + body


def itsklt_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == ITSKLT_MAGIC, "bad adaptive integer-KLT codec magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res)
    x = _itsklt_inverse(y, cols)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: table-driven tANS residual entropy back-end vs Rice, on the
# IDENTICAL LMS+Rice+xchan predictor and cross-channel front-end.
# ---------------------------------------------------------------------------
# Every cycle so far moved only the cross-channel FRONT-END; the entropy
# BACK-END (adaptive Golomb-Rice) is the one axis never touched (INSIGHTS P5,
# open-frontier #1). Rice/Golomb is the optimal prefix code ONLY for an exactly
# geometric residual; real EMG residual blocks deviate sub-Golomb, so a
# table-driven ANS coder can recover the sub-Golomb fraction and approach the
# true block entropy. This candidate keeps the incumbent's front-end verbatim
# (grid-parent cross-channel decorrelation with the same fixed-point beta, then
# the order-8 sign-sign LMS temporal predictor) and swaps ONLY the per-channel
# Rice coder for a table-driven tANS (LOCO-ANS style) -- a clean head-to-head
# that isolates the back-end's marginal bits on the identical predictor.
#
# tANS realization (FPGA-friendly: table lookups + renorm, NO per-symbol divide
# on the runtime path; the divisions live only in the once-per-block table
# build). The residual coder is a LOCO-ANS-style bucket+remainder split:
#   * zigzag the residual to u >= 0, split into a "category" c = bit-length(u)
#     (the exponent/bucket, a small bounded alphabet) and c-1 raw "mantissa"
#     bits (the low bits of u). Only the category is entropy-coded; the mantissa
#     bits are near-uniform and shipped raw -- exactly the LOCO-ANS structure
#     that keeps the ANS alphabet small and bounded for ANY int16 input.
#   * Per bounded block (ANS_BLOCK samples) a STATIC normalized frequency table
#     over the categories is built (counts -> integer-normalized to sum = 2^R),
#     shipped as tiny side-info, and used to build the tANS encode/decode
#     tables. The table is deterministic and integer, so encoder and decoder
#     build bit-identical tables from the shipped freqs.
#   * tANS state is normalized to [2^R, 2^(R+1)); the tables are derived from
#     the bitwise-rANS transition C(x,s) = (x//f_s)*M + cum_s + (x mod f_s) but
#     PRECOMPUTED per state (symbol, #renorm-bits, base) so the runtime coder is
#     pure table lookups + a variable-length bit renorm -- the tANS/LOCO-ANS
#     property (no per-symbol division at encode/decode time).
#   * The encoder runs the ANS pass in REVERSE over the block (the reverse-order
#     encode buffer LOCO-ANS/tANS require) and the decoder reads the bitstream
#     forward; ordering is reconciled by reversing the emitted bit list once.
# Embeddability is borderline BY DESIGN (P5): the per-block frequency table is
# real side-info and the reverse pass needs a block buffer, so the payoff is
# expected small and uncertain -- a refinement to MEASURE on real data, not a
# headline lever. Bounded look-ahead = one ANS_BLOCK (streaming-legal).
# ===========================================================================
ANS_MAGIC = 0x414E           # 'AN'
ANS_R = 10                   # tANS table log; table size M = 2**R = 1024
ANS_BLOCK = 2048             # static-frequency-table block (samples); bounded look-ahead


def _ans_bitlen(u):
    """Integer bit-length of each element of a uint64 array (0 -> 0). Pure
    integer (no float log), identical on encode and decode."""
    u = u.astype(np.uint64)
    c = np.zeros(u.size, np.int64)
    tmp = u.copy()
    while tmp.any():
        c += (tmp > 0).astype(np.int64)
        tmp = tmp >> np.uint64(1)
    return c


def _ans_normalize(counts, R):
    """Integer-normalize category counts to a frequency table summing to 2**R,
    with every used symbol getting freq >= 1 (so it stays encodable) and unused
    symbols freq 0. Runs ONLY in the encoder; the decoder reads the resulting
    freqs verbatim, so no cross-side determinism issue -- the shipped table is
    the single source of truth for both sides' identical table build."""
    M = 1 << R
    counts = np.asarray(counts, np.int64)
    total = int(counts.sum())
    freq = np.zeros(counts.size, np.int64)
    for s in np.flatnonzero(counts > 0):
        f = (int(counts[s]) * M) // total
        freq[s] = f if f > 0 else 1
    diff = M - int(freq.sum())               # small: |diff| <= #used symbols
    while diff > 0:                           # give surplus to the commonest symbol
        freq[int(np.argmax(counts))] += 1
        diff -= 1
    while diff < 0:                           # reclaim from a symbol with slack (freq>1)
        freq[int(np.argmax(np.where(freq > 1, freq, -1)))] -= 1
        diff += 1
    return freq


def _ans_build(freq, R, build_enc=True):
    """Build the tANS tables from a frequency table (sum = 2**R). Returns per-
    state decode tables (symbol `symt`, renorm-bit-count `nb`, renorm `base`,
    each length M) and, for the encoder, `enc_slot[s]` mapping the current state
    (x-M) to the destination slot. Derived from the bitwise-rANS transition but
    precomputed so the runtime coder never divides. Deterministic + integer:
    encode and decode build bit-identical tables from the same freqs."""
    M = 1 << R
    A = len(freq)
    cum = np.zeros(A + 1, np.int64)
    for s in range(A):
        cum[s + 1] = cum[s] + int(freq[s])
    symt = np.empty(M, np.int64)
    for s in range(A):
        if freq[s] > 0:
            symt[cum[s]:cum[s + 1]] = s          # cumulative (range-ANS) layout
    nb = np.empty(M, np.int64)
    base = np.empty(M, np.int64)
    for t in range(M):
        s = int(symt[t])
        x_pre = int(freq[s]) + (t - int(cum[s]))  # rANS C^{-1} state, in [f_s, 2 f_s)
        b = R - (x_pre.bit_length() - 1)          # renorm bits to lift into [M, 2M)
        nb[t] = b
        base[t] = x_pre << b                      # renorm base, in [M, 2M)
    enc_slot = None
    if build_enc:
        enc_slot = {s: np.empty(M, np.int64) for s in range(A) if freq[s] > 0}
        for t in range(M):
            s = int(symt[t])
            lo = int(base[t]) - M
            enc_slot[s][lo:lo + (1 << int(nb[t]))] = t
    return symt, nb, base, enc_slot


def _ans_encode_cats(cats, freq):
    """Reverse-order tANS encode of a category block. Returns (X0, packed bytes).
    State X stays in [M, 2M); bits are emitted LSB-first then the whole list is
    reversed once so the decoder can read them forward (ANS is LIFO)."""
    R, M = ANS_R, 1 << ANS_R
    _symt, nb, base, enc_slot = _ans_build(freq, R, build_enc=True)
    emit = []
    X = M                                     # canonical start = decoder's final state
    for s in reversed(cats.tolist()):
        t = int(enc_slot[s][X - M])
        b = X - int(base[t])                  # the nb[t] renorm bits (in [0, 2**nb[t]))
        for j in range(int(nb[t])):
            emit.append((b >> j) & 1)
        X = M + t
    X0 = X                                     # decoder's initial state
    packed = np.packbits(np.array(emit[::-1], np.uint8)).tobytes() if emit else b""
    return X0, packed


def _ans_decode_cats(X0, ans_bytes, n, freq):
    """Forward tANS decode of `n` categories from the (reversed) bitstream."""
    R, M = ANS_R, 1 << ANS_R
    symt, nb, base, _ = _ans_build(freq, R, build_enc=False)
    bits = (np.unpackbits(np.frombuffer(ans_bytes, np.uint8))
            if len(ans_bytes) else np.zeros(0, np.uint8))
    cats = np.empty(n, np.int64)
    X = int(X0)
    p = 0
    for i in range(n):
        t = X - M
        cats[i] = int(symt[t])
        val = 0
        for _ in range(int(nb[t])):           # MSB-first (reconciles the reversal)
            val = (val << 1) | int(bits[p]); p += 1
        X = int(base[t]) + val
    return cats


def _ans_encode_block(res_blk):
    """Encode one residual block: category tANS + raw mantissa bits + freq table."""
    u = ec.zigzag(res_blk.astype(np.int64))
    cats = _ans_bitlen(u)
    cmax = int(cats.max())
    widths = np.maximum(cats - 1, 0)                       # c-1 mantissa bits (0 for c<=1)
    base_val = np.where(cats >= 1, np.left_shift(np.int64(1), widths), np.int64(0))
    mant = u.astype(np.int64) - base_val                  # low bits of u
    total_bits = int(widths.sum())
    if total_bits:
        starts = np.concatenate(([0], np.cumsum(widths)[:-1])).astype(np.int64)
        mbits = np.zeros(total_bits, np.uint8)
        for j in range(int(widths.max())):                # LSB-first, vectorized
            sel = widths > j
            mbits[starts[sel] + j] = ((mant[sel] >> np.int64(j)) & 1).astype(np.uint8)
        mpacked = np.packbits(mbits).tobytes()
    else:
        mpacked = b""
    freq = _ans_normalize(np.bincount(cats, minlength=cmax + 1), ANS_R)
    X0, ans_packed = _ans_encode_cats(cats, freq)
    return (struct.pack("<B", cmax) + freq.astype("<u2").tobytes()
            + struct.pack("<H", X0)
            + struct.pack("<I", len(ans_packed)) + ans_packed
            + struct.pack("<I", len(mpacked)) + mpacked)


def _ans_decode_block(buf, off, n):
    (cmax,) = struct.unpack_from("<B", buf, off); off += 1
    freq = np.frombuffer(buf, "<u2", cmax + 1, off).astype(np.int64); off += 2 * (cmax + 1)
    (X0,) = struct.unpack_from("<H", buf, off); off += 2
    (ans_len,) = struct.unpack_from("<I", buf, off); off += 4
    ans_bytes = buf[off:off + ans_len]; off += ans_len
    (mant_len,) = struct.unpack_from("<I", buf, off); off += 4
    mant_bytes = buf[off:off + mant_len]; off += mant_len
    cats = _ans_decode_cats(X0, ans_bytes, n, freq)
    widths = np.maximum(cats - 1, 0)
    mant = np.zeros(n, np.int64)
    if int(widths.sum()):
        mbits = np.unpackbits(np.frombuffer(mant_bytes, np.uint8))
        starts = np.concatenate(([0], np.cumsum(widths)[:-1])).astype(np.int64)
        for j in range(int(widths.max())):
            sel = widths > j
            mant[sel] |= (mbits[starts[sel] + j].astype(np.int64) << np.int64(j))
    base_val = np.where(cats >= 1, np.left_shift(np.int64(1), widths), np.int64(0))
    u = (base_val + mant).astype(np.uint64)
    return ec.unzigzag(u), off


def _ans_encode_1d(res):
    """tANS entropy-code a 1-D residual channel, static freq table per ANS_BLOCK.
    Same call signature/role as ec.rice_encode_1d -> a drop-in back-end swap."""
    res = np.asarray(res, np.int64)
    n_total = res.size
    out = [struct.pack("<I", n_total)]
    for s in range(0, n_total, ANS_BLOCK):
        out.append(_ans_encode_block(res[s:s + ANS_BLOCK]))
    return b"".join(out)


def _ans_decode_1d(buf, off):
    (n_total,) = struct.unpack_from("<I", buf, off); off += 4
    res = np.empty(n_total, np.int64)
    pos = 0
    while pos < n_total:
        n = min(ANS_BLOCK, n_total - pos)
        blk, off = _ans_decode_block(buf, off, n)
        res[pos:pos + n] = blk
        pos += n
    return res, off


def ans_encode(x, cols=16):
    """LMS+xchan front-end IDENTICAL to the incumbent 'LMS+Rice+xchan'
    (ec.grid_parents/cross_betas/cross_forward + order-8 sign-sign LMS); only
    the per-channel entropy back-end is tANS instead of Rice."""
    x = np.asarray(x, np.int64)
    C, N = x.shape
    parent = ec.grid_parents(C, cols)
    betas = ec.cross_betas(x, parent)
    xt = ec.cross_forward(x, parent, betas)
    res = ec.lms_forward(xt)
    body = b"".join(_ans_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", ANS_MAGIC, cols, C, N)
    side = betas.astype("<i2").tobytes()          # same beta side-info as the incumbent
    return hdr + side + body


def ans_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == ANS_MAGIC, "bad tANS codec magic"
    off = 12
    betas = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = _ans_decode_1d(buf, off)
        res[c] = arr
    xt = ec.lms_inverse(res)
    x = ec.cross_inverse(xt, ec.grid_parents(C, cols), betas)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Adaptive Common Average Reference (ACAR) rank-1 common-mode
# front-end -- reversible-integer common-average removal + per-channel LMS + Rice.
# ---------------------------------------------------------------------------
# Every cross-channel front-end shipped so far removes a LOCAL, pairwise slice of
# the cross-channel redundancy: +xchan/xadapt/bestpartner subtract ONE grid
# neighbour with a gain; the (retired) iklt/iklt_adaptive rotate neighbour PAIRS.
# None of them can fully cancel the GLOBAL common-mode component -- the single
# signal shared by the WHOLE array (movement/EMG drive, power-line pickup,
# reference/electrode drift) -- because a neighbour-difference cancels common mode
# only to the extent the two neighbours share it, and leaves the array-wide DC
# drift and any far-field shared source. Common Average Reference (CAR) is the
# classic HD-EMG montage that removes exactly this: subtract the array mean from
# every channel. This is a genuinely DISTINCT slice of the cross-channel mutual
# information (INSIGHTS P1) from the neighbour subtracts -- a rank-1 GLOBAL lever,
# not a pairwise one -- and it is near-free: one running cross-channel sum per
# time sample plus one subtract, O(1)/sample-ch, RTL-trivial.
#
# Made lossless by a reversible-integer S-transform-style lift that codes the
# array TOTAL as a virtual channel (subtracting the same mean from all channels is
# rank-deficient -- it loses the array DC level -- so the lost degree of freedom
# is preserved by keeping the exact total). Per time slice, over an ON block:
#     S_t   = sum_c x[c,t]              (array total -- the virtual channel)
#     CAR_t = floor(S_t / C)            (integer common average = weighted mean)
#     y[0,t] = S_t                      (root slot carries the total, losslessly)
#     y[c,t] = x[c,t] - CAR_t   (c>=1)  (residual = channel - round(CAR))
# Inverse (exact): CAR_t = floor(y[0,t]/C); x[c,t]=y[c,t]+CAR_t (c>=1);
#     x[0,t] = y[0,t] - sum_{c>=1} x[c,t].  All integer, per-time-slice, no
# look-ahead. Channels 1..C-1 get TRUE mean-referenced CAR residuals (common mode
# removed, only ~1/C of the aggregate noise added back); the single root channel
# is inflated to the total -- the acknowledged rank-1 cost, paid on 1/C of the data.
#
# Gated per block, BACKWARD-ADAPTIVELY (INSIGHTS P4 -- zero side-info): block i is
# transformed only if the common-mode is material in the PREVIOUS reconstructed
# raw block, so the decoder recomputes the identical gate from already-restored
# data. The gate fires iff C*sum(CAR^2)/sum(x^2) exceeds a threshold set ABOVE the
# 1/C floor that independent per-channel noise produces just by array-averaging --
# so it fires only on a genuine shared component and cannot hurt low-common-mode
# segments (they pass through as identity). Block 0 bootstraps OFF (coded as-is),
# and the basis then adapts each block. Behind the front-end: the SAME order-8
# sign-sign LMS + adaptive Rice back-end as the whole family (only the spatial
# front-end differs). Distinct from cycle-1 xadapt (per-block single-neighbour
# beta) and cycle-2 bestpartner (a SELECTED neighbour): here the global array mean,
# not any one channel. Basis: Vaisman/Jordanic/Farina adaptive common-average
# filtering for HD-EMG (MBEC 2014, myocontrol/SNR benefit) -- unverified for
# compression here.
# ===========================================================================
ACAR_MAGIC = 0x4341          # 'CA'
ACAR_BLOCK = ec.BLOCK        # gate/adaptation block (aligns with the Rice block)
ACAR_GATE_NUM = 1            # gate ON iff C*sum(CAR^2)/sum(x^2) > ACAR_GATE_NUM/DEN
ACAR_GATE_DEN = 16           # threshold 1/16 ~ 2/C for C=32: above the noise floor


def _acar_gate(xprev, C):
    """Backward gate from the PREVIOUS raw block. ON iff the array common-mode is
    material enough that removing it (from C-1 channels) pays for inflating the
    root channel to the array total. Integer-only and deterministic, so encoder
    and decoder -- which both reconstruct the raw previous block exactly -- derive
    the identical decision. ON iff C*sum(CAR^2) * DEN > sum(x^2) * NUM, with
    CAR = floor(sum_c x / C). The threshold sits above the 1/C level that pure
    independent noise produces by channel-averaging, so it fires only on a genuine
    shared component. (Sums over <=256 samples of <=32 int16 -> |S|<2^21, CAR^2
    summed over the block < 2^50, times C*DEN < 2^60: inside int64.)"""
    xp = xprev.astype(np.int64)
    S = xp.sum(axis=0)                      # array total per time sample
    car = np.floor_divide(S, C)             # integer common average (floor)
    cm_pow = int((car * car).sum())
    tot_pow = int((xp * xp).sum())
    return cm_pow * C * ACAR_GATE_DEN > tot_pow * ACAR_GATE_NUM


def _acar_forward(x, cols, B=ACAR_BLOCK):
    """Reversible-integer common-average removal, per time-block. ON blocks put the
    array total in the root slot and channel-minus-CAR in the rest; OFF blocks pass
    through unchanged. Returns the transformed [C, N] int64 array. (cols is unused
    -- CAR spans the whole array, grid-agnostic -- but kept for interface parity.)"""
    C, N = x.shape
    x = x.astype(np.int64)
    y = x.copy()
    nblocks = (N + B - 1) // B
    for i in range(nblocks):
        s, e = i * B, min((i + 1) * B, N)
        on = False if i == 0 else _acar_gate(x[:, (i - 1) * B:i * B], C)
        if not on:
            continue                        # identity: low-common-mode block
        blk = x[:, s:e]
        S = blk.sum(axis=0)                 # array total per time slice
        car = np.floor_divide(S, C)         # common average (floor)
        y[0, s:e] = S                       # root slot carries the virtual total
        y[1:, s:e] = blk[1:] - car          # residuals = channel - round(CAR)
    return y


def _acar_inverse(y, cols, B=ACAR_BLOCK):
    """Invert _acar_forward. Rebuilds raw x block-by-block in time order; before
    block i is inverted, raw block i-1 is already restored, so the same backward
    gate is recomputed and the lift is undone exactly."""
    C, N = y.shape
    y = y.astype(np.int64)
    x = y.copy()
    nblocks = (N + B - 1) // B
    for i in range(nblocks):
        s, e = i * B, min((i + 1) * B, N)
        on = False if i == 0 else _acar_gate(x[:, (i - 1) * B:i * B], C)
        if not on:
            continue
        S = y[0, s:e]
        car = np.floor_divide(S, C)
        x[1:, s:e] = y[1:, s:e] + car               # restore channels 1..C-1
        x[0, s:e] = S - x[1:, s:e].sum(axis=0)       # root = total - the rest
    return x


def acar_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _acar_forward(x, cols)
    res = ec.lms_forward(y)                  # order-8 sign-sign LMS (same as family)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", ACAR_MAGIC, cols, C, N)   # NO side-info (backward gate)
    return hdr + body


def acar_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == ACAR_MAGIC, "bad ACAR codec magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res)
    x = _acar_inverse(y, cols)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Order-4 LMS under the best-partner cross-channel front-end.
# ---------------------------------------------------------------------------
# This pairs two levers that were each proven separately but never together:
#   * the SPATIAL lever -- the already-shipped, non-dominated best-partner
#     front-end (`_bp_select`/`_bp_inverse`): each channel picks its best-of-4
#     causal grid neighbour + integer gain, tiny (parent,beta) side-info -- is
#     reused VERBATIM (identical selection, identical side-info, identical
#     inverse), so the spatial basis is unchanged.
#   * the TEMPORAL lever -- INSIGHTS P2 MEASURED on real Hyser/OTB that an
#     order-4 sign-sign LMS beats the order-8 one (deeper prediction fits noise
#     and RAISES coded entropy) at ~half the state/ops. Cycle-2's best-partner
#     was built on the over-provisioned order-8 predictor; here we simply
#     right-size it to order-4.
# So this codec is `LMS+Rice+xchan_bestpartner` with ec.lms_forward/inverse
# called at order=4 instead of the family default (8). No new mechanism, no new
# side-info: the encoder and decoder both run the SAME order-4 sign-sign LMS
# (backward-adaptive, zero side-info -- INSIGHTS P4) so they stay a matched pair.
# The intent (INSIGHTS open-frontier #1) is to dominate the incumbent on BOTH
# axes -- strictly cheaper (half the temporal state/ops) AND >= ratio.
# ===========================================================================
LMS4BP_MAGIC = 0x4C34   # 'L4'
LMS4_ORDER = 4          # right-sized temporal predictor (INSIGHTS P2), vs family's 8


def lms4bp_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    xt, parents, betas = _bp_select(x, cols)          # best-partner front-end (verbatim)
    res = ec.lms_forward(xt, order=LMS4_ORDER)        # order-4 sign-sign LMS (P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", LMS4BP_MAGIC, cols, C, N)
    side = parents.astype("<i2").tobytes() + betas.astype("<i2").tobytes()
    return hdr + side + body


def lms4bp_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == LMS4BP_MAGIC, "bad lms4bp codec magic"
    off = 12
    parents = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    betas = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    xt = ec.lms_inverse(res, order=LMS4_ORDER)        # matched order-4 inverse
    x = _bp_inverse(xt, parents, betas)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Multi-parent backward-adaptive rank-1 subtract
# (LMS+Rice+xchan_multiparent).
# ---------------------------------------------------------------------------
# Every shipped cross-channel front-end that works here is the SAME rank-1
# lever: subtract ONE causal grid neighbour with a gain (+xchan whole-signal
# beta; xadapt/bestpartner variants). INSIGHTS P1-refinement measured that on
# EXTENDED arrays the shared content is spatially LOCAL, so a single neighbour
# leaves a further slice of local cross-channel mutual information uncaptured;
# INSIGHTS open-frontier #2 endorses adding a SECOND causal parent to reach it.
#
# Mechanism: replace the single grid-parent with TWO causal parents -- UP
# (g-cols) and LEFT (g-1), both grid index < g so decode still reconstructs in
# channel order -- each with its OWN backward-adaptive integer beta, and SUM
# their residual subtractions:
#     y[c,t] = x[c,t] - ((beta_up[c]*x[up,t]) >> s) - ((beta_left[c]*x[left,t]) >> s)
# This is a rank-2 LOCAL decorrelation realized as TWO independent asymmetric
# rank-1 subtracts (one per parent), NOT a joint 2x2 solve: each beta is the
# per-parent integer least-squares ratio <x_c,x_p>/<x_p,x_p> estimated
# independently from the PREVIOUS block's already-reconstructed RAW samples
# (block 0 -> beta=0). Both terms subtract the CLEAN raw parent and inject
# estimation noise only into the residual channel c -- the parent rows stay
# untouched -- which is exactly the robustness property INSIGHTS P3-refinement
# credits the rank-1 subtract with, and which the RETIRED energy-preserving
# rotation (iklt_adaptive, corrupts BOTH channels) lacks.
#
# Because both betas are recomputed by the decoder from bit-identical
# reconstructed history, NO beta is transmitted (zero side-info, backward-
# adaptive -- INSIGHTS P4), look-ahead 0. DISTINCT from the retired single-
# parent scalar xchan_adaptive: it adds a SECOND independent parent on a
# different topology (up vs left), the follow-up open-frontier #2 endorses --
# not a re-run of the dominated single-parent scalar. Behind the front-end: the
# SAME order-8 sign-sign LMS + adaptive Rice back-end as the whole family.
# Basis: MPEG-4 ALS multichannel / Choi et al. 2014 (paper-reported, unverified
# here). Gated hard on cost (each parent adds state + ops); neural budget
# verified.
# ===========================================================================
MP_MAGIC = 0x584D            # 'XM' (xchan multi-parent)
MP_BLOCK = ec.BLOCK          # backward-adaptation block (aligns with the Rice block)
MP_SHIFT = ec.CROSS_SHIFT    # fixed-point gain scale (matches the +xchan family)


def _mp_parents(C, cols):
    """Two causal grid parents per channel: (up, left). up = g-cols (row > 0),
    left = g-1 (col > 0); -1 where absent. Both indices < g so the decoder
    reconstructs in channel order and a channel with neither parent (grid
    origin) is coded as-is. Deterministic from (C, cols): identical on encode
    and decode."""
    up = np.full(C, -1, np.int64)
    left = np.full(C, -1, np.int64)
    for g in range(C):
        r, c = divmod(g, cols)
        if r > 0:
            up[g] = g - cols
        if c > 0:
            left[g] = g - 1
    return up, left


def _mp_forward(x, up, left, B=MP_BLOCK, shift=MP_SHIFT):
    """Two-parent cross-channel decorrelation with per-parent backward-adaptive
    gain. For each parent independently, beta[block i] is the integer
    least-squares ratio over the PREVIOUS block's raw samples (block 0 -> 0),
    and its rank-1 subtract of the CLEAN raw parent is SUMMED into the residual.
    Operates on the RAW signal (which the decoder rebuilds bit-exactly), so the
    betas match on both sides."""
    x = x.astype(np.int64)
    C, N = x.shape
    y = x.copy()
    nblocks = (N + B - 1) // B
    for c in range(C):
        pu, pl = int(up[c]), int(left[c])
        if pu < 0 and pl < 0:              # grid origin: no parent to subtract
            continue
        bu = bl = 0                        # block-0 bootstrap (coded as xchan-off)
        for i in range(nblocks):
            s, e = i * B, min((i + 1) * B, N)
            if i > 0:
                ps, pe = (i - 1) * B, i * B
                if pu >= 0:
                    bu = _beta_from_block(x[c, ps:pe], x[pu, ps:pe], shift)
                if pl >= 0:
                    bl = _beta_from_block(x[c, ps:pe], x[pl, ps:pe], shift)
            r = x[c, s:e].copy()
            if pu >= 0:
                r = r - ((bu * x[pu, s:e]) >> shift)   # rank-1 subtract, parent 1
            if pl >= 0:
                r = r - ((bl * x[pl, s:e]) >> shift)   # rank-1 subtract, parent 2
            y[c, s:e] = r
    return y


def _mp_inverse(y, up, left, B=MP_BLOCK, shift=MP_SHIFT):
    """Invert _mp_forward. Both parents have index < c so their rows are fully
    reconstructed before channel c; within a channel, block i's per-parent betas
    are recomputed from the already-reconstructed block i-1 -- mirroring the
    encoder exactly, with the two subtracts added back in the same order."""
    y = y.astype(np.int64)
    C, N = y.shape
    x = y.copy()
    nblocks = (N + B - 1) // B
    for c in range(C):
        pu, pl = int(up[c]), int(left[c])
        if pu < 0 and pl < 0:
            continue
        bu = bl = 0
        for i in range(nblocks):
            s, e = i * B, min((i + 1) * B, N)
            if i > 0:
                ps, pe = (i - 1) * B, i * B
                if pu >= 0:
                    bu = _beta_from_block(x[c, ps:pe], x[pu, ps:pe], shift)
                if pl >= 0:
                    bl = _beta_from_block(x[c, ps:pe], x[pl, ps:pe], shift)
            r = y[c, s:e].copy()
            if pu >= 0:
                r = r + ((bu * x[pu, s:e]) >> shift)
            if pl >= 0:
                r = r + ((bl * x[pl, s:e]) >> shift)
            x[c, s:e] = r
    return x


def mp_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    up, left = _mp_parents(C, cols)
    y = _mp_forward(x, up, left)
    res = ec.lms_forward(y)                # order-8 sign-sign LMS (same as family)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", MP_MAGIC, cols, C, N)   # NO beta side-info
    return hdr + body


def mp_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == MP_MAGIC, "bad xchan_multiparent magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res)
    up, left = _mp_parents(C, cols)
    x = _mp_inverse(y, up, left)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Cross-channel context-adaptive Rice (LMS+Rice+xctx).
# ---------------------------------------------------------------------------
# Every cycle so far moved the cross-channel FRONT-END (removing correlated
# MEANS: +xchan/xadapt/bestpartner subtract a neighbour, acar the array mean) or
# swapped the entropy engine (retired xchan_tans, P5). This candidate is on a
# DIFFERENT axis -- SECOND-ORDER / CONDITIONAL entropy -- untouched by any tried
# codec, and it keeps the Golomb-Rice engine (P5: Rice is already at the floor
# for the UNCONDITIONAL residual). It changes only how the Rice parameter k is
# SELECTED: per-sample, from a backward SPATIAL context.
#
# Mechanism (JPEG-LS/LOCO-I context modeling with a cross-channel context):
# every prior spatial front-end removes correlated first-order MEANS, but HD-EMG
# bursts (motor-unit action potentials) are spatially COHERENT, so the residual
# stays HETEROSCEDASTIC and its VARIANCE is correlated ACROSS channels even after
# mean decorrelation. A single per-block k per channel cannot track that. Here:
#   * The temporal residual is the SAME order-8 sign-sign LMS as the family
#     (res = ec.lms_forward(x)); the coder engine stays Golomb-Rice.
#   * For a channel c with causal grid parent p = grid_parents(c) (p < c, so the
#     decoder has p's full residual before it reaches c), a leaky integrator of
#     the NEIGHBOUR residual magnitude |res[p,t]| forms a running spatial-energy
#     estimate; its bit-length buckets that energy (log-energy context).
#   * Per context bucket we keep JPEG-LS statistics (A = sum of coded magnitudes,
#     N = count) and pick k as the smallest with (N<<k) >= A -- the standard
#     LOCO-I Golomb rule -- so k tracks the residual variance CONDITIONED on the
#     neighbour's current energy: high-neighbour-energy samples (spatially
#     coherent bursts) get a larger k, quiet samples a smaller one. This exploits
#     H(e_c | neighbour energy) < H(e_c): a conditional-entropy reduction a
#     per-block k structurally misses.
#   * The bucket and the per-bucket (A,N) stats are updated identically on both
#     sides from causally-available, bit-identical data, so ZERO side-info is
#     transmitted (no per-block k, no context table) -- backward-adaptive, P4.
#     Root channels (no parent) fall back to a single context (bucket 0), i.e.
#     plain per-channel JPEG-LS adaptive k with no spatial conditioning.
#
# Distinct from the RETIRED xchan_tans (P5): the entropy ENGINE stays Rice
# (already optimal for the unconditional residual); only its PARAMETER's context
# gains cross-channel information. Distinct from the spatial front-ends: nothing
# is subtracted across channels here -- the neighbour only CONDITIONS the coder.
# Borderline->embeddable: a leaky-energy add/shift + a small k lookup per sample,
# per-context (A,N) counters as state, RTL-trivial. Risk to MEASURE: if the
# per-block k already tracks local variance well, the spatial context may add
# little. Citations: JPEG-LS/LOCO-I (context-conditioned Golomb), US7580585B2
# (backward-adaptive Rice), Giurcaneanu/Tabus 2001 (context-based Golomb on
# audio) -- paper-reported, unverified here.
# ===========================================================================
XCTX_MAGIC = 0x5843     # 'XC'
XCTX_NBUCKETS = 12      # spatial-energy context buckets (log neighbour energy)
XCTX_DECAY = 2          # leaky-integrator decay for the neighbour-energy estimate
XCTX_RESET = 64         # JPEG-LS-style halving reset keeps per-context (A,N) local
XCTX_A_INIT = 4         # (A,N) seed -> initial k = 2 before any data
XCTX_N_INIT = 1


def _xctx_k(A, N):
    """LOCO-I/JPEG-LS Golomb parameter for context stats (A = accumulated coded
    magnitudes, N = count): the smallest k with (N << k) >= A. Integer-only and
    deterministic, so encode and decode derive the identical k from the identical
    (backward-updated) stats."""
    k = 0
    while (N << k) < A:
        k += 1
    return k


def _xctx_encode_channel(res, neigh):
    """Per-sample context-adaptive Rice encode of one residual channel. The
    context bucket is the bit-length of a leaky neighbour-energy integrator (a
    single bucket 0 when the channel has no parent); per-bucket JPEG-LS (A,N)
    stats pick the Rice k. Returns a length-prefixed packed-bit body. NO k or
    context is transmitted -- the decoder recomputes bucket + stats identically
    from the already-reconstructed neighbour residual."""
    u = ec.zigzag(np.asarray(res, np.int64))          # >= 0 mapped residual
    nmag = np.abs(np.asarray(neigh, np.int64)) if neigh is not None else None
    A = [XCTX_A_INIT] * XCTX_NBUCKETS
    Nc = [XCTX_N_INIT] * XCTX_NBUCKETS
    nrg = 0
    bits = []
    for t in range(u.size):
        if nmag is not None:
            nrg = nrg + int(nmag[t]) - (nrg >> XCTX_DECAY)   # leaky spatial energy
            b = nrg.bit_length()
            if b >= XCTX_NBUCKETS:
                b = XCTX_NBUCKETS - 1
        else:
            b = 0
        k = _xctx_k(A[b], Nc[b])
        ut = int(u[t])
        q = ut >> k
        bits.extend([0] * q)                          # unary quotient
        bits.append(1)                                # stop bit
        for j in range(k - 1, -1, -1):                # k remainder bits, MSB first
            bits.append((ut >> j) & 1)
        A[b] += ut
        Nc[b] += 1
        if Nc[b] >= XCTX_RESET:                       # halving reset -> local adapt
            A[b] >>= 1
            Nc[b] >>= 1
    packed = np.packbits(np.array(bits, np.uint8)).tobytes() if bits else b""
    return struct.pack("<I", len(packed)) + packed


def _xctx_decode_channel(buf, off, N, neigh):
    """Invert _xctx_encode_channel. Mirrors the encoder's bucket + per-context
    (A,N) update from the already-reconstructed neighbour residual, so it derives
    the identical per-sample k with no transmitted parameters."""
    (nbytes,) = struct.unpack_from("<I", buf, off); off += 4
    bits = (np.unpackbits(np.frombuffer(buf, np.uint8, nbytes, off))
            if nbytes else np.zeros(0, np.uint8))
    off += nbytes
    nmag = np.abs(np.asarray(neigh, np.int64)) if neigh is not None else None
    A = [XCTX_A_INIT] * XCTX_NBUCKETS
    Nc = [XCTX_N_INIT] * XCTX_NBUCKETS
    nrg = 0
    u = np.empty(N, np.int64)
    pos = 0
    for t in range(N):
        if nmag is not None:
            nrg = nrg + int(nmag[t]) - (nrg >> XCTX_DECAY)
            b = nrg.bit_length()
            if b >= XCTX_NBUCKETS:
                b = XCTX_NBUCKETS - 1
        else:
            b = 0
        k = _xctx_k(A[b], Nc[b])
        q = 0
        while bits[pos] == 0:                          # count unary zeros
            q += 1; pos += 1
        pos += 1                                        # skip stop bit
        r = 0
        for _ in range(k):                              # k remainder bits, MSB first
            r = (r << 1) | int(bits[pos]); pos += 1
        ut = (q << k) | r
        u[t] = ut
        A[b] += ut
        Nc[b] += 1
        if Nc[b] >= XCTX_RESET:
            A[b] >>= 1
            Nc[b] >>= 1
    return ec.unzigzag(u.astype(np.uint64)), off


def xctx_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    res = ec.lms_forward(x)                 # order-8 sign-sign LMS (same as family)
    parent = ec.grid_parents(C, cols)
    body = []
    for c in range(C):
        p = int(parent[c])
        neigh = res[p] if p >= 0 else None
        body.append(_xctx_encode_channel(res[c], neigh))
    hdr = struct.pack("<HHII", XCTX_MAGIC, cols, C, N)   # NO side-info
    return hdr + b"".join(body)


def xctx_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == XCTX_MAGIC, "bad xctx magic"
    off = 12
    parent = ec.grid_parents(C, cols)
    res = np.empty((C, N), np.int64)
    for c in range(C):
        p = int(parent[c])
        neigh = res[p] if p >= 0 else None    # p < c so already reconstructed
        arr, off = _xctx_decode_channel(buf, off, N, neigh)
        res[c] = arr
    x = ec.lms_inverse(res)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: two-stage scale-matched spatial front-end -- GLOBAL adaptive
# CAR then LOCAL order-4 best-partner (acar+lms4bp).
# ---------------------------------------------------------------------------
# INSIGHTS P1-refinement MEASURED that the cross-channel mutual information splits
# into two distinct, NON-INTERCHANGEABLE slices and array size selects which one
# dominates: removing the GLOBAL rank-1 common-mode (`LMS+Rice+acar`, the array-mean
# lift) captured +14.4% on the small tightly-coupled 64-ch OTB array but collapsed
# to +0.8..2.5% on the larger 128-/320-ch Hyser/CEMHSEY arrays, where the LOCAL
# single-/best-neighbour subtract still got +10.8..13.1%. CAR removes exactly one
# eigenvector (DC-across-array); best-partner removes the local pairwise weight.
# Neither codec can reach the other's slice: a global mean does not cancel local
# pairwise redundancy, and a neighbour difference does not cancel the array-wide DC
# drift and far-field shared source.
#
# This candidate CASCADES the two already-verified primitives so it captures BOTH
# slices where both exist, in the order that keeps them orthogonal:
#   Stage 1 (GLOBAL): the ACAR reversible-integer S-transform lift (`_acar_forward`,
#     backward-gated, ZERO side-info) reused VERBATIM -- removes the global array
#     common-mode, leaving a CAR residual whose remaining cross-channel structure is
#     the LOCAL pairwise part.
#   Stage 2 (LOCAL): the promoted best-partner subtract (`_bp_select`, per-channel
#     best-of-4 causal grid neighbour + integer gain, tiny 2xint16/ch side-info)
#     reused VERBATIM, applied to the CAR RESIDUAL -- removes the local pairwise MI
#     that CAR left behind.
#   Back-end: the order-4 sign-sign LMS + adaptive Rice of the promoted best
#     `LMS4+Rice+xchan_bestpartner` (INSIGHTS P2: order-4 beats order-8).
#
# Why the two stages are ORTHOGONAL BY CONSTRUCTION (and so, unlike the RETIRED
# summed multi-parent, cannot double-count): stage 1 removes the array-mean
# component; the residual it leaves is mean-zero across the array by construction, so
# its local pairwise covariance is uncorrelated with the global mean stage 1 already
# took. The retired `xchan_multiparent` summed two CORRELATED local parents and
# over-subtracted their shared mode; here the two stages act on ORTHOGONAL subspaces
# (one global eigenvector vs the local-pairwise complement), so there is nothing to
# double-count.
#
# Losslessness of the cascade: both stages are exact integer inverses. Encode is
# x -> acar_forward -> bp_select -> LMS4 -> Rice; decode inverts in reverse order --
# Rice -> lms_inverse(order4) -> bp_inverse (the transmitted parents/betas restore the
# CAR residual exactly, parents[g]<g so each parent row is already rebuilt) ->
# acar_inverse (recomputes the SAME backward gate from the reconstructed raw previous
# block, block-by-block in time order). Channel 0 carries ACAR's virtual array total
# on ON blocks and has no best-partner candidate (grid origin), so it passes stage 2
# through unchanged -- the two stages compose cleanly. Cost is amortized O(1)/sample-ch
# for CAR on top of best-partner's per-sample subtract (INSIGHTS open-frontier #1).
# Risk (to MEASURE, not to pre-judge): on large arrays where redundancy is already
# local, CAR may not clear its per-block gate and add ~nothing -- the slices may be
# additive (both fire) or redundant after best-partner already took the local slice.
# ===========================================================================
ACARBP_MAGIC = 0x4143   # 'AC' (adaptive-CAR + best-partner cascade)


def acarbp_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _acar_forward(x, cols)                      # stage 1: GLOBAL common-mode lift (verbatim)
    xt, parents, betas = _bp_select(y, cols)        # stage 2: LOCAL best-partner on the CAR residual
    res = ec.lms_forward(xt, order=LMS4_ORDER)      # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", ACARBP_MAGIC, cols, C, N)
    side = parents.astype("<i2").tobytes() + betas.astype("<i2").tobytes()
    return hdr + side + body


def acarbp_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == ACARBP_MAGIC, "bad acar+bestpartner codec magic"
    off = 12
    parents = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    betas = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    xt = ec.lms_inverse(res, order=LMS4_ORDER)      # matched order-4 inverse
    y = _bp_inverse(xt, parents, betas)             # undo stage 2 (LOCAL best-partner)
    x = _acar_inverse(y, cols)                      # undo stage 1 (GLOBAL CAR lift)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: SCALE-SELECTED two-stage spatial cascade
# (LMS4+Rice+acar_sel+bestpartner).
# ---------------------------------------------------------------------------
# The always-on two-stage cascade above (`LMS4+Rice+acar+bestpartner`) MEASURED a
# clean split by ARRAY SIZE (INSIGHTS P1-refinement, cycle-10 frontier #1): the
# CAR stage's isolated marginal gain over best-partner alone was +0.81% ratio on
# the tight 64-ch OTB array (a genuine non-dominated max-ratio corner) but NEGATIVE
# on the large 128-/320-ch Hyser/CEMHSEY arrays (-0.26 / -0.23 pp) -- because the
# two cross-channel MI slices are additive ONLY where the GLOBAL common-mode is a
# real eigenvector, which array size selects. On tight arrays the DC-across-array
# mode is physical and orthogonal to the local pairwise mode, so cascading captures
# both; on large arrays the shared content is spatially LOCAL, so once best-partner
# removes the local slice a fired CAR lift subtracts a MISMATCHED global basis and
# injects slightly more noise than it removes. The always-on cascade's per-block
# ACAR gate is a local-energy heuristic that still fires on the large arrays and
# nets negative there -- the regression this candidate removes.
#
# This is a META-GATE over the two ALREADY-VERIFIED primitives -- NOT a new
# mechanism. Per recording it SELECTS the spatial front-end from a decoder-derivable
# GLOBAL-vs-LOCAL coherence statistic:
#   * TIGHT array (global common-mode is a real eigenvector) -> the full cascade
#     `_acar_forward` (stage 1, backward-gated GLOBAL CAR lift) THEN `_bp_select`
#     (stage 2, LOCAL best-partner) -- identical to `LMS4+Rice+acar+bestpartner`.
#   * EXTENDED array (global mode is NOT a coherent eigenvector) -> best-partner
#     ONLY -- identical to the promoted `LMS4+Rice+xchan_bestpartner`, so the CAR
#     stage cannot inject its mismatched-basis noise.
# The scale statistic is the ARRAY CHANNEL COUNT C, which the decoder reads from the
# header BEFORE any reconstruction -- a deterministic integer comparison, ZERO
# side-info, ZERO circularity (unlike an energy ratio on reconstructed data, which
# a per-recording global CAR decision cannot use without knowing the decision it is
# trying to make). C is exactly the physical variable INSIGHTS P1-refinement names
# decisive: at C<=64 the array is tight enough that the array-mean is a coherent
# eigenvector (OTB 64-ch: CAR helps); at C>=128 the shared content is local (Hyser
# 128-ch, CEMHSEY 320-ch, CapgMyo 128-ch: CAR hurts or its gate never usefully
# fires). The threshold ACARSEL_MAX_CH=64 is the measured boundary between the two
# regimes -- inclusive of the 64-ch tight array, below the 128-ch extended arrays.
#
# Losslessness: both branches are exact-integer-invertible primitives reused
# VERBATIM; the header format is IDENTICAL across branches (magic, cols, C, N, then
# the best-partner parents/betas side-info, then Rice body), so the decoder derives
# the SAME branch from C and inverts the matching cascade. On C<=64 it undoes
# stage 2 then stage 1 (bp_inverse -> acar_inverse); on C>=128 it undoes stage 2
# only. Cost is the union of the branch it takes (never both) -- so it is Pareto-
# bounded by the more expensive branch (the cascade), with the CAR ops paid only on
# tight arrays. Intent: keep the OTB max-ratio corner WITHOUT the large-array
# regression -- a low-mechanism-risk, low-to-medium-payoff salvage of frontier #1.
# Basis: Vaisman/Jordanic/Farina adaptive-CAR (stage 1) + the shipped best-partner
# (stage 2); the meta-gate is novel here (unverified for compression).
# ===========================================================================
ACARSEL_MAGIC = 0x5353   # 'SS' (scale-selected cascade)
ACARSEL_MAX_CH = 64      # recording-level scale gate: CAR cascade only for C<=this
                         # (tight arrays where the global common-mode is a real
                         # eigenvector -- P1-refinement); best-partner-only above.


def _acarsel_use_car(C):
    """Decoder-derivable recording-level scale gate. Enable the GLOBAL adaptive-CAR
    stage iff the array is tight enough (C<=ACARSEL_MAX_CH) that the array-mean is a
    coherent eigenvector. Pure integer comparison on the channel count C, which the
    decoder reads from the header before any reconstruction -> deterministic,
    zero-circularity, zero side-info; encoder and decoder derive the identical
    decision from the identical C."""
    return C <= ACARSEL_MAX_CH


def acarsel_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    if _acarsel_use_car(C):
        y = _acar_forward(x, cols)                  # stage 1: GLOBAL CAR (tight arrays only)
    else:
        y = x                                        # extended array: skip stage 1
    xt, parents, betas = _bp_select(y, cols)        # stage 2: LOCAL best-partner (both branches)
    res = ec.lms_forward(xt, order=LMS4_ORDER)      # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", ACARSEL_MAGIC, cols, C, N)
    side = parents.astype("<i2").tobytes() + betas.astype("<i2").tobytes()
    return hdr + side + body


def acarsel_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == ACARSEL_MAGIC, "bad scale-selected cascade codec magic"
    off = 12
    parents = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    betas = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    xt = ec.lms_inverse(res, order=LMS4_ORDER)      # matched order-4 inverse
    y = _bp_inverse(xt, parents, betas)             # undo stage 2 (LOCAL best-partner)
    if _acarsel_use_car(C):
        x = _acar_inverse(y, cols)                  # undo stage 1 only where it was applied
    else:
        x = y
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Joint asymmetric 2-parent adaptive sign-LMS spatial predictor
# (LMS+Rice+xchan_joint2).
# ---------------------------------------------------------------------------
# INSIGHTS open-frontier #2/#3 and the RETIRED `xchan_multiparent` post-mortem
# leave exactly ONE unspent second-parent escape: extending the spatial support to
# TWO causal parents pays only through a JOINT decorrelation that accounts for
# parent-parent covariance -- NOT a sum of two independent marginal rank-1
# subtracts (which double-counts the correlated parents' shared mode and
# over-subtracts). The retired multiparent estimated each beta_p = <x_c,x_p>/
# <x_p,x_p> as if its parent were the SOLE regressor, then SUMMED -- the classic
# marginal-vs-multiple regression gap under collinear predictors. INSIGHTS also
# names the naive joint fix (an energy-preserving 2x2 Givens rotation) a settled
# dead end, because a stale/noisy angle corrupts BOTH channels (iklt_adaptive).
#
# This candidate is the de-risked realization of that one open escape: a JOINT
# (not summed, not rotational) solve done as ONE backward-adaptive sign-sign LMS
# with TWO SPATIAL taps. Per channel c with causal parents up (g-cols) and left
# (g-1), a single joint predictor
#     pred[t] = (w_u * x[up,t] + w_l * x[left,t]) >> shift
#     e[t]    = x[c,t] - pred[t]                    (the coded cross-residual)
# and BOTH taps co-adapt against the SAME post-subtraction residual e[t]:
#     w_u += sign(e[t]) * sign(x[up,t])
#     w_l += sign(e[t]) * sign(x[left,t])
# Because both taps are driven by the shared residual AFTER both current taps have
# subtracted, w_u adapts to the correlation REMAINING once left's contribution is
# out (and vice-versa) -- exactly the multiple-regression coupling the summed
# marginal betas lacked. This is the LMS stochastic-gradient realization of the
# 2x2 normal-equations solve: the taps jointly descend the shared squared error,
# so the parent-parent covariance enters through the shared residual, never
# double-counting. It overcomes the multiparent failure WITHOUT a matrix inverse.
#
# ASYMMETRIC (rank-1 residual-only injection): the predictor only subtracts from
# channel c's coded residual; the raw parent rows x[up]/x[left] are used as inputs
# and left CLEAN, so estimation noise never touches the parents -- the robustness
# property INSIGHTS P3-refinement credits the rank-1 subtract with and the RETIRED
# energy-preserving rotation (iklt_adaptive, corrupts both channels) lacked. This
# overcomes the SECOND retired failure.
#
# Backward-adaptive & multiplierless in the family sense (sign-sign LMS: the tap
# update is +/-1, no multiply): w_u/w_l evolve per sample from data the decoder has
# bit-identically (both parents have grid index < c, so their rows are fully
# reconstructed before c, and e[t] IS the coded residual). No angle, no beta, NO
# side-info is transmitted; look-ahead 0. Behind the spatial front-end sits the
# order-4 sign-sign LMS temporal predictor (INSIGHTS P2: order-4 beats order-8 --
# "+1 spatial pair on the order-4 base") + adaptive Rice -- the promoted best's
# back-end. Grounded in MPEG-4 ALS RLS-LMS multichannel / multivariate-RLS
# (arXiv 1605.04418, paper-reported, unverified here). Gated hard on the neural
# 125-cyc budget (two extra taps only). INSIGHTS open-frontier #3.
# ===========================================================================
XJ2_MAGIC = 0x584A          # 'XJ' (xchan joint 2-parent)
XJ2_SHIFT = ec.CROSS_SHIFT  # fixed-point spatial-weight scale (matches +xchan family)
XJ2_ORDER = LMS4_ORDER      # order-4 temporal base behind the spatial front-end (P2)


def _xj2_parents(C, cols):
    """Two causal grid parents per channel: up=g-cols (row>0), left=g-1 (col>0);
    -1 where absent. Both idx<g so decode reconstructs in channel order and the
    grid origin (neither parent) is coded as-is. Deterministic from (C,cols) ->
    identical on encode and decode."""
    up = np.full(C, -1, np.int64)
    left = np.full(C, -1, np.int64)
    for g in range(C):
        r, c = divmod(g, cols)
        if r > 0:
            up[g] = g - cols
        if c > 0:
            left[g] = g - 1
    return up, left


def _xj2_forward(x, up, left, shift=XJ2_SHIFT):
    """Joint 2-parent spatial sign-sign LMS decorrelation. For each channel c with
    causal parents up/left, ONE joint predictor with two spatial taps (w_u,w_l)
    predicts x[c,t] from the SAME-slice raw parents; both taps co-adapt against the
    SHARED post-subtraction residual e (sign-sign LMS -> +/-1 tap update, no
    multiply). Only channel c's residual e is coded; the raw parent rows are left
    clean. Integer-only, per-sample, look-ahead 0. Returns the cross-residual
    [C,N] int64."""
    x = x.astype(np.int64)
    C, N = x.shape
    y = x.copy()
    for c in range(C):
        pu, pl = int(up[c]), int(left[c])
        if pu < 0 and pl < 0:
            continue                        # grid origin: coded as-is
        prow = x[pu] if pu >= 0 else None
        lrow = x[pl] if pl >= 0 else None
        xc = x[c]
        yc = y[c]
        wu = wl = 0
        for t in range(N):
            u = int(prow[t]) if prow is not None else 0
            l = int(lrow[t]) if lrow is not None else 0
            pred = (wu * u + wl * l) >> shift
            e = int(xc[t]) - pred
            yc[t] = e
            se = 1 if e > 0 else (-1 if e < 0 else 0)
            if prow is not None:
                wu += se * (1 if u > 0 else (-1 if u < 0 else 0))
            if lrow is not None:
                wl += se * (1 if l > 0 else (-1 if l < 0 else 0))
    return y


def _xj2_inverse(y, up, left, shift=XJ2_SHIFT):
    """Invert _xj2_forward. parents idx<c so their rows are fully reconstructed
    before channel c; within a channel the two taps are re-derived per sample from
    the shared residual e=y[c,t] (identical to the encoder's) and the reconstructed
    parents -- so pred, and hence x[c,t]=e+pred, match bit-for-bit."""
    y = y.astype(np.int64)
    C, N = y.shape
    x = y.copy()
    for c in range(C):
        pu, pl = int(up[c]), int(left[c])
        if pu < 0 and pl < 0:
            continue
        prow = x[pu] if pu >= 0 else None   # parent idx < c -> already reconstructed
        lrow = x[pl] if pl >= 0 else None
        yc = y[c]
        xc = x[c]
        wu = wl = 0
        for t in range(N):
            u = int(prow[t]) if prow is not None else 0
            l = int(lrow[t]) if lrow is not None else 0
            pred = (wu * u + wl * l) >> shift
            e = int(yc[t])
            xc[t] = e + pred
            se = 1 if e > 0 else (-1 if e < 0 else 0)
            if prow is not None:
                wu += se * (1 if u > 0 else (-1 if u < 0 else 0))
            if lrow is not None:
                wl += se * (1 if l > 0 else (-1 if l < 0 else 0))
    return x


def xj2_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    up, left = _xj2_parents(C, cols)
    y = _xj2_forward(x, up, left)
    res = ec.lms_forward(y, order=XJ2_ORDER)   # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", XJ2_MAGIC, cols, C, N)   # NO side-info (backward-adaptive)
    return hdr + body


def xj2_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == XJ2_MAGIC, "bad xchan_joint2 magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res, order=XJ2_ORDER)   # matched order-4 inverse
    up, left = _xj2_parents(C, cols)
    x = _xj2_inverse(y, up, left)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Backward-adaptive per-block best-partner RE-SELECTION
# (LMS4+Rice+xchan_bestpartner_adaptive).
# ---------------------------------------------------------------------------
# The PROMOTED best `LMS4+Rice+xchan_bestpartner` picks each channel's cross-
# partner (best-of-4 causal grid neighbour) AND its integer gain beta OFFLINE
# over the WHOLE recording, then ships the chosen (parent, beta) pair as a
# 2xint16/ch header. That whole-signal look-ahead + header side-info is its last
# non-embeddable caveat (INSIGHTS P4 / open-frontier #3): the on-node decoder
# cannot see the whole signal, and the header is real transmitted bits.
#
# Mechanism: re-select partner IDENTITY + integer beta PER BLOCK from the
# PREVIOUS already-reconstructed RAW block's 4-neighbourhood, decoder mirroring
# the identical selection -> ZERO side-info, look-ahead 0. For channel g, block i
# (i>0): over the previous block (i-1) of the RAW channels, for each causal
# neighbour candidate (left/up/up-left/up-right, all grid idx < g -- reused
# `_bp_candidates`) derive the integer least-squares gain (`_bp_opt_beta`) and
# score the resulting cross-residual's estimated Rice bits (`_bp_score`), also
# scoring the no-partner option; keep the min-bits (partner, beta). That pair is
# then applied to the CURRENT block: y[g,blk i] = x[g,blk i] - ((beta*x[p,blk i])
# >> shift). Because the reconstruction is lossless, the RAW previous block the
# decoder holds is bit-identical to the encoder's, and every candidate partner
# has grid idx < g so its row is fully reconstructed -- so the decoder recomputes
# the SAME (partner, beta) causally and NOTHING is transmitted. Block 0
# bootstraps to no-partner (coded as-is; no prior block exists), then the
# selection re-adapts each block.
#
# This is EXACTLY the promoted codec with its offline whole-signal partner/beta
# swapped for per-block backward-adaptive re-selection: same 4-candidate causal
# neighbourhood, same integer-LS beta, same Rice-bits scoring, same order-4
# sign-sign LMS + adaptive Rice back-end (INSIGHTS P2) -- only the ESTIMATION is
# now backward-adaptive (INSIGHTS P4), dropping both the look-ahead and the
# 2xint16/ch header. Distinct from the RETIRED `LMS+Rice+xchan_adaptive` (a
# single FIXED-grid-parent scalar beta, backward-adaptive gain but NO partner
# selection): here the partner IDENTITY itself is re-selected per block -- the
# exact port-caveat closure INSIGHTS open-frontier #3 endorses. Ratio risk to
# MEASURE: a stale partner across a burst boundary (the previous block may not
# predict the next one's best neighbour on non-stationary HD-sEMG); this is an
# embeddability/port lever (makes the shipped leaderboard best fully on-node),
# not a ratio play -- the question is whether it HOLDS the offline ratio.
# ===========================================================================
LMS4BPA_MAGIC = 0x4234        # 'B4' (order-4 backward-adaptive best-partner)
LMS4BPA_BLOCK = ec.BLOCK      # re-selection block (aligns with the Rice block)


def _bpa_select_block(xc_prev, x, cands, ps, pe):
    """Backward per-block partner+beta selection from the PREVIOUS raw block.
    Mirrors the offline best-partner selection (`_bp_opt_beta`/`_bp_score`) but
    restricted to block (i-1): return (partner, beta) with the fewest estimated
    Rice bits over that block, or (-1, 0) for the no-partner option. Integer-only
    and deterministic, so encode and decode -- which both hold the bit-identical
    reconstructed previous block -- derive the identical choice."""
    best_bits = _bp_score(xc_prev)            # option: no cross-channel subtract
    best_p, best_b = -1, 0
    for p in cands:
        b = _bp_opt_beta(xc_prev, x[p, ps:pe], BP_SHIFT)
        if b == 0:
            continue
        resid = xc_prev - ((b * x[p, ps:pe]) >> BP_SHIFT)
        bits = _bp_score(resid)
        if bits < best_bits:
            best_bits, best_p, best_b = bits, p, b
    return best_p, best_b


def _lms4bpa_forward(x, cols, B=LMS4BPA_BLOCK):
    """Cross-channel decorrelation with backward-adaptive per-block best-partner
    RE-SELECTION. Block i's (partner, beta) come from the PREVIOUS raw block
    (block 0 -> no partner); the chosen rank-1 subtract is applied to block i of
    the RAW signal. Returns the transformed [C, N] int64 array."""
    C, N = x.shape
    x = x.astype(np.int64)
    y = x.copy()
    nblocks = (N + B - 1) // B
    for g in range(C):
        cands = _bp_candidates(g, cols, C)
        if not cands:                          # grid origin: no causal neighbour
            continue
        for i in range(1, nblocks):            # block 0 is coded as-is (no prior)
            s, e = i * B, min((i + 1) * B, N)
            ps, pe = (i - 1) * B, i * B
            p, b = _bpa_select_block(x[g, ps:pe], x, cands, ps, pe)
            if p >= 0:
                y[g, s:e] = x[g, s:e] - ((b * x[p, s:e]) >> BP_SHIFT)
    return y


def _lms4bpa_inverse(y, cols, B=LMS4BPA_BLOCK):
    """Invert _lms4bpa_forward. Each candidate partner has grid idx < g so its row
    is fully reconstructed before g; within a channel we rebuild raw block-by-block
    in time order, so block i-1 is restored before block i and the SAME per-block
    (partner, beta) is recomputed from it -- mirroring the encoder exactly."""
    C, N = y.shape
    y = y.astype(np.int64)
    x = y.copy()
    nblocks = (N + B - 1) // B
    for g in range(C):
        cands = _bp_candidates(g, cols, C)
        if not cands:
            continue
        for i in range(1, nblocks):
            s, e = i * B, min((i + 1) * B, N)
            ps, pe = (i - 1) * B, i * B
            p, b = _bpa_select_block(x[g, ps:pe], x, cands, ps, pe)
            if p >= 0:
                x[g, s:e] = y[g, s:e] + ((b * x[p, s:e]) >> BP_SHIFT)
    return x


def lms4bpa_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _lms4bpa_forward(x, cols)                    # backward-adaptive per-block re-selection
    res = ec.lms_forward(y, order=LMS4_ORDER)        # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", LMS4BPA_MAGIC, cols, C, N)   # NO (parent,beta) side-info
    return hdr + body


def lms4bpa_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == LMS4BPA_MAGIC, "bad lms4bp_adaptive codec magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res, order=LMS4_ORDER)        # matched order-4 inverse
    x = _lms4bpa_inverse(y, cols)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Joint-solved SELECTED best pair -- selection x count fused
# (LMS4+Rice+xchan_jointbp2).
# ---------------------------------------------------------------------------
# INSIGHTS P1b PROVED the two spatial degrees of freedom -- *which* parent
# (best-partner SELECTION) and *how many* parents (joint COUNT) -- are used alone
# as SUBSTITUTES, and array geometry picks the winner: the joint 2-parent solve
# (`xchan_joint2`) took the highest Hyser cross-channel gain of any codec (+12.26%)
# via a second co-adapted parent but LOST the tight OTB array because it used a
# FIXED up+left pair where SELECTION matters; best-partner won tight OTB via
# selection but is SINGLE-parent. INSIGHTS open-frontier #1 (the highest-payoff
# live lever) names the one untried way to STACK them into a combined win on BOTH
# array scales: jointly solve the best *pair* of causal neighbours (co-adaptive
# sign-LMS, as in joint2) instead of a *fixed* up+left pair, with the pair chosen
# per channel like best-partner -- done backward-adaptively to stay zero-side-info.
#
# Mechanism = SELECTION (per block, backward) x COUNT (joint co-adaptive pair):
#   1. SELECT (per channel, per block i>0): over the PREVIOUS already-reconstructed
#      RAW block, scan the <=4 causal grid neighbours (left/up/up-left/up-right, all
#      grid idx<c -- reused `_bp_candidates`). Score, in estimated Rice bits, the
#      no-parent option, each single-parent option (integer-LS gain, reusing
#      `_bp_opt_beta`/`_bp_score` -- the best-partner path), AND every candidate
#      PAIR under a JOINT 2x2 integer least-squares solve (`_jbp2_pair_resid`, which
#      accounts for parent-parent covariance -- the marginal->multiple fix, NOT the
#      retired summed multiparent's double-count). Keep the min-bits option: a pair
#      (pu,pl), a single (pu,-1), or none (-1,-1). This fuses selection AND count --
#      count falls out of the same scored search, so a useless second parent is not
#      forced on tight arrays (the exact P1b tension).
#   2. PREDICT that block with ONE joint co-adaptive sign-sign LMS on the SELECTED
#      pair (structure verbatim from `xchan_joint2`): pred=(w_u*x[pu]+w_l*x[pl])>>s,
#      e=x[c]-pred, and BOTH taps co-adapt against the SHARED post-subtraction
#      residual (w_u+=sign(e)sign(x[pu]), w_l+=sign(e)sign(x[pl]) -- +/-1 update,
#      multiplierless). The taps descend the shared residual so each adapts to the
#      correlation REMAINING once the other parent's contribution is out -- the
#      stochastic-gradient 2x2 normal-equations solve. ASYMMETRIC rank-1
#      residual-only injection: only channel c's coded residual is modified; the raw
#      parent rows are inputs left CLEAN (robustness INSIGHTS P3-refinement credits
#      the rank-1 subtract with; the retired energy-preserving iklt_adaptive rotation
#      corrupted both channels). Taps PERSIST across blocks (the selected pair is
#      stable within a recording -- P4-refinement -- so warm taps rarely see a
#      pair-change transient); a slot whose parent is absent has zero input, so its
#      tap is frozen and contributes nothing.
#   3. BOOTSTRAP: block 0 (no previous block to select from) uses the fixed grid
#      (up,left) pair -- the joint2 pair -- so the taps warm from t=0; block 1 on
#      re-selects. ZERO side-info (both the selected pair AND the taps are recomputed
#      by the decoder from bit-identical reconstructed history), look-ahead 0.
#
# Distinct from every relative on the axis each names decisive:
#   - vs RETIRED `xchan_multiparent`: summed MARGINAL betas double-count the parents'
#     shared mode; here a JOINT gradient (and a JOINT 2x2 LS at selection time) cannot.
#   - vs RETIRED `iklt_adaptive`: energy-preserving rotation corrupts both channels;
#     here the predictor is asymmetric, rank-1 residual-only, parents left clean.
#   - vs KEPT `xchan_joint2`: FIXED up+left pair -> per-channel per-block SELECTED pair.
#   - vs PROMOTED `bestpartner`/`bestpartner_adaptive`: single selected parent ->
#     jointly-solved selected PAIR (adds the second co-adapted tap on top of selection).
# Behind the spatial front-end: the order-4 sign-sign LMS temporal predictor
# (INSIGHTS P2, the promoted best's back-end) + adaptive Rice. Embeddable: +1 spatial
# tap + per-block pair re-selection on the order-4 base, zero side-info; clears the
# tight 125-cyc neural budget. Grounded in MPEG-4 ALS multichannel prediction / Choi
# et al. Sensors 2014 correlation-sorted channel pairing (paper-reported, unverified
# here). INSIGHTS open-frontier #1.
# ===========================================================================
JBP2_MAGIC = 0x4A42          # 'JB' (jointly-solved best pair)
JBP2_SHIFT = ec.CROSS_SHIFT  # fixed-point spatial-tap scale (matches the +xchan family)
JBP2_ORDER = LMS4_ORDER      # order-4 temporal base behind the spatial front-end (P2)
JBP2_BLOCK = ec.BLOCK        # re-selection block (aligns with the Rice block)


def _round_div(num, den):
    """Symmetric rounded integer divide round(num/den) for den > 0, integer-only
    (Python big-ints, so the 2x2-solve intermediates never overflow). Identical on
    encode and decode -- used only to rank candidate pairs at SELECTION time."""
    if num >= 0:
        return (num + den // 2) // den
    return -(((-num) + den // 2) // den)


def _jbp2_pair_resid(xc, xp, xq, shift=JBP2_SHIFT):
    """Residual of channel block xc under a JOINT 2x2 integer least-squares fit on
    the pair (xp, xq) over one previous block -- the marginal->multiple fix at
    SELECTION time (accounts for parent-parent covariance, so it cannot double-count
    the parents' shared mode the way the retired summed multiparent did). Returns
    the residual 1-D array, or None if the pair is degenerate/collinear (det<=0)."""
    xc = xc.astype(np.int64); xp = xp.astype(np.int64); xq = xq.astype(np.int64)
    Spp = int((xp * xp).sum()); Sqq = int((xq * xq).sum()); Spq = int((xp * xq).sum())
    Scp = int((xc * xp).sum()); Scq = int((xc * xq).sum())
    det = Spp * Sqq - Spq * Spq
    if det <= 0:
        return None
    a = _round_div((Scp * Sqq - Scq * Spq) << shift, det)   # fixed-point joint gains
    b = _round_div((Scq * Spp - Scp * Spq) << shift, det)
    a = max(-32768, min(32767, a)); b = max(-32768, min(32767, b))
    pred = (a * xp + b * xq) >> np.int64(shift)
    return xc - pred


def _jbp2_select_block(xc_prev, x, cands, ps, pe):
    """Backward per-block SELECTION of the best pair/single/none from the PREVIOUS
    raw block, scored in estimated Rice bits. Returns (pu, pl): a jointly-solved
    pair (both >=0), a single best-partner (pu>=0, pl=-1), or none (-1,-1). Fuses
    selection AND count -- count falls out of the scored search, so a useless second
    parent is not forced. Integer-only and deterministic, so encode and decode --
    both holding the bit-identical reconstructed previous block -- pick identically."""
    best_bits = _bp_score(xc_prev)                # option: no cross-channel parent
    best_pu, best_pl = -1, -1
    for p in cands:                               # single-parent options (marginal LS)
        b = _bp_opt_beta(xc_prev, x[p, ps:pe], BP_SHIFT)
        if b == 0:
            continue
        bits = _bp_score(xc_prev - ((b * x[p, ps:pe]) >> BP_SHIFT))
        if bits < best_bits:
            best_bits, best_pu, best_pl = bits, p, -1
    L = len(cands)                                # joint pair options (2x2 LS)
    for ii in range(L):
        for jj in range(ii + 1, L):
            resid = _jbp2_pair_resid(xc_prev, x[cands[ii], ps:pe], x[cands[jj], ps:pe])
            if resid is None:
                continue
            bits = _bp_score(resid)
            if bits < best_bits:
                best_bits, best_pu, best_pl = bits, cands[ii], cands[jj]
    return best_pu, best_pl


def _jbp2_forward(x, cols, B=JBP2_BLOCK, shift=JBP2_SHIFT):
    """Joint-solved SELECTED-pair spatial sign-sign LMS decorrelation. Per channel
    c and block i: the pair (pu,pl) is re-selected from the previous raw block
    (block 0 -> fixed grid (up,left) bootstrap); ONE joint 2-tap sign-sign LMS
    predicts x[c] from that pair, both taps co-adapting against the shared residual,
    taps PERSISTING across blocks. Only channel c's residual is coded; the raw
    parent rows are left clean. Integer-only, per-sample, look-ahead 0. Returns the
    cross-residual [C,N] int64."""
    C, N = x.shape
    x = x.astype(np.int64)
    y = x.copy()
    up, left = _xj2_parents(C, cols)
    nblocks = (N + B - 1) // B
    for c in range(C):
        cands = _bp_candidates(c, cols, C)
        if not cands:
            continue                              # grid origin: coded as-is
        xc = x[c]; yc = y[c]
        wu = wl = 0
        for i in range(nblocks):
            s, e = i * B, min((i + 1) * B, N)
            if i == 0:
                pu, pl = int(up[c]), int(left[c])          # fixed-pair bootstrap
            else:
                pu, pl = _jbp2_select_block(x[c, (i - 1) * B:i * B], x, cands,
                                            (i - 1) * B, i * B)
            prow = x[pu] if pu >= 0 else None
            lrow = x[pl] if pl >= 0 else None
            for t in range(s, e):
                u = int(prow[t]) if prow is not None else 0
                l = int(lrow[t]) if lrow is not None else 0
                pred = (wu * u + wl * l) >> shift
                ev = int(xc[t]) - pred
                yc[t] = ev
                se = 1 if ev > 0 else (-1 if ev < 0 else 0)
                if prow is not None:
                    wu += se * (1 if u > 0 else (-1 if u < 0 else 0))
                if lrow is not None:
                    wl += se * (1 if l > 0 else (-1 if l < 0 else 0))
    return y


def _jbp2_inverse(y, cols, B=JBP2_BLOCK, shift=JBP2_SHIFT):
    """Invert _jbp2_forward. Every candidate parent has grid idx < c so its row is
    fully reconstructed before c; within a channel we rebuild raw block-by-block in
    time order, so block i-1 is restored before block i and the SAME per-block pair
    is re-selected from it, with the SAME persistent taps re-derived per sample from
    the shared residual e=y[c,t] and reconstructed parents -- mirroring the encoder
    bit-for-bit."""
    C, N = y.shape
    y = y.astype(np.int64)
    x = y.copy()
    up, left = _xj2_parents(C, cols)
    nblocks = (N + B - 1) // B
    for c in range(C):
        cands = _bp_candidates(c, cols, C)
        if not cands:
            continue
        yc = y[c]; xc = x[c]
        wu = wl = 0
        for i in range(nblocks):
            s, e = i * B, min((i + 1) * B, N)
            if i == 0:
                pu, pl = int(up[c]), int(left[c])
            else:
                pu, pl = _jbp2_select_block(x[c, (i - 1) * B:i * B], x, cands,
                                            (i - 1) * B, i * B)
            prow = x[pu] if pu >= 0 else None    # parent idx < c -> already reconstructed
            lrow = x[pl] if pl >= 0 else None
            for t in range(s, e):
                u = int(prow[t]) if prow is not None else 0
                l = int(lrow[t]) if lrow is not None else 0
                pred = (wu * u + wl * l) >> shift
                ev = int(yc[t])
                xc[t] = ev + pred
                se = 1 if ev > 0 else (-1 if ev < 0 else 0)
                if prow is not None:
                    wu += se * (1 if u > 0 else (-1 if u < 0 else 0))
                if lrow is not None:
                    wl += se * (1 if l > 0 else (-1 if l < 0 else 0))
    return x


def jbp2_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _jbp2_forward(x, cols)
    res = ec.lms_forward(y, order=JBP2_ORDER)    # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", JBP2_MAGIC, cols, C, N)   # NO side-info (backward-adaptive)
    return hdr + body


def jbp2_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == JBP2_MAGIC, "bad xchan_jointbp2 magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res, order=JBP2_ORDER)    # matched order-4 inverse
    x = _jbp2_inverse(y, cols)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: regime-switched (context-gated) order-4 temporal predictor
# behind the proven best-partner spatial front-end
# (LMS4rs+Rice+xchan_bestpartner).
# ---------------------------------------------------------------------------
# INSIGHTS open-frontier #2: with every SPATIAL lever within ~1% of a shared
# ceiling, the remaining bits live in the TEMPORAL residual's OWN entropy. P5
# proved the coder and the Rice-parameter context are dead, so the residual
# entropy is set UPSTREAM, by the predictor. HD-sEMG is strongly non-stationary
# and bursty (quiescent baseline vs MUAP bursts): a SINGLE adaptive sign-LMS
# under-fits the high-variance burst segments, where most residual bits live,
# because one coefficient set must compromise between the two regimes' very
# different local AR statistics.
#
# Mechanism (novel axis vs everything shipped): keep the promoted order-4
# best-partner spatial front-end verbatim (_bp_select / _bp_inverse), but
# replace the single order-4 sign-sign LMS with a small BANK of order-4 sign-LMS
# predictors, one SELECTED PER SAMPLE by a backward-derived ACTIVITY REGIME:
#   * Per channel we keep two leaky integer integrators of |residual|: a FAST one
#     (window ~2^RSW_WR samples, "recent activity") and a SLOW one (window
#     ~2^RSW_WL, "long-term level"). Both are updated AFTER each sample from the
#     residual magnitude, so at sample t they summarise only residuals < t.
#   * The regime is a quantized bucket of recent-vs-long-term activity (recent
#     level below / around / above the long-term level -> quiescent / normal /
#     burst). Using recent RELATIVE to a backward long-term reference makes the
#     split scale-free (no per-dataset threshold, no side-info).
#   * Only the SELECTED regime's order-4 weights predict and adapt this sample;
#     each regime therefore accumulates coefficients matched to its OWN local AR
#     statistics, lowering the conditional residual variance H(e|regime) < H(e).
#   * The regime at t depends only on causally-available reconstructed residuals,
#     which the decoder reproduces bit-for-bit (it reads e[t] from the stream),
#     so the decoder mirrors the SAME regime and the SAME per-regime updates ->
#     ZERO side-info, fully causal, look-ahead 0 (INSIGHTS P4). Order stays 4 per
#     P2; only the NUMBER of coefficient sets grows (RSW_NREG small = 3).
#
# Distinct from the RETIRED cross-channel-context Rice (`LMS+Rice+xctx`, P5):
# that conditioned the Rice PARAMETER k on a neighbour-energy context and left
# the residual itself unchanged (H(e|neighbour energy) ~= H(e) after LMS, so it
# lost). This conditions the PREDICTOR COEFFICIENTS on a TEMPORAL activity
# regime, reducing the residual UPSTREAM of the coder -- a different quantity on
# a different axis. NOT an entropy back-end swap (coder stays adaptive Rice).
# Grounded in adaptive switching linear prediction (Seemann & Tischer) and
# context-dependent MAE-minimizing prediction (Ulacha 2024) (paper-reported,
# unverified here).
# ===========================================================================
RSBP_MAGIC = 0x5253          # 'RS' (regime-switched best-partner)
RSW_ORDER = LMS4_ORDER       # order-4 temporal predictor (INSIGHTS P2), unchanged
RSW_SHIFT = ec.LMS_SHIFT     # fixed-point weight scale, same as the family LMS (8)
RSW_NREG = 3                 # predictor-bank size: quiescent / normal / burst (small, P2)
RSW_WR = 4                   # fast (recent) leaky-energy window ~2^4 samples
RSW_WL = 9                   # slow (long-term) leaky-energy window ~2^9 samples


def _rsw_regime(recent, long_):
    """Per-channel activity regime from the backward leaky energy integrators.
    Compares the recent level (recent >> RSW_WR) against the long-term level
    (long_ >> RSW_WL) via cross-multiplication so the test is exact integer
    arithmetic with no precision loss:
        recent/2^WR  <  3/4 * long_/2^WL   -> regime 0 (quiescent)
        recent/2^WR  >  3/2 * long_/2^WL   -> regime 2 (burst)
        otherwise                          -> regime 1 (normal)
    Both accumulators are >= 0 (they accumulate |e| and subtract a non-negative
    leak), so the shifts are floors on non-negative int64 -- deterministic and
    identical on encode and decode. Bootstrap (both 0) -> regime 1."""
    A = recent << np.int64(RSW_WL)          # recent level on the common 2^(WR+WL) scale
    B = long_ << np.int64(RSW_WR)           # long-term level on the same scale
    r = np.ones(recent.size, np.int64)      # default: normal
    r[4 * A < 3 * B] = 0                     # recent < 0.75 * long  -> quiescent
    r[2 * A > 3 * B] = 2                     # recent > 1.5  * long  -> burst
    return r


def _rsw_forward(x, order=RSW_ORDER, shift=RSW_SHIFT, nreg=RSW_NREG):
    """Regime-switched sign-sign LMS: a bank of `nreg` order-`order` predictors,
    one selected per sample-channel by the backward activity regime. Vectorized
    over channels; mirrors ec.lms_forward except for the per-sample bank pick."""
    C, N = x.shape
    x = x.astype(np.int64)
    w = np.zeros((C, nreg, order), np.int64)   # per-channel per-regime weights
    hist = np.zeros((C, order), np.int64)      # shared past reconstructed samples
    recent = np.zeros(C, np.int64)             # fast leaky |e| integrator
    long_ = np.zeros(C, np.int64)              # slow leaky |e| integrator
    res = np.empty((C, N), np.int64)
    ci = np.arange(C)
    for t in range(N):
        r = _rsw_regime(recent, long_)         # regime from residuals < t
        wsel = w[ci, r, :]                     # [C, order] selected bank
        pred = (wsel * hist).sum(axis=1) >> shift
        e = x[:, t] - pred
        res[:, t] = e
        # adapt ONLY the selected regime's weights (identical rule in decoder)
        w[ci, r, :] = wsel + np.sign(e)[:, None] * np.sign(hist)
        ae = np.abs(e)
        recent += ae - (recent >> np.int64(RSW_WR))
        long_ += ae - (long_ >> np.int64(RSW_WL))
        hist[:, 1:] = hist[:, :-1]
        hist[:, 0] = x[:, t]
    return res


def _rsw_inverse(res, order=RSW_ORDER, shift=RSW_SHIFT, nreg=RSW_NREG):
    """Exact inverse of _rsw_forward. Reconstructs x[:, t] = pred + e where the
    regime, bank selection and per-regime update are recomputed from the SAME
    causally-available residuals the encoder used -> zero side-info."""
    C, N = res.shape
    res = res.astype(np.int64)
    w = np.zeros((C, nreg, order), np.int64)
    hist = np.zeros((C, order), np.int64)
    recent = np.zeros(C, np.int64)
    long_ = np.zeros(C, np.int64)
    x = np.empty((C, N), np.int64)
    ci = np.arange(C)
    for t in range(N):
        r = _rsw_regime(recent, long_)
        wsel = w[ci, r, :]
        pred = (wsel * hist).sum(axis=1) >> shift
        e = res[:, t]
        xt = pred + e
        x[:, t] = xt
        w[ci, r, :] = wsel + np.sign(e)[:, None] * np.sign(hist)
        ae = np.abs(e)
        recent += ae - (recent >> np.int64(RSW_WR))
        long_ += ae - (long_ >> np.int64(RSW_WL))
        hist[:, 1:] = hist[:, :-1]
        hist[:, 0] = xt
    return x


def rsbp_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    xt, parents, betas = _bp_select(x, cols)     # proven best-partner front-end
    res = _rsw_forward(xt)                        # regime-switched order-4 sign-LMS
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", RSBP_MAGIC, cols, C, N)
    side = parents.astype("<i2").tobytes() + betas.astype("<i2").tobytes()
    return hdr + side + body


def rsbp_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == RSBP_MAGIC, "bad regime-switched bestpartner codec magic"
    off = 12
    parents = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    betas = np.frombuffer(buf, "<i2", C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    xt = _rsw_inverse(res)
    x = _bp_inverse(xt, parents, betas)
    return x.astype(np.int16)


# ===========================================================================
# Uniform codec objects + the registry
# ===========================================================================
class Codec:
    def __init__(self, name, encode, decode, meta, family="", desc="",
                 retired=False, retired_reason=""):
        self.name = name
        self.encode = encode
        self.decode = decode
        self.meta = meta
        self.family = family
        self.desc = desc
        self.cost = cost.score(meta)
        # `retired` marks a codec that has been conclusively verified
        # Pareto-dominated on REAL data (never for merely "not the best" --
        # a non-dominated but marginal codec, like a best-of-N Pareto corner,
        # stays active). Retired codecs are NEVER deleted -- the code and its
        # self-test coverage stay forever for reproducibility and so the
        # verdict can be re-checked -- they are just excluded from the default
        # bench.py/leaderboard sweep so they stop being re-benchmarked and
        # re-reported every cycle. Set `retired_reason` to the experiment
        # record / cycle that made the call.
        self.retired = retired
        self.retired_reason = retired_reason


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

# xchan_adaptive (backward-adaptive realization): on top of the +xchan per-sample
# work (1 mul + 1 shift + 1 sub), each block-channel accumulates two running
# dot-products (<x_c,x_p> and <x_p,x_p>, ~2 macs/sample-ch) and does ONE rounded
# integer divide at the block boundary (amortised ~divide/BLOCK). The decoder
# RECOMPUTES the same beta (it is not transmitted), so dec_ops == enc_ops here.
_XADAPT_XTRA = 3   # 2 dot-product macs + amortised block divide, per sample-ch
# state/ch: order-8 LMS (40) + current beta int16 (2) + two int64 block
# accumulators for the running dot-products (16).
_XADAPT_STATE = _LMS_STATE + 18
_XADAPT_NOTE = (
    "backward-adaptive per-block cross-channel gain: beta[block i] is the "
    "integer least-squares ratio <x_c,x_p>/<x_p,x_p> over the PREVIOUS block's "
    "already-reconstructed samples (block 0 -> beta=0). Fully causal, "
    "lookahead=0, and NO beta side-info -- the decoder recomputes it. Replaces "
    "the whole-signal float beta + header side-info of the +xchan variants.")

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

# RETIRED (cycle 1, compression-cycle-2026-07-08): backward-adaptive per-block
# cross-channel beta -- no side-info, fully causal -> lookahead 0, unlike the
# whole-signal +xchan variants above. Kept registered (bit-exact, embedded_ok,
# double-verified) for reproducibility, but excluded from the default
# bench.py/leaderboard sweep: two independent verifiers confirmed it is
# Pareto-dominated by LMS+Rice+xchan on real otb_hdsemg_vl (2.13x/cost 0.065
# vs the incumbent's 2.14x/cost 0.057 -- worse ratio AND higher cost). See
# experiments/001_lms_rice_xchan_adaptive.md for the full record.
_register(Codec("LMS+Rice+xchan_adaptive", xadapt_encode, xadapt_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _XCHAN_OPS + _XADAPT_XTRA,
    dec_ops=_LMS_OPS + _XCHAN_OPS + _XADAPT_XTRA,
    state_bytes_per_ch=_XADAPT_STATE, causal=True, lookahead_samples=0,
    block_size=XADAPT_BLOCK, notes=_XADAPT_NOTE), family="cross-channel",
    desc="LMS + grid-neighbour decorrelation, backward-adaptive per-block gain",
    retired=True,
    retired_reason="Pareto-dominated by LMS+Rice+xchan on real otb_hdsemg_vl "
                    "(2.13x/0.065 vs 2.14x/0.057); double-verified PROMOTE on "
                    "correctness/embeddability only, never on ratio. "
                    "experiments/001_lms_rice_xchan_adaptive.md, cycle 2026-07-08."))

# NEW candidate: best-partner cross-channel selection (this cycle).
# Encode adds, on top of LMS+xchan, a per-channel scan over <=4 causal-neighbour
# candidates (each ~2 MACs/sample to accumulate <xg,xp> and <xp,xp>) to pick the
# best partner -> ~8 extra enc ops/sample-ch; the decoder does NOT search (it
# reads the chosen parent+beta side-info), so its op count matches plain xchan.
# State adds one parent-id byte/ch beyond the incumbent xchan state.
# integer-KLT front-end: a fixed reversible integer inter-channel transform
# applied per time-slice before LMS. The schedule has ~one horizontal + ~one
# vertical rotation per channel (~2 rotations/channel, each rotation touching 2
# channels -> ~1 rotation attributable per sample-ch on each axis). Each rotation
# is 3 lifting shears = 3 x (1 mul + 1 rounded shift + 1 add) ~ 12 ops; ~2
# rotations touch each channel -> ~24 ops/sample-ch. The transform is stateless
# in time (fixed global coefficients, zero look-ahead), so it adds NO persistent
# per-channel state on top of the LMS state. Decoder does the inverse rotations
# at the same cost, so dec_ops == enc_ops.
_IKLT_OPS = 24
_IKLT_NOTE = (
    "fixed multiplierless reversible integer inter-channel transform "
    "(integer-KLT via 3-step lifting/Givens rotations at theta=45deg -- the "
    "EXACT KLT of a stationary isotropic equal-variance neighbour pair for any "
    "correlation, so data-independent: no training, no eigendecomposition, no "
    "side-info). Applied per time-slice over a fixed grid-neighbour schedule "
    "(all horizontal then all vertical adjacent pairs, channel order); the "
    "cascade mixes each channel across a neighbourhood -> genuinely MULTI-TAP, "
    "distinct from the rank-1 single-neighbour subtract of +xchan/bestpartner. "
    "Transform is within a time-slice so temporal look-ahead=0; decoder applies "
    "the inverse rotations in reverse order. Then per-channel LMS+Rice as usual.")

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
    desc="LMS + best-of-4 causal-neighbour cross-channel selection + Rice",
    retired=True,
    retired_reason="Conclusively Pareto-dominated by its order-4 sibling "
                   "LMS4+Rice+xchan_bestpartner (promoted 2026-07-16): identical "
                   "best-partner front-end, predictor order 8->4 gives higher ratio at "
                   "LOWER cost (0.039 vs 0.063) on ALL 4 real sets (otb 2.162x vs 2.151x, "
                   "hyser 1.480x vs 1.478x, capgmyo 1.350x vs 1.349x, cemhsey 1.956x vs "
                   "1.955x) -- P2 (order-8 over-provisioned, deeper prediction fits noise). "
                   "experiments/006_lms4_rice_xchan_bestpartner.md, cycle 2026-07-16."))

# NEW candidate: fixed reversible integer-KLT (lifting) inter-channel transform
# (this cycle). Multi-tap spatial front-end; zero temporal look-ahead; no
# side-info. Encode and decode both run the transform (fwd / inverse rotations),
# so enc_ops == dec_ops. No persistent transform state beyond the LMS weights.
_register(Codec("LMS+Rice+iklt", iklt_encode, iklt_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _IKLT_OPS, dec_ops=_LMS_OPS + _IKLT_OPS,
    state_bytes_per_ch=_LMS_STATE, causal=True, lookahead_samples=0,
    block_size=ec.BLOCK, notes=_IKLT_NOTE), family="cross-channel",
    desc="fixed reversible integer-KLT (lifting) inter-channel transform + LMS + Rice",
    retired=True,
    retired_reason="Pareto-dominated by LMS+Rice+xchan on real otb_hdsemg_vl "
                    "(iklt 2.07x/cost 0.068 vs incumbent 2.24x/cost 0.057 -- worse "
                    "ratio AND higher cost; also dominated by bestpartner 2.25x/0.063 "
                    "and delta+Rice+xchan 2.19x/0.013). Fixed 45deg integer-KLT captures "
                    "only +8.8% real xchan gain vs single-neighbour subtract's +18.0%. "
                    "experiments/002_lms_rice_iklt.md, cycle 2026-07-13."))

# NEW candidate (this cycle): DATA-DEPENDENT adaptive integer-lifting rotation
# cascade (backward-adaptive Givens angle). Same rotation cascade as the retired
# iklt (~24 ops/sample-ch of 3-lift shears), PLUS a backward angle estimate: per
# schedule pair, accumulate three running dot-products (saa,sbb,sab ~ 3 macs/
# sample-ch over the previous block) and, once per block, an argmin over the
# 31-entry angle table (amortised ~31/256 ~ 0.1 op/sample-ch). The decoder
# RECOMPUTES the same angle (it is not transmitted), so dec_ops == enc_ops.
# State adds, on top of the LMS weights, the three int64 covariance accumulators
# for the pair a channel is currently in (24 B) + the current angle index (~1 B).
_ITSKLT_XTRA = 4    # 3 covariance-accumulate macs + amortised per-block argmin
_ITSKLT_STATE = _LMS_STATE + 26
_ITSKLT_NOTE = (
    "backward-adaptive DATA-DEPENDENT integer-lifting Givens rotation cascade. "
    "Keeps the retired iklt's multiplierless reversible 3-lift shear butterfly "
    "(lossless for ANY integer lift coeffs) but the rotation ANGLE per grid-"
    "neighbour pair per time-block is chosen from the pair's 2x2 covariance over "
    "the PREVIOUS reconstructed RAW block: the tabulated (31 angles, -60..60deg) "
    "theta minimizing the post-rotation off-diagonal |0.5(sbb-saa)sin2t+sab cos2t|,"
    " via an integer sin/cos table (no atan, no eigendecomposition, no float in "
    "the codec path). theta[block i] uses only raw block i-1 which the decoder "
    "reconstructs before it reaches block i -> zero side-info, look-ahead 0, "
    "decoder recomputes the angle. Block 0 bootstraps to identity (theta=0). "
    "Cascaded over all horizontal then all vertical adjacent pairs (=_iklt_pairs) "
    "-> multi-tap, distinct from the rank-1 single-neighbour subtract. Then the "
    "unchanged order-8 sign-sign LMS + adaptive Rice back-end (only the spatial "
    "basis differs from the retired fixed-45deg iklt).")
_register(Codec("LMS+Rice+iklt_adaptive", itsklt_encode, itsklt_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _IKLT_OPS + _ITSKLT_XTRA,
    dec_ops=_LMS_OPS + _IKLT_OPS + _ITSKLT_XTRA,
    state_bytes_per_ch=_ITSKLT_STATE, causal=True, lookahead_samples=0,
    block_size=ITSKLT_BLOCK, notes=_ITSKLT_NOTE), family="cross-channel",
    desc="data-dependent backward-adaptive integer-KLT (lifted Givens angle) "
         "cascade + LMS + Rice",
    retired=True,
    retired_reason="Pareto-dominated by LMS+Rice+xchan on ALL 4 real sets "
    "(cycle 2026-07-14, results/cycle_bench.csv): otb 1.885x/0.083 vs 2.143x/"
    "0.057, hyser 1.352x vs 1.474x, capgmyo 1.326x vs 1.349x, cemhsey 1.761x vs "
    "1.955x -- worse ratio AND higher cost everywhere. Backward-adaptive rotation "
    "angle from the previous block is a stale/noisy estimate on non-stationary "
    "HD-sEMG and the rotation corrupts BOTH channels, so it captures only "
    "+1.7..+3.3% cross-channel gain (worse than even the retired fixed iklt). "
    "See experiments/003_lms_rice_iklt_adaptive.md."))

# NEW seeded candidate
_register(Codec("fixed0-3+Rice", fixed_encode, fixed_decode, CodecMeta(
    integer_only=True, enc_ops=_FIXED_OPS, dec_ops=_FIXED_OPS,
    state_bytes_per_ch=_FIXED_STATE, causal=True, lookahead_samples=ec.BLOCK,
    block_size=ec.BLOCK), family="temporal",
    desc="FLAC fixed predictors ord 0-3, best-per-block + Rice"))

# NEW candidate (this cycle): table-driven tANS entropy back-end vs Rice on the
# IDENTICAL LMS+xchan predictor/front-end (INSIGHTS P5, open-frontier #1). Over
# the incumbent LMS+xchan per-sample work, the tANS back-end adds, per sample-
# ch: category bit-length (~3 ops), mantissa split (~2), one tANS table lookup +
# a short variable-length bit renorm (~5), a category histogram accumulate (~1),
# and the amortised per-block table build (2*M table entries / ANS_BLOCK ~ 1
# op/sample-ch) -> ~+12 ops over Rice. Decode is symmetric (also table lookups +
# renorm, no per-symbol divide), so dec_ops == enc_ops. The runtime path is
# division-free; the only divides are in the once-per-block freq normalization
# and table build. Persistent state adds, on top of the LMS+xchan state, the
# per-block category frequency table (~90 B) and Rice-k-equivalent bookkeeping;
# the M-entry tANS lookup tables and the ANS_BLOCK reverse-encode symbol buffer
# are SHARED working memory (rebuilt per block, not multiplied per channel) --
# noted, not charged per-channel. Bounded look-ahead = one ANS_BLOCK.
_ANS_XTRA = 12
_ANS_STATE = _LMS_STATE + _XCHAN_STATE + 90     # + per-block freq table (side-info)
_ANS_NOTE = (
    "table-driven tANS (LOCO-ANS style) entropy back-end swapped in for adaptive "
    "Golomb-Rice on the IDENTICAL LMS+Rice+xchan predictor and cross-channel "
    "front-end (same grid-parent beta side-info) -- a clean head-to-head that "
    "isolates the back-end's marginal bits (INSIGHTS P5). Residual coder is a "
    "LOCO-ANS bucket+remainder split: zigzag u, entropy-code the category "
    "c=bit-length(u) with tANS, ship c-1 raw mantissa bits (bounds the ANS "
    "alphabet for any int16 input). Per ANS_BLOCK a STATIC integer-normalized "
    "category frequency table (sum=2**R, R=10) is built and shipped as tiny "
    "side-info; both sides build bit-identical tANS tables from it. tANS state "
    "normalized to [M,2M); transitions PRECOMPUTED from the bitwise-rANS map so "
    "the runtime coder is table lookups + a variable bit renorm with NO per-"
    "symbol divide (the FPGA-friendly property; divides live only in the once-"
    "per-block table build). Encoder runs the ANS pass in reverse over the block "
    "(reverse-order encode buffer), decoder reads forward. Look-ahead = one "
    "ANS_BLOCK; the incumbent's whole-signal float beta remains a port caveat "
    "(front-end unchanged). Payoff expected small/uncertain -- a back-end "
    "refinement to MEASURE on real data, not a headline lever (P5).")
_register(Codec("LMS+Rice+xchan_tans", ans_encode, ans_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _XCHAN_OPS + _ANS_XTRA,
    dec_ops=_LMS_OPS + _XCHAN_OPS + _ANS_XTRA,
    state_bytes_per_ch=_ANS_STATE, causal=True, lookahead_samples=ANS_BLOCK,
    block_size=ANS_BLOCK, notes=_ANS_NOTE), family="entropy-backend",
    desc="LMS + grid-neighbour decorrelation + table-driven tANS residual coder "
         "(vs Rice, same predictor)",
    retired=True,
    retired_reason="Pareto-dominated by LMS+Rice+xchan (same front-end, Rice "
    "back-end) on ALL 4 real sets (cycle 2026-07-14, results/cycle_bench.csv): "
    "tANS is 1.4..1.8% SMALLER ratio at ~2x cost (0.109 vs 0.057) -- otb 2.103x "
    "vs 2.143x, hyser 1.451x vs 1.474x, capgmyo 1.330x vs 1.349x, cemhsey 1.922x "
    "vs 1.955x. Real HD-sEMG residuals are near-geometric, so Golomb-Rice is "
    "already the near-optimal prefix code and the per-block category-freq table "
    "side-info costs more than the sub-Golomb bits recovered (confirms INSIGHTS "
    "P5). See experiments/004_lms_rice_xchan_tans.md."))

# NEW candidate (this cycle): Adaptive Common Average Reference (ACAR). Over the
# per-channel LMS+Rice work, the front-end adds, per sample-ch: one accumulate
# into the running array total (~1 op), one subtract of the shared CAR (~1 op),
# and an amortised floor-divide per time slice (1 divide / C ~ 0.03 op/sample-ch);
# the backward gate re-accumulates two energy sums over the previous block (~2
# macs/sample-ch) plus one comparison per block (amortised). ~4 extra ops over
# plain LMS. The decoder RECOMPUTES the gate (not transmitted) and runs the exact
# inverse lift, so dec_ops == enc_ops. The running array total and the two energy
# accumulators are O(1) SHARED working state (one per time slice / per block, NOT
# multiplied per channel) -- noted, not charged per-channel; per-channel state is
# just the LMS weights plus the current gate flag.
_ACAR_XTRA = 4     # accumulate-to-total + CAR subtract + amortised divide + gate macs
_ACAR_STATE = _LMS_STATE + 2   # LMS weights + gate flag (array-sum/energy accs shared)
_ACAR_NOTE = (
    "Adaptive Common Average Reference: a reversible-integer S-transform-style "
    "lift that removes the GLOBAL array common-mode (weighted mean across the "
    "whole array) before the temporal predictor -- a rank-1 GLOBAL spatial lever, "
    "distinct from the pairwise/single-neighbour subtracts of +xchan/xadapt/"
    "bestpartner (a different slice of the cross-channel mutual information, "
    "INSIGHTS P1). Per ON time-slice: S=sum_c x (array total), CAR=floor(S/C); the "
    "root channel slot carries S (the virtual total channel -- preserves the array "
    "DC that subtracting the mean from all channels would lose), every other "
    "channel becomes x-CAR (true mean-referenced residual: common mode removed, "
    "only ~1/C of the aggregate noise added). Inverse is exact and integer "
    "(CAR=floor(S/C); x_c=y_c+CAR; x_0=S-sum_{c>=1}x_c), per-time-slice, "
    "look-ahead 0. GATED per block BACKWARD-ADAPTIVELY: block i is transformed only "
    "if C*sum(CAR^2)/sum(x^2) over the PREVIOUS reconstructed raw block exceeds "
    "1/16 (~2/C, above the 1/C floor independent noise makes by array-averaging) -- "
    "so it fires only on a genuine shared component, cannot hurt low-common-mode "
    "blocks (identity pass-through), and ships ZERO side-info (the decoder "
    "recomputes the gate). Block 0 bootstraps OFF. Then the unchanged order-8 "
    "sign-sign LMS + adaptive Rice back-end (only the spatial front-end differs). "
    "Basis: Vaisman/Jordanic/Farina adaptive CAR filtering for HD-EMG (MBEC 2014), "
    "a myocontrol/SNR result -- unverified for compression here.")
_register(Codec("LMS+Rice+acar", acar_encode, acar_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _ACAR_XTRA, dec_ops=_LMS_OPS + _ACAR_XTRA,
    state_bytes_per_ch=_ACAR_STATE, causal=True, lookahead_samples=0,
    block_size=ACAR_BLOCK, notes=_ACAR_NOTE), family="cross-channel",
    desc="adaptive common-average reference (reversible-integer lift, backward-"
         "gated) + LMS + Rice"))

# NEW candidate (this cycle): Order-4 LMS under the best-partner front-end
# (INSIGHTS open-frontier #1). Identical to LMS+Rice+xchan_bestpartner but with
# the temporal predictor right-sized order-8 -> order-4 (INSIGHTS P2). Op/state
# accounting mirrors bestpartner with the LMS half-sized: order-4 sign-sign LMS
# is ~4 mac + 1 shift + 4-tap sign update(~8) + hist shift(4) + rice(~9) ~ 26 ops
# and 4 weights + 4 history = 8xint16 = 16 B + rice bookkeeping ~ 24 B state (vs
# the order-8 _LMS_OPS=50 / _LMS_STATE=40). Encoder adds the +xchan per-sample
# work and the best-partner neighbour scan (~8 ops); the decoder is search-free
# (reads the chosen parent+beta side-info), so dec_ops omits the scan.
_LMS4_OPS = 26
_LMS4_STATE = 24
_LMS4BP_NOTE = (
    "best-partner cross-channel front-end (per-channel best-of-4 causal grid "
    "neighbour + integer gain, 2xint16/ch side-info -- reused verbatim from "
    "LMS+Rice+xchan_bestpartner) with the temporal predictor right-sized from "
    "the family's order-8 to order-4 sign-sign LMS (INSIGHTS P2: order-4 beats "
    "order-8 on real Hyser/OTB -- deeper prediction fits noise and raises coded "
    "entropy -- at ~half the state/ops). Same backward-adaptive LMS on both "
    "sides (zero temporal side-info, INSIGHTS P4); only the predictor order "
    "differs from bestpartner. Selection derived offline over the whole signal "
    "like the incumbent xchan/bestpartner beta; embeddable realization selects "
    "per block (look-ahead=block). Decoder is search-free.")
_register(Codec("LMS4+Rice+xchan_bestpartner", lms4bp_encode, lms4bp_decode,
    CodecMeta(
        integer_only=True, enc_ops=_LMS4_OPS + _XCHAN_OPS + _BP_SELECT_OPS,
        dec_ops=_LMS4_OPS + _XCHAN_OPS,
        state_bytes_per_ch=_LMS4_STATE + _BP_STATE, causal=True,
        lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_LMS4BP_NOTE),
    family="cross-channel",
    desc="order-4 LMS + best-of-4 causal-neighbour cross-channel selection + Rice"))

# NEW candidate (this cycle): regime-switched (context-gated) order-4 temporal
# predictor under the promoted best-partner front-end (INSIGHTS open-frontier #2).
# Same spatial front-end + same order-4 sign-LMS math as LMS4+Rice+xchan_bestpartner,
# but the single temporal predictor becomes a BANK of RSW_NREG=3 order-4 sign-LMS
# predictors, one selected per sample-channel by a backward activity regime. Ops:
# on top of the order-4 LMS work (_LMS4_OPS) each sample adds two leaky-integrator
# updates (2 add + 2 shift ~4), one 3-way regime compare (~3), and a bank index
# (amortised ~1) -> ~8 extra ops/sample-ch; the per-regime update touches the SAME
# 4 taps, so the mac/adapt work is unchanged (only WHICH set). State: RSW_NREG=3
# weight banks of order-4 (3x4 int16 = 24 B) + order-4 history (8 B) + two int32
# energy accumulators (8 B) ~ 40 B, plus the best-partner side-info state (_BP_STATE).
# Backward-adaptive regime -> ZERO temporal side-info, look-ahead 0 (P4). Decoder
# mirrors the regime from reconstructed residuals, so dec_ops == enc_ops minus the
# best-partner neighbour scan (like bestpartner, decoder is selection-search-free).
_RSW_XTRA = 8
_RSW_STATE = 4 * RSW_NREG * 2 + RSW_ORDER * 2 + 8   # 3x4 weights + 4 hist + 2 acc (int16 units)
_RSBP_NOTE = (
    "regime-switched temporal predictor: keeps the promoted order-4 best-partner "
    "spatial front-end (best-of-4 causal grid neighbour + integer gain, 2xint16/ch "
    "side-info, reused verbatim) and replaces the single order-4 sign-sign LMS with "
    "a BANK of 3 order-4 sign-LMS predictors, one selected PER SAMPLE by a backward "
    "activity regime (recent vs long-term leaky |residual| energy, quiescent/normal/"
    "burst). Each regime adapts only on its own samples -> conditional residual "
    "variance H(e|regime) < H(e) on bursty HD-sEMG (INSIGHTS open-frontier #2: attack "
    "the temporal residual entropy, not the decorrelator). Regime derived from "
    "causally-reconstructed residuals only -> ZERO temporal side-info, look-ahead 0 "
    "(P4); order stays 4 (P2), only the number of coefficient sets grows. Distinct "
    "from the RETIRED xctx (P5): xctx conditioned the Rice PARAMETER on a cross-channel "
    "context leaving the residual unchanged; this conditions the PREDICTOR "
    "COEFFICIENTS on a temporal regime, reducing the residual upstream. Coder stays "
    "adaptive Rice (no back-end swap). Selection front-end derived offline like the "
    "incumbent bestpartner (embeddable realization selects per block, look-ahead=block).")
_register(Codec("LMS4rs+Rice+xchan_bestpartner", rsbp_encode, rsbp_decode,
    CodecMeta(
        integer_only=True, enc_ops=_LMS4_OPS + _RSW_XTRA + _XCHAN_OPS + _BP_SELECT_OPS,
        dec_ops=_LMS4_OPS + _RSW_XTRA + _XCHAN_OPS,
        state_bytes_per_ch=_RSW_STATE + _BP_STATE, causal=True,
        lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_RSBP_NOTE),
    family="cross-channel",
    desc="regime-switched (context-gated) order-4 LMS bank + best-partner + Rice",
    retired=True,
    retired_reason="Conclusively Pareto-dominated by LMS4+Rice+xchan_bestpartner on ALL "
                   "4 real sets (worse ratio AND higher cost 0.0524 vs 0.0394: otb 2.126x "
                   "vs 2.162x, hyser 1.476x vs 1.480x, capgmyo 1.340x vs 1.350x, cemhsey "
                   "1.954x vs 1.956x). Splitting the order-4 predictor into a 3-regime bank "
                   "did NOT lower H(e|regime) below H(e): after order-4 LMS the residual is "
                   "near-white (P2), 'burst' segments are higher-variance NOISE not distinct "
                   "linear dynamics, and per-regime banks each see ~1/3 the samples so they "
                   "adapt noisier -- fragmenting adaptation raised coded bits. Temporal "
                   "residual-entropy lever (frontier #2) spent NEGATIVE. "
                   "experiments/013_lms4rs_rice_xchan_bestpartner.md, cycle 2026-07-22."))

# NEW candidate (this cycle): two-stage scale-matched spatial front-end -- GLOBAL
# adaptive-CAR THEN LOCAL order-4 best-partner (INSIGHTS open-frontier #1). A pure
# CASCADE of two already-verified primitives, so its cost is the union of theirs:
# the ACAR lift's per-sample work (_ACAR_XTRA: accumulate-to-total + CAR subtract +
# amortised floor-divide + backward-gate macs) PLUS the best-partner front-end's
# per-sample subtract (_XCHAN_OPS) and its OFFLINE neighbour scan (_BP_SELECT_OPS,
# encoder only -- the decoder reads the chosen parent+beta side-info and is
# search-free) PLUS the right-sized order-4 LMS (_LMS4_OPS, INSIGHTS P2). The decoder
# RECOMPUTES the backward ACAR gate (not transmitted) and runs both exact inverse
# lifts, so dec_ops == enc_ops minus only the encoder-only best-partner scan. State
# is the order-4 LMS weights/history + best-partner bookkeeping (_BP_STATE) + the
# 1-byte ACAR gate flag; the ACAR array-total and energy accumulators are O(1)
# SHARED working state (one per time-slice/block, not per channel). ACAR ships ZERO
# side-info (backward gate, look-ahead 0); the best-partner (parent,beta) pair is the
# only side-info and, like the incumbent bestpartner, is derived offline over the
# whole signal -- embeddable realization selects per block (look-ahead=block).
_ACARBP_NOTE = (
    "two-stage scale-matched cross-channel front-end: GLOBAL adaptive-CAR (stage 1) "
    "THEN LOCAL best-partner (stage 2), both reused VERBATIM, behind order-4 LMS+Rice "
    "(the promoted best's back-end, INSIGHTS P2). Stage 1 is the ACAR reversible-"
    "integer S-transform lift (root slot carries the array total S; every other "
    "channel becomes x-floor(S/C)), backward-GATED per block (transformed only if "
    "C*sum(CAR^2)/sum(x^2) over the PREVIOUS reconstructed raw block exceeds 1/16 ~2/C, "
    "above the 1/C floor independent noise makes by array-averaging), block-0 OFF, "
    "ZERO side-info -- removes the GLOBAL rank-1 common-mode (one eigenvector, "
    "DC-across-array). Stage 2 is the best-of-4 causal grid neighbour + integer gain "
    "(2xint16/ch side-info) applied to the CAR RESIDUAL -- removes the LOCAL pairwise "
    "MI CAR leaves. The two slices are distinct and NON-INTERCHANGEABLE (INSIGHTS "
    "P1-refinement: CAR wins tight arrays +14.4% OTB, pairwise wins large Hyser/CEMHSEY "
    "+10.8..13.1%); cascading captures BOTH where both exist. ORTHOGONAL BY "
    "CONSTRUCTION -- stage 1 removes the array mean, leaving a residual whose local "
    "pairwise covariance is uncorrelated with the global mean it took, so unlike the "
    "RETIRED summed multi-parent (correlated parents -> over-subtract) the stages "
    "cannot double-count. Decode inverts in reverse (Rice -> lms_inverse(order4) -> "
    "bp_inverse -> acar_inverse), the ACAR gate recomputed from reconstructed raw "
    "history -- fully causal, bit-exact. Risk (to measure): on large arrays CAR may "
    "not clear its gate and add ~nothing after best-partner already took the local "
    "slice -- measure whether the slices are additive or redundant.")
_register(Codec("LMS4+Rice+acar+bestpartner", acarbp_encode, acarbp_decode,
    CodecMeta(
        integer_only=True,
        enc_ops=_LMS4_OPS + _ACAR_XTRA + _XCHAN_OPS + _BP_SELECT_OPS,
        dec_ops=_LMS4_OPS + _ACAR_XTRA + _XCHAN_OPS,
        state_bytes_per_ch=_LMS4_STATE + _BP_STATE + 2, causal=True,
        lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_ACARBP_NOTE),
    family="cross-channel",
    desc="two-stage spatial front-end: global adaptive-CAR lift THEN local order-4 "
         "best-partner subtract + Rice",
    retired=True,
    retired_reason="Conclusively Pareto-dominated (superseded) by its scale-selected "
                   "version LMS4+Rice+acar_sel+bestpartner at EQUAL cost 0.043: acar_sel "
                   "reproduces this codec's OTB max-ratio corner EXACTLY (2.1795x, "
                   "byte-identical body) but is strictly BETTER on the large arrays where "
                   "the always-on CAR lift injected mismatched-basis noise (hyser 1.4804x "
                   "vs 1.4770x, cemhsey 1.9555x vs 1.9515x; capgmyo tie 1.3505x). >= ratio "
                   "on every real set, strictly > on 2, at the same cost -> no remaining "
                   "trade-off. The scale gate makes always-on cascade obsolete. "
                   "experiments/014_lms4_rice_acar_sel_bestpartner.md, cycle 2026-07-22."))

# NEW candidate (this cycle): SCALE-SELECTED two-stage cascade (INSIGHTS
# open-frontier #3, salvage of frontier #1). A META-GATE over two already-verified
# primitives: per recording it picks the CAR+best-partner cascade
# (LMS4+Rice+acar+bestpartner) for TIGHT arrays vs best-partner-only
# (LMS4+Rice+xchan_bestpartner) for EXTENDED arrays, from the decoder-derivable
# array channel count C. Cost is the union of the branch it takes (never both), so
# it is Pareto-bounded by the cascade branch and reduces to best-partner's cost on
# large arrays; ops/state below quote the worst case (the cascade branch, C<=64),
# identical to LMS4+Rice+acar+bestpartner. The scale gate is a pure integer compare
# on C -- ZERO side-info, ZERO circularity (the decoder reads C from the header
# before reconstructing) -- so dec_ops mirrors the always-on cascade's.
_ACARSEL_NOTE = (
    "SCALE-SELECTED two-stage cross-channel cascade -- a META-GATE over two "
    "already-verified primitives (NOT a new mechanism). Per recording, from the "
    "decoder-derivable ARRAY CHANNEL COUNT C, it selects the spatial front-end: "
    "C<=64 (TIGHT array, the array-mean is a coherent eigenvector -- INSIGHTS "
    "P1-refinement) -> the full cascade (GLOBAL backward-gated adaptive-CAR lift "
    "_acar_forward THEN LOCAL best-partner _bp_select, identical to "
    "LMS4+Rice+acar+bestpartner); C>=128 (EXTENDED array, shared content is spatially "
    "LOCAL) -> best-partner ONLY (identical to the promoted LMS4+Rice+xchan_bestpartner), "
    "so the CAR stage cannot subtract its mismatched global basis and inject noise. "
    "The gate is a pure integer compare on C, which the decoder reads from the header "
    "BEFORE any reconstruction -> deterministic, ZERO circularity, ZERO side-info "
    "beyond best-partner's own (parent,beta) pair. Threshold ACARSEL_MAX_CH=64 is the "
    "measured regime boundary: CAR helped +0.81% on the 64-ch OTB array but was "
    "-0.23..-0.26 pp on the 128-/320-ch Hyser/CEMHSEY arrays (the two MI slices are "
    "additive only where the global mode is a real eigenvector). Keeps the OTB "
    "max-ratio corner WITHOUT the large-array regression the always-on cascade paid. "
    "Both branches are exact-integer-invertible verified primitives, identical header "
    "format; decode derives the SAME branch from C and inverts the matching cascade "
    "(bp_inverse then, only for C<=64, acar_inverse). Behind both: order-4 sign-sign "
    "LMS + adaptive Rice (INSIGHTS P2). Cost/state quoted for the worst case (cascade "
    "branch); best-partner-only on large arrays is strictly cheaper. Basis: "
    "Vaisman/Farina adaptive-CAR + the shipped best-partner; meta-gate novel here.")
_register(Codec("LMS4+Rice+acar_sel+bestpartner", acarsel_encode, acarsel_decode,
    CodecMeta(
        integer_only=True,
        enc_ops=_LMS4_OPS + _ACAR_XTRA + _XCHAN_OPS + _BP_SELECT_OPS,
        dec_ops=_LMS4_OPS + _ACAR_XTRA + _XCHAN_OPS,
        state_bytes_per_ch=_LMS4_STATE + _BP_STATE + 2, causal=True,
        lookahead_samples=ec.BLOCK, block_size=ec.BLOCK, notes=_ACARSEL_NOTE),
    family="cross-channel",
    desc="scale-selected spatial cascade: per-recording gate (by channel count) "
         "between global adaptive-CAR+best-partner and best-partner-only + Rice"))

# NEW candidate (this cycle): Multi-parent backward-adaptive rank-1 subtract
# (INSIGHTS open-frontier #2). Extends the single grid-parent to TWO causal
# parents (up + left), each with its OWN backward-adaptive integer beta, the two
# rank-1 residual subtracts SUMMED. Over the incumbent +xchan per-sample work,
# each parent contributes: the rank-1 subtract itself (1 mul + 1 shift + 1 sub ~
# _XCHAN_OPS) PLUS a backward beta estimate (two running dot-products <x_c,x_p>,
# <x_p,x_p> ~ 2 macs + an amortised block divide ~ _XADAPT_XTRA). Two parents ->
# 2*(_XCHAN_OPS + _XADAPT_XTRA) extra ops over plain LMS. The decoder RECOMPUTES
# both betas (nothing transmitted), so dec_ops == enc_ops. State adds, on top of
# the order-8 LMS weights, two int16 betas (4 B) + two int64 covariance
# accumulators per parent (2 parents x 2 x 8 = 32 B) = 36 B. Backward-adaptive
# so look-ahead 0 and ZERO side-info (INSIGHTS P4).
_MP_XTRA = 2 * (_XCHAN_OPS + _XADAPT_XTRA)   # two parents: subtract + backward beta each
_MP_STATE = _LMS_STATE + 36                  # LMS + 2 betas + 2x2 int64 accumulators
_MP_NOTE = (
    "multi-parent backward-adaptive rank-1 cross-channel subtract: TWO causal "
    "grid parents per channel -- up (g-cols) and left (g-1), both idx<g -- each "
    "with its OWN backward-adaptive integer gain beta = <x_c,x_p>/<x_p,x_p> "
    "(fixed-point) estimated from the PREVIOUS block's already-reconstructed RAW "
    "samples (block 0 -> beta=0), the two rank-1 residual subtracts SUMMED: "
    "y[c]=x[c]-((bu*x[up])>>s)-((bl*x[left])>>s). A rank-2 LOCAL decorrelation as "
    "TWO independent asymmetric rank-1 subtracts (not a joint 2x2 solve); each "
    "subtracts the CLEAN raw parent and injects estimation noise only into "
    "residual channel c, leaving both parent rows untouched -- the robustness "
    "property INSIGHTS P3-refinement credits the rank-1 subtract with (unlike the "
    "retired energy-preserving iklt_adaptive rotation that corrupts both "
    "channels). Both betas recomputed by the decoder from bit-identical "
    "reconstructed history -> zero side-info, look-ahead 0, backward-adaptive "
    "(INSIGHTS P4). Targets the residual LOCAL spatial MI one parent leaves on "
    "extended high-|corr| arrays (INSIGHTS P1-refinement, open-frontier #2); "
    "distinct from the retired single-parent scalar xchan_adaptive by adding a "
    "second independent parent on a different topology. Then the unchanged "
    "order-8 sign-sign LMS + adaptive Rice back-end. Basis: MPEG-4 ALS "
    "multichannel / Choi et al. 2014 (paper-reported, unverified here).")
_register(Codec("LMS+Rice+xchan_multiparent", mp_encode, mp_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _MP_XTRA, dec_ops=_LMS_OPS + _MP_XTRA,
    state_bytes_per_ch=_MP_STATE, causal=True, lookahead_samples=0,
    block_size=MP_BLOCK, notes=_MP_NOTE), family="cross-channel",
    desc="LMS + two-parent (up+left) backward-adaptive rank-1 cross-channel "
         "decorrelation + Rice",
    retired=True,
    retired_reason="Conclusively Pareto-dominated by LMS+Rice+xchan on ALL 4 real "
                   "sets (worse ratio AND higher cost 0.078 vs 0.057: otb 1.971x vs "
                   "2.143x, hyser 1.398x vs 1.474x, capgmyo 1.347x vs 1.349x, cemhsey "
                   "1.872x vs 1.955x). Summing two INDEPENDENT marginal rank-1 subtracts "
                   "over-subtracts the up/left parents' shared common mode (Cov(up,left)>0 "
                   "ignored) -- captures only ~half the single-parent xchan gain. "
                   "experiments/007_lms_rice_xchan_multiparent.md, cycle 2026-07-16."))

# NEW candidate (this cycle): Cross-channel context-adaptive Rice (SECOND-ORDER /
# CONDITIONAL entropy axis, untouched by any tried codec). Keeps the order-8
# sign-sign LMS residual and the Golomb-Rice ENGINE, but selects the per-sample
# Rice k from a backward SPATIAL context (JPEG-LS/LOCO-I context modeling). Over
# the plain LMS+Rice per-sample work, the xctx back-end adds, per sample-ch: a
# leaky neighbour-energy update (1 add + 1 shift + 1 sub ~3), a bit-length bucket
# (~1), the LOCO-I k lookup (a short while-loop, amortised ~2), and the
# per-context (A,N) accumulate + occasional halving (~1) -> ~+8 ops over Rice.
# Decode mirrors the identical bucket + stats update (also no per-symbol divide),
# so dec_ops == enc_ops. Persistent state adds, on top of the order-8 LMS
# weights, XCTX_NBUCKETS per-context stat pairs (A int32 + N int16 ~ 6 B each ->
# ~72 B) + the leaky-energy accumulator (~4 B). Backward-adaptive: ZERO side-info
# (no k, no context table transmitted), look-ahead 0 -- the decoder recomputes
# every k from the already-reconstructed neighbour residual.
_XCTX_XTRA = 8
_XCTX_STATE = _LMS_STATE + XCTX_NBUCKETS * 6 + 4   # LMS + per-context (A,N) + nrg
_XCTX_NOTE = (
    "cross-channel context-adaptive Golomb-Rice: same order-8 sign-sign LMS "
    "residual and the SAME Rice engine as the family (P5 -- Rice is at the floor "
    "for the unconditional residual), but the per-sample Rice k is selected from "
    "a backward SPATIAL context (JPEG-LS/LOCO-I context modeling on a "
    "cross-channel context). For channel c with causal grid parent p (p<c, so "
    "the decoder has p's full residual first), a leaky integrator of |res[p,t]| "
    "estimates the neighbour spatial energy; its bit-length buckets that energy "
    "(XCTX_NBUCKETS log-energy buckets). Per bucket, JPEG-LS stats (A=sum coded "
    "magnitudes, N=count, halving-reset at 64) pick k = smallest with (N<<k)>=A, "
    "so k tracks the residual variance CONDITIONED on the neighbour's current "
    "energy -- exploiting H(e_c|neighbour energy) < H(e_c), the across-channel "
    "HETEROSCEDASTICITY a single per-block k misses (spatially coherent MUAP "
    "bursts stay variance-correlated across channels even after mean "
    "decorrelation). Bucket + (A,N) updated identically on both sides from "
    "bit-identical causal data -> ZERO side-info (no per-block k, no context "
    "table), backward-adaptive (P4), look-ahead 0. Root channels (no parent) "
    "fall back to a single context = plain per-channel JPEG-LS adaptive k. "
    "Distinct from the RETIRED xchan_tans (P5): the entropy ENGINE stays Rice; "
    "only its PARAMETER's context gains cross-channel information. Nothing is "
    "subtracted across channels -- the neighbour only CONDITIONS the coder. "
    "Basis: JPEG-LS/LOCO-I context-conditioned Golomb, US7580585B2 "
    "(backward-adaptive Rice), Giurcaneanu/Tabus 2001 -- unverified here.")
_register(Codec("LMS+Rice+xctx", xctx_encode, xctx_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS_OPS + _XCTX_XTRA, dec_ops=_LMS_OPS + _XCTX_XTRA,
    state_bytes_per_ch=_XCTX_STATE, causal=True, lookahead_samples=0,
    block_size=ec.BLOCK, notes=_XCTX_NOTE), family="entropy-backend",
    desc="LMS + cross-channel context-adaptive Rice k (JPEG-LS-style spatial "
         "context, zero side-info)",
    retired=True,
    retired_reason="Conclusively Pareto-dominated even by plain LMS+Rice (no xchan) on "
                   "ALL 4 real sets (worse ratio AND far higher cost 0.095 vs 0.052: otb "
                   "1.783x vs 1.825x, hyser 1.293x vs 1.330x, capgmyo 1.297x vs 1.332x, "
                   "cemhsey 1.682x vs 1.729x). After LMS whitening the residual is not "
                   "cross-channel heteroscedastic enough: H(e_c|neighbour energy) ~= "
                   "H(e_c), so the 12-bucket context split's model cost dominates any "
                   "conditional-entropy gain -- confirms/extends P5. "
                   "experiments/008_lms_rice_xctx.md, cycle 2026-07-16."))

# NEW candidate (this cycle): Joint asymmetric 2-parent adaptive sign-LMS spatial
# predictor (INSIGHTS open-frontier #3 -- the de-risked JOINT second-parent
# escape). Over the order-4 LMS+Rice per-sample work, the spatial front-end adds,
# per sample-ch: a 2-tap prediction (2 mul + 1 add + 1 shift ~4), the residual
# subtract (1), and the two sign-sign tap updates (2 signs + 2 signs + 2 adds ~4)
# -> ~+9 ops over the order-4 temporal LMS. Backward-adaptive (both taps re-derived
# from the shared residual + reconstructed parents), so the decoder does the
# IDENTICAL work: dec_ops == enc_ops. State adds, on top of the order-4 LMS
# weights/history, just the two int16 spatial taps (4 B) per channel; NO side-info
# is transmitted (zero header, look-ahead 0 -- INSIGHTS P4). Two extra taps only,
# so it clears the tight neural 125-cyc budget.
_XJ2_XTRA = 9      # 2-tap spatial predict (mac+shift) + subtract + two sign-sign updates
_XJ2_STATE = _LMS4_STATE + 4   # order-4 LMS state + two int16 spatial taps (w_u,w_l)
_XJ2_NOTE = (
    "joint asymmetric 2-parent spatial sign-sign LMS: predicts channel c from BOTH "
    "causal grid parents up (g-cols) and left (g-1) with ONE joint predictor "
    "pred=(w_u*x[up]+w_l*x[left])>>shift, and BOTH taps co-adapt against the SAME "
    "post-subtraction residual e=x[c]-pred via sign-sign LMS (w_u+=sign(e)sign(x[up]), "
    "w_l+=sign(e)sign(x[left]) -- +/-1 tap update, multiplierless). Because the taps "
    "descend the SHARED residual after both current taps subtract, each adapts to the "
    "correlation REMAINING once the other parent's contribution is out -- the "
    "stochastic-gradient realization of the 2x2 normal-equations (multiple-regression) "
    "solve that accounts for parent-parent covariance. This is the ONLY unspent "
    "second-parent escape INSIGHTS leaves: a JOINT solve, NOT the retired "
    "xchan_multiparent's SUM of two independent MARGINAL rank-1 betas (which "
    "double-counts the correlated parents' shared mode -> over-subtracts). ASYMMETRIC "
    "rank-1 residual-only injection: only channel c's coded residual is modified; the "
    "raw parent rows are inputs left CLEAN, so estimation noise never touches the "
    "parents -- unlike the retired energy-preserving iklt_adaptive rotation that "
    "corrupts both channels (INSIGHTS P3-refinement robustness). Both taps re-derived "
    "by the decoder from bit-identical reconstructed parents (idx<c) and the coded "
    "residual e -> ZERO side-info, look-ahead 0, backward-adaptive (INSIGHTS P4). "
    "Behind the spatial front-end: the order-4 sign-sign LMS temporal predictor "
    "(INSIGHTS P2, the promoted best's back-end) + adaptive Rice. Basis: MPEG-4 ALS "
    "RLS-LMS multichannel / multivariate-RLS (arXiv 1605.04418, paper-reported, "
    "unverified here).")
_register(Codec("LMS+Rice+xchan_joint2", xj2_encode, xj2_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS4_OPS + _XJ2_XTRA, dec_ops=_LMS4_OPS + _XJ2_XTRA,
    state_bytes_per_ch=_XJ2_STATE, causal=True, lookahead_samples=0,
    block_size=ec.BLOCK, notes=_XJ2_NOTE), family="cross-channel",
    desc="order-4 LMS + joint 2-parent (up+left) backward-adaptive sign-sign LMS "
         "spatial predictor (zero side-info) + Rice"))

# NEW candidate (this cycle): Backward-adaptive per-block best-partner RE-SELECTION
# (INSIGHTS open-frontier #3 -- the port-caveat closure for the PROMOTED best).
# Identical to LMS4+Rice+xchan_bestpartner but the (partner, beta) pair is re-
# selected PER BLOCK from the PREVIOUS reconstructed raw block instead of derived
# offline over the whole signal -- so BOTH the whole-signal look-ahead AND the
# 2xint16/ch header are removed. Over the order-4 LMS+Rice per-sample work, the
# front-end adds the per-block re-selection scan: for each of <=4 causal
# candidates, two running dot-products <x_c,x_p>/<x_p,x_p> over the previous block
# (~2 macs/sample-ch each) + the residual Rice-bits estimate, then an amortised
# per-block argmin, PLUS the chosen rank-1 subtract on the current block
# (_XCHAN_OPS). Unlike the offline bestpartner (decoder search-free), the decoder
# here RECOMPUTES the same selection from bit-identical reconstructed history, so
# dec_ops == enc_ops. Persistent per-channel state is the order-4 LMS
# weights/history + the current (partner id, beta) (~3 B); the <=4-candidate
# covariance accumulators are O(1) SHARED working state (reused per channel-block,
# not multiplied per channel) -- noted, not charged per-channel. Backward-adaptive
# so look-ahead 0 and ZERO side-info (INSIGHTS P4) -- the embeddability win over
# the promoted best.
_LMS4BPA_SELECT = 10   # <=4-candidate backward scan (2 macs each) + amortised argmin
_LMS4BPA_STATE = _LMS4_STATE + 3   # order-4 LMS + current (partner byte, int16 beta)
_LMS4BPA_NOTE = (
    "backward-adaptive per-block best-partner RE-SELECTION: the PROMOTED best "
    "LMS4+Rice+xchan_bestpartner with its offline whole-signal (partner, beta) "
    "swapped for per-block backward re-selection. For channel g, block i>0: over "
    "the PREVIOUS already-reconstructed RAW block, scan the <=4 causal grid "
    "neighbours (left/up/up-left/up-right, all idx<g -- same _bp_candidates), "
    "derive each candidate's integer least-squares gain (_bp_opt_beta) and score "
    "the resulting cross-residual's estimated Rice bits (_bp_score), also scoring "
    "the no-partner option, and keep the min-bits (partner, beta). That pair is "
    "applied as a rank-1 subtract to the CURRENT block: y[g]=x[g]-((beta*x[p])>>s). "
    "Because reconstruction is lossless the decoder holds the bit-identical raw "
    "previous block and every candidate partner (idx<g) is already reconstructed, "
    "so it recomputes the SAME (partner, beta) causally -> ZERO side-info (no "
    "2xint16/ch header), look-ahead 0. Block 0 bootstraps to no-partner (coded "
    "as-is). Same 4-candidate neighbourhood, integer-LS beta, Rice-bits scoring, "
    "and order-4 sign-sign LMS + adaptive Rice back-end as the promoted best "
    "(INSIGHTS P2); only the ESTIMATION is now backward-adaptive (INSIGHTS P4), "
    "closing the promoted codec's last port caveat (offline partner/beta + header). "
    "Distinct from the RETIRED LMS+Rice+xchan_adaptive (single FIXED-grid-parent "
    "scalar beta, NO partner selection): here the partner IDENTITY itself is "
    "re-selected per block. Ratio risk to MEASURE: a stale partner across a burst "
    "boundary on non-stationary HD-sEMG -- an embeddability/port lever, not a ratio "
    "play; the question is whether it HOLDS the promoted offline ratio.")
_register(Codec("LMS4+Rice+xchan_bestpartner_adaptive", lms4bpa_encode, lms4bpa_decode,
    CodecMeta(
        integer_only=True, enc_ops=_LMS4_OPS + _XCHAN_OPS + _LMS4BPA_SELECT,
        dec_ops=_LMS4_OPS + _XCHAN_OPS + _LMS4BPA_SELECT,
        state_bytes_per_ch=_LMS4BPA_STATE, causal=True, lookahead_samples=0,
        block_size=LMS4BPA_BLOCK, notes=_LMS4BPA_NOTE), family="cross-channel",
    desc="order-4 LMS + backward-adaptive per-block best-of-4 partner RE-SELECTION "
         "(zero side-info) + Rice"))

# NEW candidate (this cycle): Joint-solved SELECTED best pair -- selection x count
# fused (INSIGHTS open-frontier #1, the highest-payoff live lever). Stacks the two
# proven-but-substitute spatial degrees of freedom: per-block backward best-PAIR
# SELECTION (like bestpartner_adaptive, but choosing a pair) THEN a joint co-adaptive
# 2-tap sign-sign LMS on that pair (like joint2, but on the selected pair, not a fixed
# up+left one). Over the order-4 LMS+Rice per-sample work, the front-end adds: the
# joint 2-tap predict + subtract + two sign-sign tap updates (~_XJ2_XTRA) PLUS the
# per-block backward re-selection scan (<=4 single-candidate marginal dot-products +
# <=6 candidate-pair 2x2 LS solves over the previous block, amortised over the block
# ~_JBP2_SELECT). Backward-adaptive: both the selected pair AND the two taps are
# recomputed by the decoder from bit-identical reconstructed history, so the decoder
# does the IDENTICAL work -> dec_ops == enc_ops, ZERO side-info, look-ahead 0
# (INSIGHTS P4). Persistent per-channel state is the order-4 LMS weights/history + the
# two int16 spatial taps (w_u,w_l, 4 B) + the current selected (pu,pl) ids (~2 B); the
# <=6-pair covariance accumulators are O(1) SHARED working state (reused per
# channel-block, not multiplied per channel) -- noted, not charged per-channel. Two
# extra taps + a bounded per-block scan on the order-4 base -> clears the tight neural
# 125-cyc budget.
_JBP2_SELECT = 14   # <=4 single (2 macs each) + <=6 pair 2x2-LS scans, amortised/block
_JBP2_STATE = _LMS4_STATE + 6   # order-4 LMS + two int16 taps (4 B) + (pu,pl) ids (2 B)
_JBP2_NOTE = (
    "joint-solved SELECTED best pair -- selection x COUNT fused (INSIGHTS P1b + "
    "open-frontier #1): stacks the two spatial degrees of freedom P1b proved are used "
    "alone as substitutes. STAGE 1 SELECTION (per channel, per block i>0, backward): "
    "over the PREVIOUS already-reconstructed RAW block, score in estimated Rice bits "
    "the no-parent option, each single-parent option (integer-LS gain, reusing "
    "_bp_opt_beta/_bp_score -- the best-partner path), AND every causal-neighbour PAIR "
    "under a JOINT 2x2 integer least-squares solve (_jbp2_pair_resid: fixed-point gains "
    "from the pair's covariance, accounting for parent-parent covariance -- the "
    "marginal->multiple fix, so it CANNOT double-count the shared mode the RETIRED "
    "summed multiparent did); keep the min-bits pair (pu,pl>=0) / single (pu>=0,pl=-1) "
    "/ none (-1,-1). Candidates = <=4 causal grid neighbours (left/up/up-left/up-right, "
    "all idx<c, reused _bp_candidates). COUNT falls out of the same scored search, so a "
    "useless second parent is not forced on tight arrays (the exact P1b tension). "
    "STAGE 2 PREDICT: ONE joint co-adaptive sign-sign LMS on the SELECTED pair "
    "(structure verbatim from xchan_joint2) -- pred=(w_u*x[pu]+w_l*x[pl])>>shift, "
    "e=x[c]-pred, BOTH taps co-adapt against the SHARED post-subtraction residual "
    "(w_u+=sign(e)sign(x[pu]), w_l+=sign(e)sign(x[pl]), +/-1 update, multiplierless); "
    "each tap adapts to the correlation REMAINING once the other's contribution is out "
    "(stochastic-gradient 2x2 normal-equations solve). ASYMMETRIC rank-1 residual-only "
    "injection: only channel c's coded residual is modified; raw parent rows are inputs "
    "left CLEAN (robustness INSIGHTS P3-refinement; the retired energy-preserving "
    "iklt_adaptive rotation corrupted both channels). Taps PERSIST across blocks (the "
    "selected pair is stable within a recording, P4-refinement); a slot whose parent is "
    "absent has zero input so its tap is frozen. Block 0 bootstraps to the fixed grid "
    "(up,left) pair (taps warm from t=0); block 1 on re-selects. Both the selected pair "
    "AND the taps are recomputed by the decoder from bit-identical reconstructed "
    "history (all candidate parents idx<c fully reconstructed) -> ZERO side-info, "
    "look-ahead 0, backward-adaptive (INSIGHTS P4). Behind the front-end: order-4 "
    "sign-sign LMS temporal predictor (INSIGHTS P2) + adaptive Rice -- the promoted "
    "best's back-end. Distinct from RETIRED multiparent (summed marginal betas "
    "double-count -> joint 2x2/gradient cannot), RETIRED iklt_adaptive (rotation "
    "corrupts both channels -> asymmetric rank-1 residual-only, parents clean), KEPT "
    "joint2 (fixed pair -> selected pair), PROMOTED bestpartner (single parent -> "
    "jointly-solved pair). The one untried way to STACK selection AND count into a win "
    "on BOTH array scales. Basis: MPEG-4 ALS multichannel prediction / Choi et al. "
    "Sensors 2014 correlation-sorted channel pairing (paper-reported, unverified here).")
_register(Codec("LMS4+Rice+xchan_jointbp2", jbp2_encode, jbp2_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS4_OPS + _XJ2_XTRA + _JBP2_SELECT,
    dec_ops=_LMS4_OPS + _XJ2_XTRA + _JBP2_SELECT,
    state_bytes_per_ch=_JBP2_STATE, causal=True, lookahead_samples=0,
    block_size=JBP2_BLOCK, notes=_JBP2_NOTE), family="cross-channel",
    desc="order-4 LMS + backward-adaptive per-block best-PAIR selection + joint "
         "co-adaptive 2-tap sign-sign LMS spatial predictor (zero side-info) + Rice"))


def list_codecs(include_retired=False):
    """Codecs for the default bench.py/search.py sweep and the leaderboard.
    Retired codecs (Pareto-dominated on real data, per a verifier's audit) are
    excluded by default so they stop being re-benchmarked and re-reported every
    cycle -- pass include_retired=True to re-check one explicitly (e.g. to
    reproduce an old verdict, or re-audit after a shared primitive changes)."""
    return [c for c in REGISTRY.values() if include_retired or not c.retired]


def list_retired():
    return [c for c in REGISTRY.values() if c.retired]


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

    # Bit-exactness is checked for EVERY codec ever registered, retired or not
    # -- retirement means "excluded from the default bench/leaderboard sweep",
    # never "excused from correctness." Nothing is deleted or untested.
    all_codecs = list_codecs(include_retired=True)
    n_retired = len(list_retired())
    print(f"registry self-test on random int16 [{C} x {N}], {len(all_codecs)} codecs"
          f" ({n_retired} retired, excluded from the default sweep)\n")
    print(f"{'codec':<20}{'ratio':>7}{'round-trip':>12}{'emb_ok':>8}"
          f"{'neural':>8}{'cost':>8}  status")
    print("-" * 71)
    all_ok = True
    for c in all_codecs:
        blob = c.encode(x, cols=cols)
        y = c.decode(blob)
        ok = np.array_equal(x, y)
        all_ok &= ok
        ratio = x.nbytes / len(blob)
        status = "RETIRED" if c.retired else ""
        print(f"{c.name:<20}{ratio:>6.2f}x{('OK' if ok else 'FAIL!'):>12}"
              f"{('OK' if c.cost.embedded_ok else 'no'):>8}"
              f"{('OK' if c.cost.neural_ok else '-'):>8}{c.cost.cost:>8.3f}  {status}")
        assert ok, f"round-trip mismatch for {c.name}"
    assert all_ok
    print("\nregistry self-test: ALL round-trips bit-exact")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    # default action is the self-test (the verifier hook invokes with --selftest)
    _selftest()
