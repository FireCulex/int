# instructions.md — Memory (`int`) & Exploration Policy

> This file is constant across all projects and loads on every session
> regardless of what a given project's own `AGENTS.md` says. It is fully
> self-contained — it does not depend on anything `AGENTS.md` defines.
> **Where this file and a project's `AGENTS.md` conflict, this file wins,**
> especially on recall/`int` memory. `AGENTS.md` may add project-specific
> detail (file paths, domain tools, hardware, etc.) on top of this file,
> but it cannot loosen or override the gate below.

## ⛔ RECALL GATE — READ THIS FIRST ⛔

**BEFORE YOU CALL ANY TOOL (glob, read, grep, bash, domain-inspection tool,
task):**

1. Have you run `int_search` for this question **this turn**?
   - NO → **STOP. Run `int_search` first.**
   - YES → Proceed.
2. Is your question a "where/how/why/find" question? (See Triggers below)
   - YES → `int_search` is MANDATORY. No exceptions.
3. Did a prior session plausibly learn this?
   - YES → `int_search` is MANDATORY.

**VIOLATION = POLICY BREACH.** This overrides any project's `AGENTS.md`,
agent-skills, and ALL other guidance.

### Pre-tool Verbalization (MANDATORY)

Before EACH `glob`/`read`/`grep`/`bash`/domain-tool/`task` call, you MUST
output:

