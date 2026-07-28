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
# NEW candidate: Delay-compensated spatio-temporal cross-channel FIR
# (LMS4+Rice+xstfir).
# ---------------------------------------------------------------------------
# THE ONE DEGREE OF FREEDOM NO REGISTERED CODEC HAS EVER VARIED: the LAG of the
# cross-channel predictor. Every spatial front-end in this registry -- +xchan,
# xchan_adaptive, bestpartner, bestpartner_adaptive, acar, acar+bestpartner,
# multiparent (retired), joint2, iklt/iklt_adaptive (retired), xres (retired) --
# predicts channel c from its neighbour(s) at ZERO LAG only: a scalar gain on
# x_p[n]. They differ in WHICH parent (selection), HOW MANY parents (count), or
# WHICH BASIS (rotation) -- never in WHEN.
#
# SIGNAL MODEL (why lag is the missing freedom): HD-sEMG is not a static spatial
# mixture. Motor-unit action potentials PROPAGATE along the muscle fibres at
# ~3-5 m/s, so at 8-10 mm inter-electrode distance the SAME waveform reaches the
# neighbour DELAYED -- this is literally how muscle-fibre conduction velocity is
# measured (the inter-electrode time delay along the fibre direction), and the
# propagating component is exactly what distinguishes true MUAPs from
# non-propagating crosstalk. Wiener theory then says the optimal predictor from a
# DELAYED common source is a fractional-delay FILTER, not a GAIN: a zero-lag
# scalar beta can only remove the projection onto rho(0), so it structurally
# leaves rho(tau*)^2 - rho(0)^2 of the shared energy in the residual REGARDLESS
# of which parent is selected or how many parents are added. That is a concrete,
# falsifiable identity for the ~1% ceiling every zero-lag spatial variant here
# shares (INSIGHTS: "every spatial lever is now within ~1% of a shared ceiling").
#
# PRECEDENT: MPEG-4 ALS's own inter-channel prediction -- the standard this
# project cites for "+xchan" -- is a THREE-TAP filter with an explicitly chosen
# lag, never the 1-tap zero-lag reduction our codecs implement (Liebchen et al.,
# "Extended linear prediction tools for lossless audio coding", MPEG-4 ALS;
# paper-reported, unverified here). Signal-model sources: MFCV estimation reviews
# (ScienceDirect S1050641118303110; PMC3616828). Both cited, both unverified here.
#
# MECHANISM: replace the scalar zero-lag subtract beta*x_p[n] with a short FIR
# over the parent's RECONSTRUCTED history, plus one tap on an opposite-side
# neighbour read from the fully-reconstructed PREVIOUS time slice:
#     pred[c,n] = ( w0*x_p[n] + w1*x_p[n-1] + w2*x_p[n-2] + w3*x_q[n-1] ) >> shift
#     e[c,n]    = x[c,n] - pred[c,n]                 (the coded cross-residual)
# with ONE joint sign-sign LMS over all four taps against the SHARED
# post-subtraction residual:
#     w_i += sign(e[c,n]) * sign(a_i)                (+/-1 update, multiplierless)
# Being JOINT (all taps descend the same residual after all current taps have
# subtracted) it is the stochastic-gradient realization of the 4x4 normal-equations
# solve, so lag-to-lag covariance enters through the shared residual and is never
# double-counted -- the same marginal-vs-multiple fix joint2 made for parents,
# applied here across LAGS. The lag-1/lag-2 taps let the filter synthesize the
# fractional inter-electrode delay the propagating MUAP imposes; the opposite-side
# tap x_q[n-1] covers propagation in the OTHER direction (for a wave travelling
# q->c->p, the downstream-side neighbour's previous sample carries the wavefront
# c is about to see), which a same-side-only FIR cannot reach.
#
# WHY x_q[n-1] IS LEGAL (and why this stage is time-major): the parent p has grid
# index < c, so within time slice n it is already reconstructed; the opposite-side
# neighbour q has grid index > c, so it is readable only at lag >= 1. This stage
# therefore reconstructs TIME-MAJOR -- for each n, channels in index order --
# which makes the whole slice n-1 fully available while keeping same-slice reads
# restricted to idx<c. Both orderings are deterministic from (C, cols), so the
# decoder mirrors them exactly. Look-ahead 0, ZERO side-info (INSIGHTS P4).
#
# ASYMMETRIC (residual-only injection): only channel c's coded residual is
# modified; the parent and opposite-neighbour rows are INPUTS, left CLEAN -- the
# robustness property INSIGHTS P3-refinement credits the rank-1 subtract with and
# the RETIRED energy-preserving iklt_adaptive rotation (corrupts BOTH channels)
# lacked. Behind the spatial front-end sits the order-4 sign-sign LMS temporal
# predictor + adaptive Rice (INSIGHTS P2/P5, the promoted best's back-end).
#
# NOT P2's dead deeper-temporal-order lever: the extra taps read a DIFFERENT
# channel's past (which carries the delayed copy of the propagating MUAP), not
# the coding channel's own past, which order-4 has already whitened. Distinct
# from KEPT xchan_joint2 and from the sibling-PR jointbp2/joint2_bpa (those add a
# second PARENT at lag 0 -- P1b showed selection and count are SUBSTITUTES
# precisely because both are capped by rho(0); LAG is the orthogonal freedom that
# raises the cap). Distinct from RETIRED xchan_multiparent (summed MARGINAL betas
# double-count correlated parents; jointly co-adapting taps structurally cannot).
# Distinct from RETIRED iklt_adaptive (energy-preserving rotation corrupts both
# channels; this is asymmetric). Distinct from RETIRED xres (innovation-domain
# subtract at lag 0 -- this stays in the better-conditioned RAW domain and moves
# the LAG instead).
# ===========================================================================
XSTFIR_MAGIC = 0x5346       # 'SF' (spatio-temporal FIR)
XSTFIR_SHIFT = ec.CROSS_SHIFT   # fixed-point spatial-tap scale (matches +xchan family)
XSTFIR_ORDER = LMS4_ORDER   # order-4 temporal base behind the spatial front-end (P2)
XSTFIR_PLAGS = 3            # parent taps at lags 0,1,2 (the delay-compensating FIR)


def _xstfir_neighbours(C, cols):
    """Per channel: (parent p, opposite-side neighbour q).

    p is the SAME causal grid parent the +xchan family uses (left = g-1 when the
    channel is not in column 0, else up = g-cols), so p < g always and its
    current-slice sample is available. q is the mirror-image neighbour on the
    OTHER side along the same grid axis (right = g+1 for a left-parent channel,
    down = g+cols for an up-parent one); q > g, so it is read only at lag 1 from
    the fully-reconstructed previous time slice. -1 marks an absent neighbour
    (the tap then reads 0 and its sign-sign update is a no-op). Deterministic
    from (C, cols) -> identical on encode and decode."""
    par = np.full(C, -1, np.int64)
    opp = np.full(C, -1, np.int64)
    for g in range(C):
        r, c = divmod(g, cols)
        if c > 0:
            par[g] = g - 1                     # left parent (same row)
            if c + 1 < cols and g + 1 < C:
                opp[g] = g + 1                 # right neighbour (opposite side)
        elif r > 0:
            par[g] = g - cols                  # up parent (first column)
            if g + cols < C:
                opp[g] = g + cols              # down neighbour (opposite side)
    return par, opp


def _xstfir_taps(Xp, Xq, t):
    """The four causal regressor values at time t: parent lags 0,1,2 and the
    opposite-side neighbour at lag 1. Samples before t=0 read 0 (identically in
    encoder and decoder)."""
    a0 = Xp[t]
    a1 = Xp[t - 1] if t >= 1 else 0
    a2 = Xp[t - 2] if t >= 2 else 0
    a3 = (Xq[t - 1] if t >= 1 else 0) if Xq is not None else 0
    return a0, a1, a2, a3


