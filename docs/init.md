# Init: bootstrap a project's `int` memory store

A plain-doc workflow (not an auto-discoverable skill) for one job: when you land on a
project where `int`'s memory store is empty or stale, do a deep first-pass research
sweep and persist the genuinely reusable insights so future sessions start with context.

Invoke explicitly — the user says "init memory", "initialize memory", or "bootstrap
memory" — or when you, the assistant, judge that the cost of re-discovering this
project exceeds the cost of running this workflow. It is **not** invoked automatically
on every session; recall (`search`) is the everyday path, init is the exception.

This workflow is the high-cost-discovery case the two rules in
[`AGENTS.md`](../AGENTS.md) (search first; save when re-discovery cost > save cost)
are about. Run init rarely; run search always.

## When to use

- First session on a project whose store is empty or has no non-trivial memories
  (`list` returns only noise).
- A project has changed shape enough (major refactor, stack swap, new subsystem) that
  prior memories are stale and a fresh synthesis is cheaper than patching.
- The user explicitly asks to initialize / bootstrap / seed memory.

## When NOT to use

- The store already has architecture + project-config memories you can recall via
  `search`. Run init's *reflection* step against what's there; if it covers the
  ground, **stop** — augment with `add` for the gaps, don't re-bootstrap.
- You only need to save one fact. Use `add` directly.
- Cross-project or per-user preferences. Not in v1 — `int` is single-tenant and
  project-scoped; there is no `scope: "user"` analog.

## Tool-surface map (`int` v1, post-`read`-removal)

`int` exposes four MCP tools, all project-scoped:

| Operation | Tool | Notes |
|---|---|---|
| Store a memory | `add(project, type, content)` | Returns `memory_id` (UUID str). |
| Remove a memory | `delete(memory_id)` | Returns `bool`. Edit = `delete` then `add` (new UUID). |
| Semantic recall | `search(project, query, limit=5)` | Ranked hits, content included. |
| List metadata | `list(project)` | Metadata only (no content, no embedding call). Use for pre-flight. |

Notes for readers coming from Supermemory's `/init`:

- **No `scope`.** `int` is single-tenant and scopes by the `project` field. There is no
  user-scope persistence in v1; do not invent one.
- **No `mode`.** Each operation is its own tool — no `supermemory(mode: "add", …)`
  pseudo-call. Use the actual tool.
- **`type` is a free string** with a recommended enum (not enforced): `architecture`,
  `preference`, `command`, `learned-pattern`, `conversation`, `error-solution`,
  `project-config`. Pick from the enum when it fits; only invent a new tag if a clear,
  recurring pattern demands it.
- **Immutable-append lifecycle.** Revisions are `delete` + `add`. Never "update in
  place" — there is no such call, and silently re-adding leaves a stale duplicate.

## Workflow

### 0. Pre-flight: recall before bootstrap

Always, even on a project you assume is empty.

