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


def test_dream_distant_event_dates_are_supersessions(make_config, write_memory) -> None:
    """Near-duplicates with far-apart event dates land in supersessions."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "old-fact", body=_SHARED, event_date="2025-01-01")
    write_memory(scope, "new-fact", body=_SHARED, event_date="2026-01-01")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, scope, store)

    super_pairs = {tuple(sorted((a, b))) for a, b, _sim in report.supersessions}
    dup_pairs = {tuple(sorted((a, b))) for a, b, _sim in report.duplicates}
    assert ("new-fact", "old-fact") in super_pairs
    assert ("new-fact", "old-fact") not in dup_pairs


def test_dream_close_event_dates_stay_duplicates(make_config, write_memory) -> None:
    """Near-duplicates with event dates within 30 days remain duplicates."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "dup-a", body=_SHARED, event_date="2026-01-01")
    write_memory(scope, "dup-b", body=_SHARED, event_date="2026-01-15")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, scope, store)

    dup_pairs = {tuple(sorted((a, b))) for a, b, _sim in report.duplicates}
    super_pairs = {tuple(sorted((a, b))) for a, b, _sim in report.supersessions}
    assert ("dup-a", "dup-b") in dup_pairs
    assert ("dup-a", "dup-b") not in super_pairs


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