def _xstfir_upd(w, se, a0, a1, a2, a3):
    """Joint sign-sign LMS update of the four taps against the SHARED residual
    sign se (+/-1 per tap, no multiply). Identical call in encoder and decoder."""
    if se:
        if a0: w[0] += se if a0 > 0 else -se
        if a1: w[1] += se if a1 > 0 else -se
        if a2: w[2] += se if a2 > 0 else -se
        if a3: w[3] += se if a3 > 0 else -se


def _xstfir_forward(x, par, opp, shift=XSTFIR_SHIFT):
    """Delay-compensated spatio-temporal cross-channel FIR decorrelation.

    TIME-MAJOR: for each sample n, channels in index order. Channel c's predictor
    is a 3-tap FIR over parent p's raw history {x_p[n], x_p[n-1], x_p[n-2]} plus
    one tap on the opposite-side neighbour q's previous sample x_q[n-1]; all four
    taps co-adapt by ONE joint sign-sign LMS against the shared post-subtraction
    residual. Only channel c's residual is emitted; the parent/neighbour rows are
    inputs, left CLEAN. Integer-only, per-sample, look-ahead 0. Returns the
    cross-residual [C, N] int64."""
    x = x.astype(np.int64)
    C, N = x.shape
    X = [row.tolist() for row in x]          # raw rows (clean; regressor source)
    Y = [row[:] for row in X]                # residual rows (only channel c edited)
    W = [[0, 0, 0, 0] for _ in range(C)]     # per-channel spatial taps
    par = [int(v) for v in par]
    opp = [int(v) for v in opp]
    for t in range(N):
        for c in range(C):
            p = par[c]
            if p < 0:
                continue                     # grid origin: coded as-is
            q = opp[c]
            a0, a1, a2, a3 = _xstfir_taps(X[p], X[q] if q >= 0 else None, t)
            w = W[c]
            pred = (w[0] * a0 + w[1] * a1 + w[2] * a2 + w[3] * a3) >> shift
            e = X[c][t] - pred
            Y[c][t] = e
            _xstfir_upd(w, 1 if e > 0 else (-1 if e < 0 else 0), a0, a1, a2, a3)
    return np.array(Y, np.int64)


def _xstfir_inverse(y, par, opp, shift=XSTFIR_SHIFT):
    """Invert _xstfir_forward. Same TIME-MAJOR order: at slice t the parent p<c is
    already reconstructed in this slice and the opposite-side neighbour q>c is
    read at t-1 from the fully-reconstructed previous slice, so every regressor
    the encoder used is bit-identically available. The taps are re-derived from
    the coded residual e=y[c,t] (the encoder's own e) -> pred, and hence
    x[c,t]=e+pred, match bit-for-bit."""
    y = y.astype(np.int64)
    C, N = y.shape
    Y = [row.tolist() for row in y]
    X = [row[:] for row in Y]                # parentless channels are already raw
    W = [[0, 0, 0, 0] for _ in range(C)]
    par = [int(v) for v in par]
    opp = [int(v) for v in opp]
    for t in range(N):
        for c in range(C):
            p = par[c]
            if p < 0:
                continue
            q = opp[c]
            a0, a1, a2, a3 = _xstfir_taps(X[p], X[q] if q >= 0 else None, t)
            w = W[c]
            pred = (w[0] * a0 + w[1] * a1 + w[2] * a2 + w[3] * a3) >> shift
            e = Y[c][t]
            X[c][t] = e + pred
            _xstfir_upd(w, 1 if e > 0 else (-1 if e < 0 else 0), a0, a1, a2, a3)
    return np.array(X, np.int64)


def xstfir_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    par, opp = _xstfir_neighbours(C, cols)
    y = _xstfir_forward(x, par, opp)
    res = ec.lms_forward(y, order=XSTFIR_ORDER)   # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", XSTFIR_MAGIC, cols, C, N)   # NO side-info (backward-adaptive)
    return hdr + body


def xstfir_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == XSTFIR_MAGIC, "bad xstfir codec magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res, order=XSTFIR_ORDER)   # matched order-4 inverse
    par, opp = _xstfir_neighbours(C, cols)
    x = _xstfir_inverse(y, par, opp)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: maximum-weight spanning-forest channel topology with permuted
