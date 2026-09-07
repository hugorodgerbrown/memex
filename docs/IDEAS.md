# Ideas backlog

Candidate improvements for Memex that are promising but not yet actionable. The
weekly [self-update routine](self-update-routine.md) appends here when it finds a
worthwhile direction that does not yet warrant a code change; entries are picked
up by hand later.

<!-- One bullet per idea: a short title, the source/link, and why it fits Memex. -->

## Zep / Graphiti — bitemporal edge annotation for contradiction handling

**Source:** Zep — *Zep: A Temporal Knowledge Graph Architecture for Agent Memory*,
arXiv:2501.13956 (January 2025)  
**Benchmark:** 94.8 % on DMR; 18.5 % accuracy gain + 90 % latency reduction on
LongMemEval versus prior systems.

Graphiti (Zep's retrieval engine) attaches two timestamps to every stored fact:

- **event time** — when the fact was true in the real world (e.g. "Alice joined Meta
  on 1 Oct 2024").
- **ingestion time** — when the agent first recorded it.

When the agent later learns "Alice now works at Google", both facts are preserved with
their temporal context; the query layer resolves which is currently true, rather than
silently overwriting the older one.

**Why this fits Memex:** The dream cycle currently flags pairs whose cosine similarity
exceeds `MEMEX_DEDUP_THRESHOLD` and asks the user to decide whether to merge them.
It cannot tell whether two similar memories are genuinely redundant or represent a
*fact that changed over time*. A near-duplicate pair like "prefers dark mode" /
"switched back to light mode" should not be merged — one supersedes the other.

Adding an optional `event_date` frontmatter field (distinct from the file's `mtime`,
which records when the *file* was written rather than when the *event* occurred) would
give the dream cycle enough signal to distinguish the two cases:

- Same cosine similarity ≥ threshold, **similar** `event_date` → flag as *duplicate*
  candidate (current behaviour).
- Same cosine similarity ≥ threshold, **differing** `event_date` → flag as
  *supersession* candidate (one memory appears to update the other; suggest archiving
  the older fact rather than merging).

**Concrete first step:** Parse an optional `event_date: YYYY-MM-DD` key from memory
frontmatter in `markdown.py`; surface it in `DreamReport`; update `write_report` in
`dream.py` to split the current "Candidate duplicates" section into "Duplicates" and
"Possible supersessions" based on whether event dates differ.

*(Note: `event_date` parsing and supersession detection in the dream cycle are now
implemented. Query-time resolution is also implemented: `retrieve._suppress_superseded`,
gated behind `MEMEX_RESOLVE_SUPERSESSIONS` (default off), drops the older side of a
near-duplicate pair from a scope's candidate pool before it competes for a recall slot —
see "Don't Ask the LLM to Track Freshness: A Deterministic Recipe for Memory Conflict
Resolution", arXiv:2606.01435 (June 2026), which validates keeping freshness resolution
in deterministic code rather than delegating it to an LLM.)*

---

## vstash — adaptive IDF-weighted RRF for query-type-aware fusion

**Source:** vstash — *Local-First Hybrid Retrieval with Adaptive Fusion for LLM Agents*,
arXiv:2604.15484 (April 2026); <https://github.com/stffns/vstash>  
**Benchmark:** +21.4 % NDCG@10 on ArguAna over static-weight RRF; 0.7263 on SciFact
(both evaluated on the SQLite-native stack vstash shares with Memex).

Memex currently fuses vector-KNN and BM25 results with equal-weight Reciprocal Rank
Fusion: each retrieval channel contributes `1 / (k + rank)` regardless of what the
query contains. vstash identifies a gap: the optimal split between lexical and semantic
retrieval depends on the query itself.

Rare or technical query terms (high inverse document frequency) favour exact lexical
matching — a query for `"subprocess.SubprocessError"` or `"rrf_k"` should weight BM25
more heavily, because those strings appear verbatim in relevant memories and not at all
in irrelevant ones. Common or conceptual terms (low IDF) favour vector similarity —
`"how do I handle failing tests?"` has no distinctive keywords, so embedding distance
dominates.

vstash implements this with a per-query sigmoid weighting step:

1. Stem and look up the query tokens' document frequency from the FTS5 corpus.
2. Compute the mean IDF (`log(N / df)`) across query tokens.
3. Pass it through a sigmoid to obtain α ∈ (0, 1): high IDF → α near 1 (BM25-heavy);
   low IDF → α near 0 (vector-heavy).
4. Weight the RRF contributions: `α · 1/(k+r_fts) + (1−α) · 1/(k+r_vec)` in place of
   the current equal sum.

**Why this fits Memex:** vstash uses sqlite-vec + FTS5 — exactly Memex's stack — so
no new dependencies are needed. The improvement is most visible when users mix
exact-reference queries ("the `event_date` field", "MEMEX_DEDUP_THRESHOLD") with
conceptual ones ("what style rules apply here?"). Memex's memory set is small enough
that per-query IDF lookups are cheap.

**Concrete first step:** In `retrieve.py:_fused_candidates`, add a helper that queries
`SELECT COUNT(*) FROM fts_memories WHERE fts_memories MATCH '"<token>"'` for each
query token to get document frequency, then computes mean IDF over the corpus size
(`store.count()`). Apply a sigmoid centred around IDF ≈ 2.0 to produce α, and use α
to weight the FTS and vector RRF terms. Guard the behaviour behind a new
`MEMEX_ADAPTIVE_RRF` env-var (default off) so existing installs are unaffected; add
`adaptive_rrf: bool` to `Config`.

*(Implemented: `Store.document_frequency`/`Store.tokenize` and `retrieve._fts_weight`
compute the per-query α, gated behind `MEMEX_ADAPTIVE_RRF` — default off, so existing
installs see no behaviour change.)*

---

## Letta / MemGPT — pinned core-memory blocks that bypass ranked recall

**Source:** MemGPT — *MemGPT: Towards LLMs as Operating Systems*, Packer et al.,
arXiv:2310.08560 (October 2023); Letta — *Memory Blocks: The Key to Agentic Context
Management*, <https://www.letta.com/blog/memory-blocks/>.

Letta (the production framework built on the MemGPT paper) splits agent memory into
tiers, but the load-bearing primitive is the **memory block**: a small, labelled string
(conventionally `persona` and `human`) that lives permanently in the context window and
is edited *in place* by the agent itself, via explicit tool calls
(`core_memory_append`/`core_memory_replace`), rather than being retrieved by a ranked
search. Everything else — conversation history, archival facts — is paged in by
relevance, the same way Hermes' two-tier split works.

**Why this fits Memex:** Memex already took the Hermes two-tier idea (global scope as
the small always-on core, project scope as the larger searched archive — see the README
provenance line), but the global scope is not literally always-on: it competes in the
same top-`k` hybrid ranking as every other memory, so a handful of highly-relevant
project hits can push every global memory out of the injected set on a given prompt.
There is also no notion of "this memory is small and load-bearing enough that Claude
should edit it in place" — every write, whether a brand-new fact or a correction to an
existing one, is a new or replaced Markdown file with no distinction from the rest of
the store. A `pinned` memory type would close both gaps at once: guaranteed injection,
independent of ranking, for the handful of facts (e.g. "always run tox before a PR")
that should never be one bad query away from being dropped.

**Concrete first step:** Add an optional `pinned: true` frontmatter flag (parsed in
`markdown.py`, alongside the existing `event_date` field) that marks a memory as
core-tier. In `retrieve.retrieve()`, after fusing the ranked pool, unconditionally
include every scope's pinned memories ahead of the ranked hits (tagged `via="pinned"`
so they are visible as bypassing decay/rank), capped by a new `MEMEX_PINNED_MAX`
tunable (small default, e.g. 5) so an install cannot accidentally pin its way to an
unbounded prompt. Document the field in the README's memory-authoring section and
`memex add`'s `--pinned` flag would be the natural CLI affordance, though the frontmatter
key alone is enough for a first cut.

---

## RecMem — recurrence-gated distillation to cut SessionEnd token cost

**Source:** *RecMem: Recurrence-based Memory Consolidation for Efficient and
Effective Long-Running LLM Agents*, ACL 2026 Findings; arXiv:2605.16045;
<https://github.com/CaiusDai/RecMem>  
**Benchmark:** up to 7.8x lower memory-construction token cost than prior systems
on LoCoMo, with higher accuracy on both LoCoMo and LongMemEval-S despite spending
far fewer tokens.

RecMem's agents buffer every incoming interaction in a cheap "subconscious" layer
indexed with lightweight local embeddings — no LLM involved. An interaction is only
promoted to expensive LLM-based extraction once it finds a sufficient number of
semantically similar predecessors already in the buffer: recurrence is the signal
that a topic is durable and worth the extraction cost, rather than extracting
eagerly on every interaction and mostly re-deriving facts the store already has.

**Why this fits Memex:** the `SessionEnd` hook calls a model (`MEMEX_DISTILL_MODEL`,
default Haiku) on every finished session when distillation is enabled, unconditionally
— the README's own cost note flags this as "not free". Many sessions revisit the same
ground (the same tooling preference, the same recurring gotcha in a codebase) that a
prior session already proposed or that already lives in the store, so a fair share of
those calls extract nothing new. Gating the call on recurrence — has this session's
content come up before, recently, without yet becoming a memory? — would cut wasted
calls the same way RecMem cuts them, using infrastructure Memex already has: the
fastembed embedder that powers the main index.

RecMem's granularity is per-interaction within one long-running conversation; Memex's
distillation unit is a whole finished session, so the mapping is not one-to-one and
needs a real design pass (what "recurrence" means across separate sessions, how long
a candidate topic waits before its recurrence expires, where the buffer lives and how
it is pruned) rather than a mechanical port — hence an idea, not a first cut.

**Concrete first step:** embed each session's condensed transcript (already computed
in `distill.condense_transcript`) with the existing `Embedder`, and keep a small
rolling buffer of recent session embeddings per scope (e.g.
`<scope>/.memex/distill_buffer.jsonl`, capped by count and age). Before calling
`call_model`, compare the new embedding against the buffer via cosine similarity; only
proceed to the LLM extraction when at least `MEMEX_DISTILL_RECURRENCE_MIN` (default
2) prior buffered sessions clear a similarity threshold, otherwise just append to the
buffer and skip the call. Default the threshold high enough that it never blocks a
single novel session's first-ever distillation from happening eventually, once it
recurs.
*(Implemented: `pinned: true` frontmatter parses into `MemoryFile.pinned`;
`Store.pinned_ids` and `retrieve._pinned_candidates` guarantee those memories a
recall slot ahead of the ranked pool, capped by `MEMEX_PINNED_MAX` — default 5, and
`memex add --pinned` writes the flag.)*

