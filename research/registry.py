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
    desc="LMS + best-of-4 causal-neighbour cross-channel selection + Rice"))

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
