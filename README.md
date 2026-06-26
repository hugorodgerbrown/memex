# Memex — layered long-term memory for Claude Code

A working prototype of the combined memory store designed in chat. Markdown
memory files stay the system of record; Memex builds a derived index over them
and adds the three things Claude Code's grep-based memory cannot do on its own:
**meaning-based retrieval**, **structured graph recall**, and **self-maintenance**
(decay + a nightly consolidation pass), with always-on injection at prompt time.

It is installed **once, globally** (`~/.claude/memex/`) and applies to every
project. Provenance of each mechanism: hybrid search + wikilink entity graph
(GBrain), hook injection + Markdown-per-file (MemSearch), bounded always-on core +
large searchable archive (Hermes), decay-as-re-ranking + consolidation (Mem0).

## Two memory tiers (scopes)

Memex recalls across two stores at once and tags every hit with its scope:

| Scope | Lives in | Holds | Applies to |
|---|---|---|---|
| **global** | `~/.claude/memory/` | style choices, coding standards, durable preferences | every project |
| **project** | `~/.claude/projects/<mangled-path>/memory/` | facts specific to one codebase | that project only |

The project store is resolved from the session's working directory by reproducing
Claude Code's path mangling (`/` and `.` → `-`). A **worktree** resolves to its
parent repository, matching how the harness itself loads memory. A query ranks
global and project candidates in one pool, so a strongly-relevant global memory
can outrank a weakly-relevant project one and vice versa.

To add a **global** memory, write a Markdown file into `~/.claude/memory/`; to add
a **project** memory, write it into that project's memory directory (where Claude
Code already writes them). Scope is determined by which directory the file is in.

## What it is

| Concern | Choice | Why |
|---|---|---|
| Vector store | **sqlite-vec** | Embedded, single file, no daemon |
| Keyword store | **SQLite FTS5** (BM25) | Same file; catches exact names / IDs |
| Fusion | Reciprocal Rank Fusion | Combines meaning + lexical ranks |
| Graph | `[[wikilink]]` edges | Free, no LLM; one-hop recall expansion |
| Embeddings | **fastembed** (local ONNX) | Private, no API key; `hash` backend for tests |
| Forgetting | Decay re-ranking | Strength falls; the fact is never deleted |
| Consolidation | `dream` cycle (cron) | Dedup, broken links, salience — advisory only |

Each scope's index lives at `<memory-dir>/.memex/index.db` and is disposable —
`memex index --rebuild` reconstructs it from the Markdown at any time.

## Install (once)

```bash
# The local embedding model is an opt-in extra (heavy: onnxruntime + a one-time
# model download of ~130 MB). Run from anywhere.
uv sync --project ~/.claude/memex --extra fastembed

# Verify scopes resolve and the embedder loads, then build both indexes.
uv run --project ~/.claude/memex memex doctor
uv run --project ~/.claude/memex memex index --rebuild
```

`doctor` prints the resolved scopes for the current directory. Run it from inside
a project to confirm the project scope points at the right memory directory.

## CLI

All commands act on both scopes unless `--scope global|project` narrows them.

```bash
memex index [--rebuild]   # sync the indexes with the memory files
memex query "text" [-k N] # layered hybrid recall, printed for a human
memex dream               # consolidation pass → <scope>/.memex/reports/REPORT-<date>.md
memex stats               # index size + per-memory recall strength, per scope
memex doctor              # resolved scopes + sqlite-vec / embedder check
memex maintain            # index + dream the global scope and every project (cron entry)
memex distill <jsonl>     # extract memory candidates from a transcript into staging
memex candidates          # list staged candidates awaiting review
memex accept <name>       # promote a staged candidate into its scope's memory dir
```

## Wire up the hooks (always-on context) — global

Add to `~/.claude/settings.json` so the hooks fire in every project. Each hook
reads the session `cwd` from its payload and resolves the project scope itself, so
one config serves all projects:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project \"$HOME/.claude/memex\" python \"$HOME/.claude/memex/hooks/user_prompt_submit.py\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project \"$HOME/.claude/memex\" python \"$HOME/.claude/memex/hooks/stop.py\""
          }
        ]
      }
    ]
  }
}
```

A third hook, **SessionEnd**, drives distillation (below) and is inert until
enabled:

```json
"SessionEnd": [
  { "hooks": [{ "type": "command",
    "command": "uv run --project \"$HOME/.claude/memex\" python \"$HOME/.claude/memex/hooks/session_end.py\"" }]}]
