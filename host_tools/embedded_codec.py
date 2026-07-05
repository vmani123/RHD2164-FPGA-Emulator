#!/usr/bin/env python3
"""
embedded_codec.py  -  Faithful software model of the hardware-implementable
lossless codecs for the HD-EMG node. Shared by bench_lossless.py (Stage 4) and
verify_compressed.py (Stage 5).

All codecs here are integer-exact and one-pass / streaming-legal (no look-ahead
beyond a small block), i.e. portable to RTL/firmware later:

  * delta1  : order-1 DPCM (predict x[t] ~ x[t-1])
  * lms     : backward-adaptive sign-sign LMS linear predictor (order M),
              the streaming way to approach FLAC's LPC without look-ahead
  * cross   : optional cross-channel front-end -- subtract a physical grid
              neighbour (a fixed spanning tree over the electrode array) before
              the temporal predictor. This is the lever to beat per-channel FLAC.

Entropy back-end is adaptive Golomb-Rice (per-block k). Everything round-trips
bit-exact; run `python3 embedded_codec.py` to self-test.
"""
import struct
import numpy as np

MAGIC = 0x5243  # 'RC'
BLOCK = 256     # Rice adaptive-k block size (samples)

PRED_DELTA = 0
PRED_LMS = 1
LMS_ORDER = 8
LMS_SHIFT = 8   # fixed-point weight scale


# ---------------------------------------------------------------------------
# zigzag map (signed <-> unsigned), vectorized
# ---------------------------------------------------------------------------
def zigzag(s):
    s = s.astype(np.int64)
    return ((s << 1) ^ (s >> 63)).astype(np.uint64)


def unzigzag(u):
    u = u.astype(np.uint64)
    return ((u >> np.uint64(1)).astype(np.int64) ^ -(u & np.uint64(1)).astype(np.int64))


# ---------------------------------------------------------------------------
# Adaptive Golomb-Rice for a 1-D residual array
# ---------------------------------------------------------------------------
def _best_k(u_block):
    n = u_block.size
    if n == 0:
        return 0
    best_k, best_len = 0, None
    for k in range(0, 20):
        length = int((u_block >> np.uint64(k)).sum()) + n * (1 + k)
        if best_len is None or length < best_len:
            best_len, best_k = length, k
        # sum(q) shrinks fast; once it stops helping we can stop
        if (u_block >> np.uint64(k)).sum() == 0:
            break
    return best_k


def rice_encode_1d(res):
    """Encode a 1-D int array -> bytes (self-describing)."""
    u = zigzag(res)
    n = u.size
    nblocks = (n + BLOCK - 1) // BLOCK
    ks = np.zeros(nblocks, np.uint8)
    all_bits = []
    for b in range(nblocks):
        ub = u[b * BLOCK:(b + 1) * BLOCK]
        k = _best_k(ub)
        ks[b] = k
        q = (ub >> np.uint64(k)).astype(np.int64)
        r = (ub & np.uint64((1 << k) - 1)).astype(np.int64) if k else np.zeros(ub.size, np.int64)
        lengths = q + 1 + k
        total = int(lengths.sum())
        starts = np.concatenate(([0], np.cumsum(lengths)[:-1]))
        bits = np.zeros(total, np.uint8)
        bits[starts + q] = 1                      # unary stop bit
        for j in range(k):                        # k remainder bits, MSB first
            bits[starts + q + 1 + j] = (r >> (k - 1 - j)) & 1
        all_bits.append(bits)
    bit_arr = np.concatenate(all_bits) if all_bits else np.zeros(0, np.uint8)
    packed = np.packbits(bit_arr).tobytes()
    hdr = struct.pack('<IH', n, BLOCK) + ks.tobytes() + struct.pack('<I', len(packed))
    return hdr + packed