# coding order (LMS4+Rice+xtree).
# ---------------------------------------------------------------------------
# THE TWO THINGS EVERY PRIOR SPATIAL FRONT-END FROZE: the channel-graph TOPOLOGY
# and the CODING ORDER. +xchan uses a fixed raster spanning tree (left, else up);
# bestpartner / bestpartner_adaptive let each channel pick ONE parent, but only
# from the FIXED 4-neighbourhood {left, up, up-left, up-right} -- i.e. only from
# neighbours with grid index < g -- because the coding order is hard-wired to
# raster (channel 0..C-1). joint2 / jointbp2 vary the parent COUNT under the same
# raster order. Every one of them is therefore a raster-constrained GREEDY forest.
#
# THEORY (Chow & Liu 1968): under a first-order (tree-structured) dependency
# model the total conditional entropy Sum_c H(x_c | x_parent(c)) is minimized
# EXACTLY by the MAXIMUM-WEIGHT SPANNING TREE whose edge weights are the pairwise
# mutual informations -- a global optimum reachable by a single Kruskal/Prim pass,
# not by per-node greedy choice. Tate (IEEE Trans. Computers 1997, "Band Ordering
# in Lossless Compression of Multispectral Images") proves the directly analogous
# statement for lossless inter-band predictive coding: the optimal coding order is
# a MAXIMUM-WEIGHT DIRECTED SPANNING FOREST, with substantial reported gains over
# the natural/raster order; the ISPRS Annals X-1/W1 2023 hyperspectral reordering
# work restates it in the modern MST form. (Both paper-reported, unverified here.)
# A raster-constrained greedy forest is PROVABLY <= the Chow-Liu optimum; the gap
# is exactly what this candidate measures.
#
# WHY IT CAN MOVE THE NUMBER WHERE NOTHING ELSE HAS: the raster constraint bites
# hardest precisely where the harness sees no gain. CapgMyo is a DIFFERENTIAL
# 8x16 array (neighbour |corr| ~0.29, +1.3% -- the honest negative control):
# adjacent differential channels share an electrode with OPPOSITE sign, so the
# physically best-correlated partner may sit at grid distance 2, along the other
# axis, or be ANTI-correlated -- a global forest can select it, a causal-only
# 4-neighbourhood raster scan structurally cannot (half the neighbourhood is
# simply unreachable, and distance-2 is never a candidate). Likewise on the
# 128-/320-ch Hyser/CEMHSEY multi-grid mountings a raster "up"/"left" parent can
# lie on a different grid or muscle and contribute ~zero MI; a forest would never
# select such an edge.
#
# MECHANISM (per block i >= 1, everything derived from the PREVIOUS, already
# fully reconstructed raw block -- so ZERO side-info, look-ahead 0, INSIGHTS P4):
#   1. Candidate edge set (BOUNDED -- (a) of the embeddable construction): the 8
#      grid neighbours plus the 4 axis-distance-2 neighbours of each channel, i.e.
#      <=12 undirected candidates/ch (~6C edges total, 768 for C=128). This is a
#      GLOBAL, UNDIRECTED set -- it includes the down/right/down-left/down-right
#      and distance-2 edges no raster codec here can reach -- while staying far
#      inside the <=2048-edge/block budget. A naive full CxC correlation matrix
#      (~64 MAC/sample-ch at C=128) would be disqualifying at the 125-cyc neural
#      budget, which is why the candidate set is bounded by construction. (The
#      stated "previous block's surviving top-M" carry-over is subsumed: the
#      carried edges are always drawn from this same fixed geometric set, so the
#      union is the set itself.)
#   2. Edge weight = |sign-sign correlation counter| over the previous raw block,
#      time-subsampled by 8 -- (b) and (c) of the construction:
#          w(u,v) = |Sum_{t in prev block, step 8} sign(x_u[t]) * sign(x_v[t])|
#      A sign-sign product is an XOR + increment/decrement, so the estimator is
#      MULTIPLIERLESS, and every edge uses the same sample count so the raw
#      counter IS the normalized |cross-correlation| up to a common scale. By the
#      arcsine law E[sign*sign] = (2/pi) asin(rho), so the counter is monotone in
#      |rho| and hence (Gaussian model) in the pairwise MI -- exactly Chow-Liu's
#      edge weight, computed at ~6/8 MAC/sample-ch. The ABSOLUTE value is what
#      makes ANTI-correlated differential pairs (CapgMyo) first-class edges.
#   3. Maximum-weight spanning FOREST by Kruskal with union-find over those edges
#      (descending weight, deterministic index tie-break, zero-weight edges
#      dropped so uncorrelated channels stay roots). One pass over <=2048 edges
#      per 512-sample block is amortized negligible.
#   4. Orientation + CODING ORDER: each component is rooted at its lowest channel
#      index and edges are oriented away from the root by a deterministic DFS;
#      the DFS visit sequence IS the topological coding order (a parent is always
#      emitted before its children). The coding order is thus a free variable
#      chosen by the GLOBAL criterion (total forest weight), not by grid index.
#   5. Per selected edge, the ALREADY-SHIPPED adaptive rank-1 subtract: the
#      rounded integer least-squares gain beta (`_bp_opt_beta`) from the previous
#      block, kept only if it lowers that block's estimated Rice bits
#      (`_bp_score`) -- otherwise the edge is dropped and the child becomes a root
#      (which leaves the topological order valid). Applied to the CURRENT block:
#          y[c, blk] = x[c, blk] - ((beta_c * x[parent(c), blk]) >> shift)
#      Block 0 has no predecessor -> coded as-is; the forest re-adapts every block.
#
# The decoder holds the bit-identical previous raw block, so it rebuilds the SAME
# candidate set, the SAME weights, the SAME forest, the SAME coding order and the
# SAME betas, and inverts the current block in that topological order. NOTHING is
# transmitted (unlike the promoted best's 2xint16/ch header).
#
# NOT A RETIRED RE-PROPOSAL, and NOT P3's dead multi-tap transform: every edge is
# still the verified SINGLE-PARENT rank-1 adaptive subtract (asymmetric --
# residual-only injection, parent row left CLEAN, the robustness property
# P3-refinement credits), so this is not a rotation/lifting and not a summed
# multi-parent subtract (RETIRED xchan_multiparent) -- only WHICH edges exist and
# in WHAT ORDER channels are coded changes. Distinct from DONE
# LMS4+Rice+xchan_bestpartner and ..._bestpartner_adaptive: those select one
# parent per channel from the FIXED causal 4-neighbourhood under a FIXED raster
# coding order (per-channel greedy); here the candidate set is global/undirected
# and the coding order itself is optimized by a global criterion. Back-end
# unchanged: order-4 sign-sign LMS + adaptive Rice (INSIGHTS P2/P5).
# ===========================================================================
XTREE_MAGIC = 0x5854        # 'XT'
XTREE_BLOCK = ec.BLOCK      # forest re-derivation block (aligns with the Rice block)
XTREE_SHIFT = ec.CROSS_SHIFT    # same fixed-point gain scale as the +xchan family
XTREE_SUB = 8               # time-subsampling stride for the sign-sign counters
XTREE_ORDER = LMS4_ORDER    # order-4 temporal base behind the spatial front-end (P2)
# 8-neighbourhood + the 4 axis-distance-2 neighbours = <=12 candidates/channel.
XTREE_OFFS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1),
              (-2, 0), (2, 0), (0, -2), (0, 2))


def _xtree_edges(C, cols):
    """Bounded, deterministic, UNDIRECTED candidate edge set (u < v) over the
    channel grid: each channel's 8 grid neighbours plus its 4 axis-distance-2
    neighbours. Unlike the raster codecs' causal 4-neighbourhood this includes
    down/right/down-* and distance-2 edges -- reachable only because the coding
    order is a free variable here. Derived from (C, cols) alone, so encoder and
    decoder build the identical set."""
    seen = set()
    edges = []
    for g in range(C):
        r, c = divmod(g, cols)
        for dr, dc in XTREE_OFFS:
            rr, cc = r + dr, c + dc
            if rr < 0 or cc < 0 or cc >= cols:
                continue
            h = rr * cols + cc
            if h >= C or h == g:
                continue
            key = (g, h) if g < h else (h, g)
            if key not in seen:
                seen.add(key)
                edges.append(key)
    return edges


def _xtree_weights(xprev, edges, stride=XTREE_SUB):
    """|sign-sign correlation counter| per candidate edge over the previous raw
    block, time-subsampled by `stride`. sign(x) is -1/0/+1, so each term is an
    XOR + increment in hardware (multiplierless); the common sample count makes
    the raw counter a normalized |cross-correlation| up to one global scale, and
    the arcsine law makes it monotone in |rho| -> in the pairwise MI (Chow-Liu's
    edge weight). Integer-only."""
    s = np.sign(xprev[:, ::stride]).astype(np.int64)
    return [abs(int((s[u] * s[v]).sum())) for u, v in edges]


def _xtree_forest(C, edges, w):
    """Maximum-weight spanning FOREST (Kruskal + union-find) over the candidate
    edges, then orient it away from per-component roots.

    Returns (parent, topo): parent[c] = c's tree parent (-1 for a root) and topo =
    the coding order, a DFS visit sequence in which every parent precedes its
    children. Edges are taken in strictly descending weight with a deterministic
    (weight, u, v) tie-break; zero-weight edges are dropped so channels carrying
    no measurable mutual information stay roots (coded as-is). Every component is
    rooted at its lowest channel index. Pure integer/index work -> encoder and
    decoder derive the identical forest and order."""
    idx = sorted(range(len(edges)), key=lambda i: (-w[i], edges[i][0], edges[i][1]))
    uf = list(range(C))

    def find(a):
        while uf[a] != a:
            uf[a] = uf[uf[a]]
            a = uf[a]
        return a

    adj = [[] for _ in range(C)]
    for i in idx:
        if w[i] <= 0:
            break                       # no measurable MI left: stop growing
        u, v = edges[i]
        ru, rv = find(u), find(v)
        if ru != rv:
            uf[ru] = rv
            adj[u].append(v)
            adj[v].append(u)
    parent = [-1] * C
    topo = []
    seen = [False] * C
    for root in range(C):               # lowest index in each component roots it
        if seen[root]:
            continue
        seen[root] = True
        topo.append(root)
        stack = [root]
        while stack:
            u = stack.pop()
            for v in sorted(adj[u]):
                if not seen[v]:
                    seen[v] = True
                    parent[v] = u
                    topo.append(v)      # appended after its parent -> topological
                    stack.append(v)
    return parent, topo


def _xtree_block_params(x, cols, ps, pe):
    """Derive (parent, topo, beta) for one block from the PREVIOUS raw block
    x[:, ps:pe] -- which both encoder and decoder hold bit-identically.

    Builds the Chow-Liu maximum-weight spanning forest from the sign-sign edge
    weights, then for each tree edge derives the rounded integer least-squares
    gain over that same previous block and KEEPS it only if it lowers the block's
    estimated Rice bits; otherwise the edge is dropped (child becomes a root,
    leaving the topological order valid). Zero side-info: nothing here is
    transmitted."""
    C = x.shape[0]
    edges = _xtree_edges(C, cols)
    w = _xtree_weights(x[:, ps:pe], edges)
    parent, topo = _xtree_forest(C, edges, w)
    beta = [0] * C
    for c in range(C):
        p = parent[c]
        if p < 0:
            continue
        xc, xp = x[c, ps:pe], x[p, ps:pe]
        b = _bp_opt_beta(xc, xp, XTREE_SHIFT)
        if b == 0:
            parent[c] = -1
            continue
        resid = xc - ((b * xp) >> XTREE_SHIFT)
        if _bp_score(resid) < _bp_score(xc):
            beta[c] = b
        else:
            parent[c] = -1              # edge does not pay: code c as-is
    return parent, topo, beta


