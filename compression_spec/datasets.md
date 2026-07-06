# Data corpus — reference

Referenced by `../COMPRESSION_RESEARCH_AGENT_PROMPT.md` (Stage 2). Loaders normalize every source to `int16 [channels, samples]` + a physical **grid map**, with a manifest recording version, hash, license, and access notes. **Do not commit raw datasets** — download-on-demand, cache by hash. (Claude Code in this repo already has PhysioNet network access.)

**Already present:** Hyser is downloaded (`raw_data/hyser.*`, `sim_data/real_hyser.npy`) via `host_tools/load_wfdb.py`. The **ADD targets** below are CapgMyo, CEMHSEY, and a broadband-neural set.

Cover **both regimes the device targets**: surface EMG (128–320 ch, ~1–2 kS/s) and neural rate (128 ch, up to 30 kS/s).

| Source | Ch | Rate | Role | Notes / access |
|---|---|---|---|---|
| Synthetic (`gen_neural_mem.py`) | 128 (8×16) | any | controlled sweeps | the **only** source with a `--spatial-corr` knob; used for parameter sweeps, never for headline claims |
| **Hyser** (PhysioNet) — *present* | 256 (4× 8×8) | 2048 Hz | **primary HD-EMG** | 20 subjects, CC-licensed; its **force-varying** subset is ideal for ratio/cross-channel-gain **vs. contraction level** |
| **CapgMyo** (ZJU) — *add* | 128 (8×16) | ~1 kHz | **geometry-matched** | 8×16 array matches the default grid exactly → cleanest cross-channel test |
| **CEMHSEY** — *add* | 320 | HD-sEMG | high-channel stress | 11 consecutive days; the "scale channels up" regime |
| **Neuralink Challenge data** — *add* | broadband | ~20 kHz | neural-rate floor | sets the hard ~3.4× reference for broadband neural |
| GRABMyo / DANDI (optional) | — | — | extra EMG / intracortical | second sources if needed |

**Per-dataset report (required):** channels, rate, duration, per-channel noise RMS, and the **spatial-correlation ceiling** — how much of a channel's residual variance a physical neighbour explains. That number is the maximum a cross-channel predictor can gain; it's the honest upper bound on the headline lever, and it should track contraction force on Hyser. (On the present Hyser sample, neighbour |corr| ≈ 0.90 / R² ≈ 0.92, yet achieved xchan gain is only +11% — because variance-R² overstates the *lossless* ceiling on spiky data. Report the achieved gain, not R².)