def rice_decode_1d(buf, off=0):
    """Decode -> (int array, new_offset)."""
    n, bs = struct.unpack_from('<IH', buf, off); off += 6
    nblocks = (n + bs - 1) // bs
    ks = np.frombuffer(buf, np.uint8, nblocks, off); off += nblocks
    (plen,) = struct.unpack_from('<I', buf, off); off += 4
    bits = np.unpackbits(np.frombuffer(buf, np.uint8, plen, off)); off += plen

    out = np.zeros(n, np.int64)
    pos = 0
    ones = np.flatnonzero(bits)      # positions of 1-bits (stop bits + remainder bits)
    optr = 0
    idx = 0
    for b in range(nblocks):
        k = int(ks[b])
        cnt = min(bs, n - b * bs)
        # pass 1: find stop bits + remainder starts (O(symbols))
        qs = np.empty(cnt, np.int64)
        rstart = np.empty(cnt, np.int64)
        for i in range(cnt):
            while ones[optr] < pos:
                optr += 1
            stop = ones[optr]
            qs[i] = stop - pos
            rstart[i] = stop + 1
            pos = stop + 1 + k
            # advance optr past any 1-bits inside this symbol's remainder field
            while optr < ones.size and ones[optr] < pos:
                optr += 1
        # pass 2: gather k-bit remainders vectorized
        r = np.zeros(cnt, np.int64)
        for j in range(k):
            r |= bits[rstart + j].astype(np.int64) << (k - 1 - j)
        u = (qs << k) | r
        out[idx:idx + cnt] = u
        idx += cnt
    return unzigzag(out.astype(np.uint64)), off


# ---------------------------------------------------------------------------
# Temporal predictors (operate on [C, N] int arrays; vectorized over channels)
# ---------------------------------------------------------------------------
def delta_forward(x):
    res = np.empty_like(x, dtype=np.int64)
    res[:, 0] = x[:, 0]
    res[:, 1:] = np.diff(x.astype(np.int64), axis=1)
    return res


def delta_inverse(res):
    return np.cumsum(res.astype(np.int64), axis=1)


def lms_forward(x, order=LMS_ORDER, shift=LMS_SHIFT):
    C, N = x.shape
    x = x.astype(np.int64)
    w = np.zeros((C, order), np.int64)
    hist = np.zeros((C, order), np.int64)      # past reconstructed samples
    res = np.empty((C, N), np.int64)
    for t in range(N):
        pred = (w * hist).sum(axis=1) >> shift
        e = x[:, t] - pred
        res[:, t] = e
        # sign-sign LMS update (identical in decoder)
        w += np.sign(e)[:, None] * np.sign(hist)
        hist[:, 1:] = hist[:, :-1]
        hist[:, 0] = x[:, t]
    return res


def lms_inverse(res, order=LMS_ORDER, shift=LMS_SHIFT):
    C, N = res.shape
    res = res.astype(np.int64)
    w = np.zeros((C, order), np.int64)
    hist = np.zeros((C, order), np.int64)
    x = np.empty((C, N), np.int64)
    for t in range(N):
        pred = (w * hist).sum(axis=1) >> shift
        xt = pred + res[:, t]
        x[:, t] = xt
        w += np.sign(res[:, t])[:, None] * np.sign(hist)
        hist[:, 1:] = hist[:, :-1]
        hist[:, 0] = xt
    return x


# ---------------------------------------------------------------------------
# Cross-channel front-end: subtract a grid-neighbour parent (spanning tree).
# parent[c] < c so decode can reconstruct in channel order; root parent = -1.
# ---------------------------------------------------------------------------
def grid_parents(channels, cols):
    parent = np.full(channels, -1, np.int64)
    for g in range(channels):
        r, c = g // cols, g % cols
        if c > 0:
            parent[g] = g - 1          # left neighbour
        elif r > 0:
            parent[g] = g - cols       # up neighbour (first column)
    return parent


CROSS_SHIFT = 8


