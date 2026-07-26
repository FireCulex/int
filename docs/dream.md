# DREAM.md — memory consolidation

A periodic-maintenance workflow (not a per-session policy, not a bootstrap) for one
job: go back over a project's `int` store and fix what's gone stale, contradictory,
duplicated, or over-atomized since the last pass. `int-init.md` builds the store the
first time; `AGENTS.md` governs what gets saved during normal work; this doc is the
only one that goes back and cleans up what's already there.

The name is literal, not cute: consolidation during sleep is when a biological memory
system resolves interference between conflicting traces and prunes what's redundant.
Nothing else in this project's memory setup does that. Search-first and save-when-
useful (the everyday rules) only ever *add* — nothing currently *reconciles*.

## Why this doc exists — the incident that motivated it

A MiniMax-M3 session searched memory and got back two live, contradictory entries:

- `6026ca30` (older): "Loop-restart seek must explicitly zero `nextBeatIdx` and
  `nextMeasureIdx` to 0 before calling `syncMetronome()` + `initMetronomePointer()`."
- `e5ced64e` (newer): "`initMetronomePointer()` already zeroes `nextBeatIdx` internally
  — that part of the older memory is wrong. The real ordering concern is
  `lastFiredBeat`: `syncMetronome()` must run before `initMetronomePointer()`."

The model *noticed* the correction existed but didn't reliably act on it — the final
answer kept citing the stale instruction and never surfaced the corrected one. Neither
memory was ever deleted, so every future session pays the same 50/50 risk of citing
the wrong one. That's not a model failure so much as a store hygiene failure: nothing
was watching for this after `e5ced64e` was saved.

## When to use

- Explicit invocation — the user says "dream", "consolidate memory", or similar.
- You notice a search returning two results that assert opposite things about the
  same fact (like the incident above) — fix it inline via the resolution procedure
  below, but also flag that a fuller dream pass may be overdue.
- A round number of `add` calls has accumulated since the last dream pass (rule
  of thumb: every ~15-20 adds, or whenever a session's `list` count surprises you).
- Right after a burst of heavy `init`-style research, since that's when duplicate and
  over-atomized entries are most likely to have been created in one sitting.

## When NOT to use

- Mid-task, to answer a specific question — that's `search`, not this.
- The store is small and you have no evidence of drift (no contradictions surfaced,
  count is low, entries all still read as current). Don't consolidate for its own sake.
- You only need to fix one known-bad memory. Just `delete` + `add` directly;
  don't run the full pass for a single fix.

## Tool surface

Same four `int` tools as everywhere else — `add`, `delete`, `search`, `list`. This
workflow leans hardest on `list` (full inventory) and `search` (cluster discovery),
and its entire output is `delete`/`add` pairs.

## Workflow

### 0. Inventory

`list <project>` for the full metadata set: IDs, types, timestamps. This is your
map for the rest of the pass — note the count and the type distribution before you
start, so the final report can say what changed.

### 1. Contradiction sweep

For each `architecture`/`learned-pattern`/`error-solution` memory (skip `preference`
and `command` — these rarely contradict, they just go stale), run `search` using
that memory's own content as the query. Look at what comes back:

- **A hit that asserts the opposite of the memory you searched from** → contradiction.
  Go to the resolution procedure below.
- **A hit that's a near-restatement with no new information** → duplicate, not
  contradiction. Go to step 2 instead.
- **Only itself, or clearly-related-but-non-conflicting entries** → no action.

This is the expensive part of the pass and scales with store size — for a large
store, prioritize memories whose content includes ordering/sequencing language
("before", "after", "must not", "unconditionally") since those are the claims most
likely to have been superseded by a later, more careful read of the same code.

**Resolution procedure**, once a contradiction is found:

1. Determine which is actually correct. Prefer the one with more specific evidence
   (exact behavior traced to a function, not just a general claim) or, if you can,
   verify against the live source with a quick read.
2. If verification is possible and cheap, do it — don't resolve a contradiction on
   recency alone. A newer memory being wrong is exactly how you'd get a *third*,
   still-wrong entry in the store.
3. `delete` the disproven UUID.
4. If the surviving memory doesn't already state what the old one got wrong (useful
   context for why it was corrected), `delete` it too and `add` a revised
   version that states both the correct fact and, briefly, what the retracted claim
   got wrong — this helps a future session that only sees one memory instead of the
   pair understand the fact wasn't dashed off. Optional but cheap.
