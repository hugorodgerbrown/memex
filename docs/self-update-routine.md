# Self-update routine

A weekly cloud Routine keeps Memex current with the agent-memory ecosystem. Each
run it researches the landscape, opens **one report PR** summarising everything it
found, and opens **one GitHub Issue per improvement** with enough implementation
detail for Claude to build the solution in a single follow-up PR. If nothing
worthwhile is found, it opens no PR and no issues.

- **Schedule**: Mondays at 06:07 local (`7 6 * * 1`).
- **Repository**: `hugorodgerbrown/memex`.
- **Output**: a branch `routine/self-update-<date>-report` and a PR titled
  `routine: weekly ecosystem report <date>`. One GitHub Issue per improvement.
  Never merges; never pushes to `main`. You review, merge the report PR, and
  assign issues as you see fit.

## How it is wired

The Routine runs on claude.ai's cloud (a "ccr" trigger), independent of any local
app. It is bound to the GitHub repo and has `Bash`/`gh`, so it opens PRs and issues
itself.

To (re)create it, the repo needs a Claude Code cloud **environment**; the trigger
references that environment's id. Provision one by connecting `hugorodgerbrown/memex`
in Claude Code cloud (the same connection the other repos' routines use), then
create the trigger with the prompt below and `cron_expression: "7 6 * * 1"`.

## The routine prompt

The prompt is self-contained — each run starts cold with no memory of prior runs.

```text
Objective: Keep the Memex project current with the agent-memory ecosystem. Each run,
research the landscape, summarise ALL worthwhile improvements in one pull request, and
open a GitHub Issue for each one with enough detail for Claude to implement it in a
single follow-up PR. If nothing worthwhile is found, open no PR and no issues.

Repository: hugorodgerbrown/memex (checked out in the working directory). Memex is
a layered long-term memory store for Claude Code: Markdown files are the source of
truth; recall is hybrid (sqlite-vec vectors + SQLite FTS5 keyword, fused by
reciprocal-rank fusion) across two scopes (global + project); a [[wikilink]] graph
adds structured recall; decay re-ranks by recency/frequency; a 'dream' cycle
consolidates; a SessionEnd hook distils sessions into proposed memories. It is a
uv project (Python 3.14) with ruff + mypy + pytest in CI.

Each run:
1. Ground yourself in the repo: read README.md (especially the 'Context / prior
   art' table), skim src/memex/*.py and tests/, and run `git log --oneline -20`.
2. Avoid duplicates: use the GitHub MCP tools to list all open issues and open PRs.
   Do not re-propose anything already tracked in an open issue or a recent
   `routine:` PR. Check git branch -a --list 'routine/*' as well.
3. Research the current landscape with WebSearch/WebFetch: new or updated
   agent-memory projects (Mem0, GBrain, MemSearch, Hermes, Letta/MemGPT, Zep,
   Cognee, and any newcomers) and new techniques or best practices in retrieval,
   decay, consolidation, and graph memory. Identify what Memex lacks or could
   adopt.
4. Identify ALL improvements that are concrete and net-positive. For each one,
   classify it:
   (a) code change — adopting a technique, adding a feature, fixing a bug;
   (b) docs change — adding a project to the README prior-art table;
   (c) idea — promising but not yet actionable enough for a code change.
   Discard anything vague, speculative, or already covered by an open issue or PR.
5. If nothing qualifies after filtering, STOP: open no PR and no issues, and
   report 'no update this week'.
6. Implement the report on a new branch `routine/self-update-<YYYY-MM-DD>-report`
   off an up-to-date main (`git checkout main && git pull`). The branch contains
   one commit that appends all new idea-class improvements to docs/IDEAS.md (create
   the file with a brief header if absent). Code-class and docs-class improvements
   are NOT implemented here — they become Issues (step 7).
7. Open ONE pull request for the branch:
   - Title: `routine: weekly ecosystem report <YYYY-MM-DD>`
   - Body: one section per improvement found, each containing: what it is, why it
     fits Memex, the source with a link to the paper or project, and its class
     (code / docs / idea). End the body with:
     🤖 Generated with [Claude Code](https://claude.com/claude-code)
8. For every improvement identified in step 4, open a GitHub Issue using the GitHub
   MCP tools. Write each issue so that Claude can take it and build the solution in
   one PR without additional research:
   - **Title**: a clear, one-line implementation task (e.g. "Split dream-cycle
     duplicate report into duplicates and supersessions using event_date frontmatter").
   - **Body** must include all of the following:
     * **What**: a short description of the change and its user-visible effect.
     * **Why**: the source and rationale with a link to the paper or project.
     * **Implementation notes**: the specific files to modify (with function/class
       names from the actual codebase), the approach to take, and any gotchas or
       constraints (e.g. "must stay ruff + mypy clean", "keep the change advisory
       — never delete a file").
     * **Acceptance criteria**: what "done" looks like — which existing tests should
       still pass, what new test(s) to add, and what the user-visible output or CLI
       behaviour should show.
     * End with: 🤖 Proposed by the self-update routine.

Constraints: never push to main; never modify secrets, CI credentials, or LICENSE;
British English in code, comments, and docs; match the repo's existing style (grounded
tone, fully typed functions, module and function docstrings, ruff + mypy clean).

Success criteria: either one report PR exists (with docs/IDEAS.md updated for
idea-class findings) and one GitHub Issue per improvement, or a clear 'no update
this week' report with no branch, no PR, and no issues created.
```