---

## sqlite-graph-memory — cross-encoder rerank of the fused + graph-expanded pool

**Source:** sqlite-graph-memory — a small (MIT, ~3-star) pilot combining wikilink
graph expansion with cross-encoder reranking over a markdown vault;
<https://github.com/Palo-Alto-AI-Research-Lab/sqlite-graph-memory>. No published
benchmark; the repo ships its own `ab_recall` telemetry table logging how often
graph expansion promotes a note into the top-N on real queries, rather than a
paper-style number.

Its retrieval pipeline: dense-retrieve top-60 chunks, expand the top-15 hits by one
wikilink hop (capped at 40 neighbours), then rerank the *pooled* candidates with a
cross-encoder (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) down to the final
top-12. The key design point, stated directly in its docs: "graph expansion is
candidate generation, not ranking" — a linked neighbour is not assumed relevant
just because it is linked; it competes in the same reranked pool as everything
else, and an irrelevant one gets demoted rather than surfaced by association.

**Why this fits Memex:** `retrieve._expand` (`src/memex/retrieve.py`) appends every
un-visited `[[wikilink]]` neighbour of the top hit with a hard-coded score of
`0.0` and `via="graph:<name>"` — see `_expand`'s loop, which never scores or
filters what it adds. A memory two years stale that happens to link the top hit
is injected exactly as confidently as a fresh, tightly on-topic one; there is no
mechanism to catch a graph expansion that turned out to be off-topic for *this*
query. A rerank pass over the fused RRF pool plus its graph-expanded neighbours,
scored against the actual query text rather than assumed-relevant by link
proximity, would close that gap using the same "pool, then rerank" shape as
`sqlite-graph-memory`.

