# Codec candidates — reference menu

Referenced by `../COMPRESSION_RESEARCH_AGENT_PROMPT.md` (Stage 1). Lossless only. Every codec must round-trip bit-exact and carry cost metadata (see `cost_model.md`). The **reference bar** is the set of standards to beat; the **embedded candidates** are the real contenders (implement directly); the **watch-list** is survey-only.

**Already implemented in `host_tools/embedded_codec.py`** (don't re-implement — extend): `delta+Rice` (order-1 DPCM + adaptive Golomb-Rice), `LMS+Rice` (sign-sign LMS order-8 + adaptive Rice), and the `+xchan` cross-channel front-end (subtract a grid neighbour with an optimal per-channel int16 gain). Reference bar (FLAC/WavPack/mtscomp/zstd/LZMA/gzip) is already wired in `bench_lossless.py`.

## Reference bar (beat these; mostly not embeddable — that's the point)

- **FLAC** (`pyflac` or `flac` CLI) — per-channel LPC; the main target to beat.
- **WavPack**, **mtscomp** (IBL) — neurophysiology-oriented per-channel references.
- **zstd** (+ **bitshuffle** via blosc), **LZMA**, **gzip**, **bzip2** — generic references.

## Embedded-implementable candidates — NEW ones to add (the contenders)

- **FLAC's four fixed polynomial predictors + Rice** — pick-best-per-block; cheap upgrade to delta.
- **NLMS / higher-order & leaky LMS (order 8–16) + Rice** — beyond the current sign-sign order-8; the MPEG-4 ALS RLS-LMS direction, still one-pass and streaming-legal.
- **range / arithmetic coder on residuals** — compare head-to-head vs. Rice for the same predictor.
- **JPEG-LS / LOCO-I 2D prediction** (median predictor + context + Golomb) over the electrode-grid × time "image" — a cheap, well-known way to exploit 2D spatial structure losslessly.
- **richer cross-channel topologies** — beyond single best-neighbour: multi-neighbour / best-correlated-partner selection, and **lossless integer inter-channel decorrelation** (integer matrixing / lifting, à la MPEG-4 ALS multichannel and Dolby TrueHD/MLP).

## Watch-list (survey only; expect to fail the cost gate today — track anyway)

Convolutional autoencoders / near-lossless-with-residual, integer discrete flows (IDF), L3C, VAE-DCT entropy models. These go in `SURVEY.md` for the record; **never promote one into the registry without explicit human approval.**