```

* **UserPromptSubmit** runs layered hybrid recall for the prompt and injects the
  top-K memories (across both scopes) into context. It degrades silently — a
  missing index or slow embedder prints nothing and never blocks the prompt.
* **Stop** runs an incremental re-index of every active scope so memories written
  during the turn are searchable next time. Only changed files are re-embedded.
* **SessionEnd** distils the finished session into staged candidates, but only
  when `MEMEX_DISTILL_ENABLED=1` (it costs tokens). Off by default.

Cost note: a global `UserPromptSubmit` hook shells out on every prompt in every
project. The work is small and degrades silently where there is no index, but it
is not free. Remove the block to disable.

## Transcript distillation (the write side)

`memex` can read a finished conversation and propose new memories. The flow is
review-gated — nothing is written into the live store without a human accept:

1. **SessionEnd hook** (when `MEMEX_DISTILL_ENABLED=1`) condenses the transcript,
   asks a small model (`MEMEX_DISTILL_MODEL`, default Haiku) for durable facts,
   and writes them to `<scope>/.memex/candidates/` as *proposed* memories. The
   model assigns each a scope: global for cross-project facts, project otherwise.
2. **Review**: `memex candidates` lists what was staged.
3. **Accept**: `memex accept <name>` moves a candidate into its scope's memory
   directory (and strips the `proposed` marker); `memex index` then makes it
   searchable. To reject, delete the staged file.

Run it by hand on any transcript: `memex distill path/to/session.jsonl`. The
model call goes through the `claude` CLI (no API key); if the CLI is missing it
stages nothing and exits cleanly.

## Schedule the dream cycle

`memex maintain` re-indexes and runs the dream pass over the global scope and
every project under `~/.claude/projects/` — the entry point for a scheduled run
(a session's `Stop` hook keeps its own project fresh; maintenance covers the rest
and produces the reports). Pick one scheduler:

**launchd** (macOS, nightly at 03:00) — install the bundled agent:

```bash
sed "s#HOME_PLACEHOLDER#$HOME#g" ~/.claude/memex/scripts/com.memex.dream.plist \
  > ~/Library/LaunchAgents/com.memex.dream.plist
launchctl load ~/Library/LaunchAgents/com.memex.dream.plist
# remove with: launchctl unload ~/Library/LaunchAgents/com.memex.dream.plist
```

**Local cron**:

```cron
0 3 * * * $HOME/.claude/memex/scripts/run-maintenance.sh
```

**Claude Code routine** — use the `/schedule` skill to run `memex maintain` daily
and surface the reports.

The pass is **advisory**: it writes a dated report per scope (candidate
duplicates, broken `[[wikilinks]]`, memories missing from `MEMORY.md`, salience
ranking) and updates salience scores, but never edits or deletes a memory file.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MEMEX_GLOBAL_MEMORY_DIR` | `~/.claude/memory` | Global-scope memory directory |
| `MEMEX_PROJECT_MEMORY_DIR` | derived from `cwd` | Override project-scope directory |
| `MEMEX_EMBED_BACKEND` | `fastembed` | `fastembed` or `hash` (offline test) |
| `MEMEX_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | fastembed model |
| `MEMEX_EMBED_DIM` | `384` | Must match the model |
| `MEMEX_TOP_K` | `3` | Memories injected per prompt |
| `MEMEX_DECAY_HALF_LIFE` | `30` | Days; recency half-life |
| `MEMEX_DECAY_FLOOR` / `_CEILING` | `0.3` / `1.5` | Decay multiplier bounds |
| `MEMEX_DEDUP_THRESHOLD` | `0.92` | Cosine similarity for dup flagging |
| `MEMEX_DISTILL_ENABLED` | unset (off) | `1` to enable SessionEnd distillation |
| `MEMEX_DISTILL_MODEL` | `claude-haiku-4-5-20251001` | Model for distillation |

## Not yet wired (deliberate extension points)

* **LLM contradiction detection** — `dream` flags near-duplicates by cosine
  similarity; semantic contradiction ("two memories give opposite advice") would
  need an LLM judge over the flagged pairs.
* **Auto-accept policy** — distillation stages candidates for manual review.
  A confidence threshold could auto-accept high-signal global facts.
* **Embedding privacy** — the default backend is local, so memory contents never
  leave the machine. Swapping in an API embedder would change that.