def _xtree_forward(x, cols, B=XTREE_BLOCK):
    """Cross-channel decorrelation along a per-block maximum-weight spanning
    forest. Block i's topology, coding order and gains all come from block i-1's
    raw samples (block 0 -> no forest, coded as-is); the rank-1 subtract is then
    applied to block i of the RAW signal. Returns the transformed [C, N] int64."""
    C, N = x.shape
    x = x.astype(np.int64)
    y = x.copy()
    nblocks = (N + B - 1) // B
    for i in range(1, nblocks):
        s, e = i * B, min((i + 1) * B, N)
        parent, _topo, beta = _xtree_block_params(x, cols, (i - 1) * B, i * B)
        for c in range(C):
            p = parent[c]
            if p >= 0:
                y[c, s:e] = x[c, s:e] - ((beta[c] * x[p, s:e]) >> XTREE_SHIFT)
    return y


def _xtree_inverse(y, cols, B=XTREE_BLOCK):
    """Invert _xtree_forward. Blocks are rebuilt in time order, so when block i is
    reached block i-1 is fully reconstructed for ALL channels and the identical
    forest / coding order / betas are re-derived from it. Within the block,
    channels are restored in the forest's TOPOLOGICAL order, so each channel's
    tree parent -- which may have a HIGHER grid index than the child, the freedom
    the raster codecs lack -- is already reconstructed when it is read."""
    C, N = y.shape
    y = y.astype(np.int64)
    x = y.copy()                        # roots (and block 0) are already raw
    nblocks = (N + B - 1) // B
    for i in range(1, nblocks):
        s, e = i * B, min((i + 1) * B, N)
        parent, topo, beta = _xtree_block_params(x, cols, (i - 1) * B, i * B)
        for c in topo:
            p = parent[c]
            if p >= 0:
                x[c, s:e] = y[c, s:e] + ((beta[c] * x[p, s:e]) >> XTREE_SHIFT)
    return x


