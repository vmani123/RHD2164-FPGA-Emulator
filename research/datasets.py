#!/usr/bin/env python3
"""
datasets.py  -  The corpus abstraction for the lossless-compression search
(Stage 2 of COMPRESSION_RESEARCH_AGENT_PROMPT.md).

Normalizes every data source to `int16 [channels, samples]` + a physical grid
map + a manifest entry (name, source, license, fs, shape, sha256, spatial ceiling)
so the benchmark can iterate `codec x dataset` reproducibly. It **reuses**
`host_tools/gen_neural_mem.py` (synthetic) and `host_tools/load_wfdb.py`
(PhysioNet WFDB) rather than re-implementing loaders.

Two source classes:
  * synthetic  - always available, reproducible from a seed. The `--spatial-corr`
                 knob makes this the ONLY source for controlled sweeps. Never a
                 headline (non-negotiable #3).
  * wfdb       - real PhysioNet format-16 records (Hyser HD-sEMG is the primary
                 real set). Download-on-demand, cached by content hash.

Real datasets are download-on-demand and are NOT committed (see .gitignore /
compression_spec/datasets.md). Some hosts (physionet.org, zenodo.org) may be
blocked by the environment's network policy; loaders that can't reach their
source raise a clear error and are marked `available=False` in the manifest so
the corpus degrades gracefully to what's reachable.

Usage:
    python3 research/datasets.py --list           # show the declared corpus
    python3 research/datasets.py --report          # build available sets, write manifest
    python3 research/datasets.py --report --json results/datasets_manifest.json
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys

import numpy as np

LSB_UV = 0.195   # RHD2164 ADC step (uV/LSB) -- quantize real uV recordings to counts

HOST = os.path.join(os.path.dirname(__file__), "..", "host_tools")
sys.path.insert(0, HOST)
import gen_neural_mem as gnm  # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), "..", "sim_data", "corpus")


# ---------------------------------------------------------------------------
# Dataset specs
# ---------------------------------------------------------------------------
class Dataset:
    def __init__(self, name, kind, grid, fs, license, source, params=None,
                 available=None, note=""):
        self.name = name
        self.kind = kind              # 'synthetic' | 'wfdb'
        self.grid = tuple(grid)       # (rows, cols)
        self.fs = float(fs)
        self.license = license
        self.source = source
        self.params = params or {}
        self.note = note
        self._available = available   # None => probe on load

    # -- loaders return (x int16 [C, N], grid) ------------------------------
    def load(self, max_samples=0):
        if self.kind == "synthetic":
            x = self._load_synthetic()
        elif self.kind == "wfdb":
            x = self._load_wfdb()
        elif self.kind == "otb":
            x = self._load_otb()
        else:
            raise ValueError(f"unknown dataset kind {self.kind!r}")
        if max_samples and x.shape[1] > max_samples:
            x = x[:, :max_samples]
        return x.astype(np.int16), self.grid

    def _load_synthetic(self):
        p = self.params
        rows, cols = self.grid
        channels = rows * cols
        rng = np.random.default_rng(p.get("seed", 0))
        n = int(round(p["seconds"] * self.fs))
        sig = gnm.gen_neural(
            channels, n, self.fs, rng, (rows, cols),
            noise_rms=p.get("noise_rms", 12.0),
            firing_rate_hz=p.get("firing_rate_hz", 50.0),
            spatial_corr=p["spatial_corr"],
            spike_amp_counts=p.get("spike_amp", 350.0),
            prop_velocity=p.get("prop_velocity", 0.0))
        return gnm.to_int16(sig)

    def _load_wfdb(self):
        import load_wfdb  # noqa: E402  (imports urllib lazily)
        os.makedirs(CACHE, exist_ok=True)
        base = os.path.join(CACHE, self.name)
        if not os.path.exists(base + ".dat"):
            try:
                load_wfdb.fetch(self.source, base)
            except Exception as e:
                raise RuntimeError(
                    f"cannot download {self.name} from {self.source}: {e} "
                    f"(network policy may block this host)")
        x, fs, _labels = load_wfdb.read_wfdb16(base)
        chans = self.grid[0] * self.grid[1]
        off = self.params.get("chan_offset", 0)
        x = x[off:off + chans]
        if x.shape[0] < chans:
            raise RuntimeError(f"{self.name}: record has {x.shape[0]} < {chans} ch")
        # zero-mean per channel (RHD-with-DSP-HPF-like), keep int16
        x = np.clip(x - np.round(x.mean(axis=1, keepdims=True)),
                    -32768, 32767).astype(np.int16)
        return x

    @staticmethod
    def _otb_matpath():
        """Locate the real HD-sEMG sample bundled with the pip-installed openhdemg
        package (no blocked host needed -- it ships in the wheel)."""
        spec = importlib.util.find_spec("openhdemg")
        if spec is None or not spec.submodule_search_locations:
            return None
        p = os.path.join(spec.submodule_search_locations[0],
                         "library", "decomposed_test_files", "otb_testfile.mat")
        return p if os.path.exists(p) else None

    def _load_otb(self):
        import scipy.io as sio
        path = self.params.get("matpath") or self._otb_matpath()
        if not path:
            raise RuntimeError("openhdemg not installed; `pip install openhdemg scipy` "
                               "to get its bundled real HD-sEMG sample")
        m = sio.loadmat(path, squeeze_me=True, struct_as_record=False)
        data = np.asarray(m["Data"], dtype=np.float64)
        nch = self.params.get("emg_channels", 64)
        emg = data[:, :nch]                              # cols 0..63 = electrode grid
        # zero-mean per channel + quantize to RHD2164 counts (real uV -> int16)
        q = np.clip(np.round((emg - emg.mean(0)) / LSB_UV), -32768, 32767)
        return q.T.astype(np.int16)                       # [channels, samples]

    def available(self):
        if self.kind == "synthetic":
            return True
        if self._available is not None:
            return self._available
        if self.kind == "otb":
            return self._otb_matpath() is not None
        # a wfdb set is available if already cached; we do not probe the network
        # here (that happens on load) -- treat "cached" as available.
        return os.path.exists(os.path.join(CACHE, self.name + ".dat"))


# ---------------------------------------------------------------------------
# The declared corpus (compression_spec/datasets.md)
# ---------------------------------------------------------------------------
def corpus():
    sets = []
    # Synthetic sweep set: geometry-matched 8x16, spanning the spatial-corr knob.
    # Always available; for controlled sweeps only, never a headline.
    for sc in (0.0, 0.3, 0.6, 0.9):
        sets.append(Dataset(
            name=f"synth_sc{sc:.1f}", kind="synthetic", grid=(8, 16), fs=30000,
            license="generated", source="gen_neural_mem.py",
            params=dict(seconds=0.5, spatial_corr=sc, seed=1),
            note="controlled spatial-correlation sweep (sweeps only)"))

    # Real HD-sEMG that is reachable HERE: the OTB GR08MM1305 sample bundled with
    # the pip-installed openhdemg package (64-ch 5x13 electrode grid @ 2048 Hz,
    # vastus lateralis, ~32 s). No blocked host needed -- ships in the wheel.
    # Channel index runs down each column of 13, so grid (5,13)/cols=13 makes the
    # left-neighbour parent (g-1) the nearest physical (within-column) electrode.
    sets.append(Dataset(
        name="otb_hdsemg_vl", kind="otb", grid=(5, 13), fs=2048,
        license="openhdemg sample (CC-BY-4.0)", source="pip:openhdemg (bundled otb_testfile.mat)",
        params=dict(emg_channels=64),
        note="REAL 64-ch HD-sEMG grid; reachable via PyPI (physionet-free)"))

    # Real primary: Hyser HD-sEMG (PhysioNet, CC-BY). 128 of 256 ch @ 2048 Hz.
    sets.append(Dataset(
        name="hyser_1dof_f1_s1", kind="wfdb", grid=(8, 16), fs=2048,
        license="CC-BY (PhysioNet hd-semg 1.0.0)",
        source=("https://physionet.org/files/hd-semg/1.0.0/1dof_dataset/"
                "subject01_session1/1dof_raw_finger1_sample1"),
        params=dict(chan_offset=0),
        note="primary real HD-sEMG; force-varying subset ideal for xchan-vs-force"))

    # ADD targets from datasets.md that need a format-specific reader AND a
    # reachable host. Declared here so the manifest records them as pending;
    # implement the reader when the network policy permits the download.
    sets.append(Dataset(
        name="capgmyo_dbA", kind="wfdb", grid=(8, 16), fs=1000,
        license="ZJU CapgMyo (research use)", source="TODO:capgmyo-mat-reader",
        available=False,
        note="geometry-matched 8x16; needs a .mat reader + reachable host (Stage 2 TODO)"))
    sets.append(Dataset(
        name="cemhsey_320", kind="wfdb", grid=(16, 20), fs=2048,
        license="CEMHSEY", source="TODO:cemhsey-reader", available=False,
        note="320-ch stress case; needs reader + reachable host (Stage 2 TODO)"))
    return sets


# ---------------------------------------------------------------------------
# Per-dataset report (compression_spec/datasets.md "required" fields)
# ---------------------------------------------------------------------------
def spatial_ceiling(x, grid):
    """Return (mean |corr| to 4-neighbours, mean best-neighbour R^2). This is the
    upper bound on what a 1-neighbour cross-channel predictor can gain -- and, per
    datasets.md, it OVERSTATES the lossless ceiling on spiky data (report achieved
    xchan gain from the benchmark, not this)."""
    rows, cols = grid
    channels = x.shape[0]
    gr, gc = gnm.channel_rowcol(channels, cols)
    xf = x.astype(np.float64)
    xf = xf - xf.mean(axis=1, keepdims=True)
    std = xf.std(axis=1) + 1e-12
    corrs, r2s = [], []
    for ch in range(channels):
        nb = []
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            r, c = gr[ch] + dr, gc[ch] + dc
            if 0 <= r < rows and 0 <= c < cols:
                g = r * cols + c
                if g < channels:
                    nb.append(g)
        if not nb:
            continue
        cc = [float(np.dot(xf[ch], xf[g]) / (xf.shape[1] * std[ch] * std[g])) for g in nb]
        corrs.append(float(np.mean(np.abs(cc))))
        r2s.append(float(max(c * c for c in cc)))
    return (float(np.mean(corrs)) if corrs else 0.0,
            float(np.mean(r2s)) if r2s else 0.0)


def describe(ds, max_samples=15000):
    x, grid = ds.load(max_samples=max_samples)
    noise_rms = float(np.median(np.abs(np.diff(x.astype(np.float64), axis=1)))
                      / 1.349)  # robust per-sample-diff noise proxy (MAD/1.349)
    mcorr, r2 = spatial_ceiling(x, grid)
    sha = hashlib.sha256(x.tobytes()).hexdigest()[:16]
    return dict(name=ds.name, kind=ds.kind, grid=list(grid), fs=ds.fs,
                channels=int(x.shape[0]), samples=int(x.shape[1]),
                duration_s=round(x.shape[1] / ds.fs, 3), noise_rms=round(noise_rms, 2),
                neigh_abs_corr=round(mcorr, 4), best_neigh_r2=round(r2, 4),
                sha256_16=sha, license=ds.license, source=ds.source, note=ds.note)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list the declared corpus")
    ap.add_argument("--report", action="store_true",
                    help="load available sets, print report, write manifest")
    ap.add_argument("--max-samples", type=int, default=15000)
    ap.add_argument("--json", default="results/datasets_manifest.json")
    args = ap.parse_args()

    sets = corpus()
    if args.list or not args.report:
        print(f"declared corpus ({len(sets)} datasets):")
        for ds in sets:
            av = "available" if ds.available() else "pending (unreachable/TODO)"
            print(f"  {ds.name:<20}{ds.kind:<11}grid={ds.grid}  fs={ds.fs:.0f}  "
                  f"[{av}]  {ds.note}")
        if not args.report:
            return

    print("\nbuilding available datasets + spatial-correlation ceiling ...\n")
    print(f"{'dataset':<20}{'ch':>4}{'samp':>7}{'fs':>7}{'noiseRMS':>9}"
          f"{'|corr|':>8}{'bestR2':>8}  hash")
    print("-" * 78)
    manifest = []
    for ds in sets:
        if not ds.available():
            manifest.append(dict(name=ds.name, kind=ds.kind, available=False,
                                 license=ds.license, source=ds.source, note=ds.note))
            print(f"{ds.name:<20}  -- pending ({ds.note})")
            continue
        try:
            row = describe(ds, args.max_samples)
        except Exception as e:
            manifest.append(dict(name=ds.name, kind=ds.kind, available=False,
                                 error=str(e), source=ds.source))
            print(f"{ds.name:<20}  -- FAILED: {e}")
            continue
        row["available"] = True
        manifest.append(row)
        print(f"{row['name']:<20}{row['channels']:>4}{row['samples']:>7}"
              f"{row['fs']:>7.0f}{row['noise_rms']:>9.2f}{row['neigh_abs_corr']:>8.3f}"
              f"{row['best_neigh_r2']:>8.3f}  {row['sha256_16']}")

    os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
    with open(args.json, "w") as f:
        json.dump(manifest, f, indent=2)
    n_ok = sum(1 for m in manifest if m.get("available"))
    print(f"\nwrote {args.json}  ({n_ok}/{len(manifest)} datasets available here)")
    print("note: |corr|/R2 are the spatial CEILING and overstate the lossless "
          "ceiling on spiky data; the benchmark reports ACHIEVED xchan gain.")


if __name__ == "__main__":
    main()