5. Log it in your closing report.

### 2. Near-duplicate sweep

Group memories that clearly cover the same fact in different words (this typically
falls out of the searches you already ran in step 1). For each group:

- If they're truly redundant (same fact, same specificity) → keep the clearer/denser
  one, `delete` the rest.
- If they cover the same fact at different levels of detail → keep the more detailed
  one, unless the terser one is more retrievable (matches more likely future queries)
  — in that case, merge: write one memory with the terse framing and the detailed
  content, delete both originals.

### 3. Staleness check

For `error-solution` and `architecture` memories referencing specific line numbers or
function behavior, spot-check a sample against the live source (you don't need to
re-verify every memory — sample enough to catch systemic drift, e.g. if a refactor
touched the file since the last dream pass, check everything touching that area).
Anything now describing removed/renamed/changed code: `delete`. Don't bother
`add`-ing a replacement unless the fact is still worth having in some form — a
removed feature usually just needs deletion, not a memory about its removal.

### 4. Re-synthesis pass (optional, lower priority)

If step 1 or 2 surfaced a cluster of memories that are individually fine but
collectively over-atomized (many small facts about one subsystem that a future
session would have to retrieve and mentally reassemble — see the synthesis-over-
atomization principle in `int-init.md`), consider merging them into fewer, denser
entries. Do this last, and only if the cluster is genuinely fragmented — don't merge
memories that are already appropriately scoped just to reduce the count.

### 5. Verification

Before reporting done:

- [ ] Re-run `search` for each resolved contradiction — confirm only the correct
      version is retrievable, and it lands in the top 3.
- [ ] `list` and confirm the count reflects the deletes/adds you made (no
      orphaned entries from a `delete` that didn't land or an `add` that didn't fire).
- [ ] Spot-check that no resolution introduced a *new* contradiction (this can happen
      if a merged memory drops a caveat one of its sources had).

## Closing report

State plainly: how many contradictions found and resolved (name the UUIDs and the
fact), how many duplicates merged, how many stale entries deleted, and the store
count before/after. If a re-synthesis pass ran, note what got merged and why. Flag
anything you found suspicious but chose not to touch (e.g. a memory you couldn't
verify against source because the referenced file wasn't accessible this session).

## Common rationalizations

| Rationalization | Reality |
|---|---|
| "The newer memory is probably right, I'll just trust it and move on." | Sometimes the newer one is the wrong one — see step 1's resolution procedure. Recency is a weak signal on its own; verify when it's cheap to. |
| "I'll leave both memories, the search scores will sort it out." | They won't — a reading session doesn't know one supersedes the other, and a lower-scored-but-correct memory can lose to a higher-scored-but-stale one on a given query. This is exactly how the `nextBeatIdx` incident happened. |
| "Deleting memories feels destructive, I'll just add a correction alongside it." | That's how you got two live entries in the first place. `delete` the disproven one; don't just outnumber it. |
| "This store is small, it can't have drifted yet." | Drift starts at the second `add` covering related ground, not at some size threshold. Small stores contradict themselves too — this incident happened well under 20 entries. |

## Red flags

- You're resolving a contradiction on recency alone, with no attempt to verify
  against source even when a quick read would settle it.
- A dream pass ends with the memory count unchanged — either nothing was actually
  wrong (plausible, but check your work) or the sweep wasn't thorough enough to find
  anything, which is a different failure than "everything's fine."
- You merged memories and the merged version is *less* specific than either source
  (a lossy merge is worse than two separate entries).
