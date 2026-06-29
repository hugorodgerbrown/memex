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
implemented. The remaining gap is using bitemporal logic in query-time resolution — e.g.
surfacing the most recently true version of a fact without requiring explicit merging.)*

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