def xtree_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    y = _xtree_forward(x, cols)                   # Chow-Liu forest + permuted order
    res = ec.lms_forward(y, order=XTREE_ORDER)    # order-4 sign-sign LMS (INSIGHTS P2)
    body = b"".join(ec.rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack("<HHII", XTREE_MAGIC, cols, C, N)   # NO topology/beta side-info
    return hdr + body


def xtree_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == XTREE_MAGIC, "bad xtree codec magic"
    off = 12
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        res[c] = arr
    y = ec.lms_inverse(res, order=XTREE_ORDER)    # matched order-4 inverse
    x = _xtree_inverse(y, cols)
    return x.astype(np.int16)


# ===========================================================================
# NEW candidate: Gated long-term (MUAP-firing-period) prediction
# (LMS4+Rice+ltp).
# ---------------------------------------------------------------------------
# THE LAG SCALE NO TEMPORAL LEVER HERE HAS EVER TOUCHED. Every temporal
# mechanism in this registry lives at lags 1-8: delta (lag 1), the fixed
# polynomial predictors (lags 1-3), the sign-sign LMS (order 8, then the
# right-sized order 4 -- lags 1-4), and the freshly retired regime-switched
# predictor banks (still lags 1-4, just with switched coefficients). An order-4
# filter at 2 kS/s spans 2 ms and is STRUCTURALLY BLIND to anything beyond it.
#
# THEORY: motor units fire as QUASI-PERIODIC trains at 8-30 pps with low ISI
# variability, so each channel carries a component correlated with itself at lag
# T ~= 67-250 samples (2 kS/s). A short-window whiteness test will call the
# order-4 residual "white" while that long-lag term survives untouched -- which
# is exactly why INSIGHTS P2's "the residual is already near-white after a
# low-order predictor" does NOT close this lag scale: P2 measured the SHORT
# filter's order (taps at lags 5-8, where the extra taps fit noise), not one tap
# at lag ~100. This is the redundancy MPEG-4 ALS added a DEDICATED Long-Term
# Prediction stage for (5 long-term weighted residues, each with its own lag,
# lags "hundreds of samples"), on the explicit stated grounds that "distant
# sample correlations are difficult to remove with the standard forward-adaptive
# predictor, since very high orders would be required" -- the same argument
# transplanted from pitch harmonics to MU firing periodicity. (Liebchen, MPEG-4
# ALS / "Extended linear prediction tools for lossless audio coding";
# paper-reported, unverified here.)
#
# MECHANISM -- ONE extra tap, at a long lag, on the SHORT-TERM RESIDUAL:
#     e[n]  = x[n] - short-term order-4 sign-sign LMS prediction   (unchanged)
#     e'[n] = e[n] - round(g * e[n-T])                             (this stage)
# with (T, g) re-derived per block per channel, BACKWARD-ADAPTIVELY, from the
# PREVIOUS block of the already-reconstructed residual (INSIGHTS P4 -> ZERO
# side-info, look-ahead 0):
#   1. Over the previous block (LTP_BLOCK samples, time-subsampled by LTP_SUB to
#      bound the search), accumulate for every lag T in [LTP_TMIN, LTP_TMAX] the
#      correlation num(T) = Sum e[n]e[n-T] and the lagged energy et(T), plus the
#      window energy e0. Bounded, integer-only, and identical on both sides.
#   2. T* = argmax over the bounded lag range of the normalized autocorrelation
#      rho(T)^2 = num(T)^2 / (e0 * et(T)), restricted to num(T) > 0 (a MU firing
#      period shows a POSITIVE autocorrelation peak); ties break to the shortest
#      lag. Compared by integer cross-multiplication -- no float, no sqrt.
#   3. GATE (the already-verified ACAR fire/not-fire pattern, `_acar_gate`): the
#      tap is applied ONLY if rho(T*) >= LTP_THR_NUM/LTP_THR_DEN, i.e.
#      num^2 * DEN^2 >= NUM^2 * e0 * et. The threshold sits well above the level
#      a max-over-lags of pure noise reaches on this window length, so an
#      aperiodic (interference-EMG) channel simply never fires and the output is
#      then BIT-IDENTICAL to the ungated base codec -- the stage cannot lose bits
#      by fitting noise.
#   4. g = rounded integer least-squares gain num/et in Q(LTP_SHIFT) (`_int_beta`,
#      the family's estimator), clamped to |g| <= 1.0 for stability.
# The decoder recomputes steps 1-4 from the residual it has ALREADY reconstructed
# (lossless -> bit-identical to the encoder's), so T, g and the gate decision all
# mirror exactly and NOTHING is transmitted.
#
# CAUSALITY / MATCHED PAIR: blocks whose predecessor does not have LTP_TMAX
# samples of residual history in front of it (blocks 0 and 1) run with the stage
# OFF, so every lag reference lands on already-reconstructed samples. Inside a
# block the tap is applied in chunks of LTP_TMIN samples: since every lag is
# >= LTP_TMIN, e[n-T] for n in a chunk always lies STRICTLY BEFORE that chunk, so
# the decoder can reconstruct chunk-by-chunk (and the encoder vectorizes the
# identical arithmetic over the whole chunk).
#
# NOT A RETIRED RE-PROPOSAL. (a) Distinct from P2's dead ORDER lever: that raises
# the SHORT filter's order (taps at lags 5-8, adjacent to lags the filter already
# covers, where they fit noise); this adds ONE tap at lag ~100, unreachable by
# raising the order without an absurd tap count -- and it is GATED, which the
# order lever is not. (b) Distinct from the retired regime-switched predictor
# banks (PR#6 LMS4rs, PR#7 LMS4x2, INSIGHTS P4b): those SWITCH COEFFICIENTS on
# the same short support and so re-fit noise; this EXTENDS THE SUPPORT by one tap
# to a lag the short filter cannot see. (c) Not an entropy-back-end or Rice-context
# lever (P5/P5-ext): the Rice back-end and its per-block adaptive k are untouched;
# only the residual handed to it changes. (d) Not a spatial lever at all -- no
# cross-channel front-end is involved, so it is orthogonal to (and stackable with)
# the whole +xchan family.
#
# EMBEDDABILITY (borderline, sEMG-only -- see the CodecMeta note): needs a
# per-channel residual ring buffer spanning the search window plus the maximum
# lag, and one bounded lag search per block per channel. FLAG: the 30 kS/s neural
# profile scales the lag range and the ring x15 AND neural spike trains are far
# less periodic -> the stage must be COMPILED OUT for neural.
# ===========================================================================
LTP_MAGIC = 0x4C54          # 'LT'
LTP_BLOCK = ec.BLOCK        # (T, g) re-derivation block (aligns with the Rice block)
LTP_ORDER = LMS4_ORDER      # order-4 short-term predictor underneath (INSIGHTS P2)
LTP_TMIN = 64               # 2 kS/s / 64  = 32 pps  (fast end of MU firing rates)
LTP_TMAX = 200              # 2 kS/s / 200 = 10 pps  (slow end)
LTP_SUB = 2                 # time-subsampling stride of the bounded lag search
LTP_SHIFT = ec.CROSS_SHIFT  # Q8 fixed-point long-term gain (same scale as the family)
LTP_RND = 1 << (LTP_SHIFT - 1)   # round-half-up for the "round(g*e[n-T])" product
LTP_GMAX = 1 << LTP_SHIFT   # stability clamp: |g| <= 1.0
LTP_THR_NUM = 3             # GATE: fire only if normalized autocorr rho >= 3/8
LTP_THR_DEN = 8


def _ltp_gate(num, e0, et):
    """The fire/not-fire gate: is the normalized autocorrelation at the selected
    lag above threshold?  rho^2 = num^2/(e0*et) >= (NUM/DEN)^2, i.e.
    num^2 * DEN^2 >= NUM^2 * e0 * et -- integer-only, no sqrt, no float.

    The three quantities are first scaled down by a COMMON deterministic shift so
    both sides of the comparison stay inside 64 bits on-node (num, e0 and et are
    all quadratic in the residual, so one common shift leaves the inequality's
    meaning unchanged up to truncation). Degenerate windows (either energy
    vanishing under that shift) do NOT fire. Same integers on encode and decode."""
    if num <= 0 or e0 <= 0 or et <= 0:
        return False
    s = 0
    m = max(num, e0, et)
    while (m >> s) >= (1 << 20):
        s += 1
    a, b0, bt = num >> s, e0 >> s, et >> s
    if b0 <= 0 or bt <= 0:
        return False
    return a * a * LTP_THR_DEN * LTP_THR_DEN >= LTP_THR_NUM * LTP_THR_NUM * b0 * bt


def _ltp_params(e, ps, pe):
    """Backward-adaptive (T, g) per channel for the NEXT block, derived from the
    previous block e[:, ps:pe] of the reconstructed short-term residual.

    Returns (T, g) int64 arrays; g == 0 means the gate did not fire and the
    channel is coded with the long-term tap OFF (bit-identical to the base
    codec). Integer-only and deterministic -> the decoder, which holds the
    bit-identical residual, derives the same pair with nothing transmitted."""
    C = e.shape[0]
    lags = np.arange(LTP_TMIN, LTP_TMAX + 1, dtype=np.int64)
    w = e[:, ps:pe:LTP_SUB]                       # subsampled search window
    e0 = (w * w).sum(axis=1)
    num = np.empty((C, lags.size), np.int64)
    et = np.empty((C, lags.size), np.int64)
    for j, T in enumerate(lags):
        seg = e[:, ps - int(T):pe - int(T):LTP_SUB]
        num[:, j] = (w * seg).sum(axis=1)
        et[:, j] = (seg * seg).sum(axis=1)
    # argmax of rho(T)^2 = num^2/(e0*et) over positive-correlation lags; e0 is
    # common to all lags so num^2/et suffices. num is pre-shifted by a common
    # deterministic amount so the square stays inside int64.
    sh = 0
    mx = int(np.abs(num).max()) if num.size else 0
    while (mx >> sh) >= (1 << 31):
        sh += 1
    ns = num >> sh
    q = np.where(num > 0, (ns * ns) // np.maximum(et, 1), 0)
    pick = q.argmax(axis=1)                       # ties -> shortest lag
    T = np.zeros(C, np.int64)
    g = np.zeros(C, np.int64)
    for c in range(C):
        j = int(pick[c])
        if q[c, j] <= 0:
            continue
        n_, t_, z_ = int(num[c, j]), int(et[c, j]), int(e0[c])
        if not _ltp_gate(n_, z_, t_):             # GATE: aperiodic -> stay OFF
            continue
        b = _int_beta(n_, t_, LTP_SHIFT)          # integer least-squares gain
        b = max(-LTP_GMAX, min(LTP_GMAX, b))
        if b == 0:
            continue
        T[c] = int(lags[j])
        g[c] = b
    return T, g


def _ltp_forward(e, B=LTP_BLOCK):
    """Subtract the gated long-term tap from the short-term residual:
    e'[n] = e[n] - round(g*e[n-T]). Blocks without LTP_TMAX samples of history
    ahead of their predecessor run with the stage OFF."""
    C, N = e.shape
    e = e.astype(np.int64)
    ep = e.copy()
    nblocks = (N + B - 1) // B
    for i in range(nblocks):
        ps, pe = (i - 1) * B, i * B
        if ps < LTP_TMAX:                         # blocks 0/1: no lag history yet
            continue
        T, g = _ltp_params(e, ps, pe)
        if not g.any():                           # no channel fired: nothing to do
            continue
        s, en = i * B, min((i + 1) * B, N)
        for a in range(s, en, LTP_TMIN):          # chunk < shortest lag -> causal
            b = min(a + LTP_TMIN, en)
            idx = np.arange(a, b, dtype=np.int64)[None, :] - T[:, None]
            lag = np.take_along_axis(e, idx, axis=1)
            ep[:, a:b] = e[:, a:b] - ((g[:, None] * lag + LTP_RND) >> LTP_SHIFT)
    return ep


def _ltp_inverse(ep, B=LTP_BLOCK):
    """Invert _ltp_forward. Blocks are rebuilt in time order, so block i-1 (and
    the LTP_TMAX samples before it) are fully reconstructed when block i's (T, g)
    are re-derived; within a block the chunk length equals the SHORTEST lag, so
    every e[n-T] read is already restored."""
    C, N = ep.shape
    ep = ep.astype(np.int64)
    e = ep.copy()                                 # OFF blocks/channels pass through
    nblocks = (N + B - 1) // B
    for i in range(nblocks):
        ps, pe = (i - 1) * B, i * B
        if ps < LTP_TMAX:
            continue
        T, g = _ltp_params(e, ps, pe)
        if not g.any():
            continue
        s, en = i * B, min((i + 1) * B, N)
        for a in range(s, en, LTP_TMIN):
            b = min(a + LTP_TMIN, en)
            idx = np.arange(a, b, dtype=np.int64)[None, :] - T[:, None]
            lag = np.take_along_axis(e, idx, axis=1)
            e[:, a:b] = ep[:, a:b] + ((g[:, None] * lag + LTP_RND) >> LTP_SHIFT)
    return e


def ltp_encode(x, cols=16):
    x = np.asarray(x, np.int64)
    C, N = x.shape
    e = ec.lms_forward(x, order=LTP_ORDER)        # order-4 short-term LMS (P2)
    ep = _ltp_forward(e)                          # gated long-term tap at lag T
    body = b"".join(ec.rice_encode_1d(ep[c]) for c in range(C))
    hdr = struct.pack("<HHII", LTP_MAGIC, cols, C, N)   # NO (T, g, gate) side-info
    return hdr + body


def ltp_decode(buf):
    magic, cols, C, N = struct.unpack_from("<HHII", buf, 0)
    assert magic == LTP_MAGIC, "bad ltp codec magic"
    off = 12
    ep = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = ec.rice_decode_1d(buf, off)
        ep[c] = arr
    e = _ltp_inverse(ep)                          # mirrored T, g and gate
    x = ec.lms_inverse(e, order=LTP_ORDER)        # matched order-4 inverse
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
         "best-partner subtract + Rice"))

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

