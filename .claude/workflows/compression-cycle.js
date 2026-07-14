// compression-cycle.js -- the canonical, version-controlled ONE-CYCLE workflow for
// the RHD2164 / HD-EMG lossless-compression research loop.
//
// Invoke with:  Workflow({ name: 'compression-cycle' })
//
// This is the durable definition of a research cycle: Survey -> Implement ->
// Measure -> Verify -> Analyze, dispatching 2-3 genuinely distinct candidates per
// cycle (per COMPRESSION_RESEARCH_AGENT_PROMPT.md and the .claude/agents/*.md
// role definitions), NOT one. The prior loose one-candidate script is superseded
// by this file so future cycles don't have to re-derive the structure or re-litigate
// the candidate count.
//
// Non-negotiables baked in (see COMPRESSION_RESEARCH_AGENT_PROMPT.md):
//   * lossless only (decode(encode(x))==x, asserted; PostToolUse hook blocks lossy)
//   * embedded_ok is a HARD gate; rank on the ratio-vs-cost Pareto front, never ratio
//   * REAL data decides (all four real sets are now cached under sim_data/corpus_npz/
//     and load offline); synthetic is sweeps only; any real ratio > 6x => stop/report
//   * no headline number from an agent's reasoning -- only the harness prints numbers
//   * adversarial Verify: TWO independent verifiers per candidate; promote only on a
//     unanimous PROMOTE + a real-data ratio that beats the current best. Split => hold.
//   * every agent() runs on opus explicitly (frontmatter model tiers are not auto-
//     loaded in the headless/cron env, so an unset model silently weakens the run).
//
// Every agent() uses agentType 'general-purpose' + the role text pasted inline +
// model:'opus'. This is deliberately portable: it does NOT depend on the custom
// surveyor/implementer/verifier/analyst agent types being registered in the session
// (they load inconsistently in headless runs), while still forcing the strong model.

export const meta = {
  name: 'compression-cycle',
  description: 'One RHD2164 lossless-compression research cycle: survey -> implement 2-3 distinct candidates -> measure on real+synthetic -> adversarial double-verify each -> analyze/update leaderboard',
  phases: [
    { title: 'Survey',    detail: 'refresh SURVEY.md; return 2-3 genuinely distinct embeddable candidates', model: 'opus' },
    { title: 'Implement', detail: 'add each candidate to research/registry.py (sequential), bit-exact selftest', model: 'opus' },
    { title: 'Measure',   detail: 'run research/bench.py on all real + synthetic sets; search.py on the promising ones', model: 'opus' },
    { title: 'Verify',    detail: 'two independent adversarial verifiers PER candidate; unanimous PROMOTE to advance', model: 'opus' },
    { title: 'Analyze',   detail: 'attribute each candidate, per-candidate retire call, update leaderboard / cycle log', model: 'opus' },
  ],
}

const DATE = (args && args.date) || 'this-cycle'   // pass {date:'YYYY-MM-DD'} to stamp; scripts have no clock
const BRANCH = (args && args.branch) || `compression-cycle-${DATE}`
const N_MIN = 2, N_MAX = 3

const SURVEYOR_ROLE = `You are the SURVEYOR: a web/read-only scout for lossless multichannel biosignal
compression methods (128-ch RHD2164 / HD-EMG node, STM32H745 Cortex-M7 and Spartan-7
XC7S25; integer/fixed only; causal/streaming; must beat per-channel FLAC by exploiting
cross-channel spatial correlation). You find methods; you do NOT implement or measure them.

Read first: compression_spec/candidates.md, compression_spec/cost_model.md, the current
SURVEY.md (extend, don't duplicate), research/CYCLE_LOG.md (past cycles), the experiments/
records it links to, and research/registry.py's retired codecs (run
\`PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest\` which prints each
codec with a RETIRED tag, or grep retired=True). HARD requirement: a method already
implemented and conclusively verified Pareto-dominated must NOT be re-proposed as new. If a
retired idea deserves another look (a follow-up variant that could overcome the rejection
reason), say so explicitly and name the prior attempt + why this version differs.

Return 2-3 ranked, GENUINELY DISTINCT contenders -- distinct in MECHANISM (e.g. a different
channel-pairing topology vs a different entropy back-end), not parameter variants of each
other. For each: name + one-line mechanism, why it might beat per-channel FLAC (cross-channel
spatial lever vs. just better temporal modeling), embeddability verdict against cost_model.md,
and source citation (label paper-reported ratios "(paper-reported, unverified here)").

Propose only. Never edit codecs, run benchmarks, or state a measured ratio. EDIT SURVEY.md
(Edit/Write) to record this cycle's findings in the file's existing style (keep existing
content; refresh the ranked table + the cycle-log note at the top). Then report your ranked
slate back.`

