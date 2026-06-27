# Self-update routine

A weekly cloud Routine keeps Memex current with the agent-memory ecosystem. Each
run it researches the landscape, and opens **at most one** pull request when it
finds a concrete, low-risk improvement. If nothing qualifies, it opens no PR.

- **Schedule**: Mondays at 06:07 local (`7 6 * * 1`).
- **Repository**: `hugorodgerbrown/memex`.
- **Output**: a branch `routine/self-update-<date>-<slug>` and a PR titled
  `routine: <summary>`. Never merges; never pushes to `main`. You review and merge.
- **Scope per run**: one focused diff — a code change, a README prior-art update,
  a small fix, or a one-line entry in [`IDEAS.md`](IDEAS.md). Code changes must
  pass the full gate (`ruff format --check`, `ruff check`, `mypy`, `pytest`)
  before the PR opens; docs-only changes do not.

## How it is wired

The Routine runs on claude.ai's cloud (a "ccr" trigger), independent of any local
app. It is bound to the GitHub repo and has `Bash`/`gh`, so it opens PRs itself.

To (re)create it, the repo needs a Claude Code cloud **environment**; the trigger
references that environment's id. Provision one by connecting `hugorodgerbrown/memex`
in Claude Code cloud (the same connection the other repos' routines use), then
create the trigger with the prompt below and `cron_expression: "7 6 * * 1"`.

## The routine prompt

The prompt is self-contained — each run starts cold with no memory of prior runs.

```text
Objective: Keep the Memex project current with the agent-memory ecosystem by
opening AT MOST ONE well-scoped pull request per run. If nothing worthwhile is
found, open no PR.

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
2. Avoid duplicates: run `gh pr list --state open` and `git branch -a --list
   'routine/*'`, and check `git log` for prior `routine:` commits. Do not
   re-propose anything already open or recently merged.
3. Research the current landscape with WebSearch/WebFetch: new or updated
   agent-memory projects (Mem0, GBrain, MemSearch, Hermes, Letta/MemGPT, Zep,
   Cognee, and any newcomers) and new techniques or best practices in retrieval,
   decay, consolidation, and graph memory. Identify what Memex lacks or could
   adopt.
4. Choose AT MOST ONE improvement that is concrete, self-contained, and low-risk.
   It may be: (a) adopting a technique in code; (b) adding a newly-relevant
   project to the README prior-art table with what Memex could borrow; (c) a small
   feature or bug fix; or (d) if promising but not yet actionable, a single entry
   appended to docs/IDEAS.md (create the file with a brief header if absent).
5. Bar for opening a PR: the change is clearly net-positive, is one focused diff,
   and you are confident. When unsure, prefer a docs/IDEAS proposal over
   speculative code. If nothing clears the bar, STOP: create no branch and no PR,
   and report 'no update this week'.
6. Implement on a new branch `routine/self-update-<YYYY-MM-DD>-<slug>` off an
   up-to-date main (`git checkout main && git pull`).
7. For any change touching code (anything under src/ or tests/), run the full gate
   and do NOT open a PR if any step fails — fix it or fall back to a docs/IDEAS
   proposal:
   - ensure uv is available: `command -v uv || (curl -LsSf
     https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH")`
   - `uv sync --group dev`
   - `uv run ruff format --check .`
   - `uv run ruff check .`
   - `uv run mypy`
   - `uv run pytest`
   Docs-only changes (README.md, docs/**) do not need the gate.
8. Commit (end the message with `Co-Authored-By: Claude Opus 4.8
   <noreply@anthropic.com>`), push the branch, and open a PR with `gh pr create` —
   do NOT merge. Title: `routine: <summary>`. Body: the source/rationale with a
   link to the project or article, what changed and why, the gate result for code
   changes, and a note that this is an agent-proposed change for human review. End
   the PR body with: 🤖 Generated with [Claude Code](https://claude.com/claude-code)

Constraints: at most one PR per run; never push to main; never modify secrets, CI
credentials, or LICENSE; keep diffs small and reviewable; British English in code,
comments, and docs; match the repo's existing style (grounded tone, fully typed
functions, module and function docstrings, ruff + mypy clean).

Success criteria: either one focused PR exists (CI-gated if it touches code), or a
clear 'no update this week' report with no branch and no PR created.
```