def cross_betas(x, parent, shift=CROSS_SHIFT):
    """Optimal fixed per-channel gain beta (fixed-point) for predicting a channel
    from its parent: beta ~ <x_c, x_p> / <x_p, x_p>. Sent as tiny side-info
    (one int16 per channel). Adapting the gain -- rather than subtracting the
    neighbour outright -- is what stops the independent noise floor from being
    doubled when correlation is low (beta -> 0), while still cancelling the
    shared spatial signal when correlation is high (beta -> 1<<shift)."""
    x = x.astype(np.int64)
    betas = np.zeros(x.shape[0], np.int64)
    for g in range(x.shape[0]):
        p = parent[g]
        if p < 0:
            continue
        denom = int((x[p] * x[p]).sum())
        if denom > 0:
            b = int(round((x[g] * x[p]).sum() / denom * (1 << shift)))
            betas[g] = max(-32768, min(32767, b))
    return betas


def cross_forward(x, parent, betas, shift=CROSS_SHIFT):
    x = x.astype(np.int64)
    y = x.copy()
    for g in range(x.shape[0]):
        p = parent[g]
        if p >= 0:
            y[g] = x[g] - ((betas[g] * x[p]) >> shift)
    return y


def cross_inverse(y, parent, betas, shift=CROSS_SHIFT):
    x = y.astype(np.int64).copy()
    for g in range(y.shape[0]):        # parent[g] < g so parent already restored
        p = parent[g]
        if p >= 0:
            x[g] = y[g] + ((betas[g] * x[p]) >> shift)
    return x


# ---------------------------------------------------------------------------
# Full array codec: header + per-channel Rice of the residual
# ---------------------------------------------------------------------------
def encode(x, predictor=PRED_LMS, cross=False, cols=16):
    C, N = x.shape
    betas = None
    if cross:
        parent = grid_parents(C, cols)
        betas = cross_betas(x, parent)
        xt = cross_forward(x, parent, betas)
    else:
        xt = x.astype(np.int64)
    res = lms_forward(xt) if predictor == PRED_LMS else delta_forward(xt)
    body = b''.join(rice_encode_1d(res[c]) for c in range(C))
    hdr = struct.pack('<HBBHI', MAGIC, predictor, 1 if cross else 0, cols, C) \
        + struct.pack('<I', N)
    if cross:
        hdr += betas.astype('<i2').tobytes()   # C int16 side-info
    return hdr + body


def decode(buf):
    magic, predictor, cross, cols, C = struct.unpack_from('<HBBHI', buf, 0)
    (N,) = struct.unpack_from('<I', buf, 10)
    assert magic == MAGIC, 'bad codec magic'
    off = 14
    betas = None
    if cross:
        betas = np.frombuffer(buf, '<i2', C, off).astype(np.int64); off += 2 * C
    res = np.empty((C, N), np.int64)
    for c in range(C):
        arr, off = rice_decode_1d(buf, off)
        res[c] = arr
    xt = lms_inverse(res) if predictor == PRED_LMS else delta_inverse(res)
    if cross:
        xt = cross_inverse(xt, grid_parents(C, cols), betas)
    return xt.astype(np.int16)


# ---------------------------------------------------------------------------
def _selftest():
    rng = np.random.default_rng(0)
    x = (rng.normal(0, 15, (16, 2000)).round().astype(np.int16))
    x[3, 500:520] += 400   # a spike
    for pred in (PRED_DELTA, PRED_LMS):
        for cross in (False, True):
            b = encode(x, predictor=pred, cross=cross, cols=4)
            y = decode(b)
            ok = np.array_equal(x, y)
            ratio = x.nbytes / len(b)
            print(f"  pred={pred} cross={int(cross)}  round-trip={'OK' if ok else 'FAIL'}"
                  f"  ratio={ratio:.2f}x")
            assert ok, "round-trip mismatch"
    print("embedded_codec self-test: ALL round-trips bit-exact")


if __name__ == '__main__':
    _selftest()