const IMPLEMENTER_ROLE = `You are the IMPLEMENTER. You turn ONE codec hypothesis into working, bit-exact, embeddable
code added to research/registry.py (match the style of the existing Codec(...) registrations
and encode/decode helpers). EXACTLY ONE codec this invocation -- no scope creep, no touching
other codecs, no refactors, and NEVER touch rtl/ or sim/ (emulator RTL is off-limits). If
research/registry.py already has a codec added earlier THIS cycle, build on the file as it
currently stands -- do not overwrite or revert another candidate.

Read first: host_tools/embedded_codec.py (existing delta+Rice / LMS+Rice / +xchan codecs --
match their integer-exact style), research/registry.py (uniform encode/decode + metadata
interface + retired ledger -- never re-implement a retired mechanism), compression_spec/
{candidates,cost_model}.md.

Hard rules (a PostToolUse hook re-runs the self-test on every edit and BLOCKS with exit 2 on
any non-bit-exact round-trip -- fix it, don't work around it):
1. Lossless, integer, streaming. decode(encode(x)) == x bit-for-bit for int16 [channels,
   samples]. Integer/fixed-point only -- NO float in the codec path. Causal, bounded block.
2. Self-test gate: \`PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest\`
   MUST print "ALL round-trips bit-exact" with your new codec listed OK (not FAIL/RETIRED).
3. Encoder and decoder a matched pair: any adaptation updates identically on both sides from
   causally-available data only.
4. Carry cost metadata (ops/sample-ch, per-channel state bytes, integer-only, causal/block).

Workflow: restate the one hypothesis; make the minimal edit; run the self-test until bit-exact;
if you cannot reach bit-exactness after a genuine effort, REVERT your edit (git checkout the
touched files) so the tree is clean and report the failure honestly. Do NOT run bench --
measurement is a separate phase. Report the exact Codec("...") name you registered, whether the
self-test passed, the exact command + full output, and a one-line cost-metadata summary.`

const VERIFIER_ROLE = `You are a VERIFIER: an independent gatekeeper before any codec is promoted to LEADERBOARD.md.
You trust nothing you didn't reproduce yourself. You do NOT edit codecs -- you audit only. You
run INDEPENDENTLY of any other verifier; do not assume anyone else's numbers.

Read first: compression_spec/cost_model.md (embedded_ok gate + cost formula),
research/embedded_cost.py, research/registry.py for the codec under review.

Audit checklist -- RUN it, don't reason it:
1. Bit-exact round-trip, from scratch: \`PYTHONPATH=host_tools ./.venv/bin/python
   research/registry.py --selftest\`. Any mismatch / codec missing / FAIL => REJECT.
2. Re-measure the ratio yourself on REAL data with the ground-truth tool -- run
   \`PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets otb_hdsemg_vl
   hyser_1dof_f1_s1 --csv <a scratch csv path>\` -- do NOT trust any handed-to-you CSV. Confirm
   the codec's ratio (and, if it has an xchan lever, cross-channel gain) reproduce.
3. Cost gate: confirm embedded_ok + cost are consistent with cost_model.md -- integer/fixed
   only (float => disqualified), causal + bounded block, state fits SRAM/BRAM, ops/sample-ch
   fit the rate budget. Not embedded_ok => cannot be promoted regardless of ratio.
4. Sanity: any lossless ratio > ~6x on realistic broadband REAL data => leak/degenerate =>
   REJECT and report. (Do not run sim/run_sim.sh; RTL is not touched by codec cycles.)

Verdict: PROMOTE or REJECT with reproduced evidence pasted (exact commands + outputs). When in
doubt, REJECT -- a false "win" is worse than a delayed one. NOTE: "PROMOTE" here means the codec
is correct/embeddable/reproducible enough to stay registered; whether it becomes the new
leaderboard BEST is the analyst's separate, stricter call (must beat the current best on REAL
data). Report both: your PROMOTE/REJECT and, explicitly, whether it beats the incumbent.`