# NEW candidate (this cycle): delay-compensated spatio-temporal cross-channel FIR
# (LMS4+Rice+xstfir) -- the LAG degree of freedom no registered codec has varied.
# Over the order-4 LMS+Rice per-sample work the front-end adds a 4-tap spatial FIR:
# 4 macs + 1 shift + 1 subtract (~10) plus the joint sign-sign update of 4 taps
# (sign extract + 4 conditional +/-1 adds, ~8) = ~18 ops/sample-ch, all
# multiplierless in the update path. The decoder re-derives the identical taps
# from the coded residual and reconstructed neighbours, so dec_ops == enc_ops.
# Persistent per-channel state: the order-4 LMS weights/history (_LMS4_STATE) +
# 4 int16 spatial taps (8 B) + the parent's two past samples and the opposite-side
# neighbour's previous sample (3 x int16 = 6 B) = _LMS4_STATE + 14. Backward-
# adaptive, look-ahead 0, ZERO side-info (INSIGHTS P4); comfortably inside the
# 1875 cyc/sample-ch sEMG budget and still inside the tight 125-cyc neural budget.
_XSTFIR_XTRA = 18    # 4-tap spatial FIR (4 mac + shift + sub) + 4 sign-sign updates
_XSTFIR_STATE = _LMS4_STATE + 14   # order-4 LMS + 4 int16 taps + 3 int16 delay regs
_XSTFIR_NOTE = (
    "delay-compensated spatio-temporal cross-channel predictor: replaces the "
    "zero-lag scalar subtract beta*x_p[n] -- used by EVERY registered spatial "
    "front-end -- with a short FIR over the parent's reconstructed history plus "
    "one opposite-side tap, pred[c,n]=(w0*x_p[n]+w1*x_p[n-1]+w2*x_p[n-2]+"
    "w3*x_q[n-1])>>shift, e=x[c,n]-pred, and ONE JOINT sign-sign LMS over all four "
    "taps against the SHARED post-subtraction residual (w_i+=sign(e)*sign(a_i), "
    "+/-1 update, multiplierless) -- the stochastic-gradient realization of the "
    "4x4 normal-equations solve, so lag-to-lag covariance enters through the shared "
    "residual and is never double-counted. THEORY: MUAPs propagate along the muscle "
    "fibres at ~3-5 m/s, so at 8-10 mm IED the neighbour sees the SAME waveform "
    "DELAYED (this is how MFCV is measured, and the propagating component is what "
    "distinguishes true MUAPs from non-propagating crosstalk); by Wiener theory the "
    "optimal predictor from a DELAYED common source is a fractional-delay FILTER, "
    "not a GAIN -- a zero-lag beta can only remove the projection onto rho(0) and "
    "structurally leaves rho(tau*)^2-rho(0)^2 of shared energy in the residual no "
    "matter which parent is selected or how many are added, a falsifiable identity "
    "for the ~1% ceiling all zero-lag variants share. Parent p (left, else up) has "
    "grid idx<c so its CURRENT slice is available; the opposite-side neighbour q "
    "(right, else down) has idx>c so it is read only at lag 1 -- the stage therefore "
    "runs TIME-MAJOR (per sample n, channels in index order), making slice n-1 fully "
    "available while same-slice reads stay causal. Deterministic from (C,cols), so "
    "the decoder mirrors it: ZERO side-info, look-ahead 0 (INSIGHTS P4). ASYMMETRIC "
    "residual-only injection -- parent/neighbour rows are inputs left CLEAN, unlike "
    "the RETIRED energy-preserving iklt_adaptive rotation that corrupts both channels "
    "(P3-refinement robustness). Back-end: order-4 sign-sign LMS + adaptive Rice "
    "(P2/P5). NOT P2's dead deeper-temporal-order lever -- the extra taps read a "
    "DIFFERENT channel's past (which carries the delayed copy), not the coding "
    "channel's own already-whitened past. Distinct from KEPT xchan_joint2 (a second "
    "PARENT at lag 0; P1b showed selection and count are SUBSTITUTES because both are "
    "capped by rho(0) -- LAG is the orthogonal freedom that raises the cap), from "
    "RETIRED xchan_multiparent (summed MARGINAL betas double-count; jointly co-adapting "
    "taps cannot), and from RETIRED xres (lag-0 innovation-domain subtract; this stays "
    "in the better-conditioned raw domain and moves the LAG instead). Precedent: "
    "MPEG-4 ALS inter-channel prediction is a THREE-TAP filter with an explicit lag, "
    "not the 1-tap zero-lag reduction this registry implements (Liebchen, 'Extended "
    "linear prediction tools for lossless audio coding'); signal-model sources: MFCV "
    "reviews (ScienceDirect S1050641118303110; PMC3616828). All paper-reported, "
    "unverified here.")
_register(Codec("LMS4+Rice+xstfir", xstfir_encode, xstfir_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS4_OPS + _XSTFIR_XTRA,
    dec_ops=_LMS4_OPS + _XSTFIR_XTRA,
    state_bytes_per_ch=_XSTFIR_STATE, causal=True, lookahead_samples=0,
    block_size=ec.BLOCK, notes=_XSTFIR_NOTE), family="cross-channel",
    desc="order-4 LMS + delay-compensated spatio-temporal cross-channel FIR "
         "(3 parent lags + 1 opposite-side lag-1 tap, joint sign-sign LMS, "
         "zero side-info) + Rice"))

