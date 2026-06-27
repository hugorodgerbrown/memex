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