const ANALYST_ROLE = `You are the ANALYST: read-only except for the report files listed below; skeptical,
quantitative. You never invent a number that isn't in a CSV or tool output, and you never edit
codec files (research/registry.py encode/decode logic, host_tools/embedded_codec.py) except to
set retired flags as instructed below.

Read first: the latest results/*.csv (this cycle's bench + search CSVs), research/LEADERBOARD.md,
research/CYCLE_LOG.md, recent experiments/*.md, compression_spec/cost_model.md.

For EACH candidate this cycle, deliver and then APPLY as file edits:
1. Attribution -- what moved the ratio and why (temporal predictor vs entropy back-end vs
   cross-channel front-end), tied to the mechanism.
2. Cross-channel gain, isolated, on REAL data if it has a +xchan lever -- achieved %, not a ceiling.
3. Pareto check -- on the ratio-vs-cost front, or dominated by an already-registered codec?
4. Sanity gates -- any ratio > ~6x on real broadband, any FAIL bit-exact, any regression.
5. RETIRE yes/no -- "yes" ONLY if conclusively Pareto-dominated on REAL data (worse ratio AND
   higher cost than an already-registered codec). If yes, set retired=True + a one-line
   retired_reason on that codec's Codec(...) registration in research/registry.py.
6. PROMOTE only a candidate that BOTH (a) beats the current best on REAL data AND (b) had a
   UNANIMOUS PROMOTE from its two verifiers. If a candidate's verifiers split, do NOT promote
   it -- record "verifier split, held for human review" for it in LEADERBOARD.md and CYCLE_LOG.md.
   A non-dominated-but-not-best candidate stays registered (do not retire it just for "not best").
   Do not touch the "-> one codec to port next" headline unless a candidate genuinely displaces it.
7. CYCLE_LOG.md -- append ONE row PER candidate (do not rewrite existing rows) in the existing
   column format: #, cycle date, branch, candidate, real dataset(s), measured ratio, vs prior
   best, embedded_ok, verifier verdict (note splits), promoted?, retired?, experiment record, PR
   (write TBD).
8. experiments/NNN_slug.md -- one new record per candidate (next sequential NNN), following the
   existing style (hypothesis, commands+outputs, both verifier verdicts, decision).
9. 2-3 next hypotheses ranked by expected payoff -> add to SURVEY.md's top cycle-log note (do
   not rewrite the rest of SURVEY.md).

Every number must trace to a specific CSV cell or pasted tool output. Return a tight summary of
what you attributed, decided, and edited per candidate.`

// -------------------------------------------------------------------------------------
// Phase 1 -- Survey: refresh SURVEY.md, return 2-3 distinct candidates.
// -------------------------------------------------------------------------------------
phase('Survey')
const survey = await agent(
  SURVEYOR_ROLE + `

This cycle: refresh SURVEY.md and return your ranked slate of ${N_MIN}-${N_MAX} genuinely
distinct embeddable candidates (proposals only -- no measured numbers, no codec edits outside
SURVEY.md).`,
  { schema: {
      type: 'object',
      properties: {
        checked_retired_ledger: { type: 'boolean' },
        survey_md_updated: { type: 'boolean' },
        candidates: {
          type: 'array', minItems: N_MIN, maxItems: N_MAX,
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              mechanism: { type: 'string' },
              rationale: { type: 'string' },
            },
            required: ['name', 'mechanism', 'rationale'],
          },
        },
        summary: { type: 'string' },
      },
      required: ['candidates', 'summary'],
    },
    phase: 'Survey', model: 'opus', agentType: 'general-purpose', label: 'surveyor' }
)
const candidates = (survey.candidates || []).slice(0, N_MAX)
log(`Survey done. ${candidates.length} candidates: ${candidates.map(c => c.name).join(' | ')}`)