# NEW candidate (this cycle): maximum-weight spanning-forest channel topology with
# permuted coding order (LMS4+Rice+xtree). Cost of the EMBEDDABLE construction, on
# top of the order-4 LMS+Rice per-sample work (_LMS4_OPS) and the rank-1 subtract
# itself (_XCHAN_OPS = 1 mul + 1 shift + 1 sub):
#   * sign-sign edge counters: <=12 undirected candidates/ch -> ~6 edges/ch, each
#     an XOR + increment, time-subsampled by 8  ->  ~6/8 ~ 0.8 ops/sample-ch
#     (plus the sign extraction, ~0.2)                                    ~ 1
#   * Kruskal + union-find + DFS over ~6C edges once per 256-sample block:
#     ~6C log(6C) ~ 8.4k ops/block at C=128 -> 8400/(256*128)             ~ 0.3
#   * per-channel beta + Rice-bits gate on ONE surviving edge per block: 2 running
#     dot-products (2 macs/sample-ch) + two block-boundary bit estimates + one
#     rounded divide, amortised                                           ~ 5
# -> ~6 extra ops/sample-ch. This is the whole point of the bounded candidate set:
# a naive full CxC per-block correlation matrix would be ~64 MAC/sample-ch at
# C=128 -- disqualifying at the 125-cyc neural budget -- whereas the geometric
# 12-candidate set + sign-sign counters + 8x subsampling keeps the topology search
# at ~1 op/sample-ch. The decoder re-derives the identical forest, order and betas
# (nothing is transmitted), so dec_ops == enc_ops. Persistent per-channel state:
# order-4 LMS weights/history (_LMS4_STATE) + 6 int16 edge counters (12 B) + the
# current int16 beta (2) + parent id / union-find / topo slot (3 B) = +17 B.
# Integer-only, causal, look-ahead 0, ZERO side-info (INSIGHTS P4).
_XTREE_XTRA = 6      # sign-sign counters + amortised Kruskal/DFS + beta & bits gate
_XTREE_STATE = _LMS4_STATE + 17   # order-4 LMS + 6 int16 counters + beta + ids
_XTREE_NOTE = (
    "Chow-Liu maximum-weight spanning-forest channel topology with PERMUTED "
    "coding order -- the two things every prior spatial front-end here froze. "
    "THEORY: Chow & Liu (1968) prove that under a first-order (tree) dependency "
    "model Sum_c H(x_c|x_parent(c)) is minimized EXACTLY by the maximum-weight "
    "spanning tree over pairwise-MI edge weights; Tate (IEEE Trans. Computers "
    "1997, 'Band Ordering in Lossless Compression of Multispectral Images') "
    "proves the analogous statement for lossless inter-band predictive coding -- "
    "the optimal coding order is a maximum-weight directed spanning forest, with "
    "substantial reported gains over the raster order (ISPRS Annals X-1/W1 2023 "
    "restates it as an MST; both paper-reported, unverified here). The shipped "
    "best-partner family is a raster-constrained, causal-4-neighbourhood, GREEDY "
    "forest and is therefore provably <= that optimum; this measures the gap. "
    "MECHANISM, per block, all derived from the PREVIOUS fully reconstructed raw "
    "block so ZERO side-info and look-ahead 0: (a) BOUNDED candidate edge set -- "
    "the 8 grid neighbours + the 4 axis-distance-2 neighbours, <=12 undirected "
    "candidates/ch (~6C edges, 768 at C=128), a GLOBAL undirected set including "
    "the down/right/down-* and distance-2 edges no raster codec here can reach; "
    "(b) edge weight = |sign-sign correlation counter| (XOR + increment, "
    "MULTIPLIERLESS; common sample count makes it a normalized |cross-corr|, and "
    "by the arcsine law it is monotone in |rho| -> in the pairwise MI); (c) "
    "time-subsampled every 8th sample -> ~1 op/sample-ch; then one Kruskal + "
    "union-find pass over <=2048 edges per block (amortised negligible), "
    "components rooted at their lowest index, oriented by a deterministic DFS "
    "whose visit sequence IS the topological CODING ORDER. Each tree edge is the "
    "ALREADY-SHIPPED single-parent adaptive rank-1 subtract: rounded integer-LS "
    "beta from the previous block, kept only if it lowers that block's estimated "
    "Rice bits, else the child becomes a root. Block 0 coded as-is. WHY IT CAN "
    "MOVE THE NUMBER WHERE NOTHING ELSE HAS: the raster constraint bites hardest "
    "where the harness sees no gain -- CapgMyo is a DIFFERENTIAL 8x16 array "
    "(neighbour |corr| ~0.29, +1.3%), where adjacent channels share an electrode "
    "with OPPOSITE sign so the physically best partner may sit at distance 2, "
    "along the other axis, or be ANTI-correlated (the |.| on the counter makes "
    "such edges first-class); on the 128-/320-ch multi-grid Hyser/CEMHSEY "
    "mountings a raster up/left parent can lie on a different grid or muscle and "
    "carry ~zero MI, an edge the forest would never select. COST IS THE REAL "
    "GATE: a naive full CxC per-block correlation matrix (~64 MAC/sample-ch at "
    "C=128) would be disqualified at the 125-cyc neural budget, which is why the "
    "candidate set is bounded, the estimator multiplierless and the accumulation "
    "subsampled. NOT P3's dead multi-tap transform: every edge remains the "
    "verified ASYMMETRIC single-parent rank-1 subtract (residual-only injection, "
    "parent row left CLEAN) -- only WHICH edges exist and in WHAT ORDER channels "
    "are coded changes; not a rotation (RETIRED iklt/iklt_adaptive) and not a "
    "summed multi-parent subtract (RETIRED xchan_multiparent). Distinct from DONE "
    "LMS4+Rice+xchan_bestpartner / ..._bestpartner_adaptive: those pick ONE parent "
    "per channel from the FIXED causal 4-neighbourhood under a FIXED raster order "
    "(per-channel greedy); here the candidate set is global/undirected and the "
    "CODING ORDER ITSELF is a free variable optimized by a global criterion (total "
    "forest weight). Back-end unchanged: order-4 sign-sign LMS + adaptive Rice "
    "(INSIGHTS P2/P5). Risk to MEASURE: a stale forest across a burst boundary, "
    "and whether a globally-optimal topology beats a greedy raster one by more "
    "than the ~1% ceiling every zero-lag spatial variant shares.")
_register(Codec("LMS4+Rice+xtree", xtree_encode, xtree_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS4_OPS + _XCHAN_OPS + _XTREE_XTRA,
    dec_ops=_LMS4_OPS + _XCHAN_OPS + _XTREE_XTRA,
    state_bytes_per_ch=_XTREE_STATE, causal=True, lookahead_samples=0,
    block_size=XTREE_BLOCK, notes=_XTREE_NOTE), family="cross-channel",
    desc="order-4 LMS + Chow-Liu maximum-weight spanning-forest channel topology "
         "with permuted (topological) coding order, backward-derived from "
         "sign-sign correlation counters (zero side-info) + Rice",
    retired=True,
    retired_reason="Conclusively Pareto-dominated on ALL 4 real sets (cycle 13, "
                   "results/cycle_bench.csv) by the already-registered "
                   "LMS4+Rice+xchan_bestpartner_adaptive -- same backward per-block "
                   "integer-LS beta + Rice-bits gate + order-4 LMS, differing ONLY in "
                   "the topology criterion -- worse ratio AND higher cost everywhere "
                   "(hyser 1.4617x vs 1.4770x, otb 2.1346x vs 2.1531x, capgmyo 1.3497x "
                   "vs 1.3529x, cemhsey 1.9457x vs 1.9539x; cost 0.0430 vs 0.0387); "
                   "also dominated by LMS4+Rice+xchan_bestpartner (0.0394) and "
                   "LMS+Rice+xchan_joint2 (0.0366). The forest was genuinely exercised "
                   "(34.8-42.2% of chosen parents have a HIGHER grid index, 47.7-68.7% "
                   "lie outside the causal 4-neighbourhood) -- global topology "
                   "optimisation of a coarse sign-sign MI surrogate simply loses to "
                   "greedy selection on the true min-Rice-bits criterion. See "
                   "experiments/013_lms4_rice_xtree.md, INSIGHTS P1c."))

