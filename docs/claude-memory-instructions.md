# Teaching Claude the write side

Memex's hooks handle *recall* — they inject relevant memories into context on
every prompt. They do not tell Claude how to *write* a memory when you say
"remember this". That instruction has to live in Claude's context, because the
harness — not memex — decides what Claude is told at write time.

Without it, Claude guesses the scope. The common failure is writing a
cross-project rule ("always run tox before a PR") into the *project* store, where
it never surfaces in another repo. The block below makes scope selection explicit.

## Install

Paste the block into `~/.claude/CLAUDE.md` (global, applies to every project). It
is intentionally short — it loads into every session.

```markdown
## Long-term memory (memex)

When asked to remember something, write it as a Markdown file with frontmatter,
then add a one-line pointer to that directory's `MEMORY.md`.

Choose the scope by where the fact applies:

- **Global** — `~/.claude/memory/` — cross-project facts: style choices, coding
  standards, tooling preferences, who the user is. Surfaces in every repo.
- **Project** — the repo's own memory directory (where Claude Code already writes
  memories) — facts specific to this codebase: its files, services, decisions.

When in doubt, choose project — a global memory surfaces in *every* repo's
sessions, so a misfiled one is noise everywhere. A memory that starts in a project
and later proves general can be promoted with `memex promote`.

File format:

    ---
    name: <kebab-case-slug>
    description: <one-line summary, used for recall ranking>
    metadata:
      type: user | feedback | project | reference
    ---

    <the fact. For feedback/project, add **Why:** and **How to apply:** lines.
    Link related memories with [[their-slug]].>

You can also run `memex add <slug> --scope global` (or omit `--scope` for
project) to author the file and its `MEMORY.md` line in one step.
```

## Why this is a doc, not a hook

A hook cannot inject instructions into Claude's *write* behaviour — hooks fire on
events (prompt submitted, turn stopped) and can add context, but the decision of
which scope to write to is made by Claude mid-turn from its standing instructions.
The reliable place for standing instructions is `~/.claude/CLAUDE.md`. Memex ships
the recommended text here; you opt in by pasting it.