// -------------------------------------------------------------------------------------
// Phase 2 -- Implement: one implementer per candidate, SEQUENTIAL (they share registry.py).
// -------------------------------------------------------------------------------------
phase('Implement')
const implemented = []
for (let i = 0; i < candidates.length; i++) {
  const cand = candidates[i]
  const impl = await agent(
    IMPLEMENTER_ROLE + `

This invocation's single hypothesis (candidate ${i + 1} of ${candidates.length} this cycle):
  Name: ${cand.name}
  Mechanism: ${cand.mechanism}
  Rationale: ${cand.rationale}

Add EXACTLY this one embeddable candidate to research/registry.py. Other candidates from this
same cycle may already be registered -- build on the current file, do not revert them. The gate
is \`PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest\` printing
"ALL round-trips bit-exact" with your codec listed OK.`,
    { schema: {
        type: 'object',
        properties: {
          codec_name: { type: 'string' },
          self_test_passed: { type: 'boolean' },
          self_test_output: { type: 'string' },
          cost_metadata_summary: { type: 'string' },
          notes: { type: 'string' },
        },
        required: ['codec_name', 'self_test_passed', 'notes'],
      },
      phase: 'Implement', model: 'opus', agentType: 'general-purpose',
      label: `implement:${cand.name}` }
  )
  implemented.push({ candidate: cand, ...impl })
  log(`  implemented ${impl.codec_name}: self_test_passed=${impl.self_test_passed}`)
}
const ok = implemented.filter(i => i.self_test_passed && i.codec_name)
if (!ok.length) {
  log('No candidate reached bit-exactness -- stopping before Measure/Verify/Analyze.')
  return { outcome: 'ALL_IMPLEMENT_FAILED', survey, implemented }
}
log(`Implement done. ${ok.length}/${candidates.length} bit-exact: ${ok.map(o => o.codec_name).join(', ')}`)

// -------------------------------------------------------------------------------------
// Phase 3 -- Measure: ONE bench over the whole registry (all new codecs together) on the
// full REAL corpus + synthetic sweeps. search.py on any candidate close to the incumbent.
// Mechanical runner: it pastes raw tool output verbatim; it invents no numbers.
// -------------------------------------------------------------------------------------
phase('Measure')
const measure = await agent(
  `You are a MECHANICAL measurement runner. You do not reason about compression theory or invent
any number -- you run exact commands with Bash and paste their raw stdout verbatim. New codecs
added to research/registry.py this cycle: ${ok.map(o => o.codec_name).join(', ')}.

Step 1 -- run exactly this and capture full raw output (all real sets now load offline from the
committed sim_data/corpus_npz/ cache; Hyser is the PRIMARY real set):
  PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl capgmyo_dba_s1 cemhsey_s1_d1t1 synth_sc0.6 synth_sc0.9 --max-samples 15000 --csv results/cycle_bench.csv

Step 2 -- for EACH new codec whose ratio on the PRIMARY real set hyser_1dof_f1_s1 is within ~10%
of the current best embeddable there (>= ~1.33x), OR beats it, consider it promising. If ANY new
codec is promising, run the hill-climb once and capture its full raw output:
  PYTHONPATH=host_tools ./.venv/bin/python research/search.py --datasets hyser_1dof_f1_s1 otb_hdsemg_vl --max-samples 15000 --csv results/cycle_search.csv
If none is promising, skip step 2 and say so.

Report both raw outputs verbatim. Do not summarize away numbers.`,
  { schema: {
      type: 'object',
      properties: {
        bench_command: { type: 'string' },
        bench_raw_output: { type: 'string' },
        ran_search: { type: 'boolean' },
        search_command: { type: 'string' },
        search_raw_output: { type: 'string' },
        notes: { type: 'string' },
      },
      required: ['bench_command', 'bench_raw_output', 'notes'],
    },
    phase: 'Measure', model: 'opus', agentType: 'general-purpose', label: 'measure-runner' }
)
log(`Measure done. ran_search=${measure.ran_search}`)

