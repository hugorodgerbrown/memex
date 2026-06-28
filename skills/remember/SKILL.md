---
name: remember
description: Write a long-term memory (memex) from an instruction. Use when the user runs /remember, says "remember that ...", or asks you to save a fact, preference, decision, or standard for later recall. Handles scope choice (project vs global) and writes the Markdown file plus its MEMORY.md pointer.
---

# /remember — write a memory to memex

Capture a durable fact as a memex memory: a Markdown file with frontmatter plus a
one-line pointer in that directory's `MEMORY.md`. The text after `/remember` is the
instruction — what to remember.

## 1. Get the instruction

- **Instruction given** (text followed `/remember`, or the user said "remember
  that …"): use it as the fact to store.
- **No instruction**: ask the user what they want remembered. Do not guess. Wait
  for their answer before continuing.

If the fact is one the repo already records on its own — code structure, a past
fix, git history, an existing CLAUDE.md rule — say so and ask what was non-obvious
about it; store that, not the restatement.

## 2. Choose the scope — ask before writing

Scope is the directory the file lives in. Ask the user **project or global**
before writing (use the AskUserQuestion tool), with a recommendation:

- **global** — `~/.claude/memory/` — cross-project facts: style choices, coding
  standards, tooling preferences, who the user is. Surfaces in every repo.
- **project** — this repo's memory directory — facts specific to this codebase:
  its files, services, decisions.

Recommend **project** when unsure: a misfiled global memory is noise in every
repo. A project memory that later proves general can be promoted with
`memex promote`.

## 3. Pick the type

Set `metadata.type` from the fact:

- `user` — who the user is (role, expertise, preferences).
- `feedback` — how you should work; corrections or confirmed approaches. Add
  **Why:** and **How to apply:** lines to the body.
- `project` — ongoing work, goals, constraints not derivable from the code. Add
  **Why:** and **How to apply:** lines. Convert relative dates to absolute.
- `reference` — pointers to external resources (URLs, dashboards, tickets).

## 4. Write it

Prefer the `memex add` command — it writes the frontmatter and the `MEMORY.md`
line in one step. `--scope` defaults to project; pass `--scope global` for global.

```bash
echo "<the fact>" | memex --scope <project|global> add <kebab-slug> \
  --description "<one-line summary used for recall ranking>" \
  --type <user|feedback|project|reference>
```

If `memex` is not on PATH, fall back to writing the file by hand into the scope's
memory directory and appending the pointer to its `MEMORY.md`:

```markdown
---
name: <kebab-slug>
description: <one-line summary, used for recall ranking>
metadata:
  type: <user|feedback|project|reference>
---

<the fact. For feedback/project, add **Why:** and **How to apply:** lines.
Link related memories with [[their-slug]].>
```

`MEMORY.md` pointer line:

```markdown
- [<Title>](<kebab-slug>.md) — <short hook>
```

Before saving, check for an existing memory that already covers this and update
that file instead of creating a duplicate.

## 5. Confirm

Tell the user the slug, the scope, and the path written. The next `Stop` hook
indexes it; run `memex index` if it should be searchable immediately.
