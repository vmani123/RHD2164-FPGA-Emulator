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
| `embedded_cost.py` | `embedded_ok` hard gate + continuous Pareto cost | done |
| `registry.py` | uniform `encode`/`decode` + metadata over every codec | done |
| `datasets.py` | Stage 2 corpus loaders (CapgMyo, CEMHSEY, neural) | todo |
| `search.py` | Stage 4 hill-climb over the design space | todo |
| `survey.py` | Stage 5 → `SURVEY.md` (proposes only) | todo |
| `LEADERBOARD.md` | Stage 6 best-per-category + Pareto front | todo |

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
