#!/usr/bin/env bash
# bootstrap.sh -- idempotent environment setup for the compression research loop.
#
# The cloud session is ephemeral: .venv and apt packages do NOT survive. Run this
# at the start of any new session (or from a SessionStart hook, or the weekly
# Routine) to rebuild everything the harness + verifier hook + benchmark need.
#
#   ./research/bootstrap.sh
#
# Safe to re-run. Installs: python venv + numpy/scipy/zstandard/mtscomp/tqdm and
# openhdemg (only for its bundled REAL HD-sEMG sample -- datasets.py reads the
# .mat directly, it never imports the openhdemg GUI module). Also flac/wavpack
# CLIs for the reference bar (best-effort via apt).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== python venv + deps =="
python3 -m venv .venv 2>/dev/null || true
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q numpy scipy zstandard mtscomp tqdm openhdemg

echo "== reference-bar CLIs (best effort) =="
if ! command -v flac >/dev/null 2>&1 || ! command -v wavpack >/dev/null 2>&1; then
  apt-get install -y -q flac wavpack >/dev/null 2>&1 || \
    echo "  (flac/wavpack unavailable; zstd/lzma/gzip/mtscomp reference bar still works)"
fi

echo "== verify: codec round-trips + real dataset reachable =="
export PYTHONPATH=host_tools
./.venv/bin/python research/registry.py --selftest | tail -1
./.venv/bin/python research/datasets.py --list | grep -E "otb_hdsemg_vl|available" || true
echo "bootstrap OK"
