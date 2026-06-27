# Memex — layered long-term memory for Claude Code

[![CI](https://github.com/hugorodgerbrown/memex/actions/workflows/ci.yml/badge.svg)](https://github.com/hugorodgerbrown/memex/actions/workflows/ci.yml)

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

## Using Memex day to day

Recall is **passive**; writing is **active or assisted**. You never run a command
to *get* a memory back — you choose what *goes in*.

### The loop

```
        WRITE (active/assisted)                 RECALL (passive)
   you, Claude, or an accepted   ──index──▶   UserPromptSubmit hook injects
   distillation candidate write              the top matches into context
   a Markdown file                            on every prompt — automatically
        │                                            ▲
        └──── Stop hook re-indexes after ────────────┘
              each turn; cron `maintain` sweeps the rest
```

A memory is just a Markdown file with frontmatter. Nothing is recalled until it
exists as a file and has been indexed; nothing is written without one of the three
acts below.

### How a memory gets in — three ways

1. **You tell Claude to remember it.** In a session, say "remember that we always
   run `tox` before a PR". Claude writes the memory file (to the project or global
   directory) and the `Stop` hook indexes it. This is the common path.
2. **You write it by hand.** Drop a Markdown file into `~/.claude/memory/` (global)
   or the project's memory directory. Run `memex index`, or let the next `Stop`
   hook pick it up. Use this for standards you want to author deliberately.
3. **You accept a distilled candidate.** With distillation enabled, the
   `SessionEnd` hook reads the finished conversation and *proposes* memories into
   staging. You review and confirm:

   ```bash
   memex candidates          # what the last session proposed
   memex accept use-ruff     # promote one into its scope's memory dir
   memex index               # make the accepted memory searchable
   ```

   Generation is passive; the **accept is the gate** — nothing the model proposes
   enters the live store on its own.

### Active vs passive, at a glance

| Action | Who | Active or passive |
|---|---|---|
| Recall into context | the `UserPromptSubmit` hook | passive — every prompt |
| Re-index after a turn | the `Stop` hook | passive |
| Write "remember X" | you ask, Claude writes | active |
| Author a standard by hand | you | active |
| Propose memories from a session | the `SessionEnd` hook | passive (proposal only) |
| Accept a proposal | you (`memex accept`) | active — the gate |
| Nightly dedup / salience / reports | cron `maintain` | passive |

### Forgetting and editing

The Markdown is the source of truth, so you manage memories as files:

- **Edit**: change the file; the next index run re-embeds it.
- **Delete**: remove the file; the next index run soft-deletes it from the store.
- **Demote without deleting**: leave it — decay lowers its recall strength the
  longer it goes unused, so stale memories sink on their own.
- **Promote project → global**: move the file from the project directory into
  `~/.claude/memory/`.

### Choosing the scope

Put cross-project facts (style, standards, tooling preferences) in the global
directory; put codebase-specific facts in the project directory. When in doubt,
project — a global memory surfaces in *every* repo's sessions.

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
# Installs the local embedding backend (onnxruntime); the model itself (~130 MB)
# downloads once on first use. Run from anywhere.
uv sync --project ~/.claude/memex

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
memex health              # did the scheduled maintenance run, and did it succeed?
memex maintain            # index + dream the global scope and every project (cron entry)
memex distill <jsonl>     # extract memory candidates from a transcript into staging
memex review              # interactively accept/discard staged candidates
memex candidates          # list staged candidates awaiting review (non-interactive)
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
2. **Review** — `memex review` walks each staged candidate, shows its detail, and
   prompts to **a**ccept, **d**iscard, **s**kip, or **q**uit. Accepting promotes
   the memory into its scope's directory (stripping the `proposed` marker) and the
   command re-indexes the accepted scopes so they are searchable straight away.

   ```console
   $ memex review
   2 staged candidate(s) to review.

   === use-ruff-format  [global/feedback] ===
   run ruff format before commit

   Always run ruff format. CI checks it.
   [a]ccept  [d]iscard  [s]kip  [q]uit > a
     accepted → ~/.claude/memory/use-ruff-format.md
   ```

   For scripting, the non-interactive pair still exists: `memex candidates` lists
   what was staged and `memex accept <name>` promotes one (then run `memex index`).

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

### Did it run? — `memex health`

`memex health` answers whether the schedule ran and succeeded, without any
`launchctl` incantation. It reads the tool's own artefacts: the maintenance log
(last attempt + a `=== maintenance complete ===` success marker) and each scope's
newest dream report.

```console
$ memex health
maintenance log: /tmp/memex-maintenance.log
last run:        2026-06-27T03:00:01Z (6h ago) — OK
scopes (newest dream report):
  [global] 2026-06-27 (today)
  [-Users-hugo-Projects-ambassadeurs] 2026-06-27 (today)
```

It **exits 0** when the last run succeeded and **1** when it failed or never ran,
so it doubles as a cron/monitoring check. A `FAILED` verdict means the last
attempt logged a header but no completion marker — read `/tmp/memex-maintenance.log`
for the traceback. The raw signals, if you want them directly:

```bash
launchctl list | grep memex          # middle column: 0 = last run OK (macOS)
tail -n 20 /tmp/memex-maintenance.log # last run's output
```

## Self-update routine

A weekly cloud Routine researches the agent-memory ecosystem and opens one report PR
summarising every worthwhile improvement it found, plus one GitHub Issue per
improvement with full implementation detail so Claude can build the solution in a
single follow-up PR. If nothing qualifies, it opens no PR and no issues. The
schedule, repo binding, and the exact prompt are in
[`docs/self-update-routine.md`](docs/self-update-routine.md); report PRs land titled
`routine: weekly ecosystem report <date>` for human review.

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
| `MEMEX_LOG` | `/tmp/memex-maintenance.log` | Maintenance log read by `memex health` |

## Not yet wired (deliberate extension points)

* **LLM contradiction detection** — `dream` flags near-duplicates by cosine
  similarity; semantic contradiction ("two memories give opposite advice") would
  need an LLM judge over the flagged pairs.
* **Auto-accept policy** — distillation stages candidates for manual review.
  A confidence threshold could auto-accept high-signal global facts.
* **Embedding privacy** — the default backend is local, so memory contents never
  leave the machine. Swapping in an API embedder would change that.

## Context: prior art, second brains, and the LLM OS

Memex is a synthesis, not a new idea. It takes concrete mechanisms from four
agent-memory projects and assembles them under one Claude Code-native install.

### Prior art — the memory projects it draws from

| Project | What it is | What Memex took |
|---|---|---|
| [Mem0](https://github.com/mem0ai/mem0) | A memory layer between an agent and its LLM; an extract-then-update pipeline (`ADD`/`UPDATE`/`DELETE`/`NOOP`) over a hybrid vector/graph store | **Decay-as-re-ranking** (recall strength falls, the fact is never deleted) and the **consolidation** pass |
| [GBrain](https://github.com/garrytan/gbrain) | Markdown in a git repo as the system of record, indexed into Postgres + pgvector, with a nightly enrichment cron | **Markdown-as-truth** (delete the file → soft-delete in the index), the **`[[wikilink]]` entity graph** built without an LLM, the **dream cycle**, and **hybrid search** (vector + BM25 + reciprocal-rank fusion) |
| [MemSearch](https://github.com/zilliztech/memsearch) | A Claude Code plugin: dated Markdown memory, semantic recall injected via hooks | The **hook architecture** — `UserPromptSubmit` injects top-K at prompt time, `Stop` re-indexes — and **silent-degrade** so a hook never blocks a turn |
| [Hermes](https://github.com/NousResearch/hermes-agent) | An agent with a small always-loaded `MEMORY.md` curated note plus a large searchable conversation archive | The **two-tier split**: a small always-on core (here the global scope) plus a larger archive paged in by relevance (here the project scope) |

The one-line provenance at the top of this README maps each mechanism back to its
source. Where Memex differs from all four: it spans **two scopes at once** (global +
project) in a single ranked recall, and it stays **file-first** with no required
database server — one SQLite file per scope, rebuildable from the Markdown.

### Framing — the "second brain" and Karpathy's LLM OS

The second-brain idea (Tiago Forte's
phrasing) is that you offload durable knowledge to an external, searchable store so
your own working memory is freed for thinking. Andrej Karpathy reframed the same
shape for LLMs as the *LLM OS*: the model is the CPU, the **context window is RAM**,
and external stores — files, vector databases — are the **disk**. An agent is only
as good as what it can page from disk into RAM at the right moment. Memex is exactly
that disk-and-pager for Claude Code: memories live on disk as Markdown, the
`UserPromptSubmit` hook pages the relevant ones into the context window each turn,
and decay acts as cache eviction — unused pages grow cold and fall out of easy
reach without being erased. The "second brain" framing explains *why*; the LLM-OS
framing explains *where it plugs in*.

### Obsidian as memory storage

A large community uses [Obsidian](https://obsidian.md)
as a second brain: plain Markdown notes linked by `[[wikilinks]]` into a personal
knowledge graph, and increasingly wired to LLMs (via plugins and MCP servers) so an
assistant can read and write the vault. Memex shares that DNA deliberately —
Markdown as the source of truth, `[[wikilinks]]` as graph edges — but optimises for
a different reader. Obsidian is **human-first**: you author, browse, and navigate.
Memex is **agent-first**: the index, hybrid ranking, decay, and prompt-time
injection are tuned for an LLM retrieving under a token budget, not a person
clicking through panes.

They compose. Point `MEMEX_GLOBAL_MEMORY_DIR` at an Obsidian vault and Memex will
index it in place — the vault stays a human-navigable second brain, and the same
notes become semantically recallable by the agent. The wikilink graph you already
maintain in Obsidian becomes Memex's structured-recall layer for free.
