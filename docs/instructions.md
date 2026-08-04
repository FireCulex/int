## Memory (`int`)

This project uses the `int` MCP tool for persistent memory across sessions. Policy:

- **Search first, always.** Before grepping or reading source to answer an
  architecture/behavior question, run `int.search` for the project. Only fall back to
  grep/read for what the search doesn't cover.
- **Save when re-discovery cost > save cost.** If reconstructing a fact next session
  would take more tool calls than saving it now, save it. See the `int` init workflow
  doc for the full bootstrap procedure.
- **Save negative facts, not just positive ones.** When a function conspicuously does
  *not* do something a sibling function does (e.g. "X clears these fields, Y does
  not"), that omission is itself worth its own memory — it will not be inferable from
  a positive-only description of what X does, and grep/read is the expensive way to
  rediscover it. See the seek/loop-restart entry below for a concrete example.
- **Include exact line numbers in `error-solution` memories** when a bug is tied to
  specific functions — it saves a grep+read pass to relocate them later.
- **In read-only/plan mode, end every answer that involved source reads beyond what
  `int.search` returned with a mandatory closing section, titled `Not yet in memory:`.**
  This is not discretionary — it runs every time, not just when the answer "feels"
  non-trivial:
  - Trigger: if the response included **any** `read`/`grep`/file-exploration tool
    call, the section is required, regardless of how the model judges the answer's
    importance.
  - Content: list each fact surfaced by those reads that is not already present in
    the `int.search` results from this session — one line per fact, terse, ready to
    paste into `int.add`. Do not restate facts that were already in the search
    results.
  - If every fact surfaced by the extra reads was already covered by the search
    results, write `Not yet in memory: none — fully covered by existing memories.`
    Do not omit the section; an explicit "none" is still required output, since a
    silently-omitted section is indistinguishable from a missed insight.
  - This section is separate from, and does not replace, the general non-trivial-
    synthesis flag below — it's a mechanical checklist, not a judgment call, precisely
    because judgment calls get skipped when the rest of the answer runs long.
- **Beyond the mechanical list, call out *why* a surfaced fact matters when a bare
  list item under-explains it.** Don't let a genuinely important finding get lost as
  an unremarked line in the `Not yet in memory:` list — e.g. "this ordering constraint
  (snapshot stats before `resetScoring()`, not after) took several reads to work out
  and isn't obvious from the code alone; worth flagging clearly, not just listing."
  Rule of thumb: if reaching the fact took several file reads or greps beyond what
  memory already covered, it likely deserves more than a one-line mention.
