"""Tests for the dream-cycle consolidation pass."""

from __future__ import annotations

from memex import dream, embeddings, index
from memex.store import Store

# A long shared body so two memories differing only by name embed near-identically
# under the hash backend, exceeding the dedup threshold.
_SHARED = " ".join(["consolidation", "memory", "vector", "index", "recall"] * 8)


def test_dream_flags_duplicates_and_broken_links(make_config, write_memory) -> None:
    """Near-duplicates are flagged and dangling wikilinks are reported."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "dup-one", body=_SHARED)
    write_memory(scope, "dup-two", body=_SHARED)
    write_memory(scope, "linker", body="points at [[does-not-exist]]")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, scope, store)

    dup_pairs = {tuple(sorted((a, b))) for a, b, _sim in report.duplicates}
    assert ("dup-one", "dup-two") in dup_pairs
    assert ("linker", "does-not-exist") in report.broken_links
    assert report.total == 3


def test_dream_writes_report(make_config, write_memory, tmp_path) -> None:
    """A dated report file is written for the scope."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "solo", body="a single memory")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, scope, store)
    path = dream.write_report(scope, report, today="2026-06-26")
    assert path.exists()
    assert "Memex dream cycle" in path.read_text(encoding="utf-8")
