# `research/` — the lossless-compression search layer

The **search layer** on top of the Stage-0 harness (`../COMPRESSION_HARNESS_README.md`).
It reuses `host_tools/bench_lossless.py` + `host_tools/embedded_codec.py`; it never
replaces them. Plan and non-negotiables live in
`../COMPRESSION_RESEARCH_AGENT_PROMPT.md`.

## Files

| file | role | status |
|---|---|---|
| `00_STATE.md` | Stage 0 orientation + current ratio bar (human gate) | done |
| `01_STAGE1.md` | Stage 1 record (registry + cost model) | done |
| `04_STAGE4.md` | Stage 4 record (search + ablations) | done |
| `embedded_cost.py` | `embedded_ok` hard gate + continuous Pareto cost | done |
| `registry.py` | uniform `encode`/`decode` + metadata over every codec | done |
| `datasets.py` | Stage 2 corpus loaders (Hyser, OTB, CapgMyo, CEMHSEY, synthetic) | done |
| `bench.py` | Stage 3 registry-driven benchmark → `results/*.csv` | done |
| `search.py` | Stage 4 hill-climb over the design space | done |
| `../SURVEY.md` | Stage 5 survey — cost-filtered ranked candidates (proposes only) | done, refreshed each cycle |
| `LEADERBOARD.md` | Stage 6 **current-snapshot** best-per-category + Pareto front (overwritten each cycle) | done |
| `CYCLE_LOG.md` | **append-only** one-row-per-cycle index — read this to see progress *over time* | done |
| `../experiments/NNN_slug.md` | full per-cycle record (hypothesis, commands, verifier verdicts) | done |

**Where to look for progress across cycles:** `CYCLE_LOG.md` (index) →
`../experiments/NNN_slug.md` (detail for one cycle). `LEADERBOARD.md` only shows the
*current* best; it is not a history.

## Run

```bash
# recreate the venv the workflow + verifier hook expect (gitignored)
python3 -m venv .venv && ./.venv/bin/pip install numpy zstandard

# every registered codec: bit-exact round-trip + cost score (the hook runs this)
PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest

# the embedded cost model's targets + a worked example
./.venv/bin/python research/embedded_cost.py
```

## Contract

- **Lossless only** — every codec round-trips bit-exact or the run fails loudly.
  Enforced on every edit by `.claude/hooks/verify_codec.py` (PostToolUse), which
  re-runs `registry.py --selftest` and blocks (exit 2) on any mismatch.
- **`embedded_ok` is a hard gate** — a codec that fails it can never be ranked a
  win. Rank on the ratio-vs-cost Pareto front, never ratio alone.
- **Real data decides** — synthetic (`gen_neural_mem.py --spatial-corr`) is for
  sweeps only; headline numbers come from real data via `bench_lossless.py`.