The trade-off is real and is exactly why this is an idea rather than a first cut:
a cross-encoder is a second model on top of the existing fastembed embedder, adds
a new dependency, and runs on every `UserPromptSubmit` invocation — the README
already flags that hook's cost as "not free" even without a second model in the
loop. Any implementation needs to default off, be benchmarked against plain RRF +
graph expansion on Memex's own memory sets (much smaller than a typical RAG
corpus, where a cross-encoder's per-candidate cost matters less), and pick a
model small enough not to meaningfully change hook latency.

**Concrete first step:** add an optional reranker in `retrieve.py`, gated behind a
new `MEMEX_RERANK` env var (default off) and `Config.rerank: bool`. When enabled,
after `_expand` produces the candidate pool (ranked hits + graph neighbours) and
before the `pinned + selected` merge, score each candidate's `(query, body)` pair
with a small local cross-encoder (an ONNX cross-encoder via `fastembed`'s own
`TextCrossEncoder`, which the project already depends on transitively, would avoid
a new heavyweight dependency) and re-sort by that score instead of the RRF score.
Leave pinned memories untouched — they are meant to bypass ranking entirely.

---

## ReMe — active digest consolidation vs. the dream cycle's advisory-only stance

**Source:** ReMe — *Memory Management Kit for Agents*;
<https://github.com/agentscope-ai/ReMe>. Apache-2.0, ~3.4k GitHub stars, active
as of 2026.08; ACL 2026 Findings.