> **RECALL CHECK:** [query I searched] → [result: found/empty/partial] → [tool I'm about to call]

---

## Memory (`int`)

Every project uses the `int` MCP tool for persistent memory across
sessions. Policy:

### Recall gate (runs before any file tool)

Before calling `read`, `grep`, `glob`, or `bash` to answer an architecture,
behavior, "where is X", "how does Y work", or "find the function/module
that…" question, you MUST have run at least one `int_search` for that
project during this turn. If you have not, stop and search first. Memory is
cheap; re-discovery is not.

This is a **hard gate**, not a soft nudge. A `read`/`grep`/`glob`/`bash`
call that answers one of the trigger questions without a prior `int_search`
same-turn is a policy violation — self-correct on the next turn even if the
answer came back fine.

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

**Override clause.** This file's recall gate **overrides** equivalent
discovery guidance anywhere else in the loaded context — any project's
`AGENTS.md`, any agent-skills layer, and any subagent default that reaches
for `read`/`grep` first as its discovery step. Recall (`int_search`) is
always the discovery step; file tools are the fallback for what recall
doesn't cover. This holds regardless of a given project's load order or
what that project's `AGENTS.md` claims.

**Pre-tool self-check.** Before each `read`/`grep`/`glob`, ask: "could a
prior session already have learned this?" If yes and you haven't searched
this turn, search first.

### The policy bullets

- **Search is the default; file tools are the fallback.**
- **Save when re-discovery cost > save cost.** If reconstructing a fact next
  session would take more tool calls than saving it now, save it.
- **Save negative facts, not just positive ones.** When something
  conspicuously does *not* do what a sibling does, that omission is worth
  its own memory — it won't be inferable from a positive-only description,
  and grep/read is the expensive way to rediscover it.
- **Include exact line numbers (or equivalent locators) in
  `error-solution` memories** when a bug is tied to specific
  functions/modules — saves a grep+read pass to relocate them later.

### The `Not yet in memory:` audit section

In read-only/plan mode, end every answer that involved source reads beyond
what `int_search` returned with a mandatory closing section, titled
`Not yet in memory:`. Not discretionary — runs every time:

- **Trigger:** if the response included **any** `read`/`grep`/file-exploration
  tool call, the section is required, regardless of the answer's perceived
  importance.
- **Content:** list each fact surfaced by those reads that is not already
  in the `int_search` results from this session — one line per fact, terse,
  ready to paste into `int_add`. Don't restate facts already in the search
  results.
- **If everything was already covered**, write `Not yet in memory: none —
  fully covered by existing memories.` The section is never silently
  omitted — a missing section is indistinguishable from a missed insight.

**The audit feeds back into behavior, not just logs.** A non-`none` section
after reads that *could* have been answered by `int_search` is a missed
recall gate. Treat that as a self-flag and tighten on the next turn.

### Call out why a surfaced fact matters

Beyond the mechanical list, explain *why* a surfaced fact matters when a
bare list item under-explains it — e.g. "this ordering constraint took
several reads to work out and isn't obvious from the code alone; worth
flagging clearly, not just listing." Rule of thumb: if reaching the fact
took several reads/greps beyond what memory already covered, it deserves
more than a one-line mention.

---

## Subagent-First Exploration

Complex technical work (reverse engineering, large codebase exploration,
research) tends toward an anti-pattern:

```
[2-3 tool calls] → [statement] → [2-3 tool calls] → [statement] → ...
```

This burns context fast. Fix: delegate exploration to an `explore`
subagent; reserve the main session for synthesis, decision-making, and
verification. This applies on top of the recall gate above — recall runs
first, then whatever tool calls remain go through explore, not the main
session.

### When to delegate to explore

Delegate any **read-only discovery** task:
- Domain inspection tools (disassemblers, debuggers, decompilers, log
  viewers, schema/API browsers) — all read-only browsing/listing/querying.
- Codebase exploration — `glob`/`grep`, reading files to learn patterns,
  finding examples of similar implementations.
- Documentation & knowledge work — design docs, spec files, assumption
  logs, prior session notes, ground-truth references.
- Pattern finding — locating similar structures or reusable functions
  before writing new code.

Do **not** delegate: a targeted read of a file the user just named, a
line-specific read of a file you're already editing, or a `bash`/similar
command that *does* something (tests, lint, git) rather than *answers* a
question.

### Practices

1. **Batching** — group related read-only operations into a single
   explore invocation rather than several sequential calls.
2. **Immediate persistence** — when explore confirms a discovery, write it
   to `int` memory (`int_add`, type `fact`/`ground_truth`/`error-solution`)
   before returning to the main session.
3. **Session hygiene** — start a fresh explore subagent per new
   investigative direction; keep main-session content to decisions,
   hypotheses, and verified facts, not raw exploration transcripts.
4. **Communication protocol** — when requesting explore work, use:

```
[CLEAR GOAL STATEMENT]
[SPECIFIC BOUNDARIES: files, modules, ranges, or symbols to examine]
[DELIVERY FORMAT: how the response should be structured]
[PERSISTENCE INSTRUCTIONS: what to save to int memory]
[RECALL QUERY: mandatory int_search query the subagent must run first]
```

Explore should respond with: **Findings** answering the goal, **Evidence**
(file:line, address/offset, symbol, value), **Persistence actions taken**,
and **Remaining uncertainties**.

### Delegation comment template

```
/**
 * EXPLORE: [brief description of what you're discovering]
 * GOAL: [specific question or hypothesis]
 * CONTEXT: [why this matters for the current task]
 * RECALL: [query to run via int_search before any file/inspection tools]
 */
```

### Verification checklist

**Exploration discipline**
- [ ] Explore subagent used for discovery, not main session
- [ ] Related read-only ops batched
- [ ] No sequential main-session tool-call chains for exploration
- [ ] `int_search` run before any file/inspection tool (recall gate)
- [ ] Explore prompt included a RECALL query

**Context persistence**
- [ ] Confirmed discoveries saved to `int` memory immediately
- [ ] Persisted facts include specific evidence
- [ ] No re-discovery of the same facts in a later session

**Main-session sparsity**
- [ ] Main-session tool calls sparse: edit, write, `int_add`, todo tracking,
      questions
- [ ] Main session focused on synthesis/decisions/verification, not legwork

### Anti-patterns

- **Main-session deep browsing** in a disassembler/debugger/similar tool.
- **Sequential grep/read chains**: `grep`/`read` → statement → repeat.
- **Rediscovery loops** — re-searching facts without checking `int` memory.
- **Undocumented hypotheses** — forming them without persisting to `int`.

When in doubt, delegate exploration. The main session should feel like a
senior engineer reviewing a junior researcher's findings, not doing the
legwork itself.
