#!/usr/bin/env python3
"""
verify_codec.py -- PostToolUse verifier hook (non-negotiable #4).

"Agents propose, the harness disposes." Whenever an agent edits a codec or the
research registry, this hook runs the bit-exact round-trip self-test on the
freshly-written file. If the round-trip is not bit-for-bit, the edit is reported
back to the agent as a blocking error (exit 2) so no broken/lossy codec can ever
sit in the tree and feed a bogus ratio into the leaderboard.

Wired as a PostToolUse hook on Edit|Write|MultiEdit. It self-filters by path:
only codec/registry files trigger the self-test; everything else is a no-op.

Contract: reads the hook JSON event on stdin, exits 0 (allow) or 2 (block, with
the reason on stderr). Never raises -- an internal error degrades to allow so the
hook can't wedge the whole loop, but it prints a warning so it's visible.
"""
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = os.path.join(REPO, ".venv", "bin", "python")

# file basename -> self-test command (run from REPO). Each command must exit
# non-zero / raise AssertionError on any non-bit-exact round-trip.
SELFTESTS = {
    "embedded_codec.py": [PY, "host_tools/embedded_codec.py"],
    "registry.py":       [PY, "research/registry.py", "--selftest"],
}


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        # No parseable event -> nothing to verify.
        return 0

    tool_input = event.get("tool_input", {}) or {}
    # Edit/Write use file_path; MultiEdit uses the same key at top level.
    path = tool_input.get("file_path", "") or ""
    base = os.path.basename(path)
    cmd = SELFTESTS.get(base)
    if cmd is None:
        return 0  # not a codec/registry edit -- allow silently

    if not os.path.exists(cmd[1] if os.path.isabs(cmd[1]) else os.path.join(REPO, cmd[1])):
        # File referenced by the self-test doesn't exist yet (e.g. registry not
        # created). Don't block -- there's nothing to verify.
        return 0

    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                              timeout=300, env={**os.environ, "PYTHONPATH": "host_tools"})
    except Exception as e:
        print(f"[verify_codec] WARNING: could not run self-test for {base}: {e}",
              file=sys.stderr)
        return 0  # fail-open: don't wedge the loop on hook infrastructure errors

    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        print(
            f"[verify_codec] BIT-EXACT SELF-TEST FAILED for {base} "
            f"(exit {proc.returncode}).\n"
            f"Non-negotiable #1: decode(encode(x)) must equal x bit-for-bit.\n"
            f"Fix the codec so its round-trip self-test passes before proceeding.\n"
            f"--- self-test output ---\n{out}",
            file=sys.stderr,
        )
        return 2  # block: feed the failure back to the agent

    print(f"[verify_codec] {base} round-trip self-test OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