1. Confirm the server is reachable (an `list` call that doesn't error is enough).
2. `list <project>` and skim the result:
   - **Empty / only trivial entries** → proceed to research.
   - **Non-trivial memories exist** → do **not** re-bootstrap. Instead run the
     reflection step (below) against the stored set; for each gap you find, run a
     targeted `search` for it, and only `add` if still missing. Recall-first
     applies to init too.
3. If the user gave you hard rules or constraints in step 1, treat them as
   `add` of `type: preference` once you start saving — they're cheap and you'll
   forget.

### 1. Upfront questions (optional, ask only if unclear)

Ask at most two, one at a time, only if the answer isn't already obvious from
`AGENTS.md` / `docs/` / prior memories:

- Research depth: quick (commands + stack) or deep (~50-tool-call synthesis)?
- Any hard rules I should always follow here? Any communication-style preference?

Skip this entirely if the project is well-documented or the user has already said.

### 2. Deep research

Goal: genuinely understand the project, not collect surface facts. Cross-reference
findings; resolve ambiguities; read actual file contents, not just names; look for
patterns in history that reveal workflow. Think like a new team member on day one.

**Parallel explore fan-out** (launch concurrently):

```
Task(explore, "What is the tech stack and key dependencies?")
Task(explore, "What is the project structure? Key directories?")
Task(explore, "How do you build, test, lint, and run this project?")
Task(explore, "What are the main architectural patterns and data flow?")
Task(explore, "What conventions, boundaries, or gotchas are documented?")
```

**File-based** (read in parallel where possible):

- `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md`.
- Package manifests: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.
- Config: `tsconfig.json`, `.eslintrc`, `ruff.toml`, `.prettierrc`, `mypy.ini`.
- CI: `.github/workflows/`, `Makefile`, `Justfile`.
- Project-specific docs (`docs/intent.md`, `docs/spec.md`, `docs/deployment.md`).

**Git-based:**

```bash
git log --oneline -20              # recent history
git branch -a                      # branching strategy
git log --format="%s" -50          # commit conventions
git shortlog -sn --all | head -10  # main contributors
```

Patterns to look for in history: gate-commands run before commit, conventional-commit
shape, who owns which subsystem, recurring bug-fix areas (pain points), refactors that
moved code between modules.

### 3. Save incrementally, with recall-before-save

Save **as you discover**, not in a final dump. Partial work shouldn't be lost if the
session is interrupted. For each candidate insight:

1. **Recall first.** Run `search` for the project with a query that should find a
   prior memory covering this insight.
   - **Covering hit** → skip. Don't re-save.
   - **Partial / stale hit** → `delete` the old UUID, then `add` the augmented
     version (new UUID). Note the revision in the content if useful.
   - **No hit** → proceed to add.
2. **Add.** One `add` per distinct insight. Be concise but self-contained —
   enough context for a future session to use it without re-reading the source. State
   the *why* when it isn't obvious; the *what* alone ages badly.
3. **Type it.** Pick from the enum; only invent a tag if a clear recurring pattern
   demands it.

**Type guidance:**

| `type` | What goes here |
|---|---|
| `project-config` | Tech stack, commands (build/test/lint/run), tooling, env shape. |
| `architecture` | Codebase structure, key components, data flow, subsystem boundaries. |
| `command` | A specific non-obvious command sequence worth remembering. |
| `learned-pattern` | A convention specific to this codebase (naming, file layout, idioms). |
| `preference` | Coding style or workflow rules that should always hold here. |
| `error-solution` | A real bug you hit and how it was fixed — especially non-obvious ones. |
| `conversation` | A decision or its rationale that future sessions need context for. |

**Good memories (concise, searchable, self-contained):**

- "Build/run/test gate: `uv run ruff check && uv run mypy int && uv run pytest` must
  pass before any commit. Python 3.14+, FastAPI, Qdrant in a separate container.
  Embeddings L2-normalized in `int/embeddings.py` before storage."
- "Adding a new MCP tool is an ask-first boundary (`AGENTS.md`); v1 surface is
  `add`/`delete`/`search`/`list`. Don't extend without confirming."
- "Changing `GEMINI_EMBEDDING_MODEL` or `GEMINI_EMBEDDING_DIMENSIONS` silently
  invalidates stored vectors — treat as fail-fast, not silent corruption."

**Bad memories (too thin, or too broad):**

- "There is a server." (No insight.)
- "The codebase was explored." (Not a recall target.)
- A verbatim dump of `pyproject.toml`. (Store the *synthesis*, not the file.)

### 4. Reflection

Before declaring done, reflect honestly:

- **Completeness:** Did I cover commands, architecture, conventions, and gotchas? If
  the project has a `docs/intent.md` / `AGENTS.md`, are their constraints captured?
- **Quality:** Is each memory concise, self-contained, and likely to retrieve on a
  semantic query a future session would actually write? Watch for jargon a future
  query won't reproduce.
- **Dedupe:** Did the recall-before-save loop catch overlaps? Re-run `list` and
  eyeball the count — if it grew by more than the distinct insights you found, you
  probably saved a near-duplicate.

### 5. Verification

Confirm before reporting done:

- [ ] `search` for each saved insight returns it in the top 3 (catches bad
      queries / bad typing / embedding mismatch).
- [ ] Type coverage: the relevant enum values are used where they fit.
- [ ] Re-running init on this project would now produce a no-op summary, not new
      saves (idempotency check — the recall loop is doing its job).
- [ ] `list <project>` shows the new memories' metadata without errors.

## Common rationalizations

| Rationalization | Reality |
|---|---|
| "The store's empty, I'll skip the recall step." | Recall anyway — the user may have seeded it between your last `list` and now. `AGENTS.md` says search first; init is not exempt. |
| "I found a lot, I'll just save it all." | Trust the salience rule. Not every fact earns a memory — a single file path lookup is not worth storing; a 20-tool-call synthesis is. |
| "Edit means add a new one alongside the old." | No. `delete` the old UUID first, then `add`. Otherwise you leave stale duplicates that pollute `search`. |
| "I'll batch all the adds at the end — cleaner." | Save incrementally. An interrupted session should still have the first half of the synthesis persisted. |
| "This is just one project's quirks, no need to type it carefully." | Future `search` retrieves by the *content*, but you'll filter and triage by `type`. Mis-typed memories are quietly invisible when you need them. |
| "I'll copy Supermemory's `/init` verbatim, it's close enough." | Don't. `int` has no `scope`/`mode`, the tool names differ, and there is no user-scope in v1. Use the map above. |

## Red flags

- You're about to call `add` without having run `search` for an existing
  covering memory first.
- You're adding a memory whose content is a raw file dump rather than a synthesis.
- The store count grew by more than the distinct insights you found.
- A saved memory doesn't retrieve in its own top-3 — the query was wrong, the type
  was wrong, or the content is too vague to match its own subject.
- You introduced a `scope` or `mode` argument — those don't exist on `int`'s tools.
- You didn't ask the upfront questions *and* `AGENTS.md` is silent on the rules —
  you may have silently invented a convention. Go back and check.

## When you're done

Report a one-paragraph summary: how many memories you added, what types, and any gaps
you noticed but chose not to fill (and why). Then stop — don't speculate about future
work unless the user asks.

Per `AGENTS.md`, init is policy-shaped, not trigger-detected: you judged this worth
doing once. The everyday path from here is `search` first.
