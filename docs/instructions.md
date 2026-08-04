## Memory (`int`)

This project uses the `int` MCP tool for persistent memory across sessions. Policy:

### Recall gate (runs before any file tool)

Before calling `read`, `grep`, `glob`, or `bash` to answer an architecture,
behavior, "where is X", "how does Y work", or "find the function that…" question,
you MUST have run at least one `int_search` for that project during this turn. If
you have not, stop and search first. Memory is cheap; re-discovery is not.

This is a **hard gate**, not a soft nudge. A `read`/`grep`/`glob`/`bash` call that
answers one of the trigger questions without a prior `int_search` same-turn is a
policy violation — self-correct on the next turn even if the answer came back
fine. The gate exists because tool-share stats keep showing file tools winning
over recall, and the fix is a structural pre-condition, not a reminder.

**Triggers (search before any file tool):**

- "What's the tech stack" / "what does this project use"
- "Where is X defined" / "where does X live"
- "How does Y work" / "what does Y do"
- "Why does Z behave that way"
- "Find the function that…" / "find where we…"
- Any question you would otherwise answer with `grep -r` + `read`
- Any question whose answer a prior session plausibly already learned

**Out of scope (file tools fine without prior search):**

- A line-specific read you already know the path of (e.g. you're editing a file)
- A `bash` command that *does* something (run tests, lint, git) rather than
  *answers* a question
- A targeted `read` of a file the user just named in the message

When in doubt, search. The cost is one round-trip; the cost of skipping is a
discovery chain that memory was built to replace.

**Override clause.** In this repo the recall gate **overrides** equivalent
discovery guidance in the agent-skills layer. `spec-driven-development`,
`debugging-and-error-recovery`, `using-agent-skills`, and the explore subagent
all reach for `read`/`grep` first as their default discovery step — that
default does not apply here. Here, recall (`int_search`) is the discovery
step; file tools (`read`/`grep`/`glob`/`bash`) are the fallback for what
recall doesn't cover. When a skill's workflow says "read the codebase to
understand X", satisfy that step with `int_search` first and only fall back
to reads if the search came back empty or partial.

**Pre-tool self-check.** Before each `read`/`grep`/`glob`, ask: "could a
prior session already have learned this?" If yes and you haven't searched
this turn, search first.

### The policy bullets

- **Search is the default; file tools are the fallback.** For architecture /
  behavior / "where is X" / "how does Y" questions, run `int_search` first;
  reach for `grep`/`read` only for what the search didn't cover. See the
  Recall gate above — it's the binding version of this rule.
- **Save when re-discovery cost > save cost.** If reconstructing a fact next
  session would take more tool calls than saving it now, save it. See the `int`
  init workflow doc for the full bootstrap procedure.
- **Save negative facts, not just positive ones.** When a function
  conspicuously does *not* do something a sibling function does (e.g. "X
  clears these fields, Y does not"), that omission is itself worth its own
  memory — it will not be inferable from a positive-only description of what
  X does, and grep/read is the expensive way to rediscover it. See the
  seek/loop-restart entry below for a concrete example.
- **Include exact line numbers in `error-solution` memories** when a bug is
  tied to specific functions — it saves a grep+read pass to relocate them
  later.

### The `Not yet in memory:` audit section

In read-only/plan mode, end every answer that involved source reads beyond what
`int_search` returned with a mandatory closing section, titled
`Not yet in memory:`. This is not discretionary — it runs every time, not just
when the answer "feels" non-trivial:

- **Trigger:** if the response included **any** `read`/`grep`/file-exploration
  tool call, the section is required, regardless of how the model judges the
  answer's importance.
- **Content:** list each fact surfaced by those reads that is not already
  present in the `int_search` results from this session — one line per fact,
  terse, ready to paste into `int_add`. Do not restate facts that were already
  in the search results.
- **If every fact surfaced by the extra reads was already covered by the
  search results**, write `Not yet in memory: none — fully covered by
  existing memories.` Do not omit the section; an explicit "none" is still
  required output, since a silently-omitted section is indistinguishable from
  a missed insight.
- This section is separate from, and does not replace, the general
  non-trivial-synthesis flag below — it's a mechanical checklist, not a
  judgment call, precisely because judgment calls get skipped when the rest
  of the answer runs long.

**The audit feeds back into behavior, not just logs.** A non-`none` `Not yet
in memory:` section after reads that *could* have been answered by `int_search`
is a missed recall gate — the file tools ran first and recall didn't. Treat
that as a self-flag and tighten on the next turn. The audit is what catches
gate violations after the fact; the gate is what prevents them. Both belong.

### Call out why a surfaced fact matters

Beyond the mechanical list, call out *why* a surfaced fact matters when a bare
list item under-explains it. Don't let a genuinely important finding get lost
as an unremarked line in the `Not yet in memory:` list — e.g. "this ordering
constraint (snapshot stats before `resetScoring()`, not after) took several
reads to work out and isn't obvious from the code alone; worth flagging
clearly, not just listing." Rule of thumb: if reaching the fact took several
file reads or greps beyond what memory already covered, it likely deserves
more than a one-line mention.
