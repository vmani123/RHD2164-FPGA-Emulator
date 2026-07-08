# Compression research — cycle log

**This is the file to read to see progress across cycles.** `LEADERBOARD.md` is a
*snapshot* — it gets overwritten every cycle with only the current best. This file
is **append-only**: one row is added per automated research cycle
(`COMPRESSION_RESEARCH_AGENT_PROMPT.md`) and never removed or rewritten. Full detail
for each cycle (hypothesis, exact commands, verifier transcripts) lives in
`experiments/NNN_slug.md`, linked from the row.

| # | cycle date | branch | candidate tried | real dataset(s) | measured ratio | vs. prior best | embedded_ok | verifier verdict | promoted? | experiment record | PR |
|--:|---|---|---|---|---|---|:--:|---|:--:|---|---|