# NEW candidate (this cycle): gated long-term (MUAP-firing-period) prediction
# (LMS4+Rice+ltp). Cost of the EMBEDDABLE sEMG realization, on top of the order-4
# LMS+Rice per-sample work (_LMS4_OPS):
#   * bounded lag search, once per block per channel: 137 lags x (LTP_BLOCK/LTP_SUB
#     = 128) correlation MACs / 256 samples                              ~ 69
#   * lagged energies et(T) + window energy e0: the et(T) over a fixed-length
#     window obey an EXACT integer sliding-window recurrence (E(T+SUB) = E(T)
#     - e[..]^2 + e[..]^2), so a node evaluates all 137 of them in O(1) each
#     from two parity-split running sums of squares                      ~ 2
#     (the software model above recomputes them directly from the ring for
#      clarity -- identical integers, only the op count differs)
#   * per-block argmax over 137 lags + the integer gate + ONE rounded divide
#     for g, amortised over the block                                    ~ 2
#   * applying the tap: ring read + 1 mul + 1 add + 1 shift + 1 sub + ring
#     write                                                              ~ 6
# -> ~79 extra ops/sample-ch, i.e. ~126 cyc/sample-ch total: comfortably inside
# the 1831-cyc sEMG budget, and (correctly) OUTSIDE the 125-cyc neural one. The
# decoder re-derives (T, g) and the gate identically -- nothing is transmitted --
# so dec_ops == enc_ops. Persistent per-channel state: order-4 LMS weights/history
# (_LMS4_STATE) + the residual RING BUFFER the stage needs, LTP_BLOCK + LTP_TMAX =
# 456 samples at 2 B (the search window plus the maximum lag) = 912 B + the current
# (T, g) and accumulator bookkeeping (~6 B) -> 942 B/ch, 118 KiB at 128 ch: ~46% of
# the 256 KiB SRAM budget, and ~1/4 of the XC7S25's ~202 kB BRAM after the playback
# loop's ~16/45 BRAM36k. THIS RING IS THE CANDIDATE'S REAL PRICE and the reason the
# survey rates it borderline / sEMG-only. Integer-only, causal, look-ahead 0, ZERO
# side-info (INSIGHTS P4).
_LTP_XTRA = 79       # lag search + energies + amortised argmax/gate/divide + tap
_LTP_STATE = _LMS4_STATE + 2 * (LTP_BLOCK + LTP_TMAX) + 6   # LMS4 + residual ring
_LTP_NOTE = (
    "GATED LONG-TERM PREDICTION at the MU firing period -- one extra tap at a lag "
    "~50x beyond anything else in this registry (every temporal lever here lives at "
    "lags 1-8: delta, the fixed polynomial orders, the order-8/order-4 sign-sign LMS, "
    "and the retired regime-switched banks). e'[n] = e[n] - round(g*e[n-T]) applied to "
    "the order-4 short-term LMS residual. THEORY: motor units fire as QUASI-PERIODIC "
    "trains at 8-30 pps with low ISI variability, so each channel carries a component "
    "correlated with itself at lag T ~= 67-250 samples at 2 kS/s; an order-4 filter "
    "spans 2 ms and is STRUCTURALLY BLIND to it -- a short-window whiteness test calls "
    "the residual white while the long-lag term survives, which is why INSIGHTS P2's "
    "'already near-white' observation does not close this lag scale. Same argument "
    "MPEG-4 ALS states for its DEDICATED Long-Term Prediction stage (5 long-term "
    "weighted residues, each with its own lag, lags 'hundreds of samples': 'distant "
    "sample correlations are difficult to remove with the standard forward-adaptive "
    "predictor, since very high orders would be required'), transplanted from pitch "
    "harmonics to MU firing periodicity (Liebchen, MPEG-4 ALS / 'Extended linear "
    "prediction tools for lossless audio coding'; paper-reported, unverified here). "
    "MECHANISM, all backward-adaptive from the PREVIOUS block of the already "
    "reconstructed residual -> ZERO side-info, look-ahead 0 (P4): (1) bounded lag "
    "search over T in [64, 200] samples, time-subsampled by 2, accumulating num(T), "
    "et(T) and the window energy e0; (2) T* = argmax of the normalized autocorrelation "
    "rho^2 = num^2/(e0*et) restricted to POSITIVE correlation (a firing period shows a "
    "positive peak), compared by integer cross-multiplication, ties to the shortest "
    "lag; (3) GATE -- the already-verified ACAR fire/not-fire pattern -- the tap is "
    "applied ONLY if rho(T*) >= 3/8, a threshold well above the level a max-over-137-"
    "lags of pure noise reaches on a 128-point window, so an APERIODIC channel never "
    "fires and the output is then BIT-IDENTICAL to the ungated base codec (the stage "
    "cannot lose bits by fitting noise); (4) g = rounded integer least-squares gain "
    "num/et in Q8, clamped to |g| <= 1.0. Blocks 0/1 run OFF (no lag history); inside a "
    "block the tap is applied in chunks of the SHORTEST lag so every e[n-T] read is "
    "strictly earlier -> the decoder reconstructs chunk-by-chunk and mirrors T, g and "
    "the gate exactly. NOT a retired re-proposal: (a) distinct from P2's dead ORDER "
    "lever -- that lengthens the SHORT filter (taps at lags 5-8, adjacent to lags it "
    "already covers, where they fit noise); this is ONE gated tap at lag ~100, "
    "unreachable by raising the order without an absurd tap count; (b) distinct from "
    "the retired regime-switched predictor banks (PR#6 LMS4rs, PR#7 LMS4x2, P4b), which "
    "SWITCH COEFFICIENTS on the same short support -- this EXTENDS THE SUPPORT; (c) not "
    "an entropy-back-end or Rice-context lever (P5/P5-ext): Rice and its per-block "
    "adaptive k are untouched, only the residual handed to them changes; (d) not a "
    "spatial lever -- no cross-channel front-end at all, so it is orthogonal to the "
    "whole +xchan family. EMBEDDABILITY IS BORDERLINE AND sEMG-ONLY: the per-channel "
    "residual RING BUFFER (search window + max lag = 456 samples x 2 B = 912 B/ch, "
    "118 KiB at 128 ch, ~46% of the SRAM budget) is the real price; the ring is sized "
    "to the residual's actual dynamic range (2 B on real HD-sEMG; the software model "
    "keeps int64 for exactness). FLAG: the 30 kS/s NEURAL profile scales the lag range "
    "and the ring x15 AND spike trains are far less periodic -> the stage must be "
    "COMPILED OUT for neural, which the ~126 cyc/sample-ch (> the 125-cyc neural "
    "budget) already reflects. HONEST RISK to MEASURE: at high contraction force "
    "HD-sEMG is INTERFERENCE EMG (tens of superimposed MUs per channel), which smears "
    "the composite autocorrelation toward white -- the gate may simply never fire, in "
    "which case the codec is bit-identical to the base and the lever is settled cheaply "
    "either way. (Corroborating: HD-EMG compression on trapezius/gastrocnemius reports "
    "higher file-size reduction at LOWER contraction force, PMC3984708; paper-reported, "
    "unverified here.)")
_register(Codec("LMS4+Rice+ltp", ltp_encode, ltp_decode, CodecMeta(
    integer_only=True, enc_ops=_LMS4_OPS + _LTP_XTRA,
    dec_ops=_LMS4_OPS + _LTP_XTRA,
    state_bytes_per_ch=_LTP_STATE, causal=True, lookahead_samples=0,
    block_size=LTP_BLOCK, notes=_LTP_NOTE), family="temporal",
    desc="order-4 LMS + gated long-term (MU firing-period) residual tap at a "
         "backward-selected lag T in [64,200] (zero side-info) + Rice",
    retired=True,
    retired_reason="Conclusively Pareto-dominated on ALL 4 real sets (cycle 13, "
                   "results/cycle_bench.csv) by the registered leaderboard best "
                   "LMS4+Rice+xchan_bestpartner -- worse ratio AND 13.4x the cost "
                   "(hyser 1.3319x vs 1.4804x, otb 1.8347x vs 2.1619x, capgmyo 1.3331x "
                   "vs 1.3505x, cemhsey 1.7284x vs 1.9555x; cost 0.5288 vs 0.0394). "
                   "In-memory gate-forced-off isolation on real data shows the "
                   "long-term tap itself contributes -0.012% (hyser), +0.003% (otb), "
                   "-0.035% (capgmyo), +0.022% (cemhsey) -- i.e. ZERO, and negative on "
                   "two sets -- despite the gate firing on 1.1-11.7% of block-channels: "
                   "the codec's whole edge over LMS+Rice is the order-8->4 predictor "
                   "(P2), not the LTP stage. See experiments/014_lms4_rice_ltp.md, "
                   "INSIGHTS P6."))


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