// -------------------------------------------------------------------------------------
// Phase 4 -- Verify: per candidate, TWO independent adversarial verifiers. They run in
// parallel across candidates and across the two verifiers; each reproduces from scratch.
// -------------------------------------------------------------------------------------
phase('Verify')
const verifierSchema = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['PROMOTE', 'REJECT'] },
    beats_incumbent_on_real: { type: 'boolean' },
    roundtrip_reproduced: { type: 'boolean' },
    ratio_reproduced: { type: 'string' },
    embedded_ok: { type: 'boolean' },
    evidence: { type: 'string' },
  },
  required: ['verdict', 'roundtrip_reproduced', 'embedded_ok', 'evidence'],
}
const verifyOne = (codecName, tag) => {
  const p = VERIFIER_ROLE + `

Codec under review: "${codecName}" (added to research/registry.py this cycle). Reproduce
everything yourself from scratch. You are one of two independent verifiers for this codec and
cannot see the other's work.`
  return agent(p, { schema: verifierSchema, phase: 'Verify', model: 'opus',
    agentType: 'general-purpose', label: `verify:${codecName}:${tag}` })
}
const verified = await parallel(ok.map(o => () =>
  parallel([() => verifyOne(o.codec_name, 'A'), () => verifyOne(o.codec_name, 'B')])
    .then(([a, b]) => {
      const bothPromote = a && b && a.verdict === 'PROMOTE' && b.verdict === 'PROMOTE'
      const split = a && b && a.verdict !== b.verdict
      return { codec_name: o.codec_name, a, b, bothPromote, split }
    })
))
for (const v of verified.filter(Boolean)) {
  log(`  verify ${v.codec_name}: A=${v.a ? v.a.verdict : 'null'} B=${v.b ? v.b.verdict : 'null'}` +
      ` bothPromote=${v.bothPromote} split=${v.split}`)
}

// -------------------------------------------------------------------------------------
// Phase 5 -- Analyze: attribute each candidate, apply leaderboard / cycle-log / retire edits.
// -------------------------------------------------------------------------------------
phase('Analyze')
const perCandidate = ok.map(o => {
  const v = verified.filter(Boolean).find(x => x.codec_name === o.codec_name) || {}
  return { name: o.codec_name, cost_metadata: o.cost_metadata_summary, impl_notes: o.notes,
           verifierA: v.a ? v.a.verdict : 'AGENT_FAILED', verifierB: v.b ? v.b.verdict : 'AGENT_FAILED',
           bothPromote: !!v.bothPromote, split: !!v.split }
}))
const analyze = await agent(
  ANALYST_ROLE + `

Cycle stamp: date=${DATE}, branch=${BRANCH}. Candidates this cycle (re-read the CSVs yourself for
exact cells; do not re-derive numbers from prose):

${JSON.stringify(perCandidate, null, 2)}

Measure bench command: ${measure.bench_command}
Measure raw bench output:
${measure.bench_raw_output}

${measure.ran_search ? `Search command: ${measure.search_command}\nSearch raw output:\n${measure.search_raw_output}` : 'Search phase skipped (no candidate was promising by the measure rule).'}

Promotion rule (do not override): promote a candidate as a new leaderboard best ONLY if it beats
the current best on REAL data AND its two verifiers were a UNANIMOUS PROMOTE (bothPromote true).
If split is true for a candidate, record "verifier split, held for human review" for it instead
of promoting. Apply all edits (LEADERBOARD.md, CYCLE_LOG.md one row per candidate, per-candidate
retire flags in research/registry.py, experiments/NNN_*.md, SURVEY.md next-hypotheses note).`,
  { schema: {
      type: 'object',
      properties: {
        promoted_any: { type: 'boolean' },
        promoted_names: { type: 'array', items: { type: 'string' } },
        retired_names: { type: 'array', items: { type: 'string' } },
        held_for_review_names: { type: 'array', items: { type: 'string' } },
        leaderboard_updated: { type: 'boolean' },
        cycle_log_updated: { type: 'boolean' },
        summary: { type: 'string' },
      },
      required: ['promoted_any', 'summary'],
    },
    phase: 'Analyze', model: 'opus', agentType: 'general-purpose', label: 'analyst' }
)
log(`Analyze done. promoted_any=${analyze.promoted_any} promoted=${(analyze.promoted_names||[]).join(',')} retired=${(analyze.retired_names||[]).join(',')}`)

return {
  outcome: 'CYCLE_COMPLETE',
  n_candidates: candidates.length,
  n_bit_exact: ok.length,
  survey, implemented, measure,
  verify: verified,
  analyze,
}