ReMe is close enough to Memex's own design to read as convergent evolution: durable
memory as plain Markdown with YAML frontmatter and `[[wikilinks]]` ("memory as
file, file as memory"), BM25 by default with optional vector search fused by RRF,
and a periodic `auto_dream` pass — the same name Memex independently settled on.
The difference is what that pass is allowed to do. ReMe's workspace is layered
(`session/` → `daily/` → `digest/`), and `auto_dream` actively *writes*: it
extracts reusable units from recent files and creates, corroborates, refines, or
corrects nodes in the `digest/` layer — a standing, evolving summary tier that
consolidation itself maintains.

**Why this fits Memex, and why it's an idea rather than a first cut:** Memex's own
`dream.py` module docstring states the current design is "deliberately advisory:
it writes a dated report and updates salience scores, but it never edits or
deletes a memory file… this keeps the 'never silently destroy memory' guarantee
every system surveyed learned the hard way." ReMe shows that guarantee doesn't
have to mean *no consolidation writes at all* — corroborate/refine/correct on a
distinct `digest/`-style tier, separate from the human-or-Claude-authored source
files, could let the dream cycle synthesise a standing summary (e.g. "what this
project's memories currently say about X") without ever touching an original
memory file. That is a real design decision (a new memory class, ownership rules
for who edits a digest node, how it interacts with decay and citation) rather than
a mechanical port, hence backlog rather than a PR.

**Concrete first step, if picked up:** prototype a single opt-in digest file per
scope (`<scope>/.memex/digest.md`, clearly marked machine-generated, excluded from
`memex add`/`memex promote`) that `memex dream` regenerates each run by summarising
the current top-salience memories in that scope — read-only relative to the real
memory files, so it adds a new artefact rather than touching the "never silently
destroy memory" guarantee. Index it as an ordinary low-priority recall candidate
and see whether it measurably helps broad, cross-memory questions that no single
memory answers well today.

---

## OKF — a standard trust vocabulary for memory frontmatter

**Source:** Open Knowledge Format v0.2 — a specification, not a system;
<https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>.
Published by Google Cloud, Apache-2.0. No benchmark and no observable adoption
outside its own repo, so the interop argument for it is speculative today; the
value here is the vocabulary, not conformance.

OKF specifies a "knowledge bundle" as a directory of Markdown files with YAML
frontmatter — no schema registry, no runtime, no SDK. Conformance is deliberately
trivial (parseable frontmatter plus a non-empty `type`), and consumers MUST NOT
reject a document for missing optional fields, unknown types, unknown keys, or
broken links. On top of that floor it standardises four optional field families
aimed squarely at a corpus that agents write and maintain rather than humans:

- **Provenance** — `sources: [{id, resource, title, author, usage_count,
  last_modified}]` with a `usage_window: {from, to}` sibling. It records objective
  per-source signals and deliberately stores no credibility *score*, on the
  grounds that a score is subjective, unportable, and goes stale.
- **Trust** — `generated: {by, at}` (who produced the content) kept distinct from
  `verified: [{by, at}]` (who has since confirmed it), because the writer is not
  the confirmer. Actors follow one convention: `human:<id>`, `<producer>/<version>`
  for agents, `process:<id>`. Consumers derive a tier from `verified` alone —
  absent ⇒ unverified, non-human actors ⇒ machine-confirmed, a `human:` actor ⇒
  human-reviewed.
- **Lifecycle** — `status: draft | stable | deprecated` (absent ⇒ stable) and
  `stale_after`, an absolute instant rather than a relative TTL so staleness is a
  plain `now >= stale_after` comparison with no reference to read time.
- **Attestation** — `type: Attested Computation` with `runtime`, `parameters`,
  `executor`, `attester`, for confirming a number was produced by running the
  sanctioned computation. This family is data-catalog machinery with no analogue
  in a personal memory store and is not proposed here.

**Why this fits Memex:** Memex is already structurally an OKF bundle by accident —
Markdown files, YAML frontmatter, a `type`, a `description` — and `distill._render`
has independently reinvented a private, nested subset of exactly these families:
`metadata.status: proposed`, `metadata.source: distill`, `metadata.origin_session`.
The reinvention is lossy in one specific place. `distill.accept` promotes a
candidate by string-replacing `  status: proposed\n` out of the file and writing
it to the scope's memory dir; it records nothing about *who* accepted it or *when*.
The README frames the accept as the system's central guarantee — "the **accept is
the gate** — nothing the model proposes enters the live store on its own" — yet the
single most important trust event in the pipeline leaves no trace on disk. Once
accepted, a distilled memory and a hand-authored one are distinguishable only by a
vestigial `source: distill` key. OKF's `verified: {by: human:<id>, at: <ts>}` is
that missing record, and its trust tiers are a direct read of the gate Memex
already enforces.

Three of the other fields close gaps the backlog has already circled:
`generated: {by, at}` names which model proposed a memory, so "why does Memex think
I prefer X" has an audit trail beyond an opaque `origin_session` id; `status:
deprecated` gives the dream cycle's supersession detection somewhere to write a
verdict, a third option between deleting a file and letting decay quietly sink it,
without breaking the "never silently destroy memory" guarantee `dream.py` states;
and `stale_after` adds a deterministic, absolute staleness check alongside decay's
soft relative re-ranking, continuing the line of "keep freshness resolution in
deterministic code" already argued in the Zep/Graphiti entry above. A fifth field,
`usage_count` over a `usage_window`, is data `recall_log.py` already collects per
turn and never surfaces back onto the memory.

**Why this is an idea rather than a first cut:** the fields are cheap, but the
decisions around them are not. Trust tiers are only worth recording if something
*reads* them — whether an unverified memory should be ranked below a human-verified
one, or excluded from hook injection entirely, is a retrieval policy change, not a
frontmatter change. `status: deprecated` needs an owner (does `memex dream` write
it, or only propose it?) before it can be honoured at query time. And full OKF
conformance is explicitly not the goal: `[[wikilinks]]` are load-bearing for the
entity graph in `markdown.py` and OKF has no wikilink concept, `MEMORY.md` collides
with the reserved `index.md`, and renaming `name` to OKF's `title` is churn across
the store and index for no benefit. Memex also carries `event_date` and `pinned`,
which OKF has no equivalent for — bitemporality is a thing Memex has and the spec
lacks, so this is selective borrowing in both directions.

**Concrete first step, if picked up:** flatten and standardise what distillation
already writes, without changing retrieval behaviour. In `distill._render`, emit
top-level `generated: {by: <model-id>, at: <ISO-8601 UTC>}` alongside the existing
`metadata` block; in `distill.accept`, stop string-replacing the status line out
and instead set `status: stable` and append `verified: {by: human:<id>, at: <ts>}`
by parsing and re-emitting the frontmatter, so the gate is recorded rather than
erased. Parse `generated`, `verified`, `status`, and `stale_after` in
`markdown.parse` onto `MemoryFile` and leave them unused by ranking for now. Then
surface the derived trust tier in `memex list` and the dream report, and decide
from real data whether a tier or a passed `stale_after` should influence recall
before wiring either into `retrieve.py`.

*(Partly implemented: `distill._render` now emits `generated: {by, at}` when a
candidate is staged, and `distill.accept` stamps `verified: {by: human:<user>,
at}` in place of silently erasing `status: proposed` — the accept gate the
README calls central now leaves a trace. `markdown.parse` reads both maps onto
`MemoryFile.generated_by/_at/verified_by/_at`, unused by ranking. Still open:
`status`/`stale_after`, and surfacing the derived trust tier in `memex list`
and the dream report.)*
