# Scheduled-routine prompt — RHD2164 lossless-compression research loop

Paste the block below into the 3-day scheduled routine (replacing the old
"add EXACTLY ONE candidate" prompt). It runs the **committed** workflow, so the
cycle structure — 2–3 candidates, adversarial double-verify, compounding
learnings — lives in version control, not in the scheduler text.

---

Automated cycle of the RHD2164 lossless-compression research loop. You are in a FRESH
session on the `vmani123/RHD2164-FPGA-Emulator` repo — no prior context, so this prompt is
self-contained. Use `ultracode` for this entire cycle.

**SETUP**
1. `git fetch origin`. Base your work on the latest research branch:
   `git checkout -B compression-cycle-$(date +%Y-%m-%d) origin/compression-wip`
   (fall back to `origin/main` if `compression-wip` is gone/merged).
2. Run `./research/bootstrap.sh` to rebuild the ephemeral env (venv + deps + CLIs). The
   real corpus (Hyser, OTB, CapgMyo, CEMHSEY) is now committed parsed under
   `sim_data/corpus_npz/`, so it loads offline — no re-download.
3. Read, in order: `research/INSIGHTS.md` (the distilled, theory-rooted learnings —
   this steers candidate selection), `research/LEADERBOARD.md`, `research/CYCLE_LOG.md`,
   `SURVEY.md`, `COMPRESSION_RESEARCH_AGENT_PROMPT.md`, and `results/*.csv`.

**ONE CYCLE — run the committed workflow (do NOT hand-roll a one-candidate cycle):**
4. Invoke `Workflow({ name: 'compression-cycle', args: { date: '<YYYY-MM-DD today>', branch: '<your cycle branch>' } })`.
   That workflow *is* the canonical cycle: Survey → Implement → Measure → Verify → Analyze
   over **2–3 genuinely distinct candidates**, with an adversarial **two-verifier gate per
   candidate** (promote only on a unanimous PROMOTE that also beats the current best on REAL
   data; split → hold for human review), and it refreshes `research/INSIGHTS.md` with the
   cycle's distilled learning. Every `agent()` runs on the strongest model.
   - Candidates may be published methods **or novel codecs the surveyor designs** by
     combining/extending the existing primitives — welcome **only when rooted in sound
     compression theory** (the mechanism is stated before measuring). Every design still
     faces the non-negotiables below.
5. When the workflow finishes, read its result + the files it edited. Confirm
   `./sim/run_sim.sh` is still green (paste it) — the cycle must not touch RTL.

**NON-NEGOTIABLES** (the workflow enforces these; verify them): lossless only
(`decode(encode(x))==x`, asserted; the PostToolUse hook blocks lossy round-trips);
`embedded_ok` is a HARD gate (rank on the ratio-vs-cost Pareto front, never ratio alone);
**REAL data decides** (synthetic is sweeps only; any real lossless ratio > ~6× ⇒ likely a
leak — stop and report); no headline number from an agent's reasoning (only the harness
prints numbers); never touch the emulator SPI/DDR/timing RTL (keep the sim green).

**FINISH**
6. Commit with a clear message and push the `compression-cycle-<date>` branch
   (`git push -u origin`, retry with backoff on network errors). Open a PR titled
   "Compression cycle <date>" summarizing, **per candidate**: the mechanism tried, its
   real-data ratio vs the current best, whether it was promoted (or held on a verifier
   split), the updated Pareto front, and the new INSIGHTS.md learning. If the cycle produced
   no improvement, still open the PR documenting the negative result (that is useful signal).
   If literally nothing changed, note that and skip the PR.

---

*Note:* if `Workflow({ name: 'compression-cycle' })` ever fails to resolve the name in a
headless run, fall back to
`Workflow({ scriptPath: '.claude/workflows/compression-cycle.js', args: { … } })` — same
committed script.
