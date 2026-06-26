"""Memex — a layered long-term memory store for Claude Code.

Markdown memory files remain the system of record; this package builds a derived
index (vector + keyword + entity graph) over them, serves hybrid recall with
decay re-ranking, and runs a nightly consolidation ("dream") pass. See README.md
for the design and its provenance (Mem0, GBrain, MemSearch, Hermes).
"""

__version__ = "0.1.0"
