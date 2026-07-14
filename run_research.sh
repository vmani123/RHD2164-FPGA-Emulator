#!/usr/bin/env bash
# run_research.sh -- headless driver for the lossless-compression research loop.
#
# Runs the orchestrator agent non-interactively (`claude -p`) for a bounded number
# of cycles. Deterministic tools remain ground truth; the PostToolUse hook enforces
# bit-exactness on every codec edit. Human gates (after Stage 0 and Stage 2) are
# respected by the orchestrator's own stop conditions -- this script does not push
# past them.
#
# Usage:
#   ./run_research.sh                 # one orchestrator cycle, default turn budget
#   ./run_research.sh 8               # allow up to 8 tool turns this run
#   ./run_research.sh 8 "Test JPEG-LS 2D on real Hyser vs delta+Rice"
#                                     # seed the cycle with a specific hypothesis
#
# Requires the `claude` CLI on PATH and this repo's .venv + host_tools present.
set -euo pipefail

cd "$(dirname "$0")"

MAX_TURNS="${1:-6}"
HYPOTHESIS="${2:-}"
LOG_DIR="experiments/headless"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/run_${STAMP}.log"

PROMPT="You are the orchestrator. Run ONE research cycle of the lossless-compression \
loop by invoking the committed workflow: Workflow({ name: 'compression-cycle' }). That \
workflow encodes the canonical Survey -> Implement -> Measure -> Verify -> Analyze \
pipeline over 2-3 genuinely distinct candidates (per COMPRESSION_RESEARCH_AGENT_PROMPT.md \
and the .claude/agents/*.md role defs), with an adversarial two-verifier gate per \
candidate. Do not hand-roll a one-candidate cycle. Read state first \
(research/LEADERBOARD.md, latest results/*.csv, recent experiments/*.md), then run the \
workflow, then commit the result. Respect every non-negotiable. STOP at the Stage 0 / \
Stage 2 human-review gates and stop if there is no ratio gain over the last N cycles with \
the Pareto front unchanged. Never invent a performance number; never promote a watch-list \
method."

if [[ -n "$HYPOTHESIS" ]]; then
  PROMPT="$PROMPT

Seed hypothesis for this cycle: $HYPOTHESIS"
fi

echo "== research run $STAMP  (max-turns=$MAX_TURNS) ==" | tee "$LOG"
[[ -n "$HYPOTHESIS" ]] && echo "seed: $HYPOTHESIS" | tee -a "$LOG"

# --agents flag pins the orchestrator definition; --max-turns bounds the run.
claude -p "$PROMPT" \
  --agents orchestrator \
  --max-turns "$MAX_TURNS" \
  --permission-mode acceptEdits \
  2>&1 | tee -a "$LOG"

echo "== done; transcript at $LOG ==" | tee -a "$LOG"
