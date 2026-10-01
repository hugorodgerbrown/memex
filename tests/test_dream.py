"""Tests for the dream-cycle consolidation pass."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_dream_flags_mentioned_but_unlinked_memories(make_config, write_memory) -> None:
    """A memory naming another by its exact slug, without a wikilink, is flagged."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "use-tox", body="Always run tox before a PR.")
    write_memory(
        scope,
        "ci-checklist",
        body="Remember use-tox and lint before pushing. See [[does-not-exist]].",
    )
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, scope, store)

    assert ("ci-checklist", "use-tox") in report.missing_links
    assert ("use-tox", "ci-checklist") not in report.missing_links


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


def _write_recall_log(path: Path, hit_names: list[list[str]]) -> None:
    """Write a synthetic recall log with one record per element of ``hit_names``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for names in hit_names:
            record = {
                "ts": "2026-06-26T00:00:00Z",
                "cwd": "",
                "prompt": "test",
                "hits": [
                    {
                        "name": name,
                        "scope": "global",
                        "mtype": "reference",
                        "score": 1.0,
                    }
                    for name in names
                ],
            }
            handle.write(json.dumps(record) + "\n")


def test_dream_flags_frequently_coretrieved_unlinked_pairs(
    make_config, write_memory, tmp_path
) -> None:
    """Two unlinked memories retrieved together often enough are flagged."""
    recall_log_path = tmp_path / "recall.log"
    cfg = make_config(recall_log=recall_log_path, cooccurrence_min=3)
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="first memory")
    write_memory(scope, "beta", body="second memory")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)
    _write_recall_log(recall_log_path, [["alpha", "beta"]] * 3)

    report = dream.run(cfg, scope, store)

    assert ("alpha", "beta", 3) in report.coretrieved


def test_dream_ignores_coretrieved_pairs_below_threshold(
    make_config, write_memory, tmp_path
) -> None:
    """A pair retrieved together fewer than the minimum times is not flagged."""
    recall_log_path = tmp_path / "recall.log"
    cfg = make_config(recall_log=recall_log_path, cooccurrence_min=3)
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="first memory")
    write_memory(scope, "beta", body="second memory")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)
    _write_recall_log(recall_log_path, [["alpha", "beta"]] * 2)

    report = dream.run(cfg, scope, store)

    assert report.coretrieved == []


def test_dream_ignores_coretrieved_pairs_already_linked(
    make_config, write_memory, tmp_path
) -> None:
    """A pair that already wikilinks each other is not flagged again."""
    recall_log_path = tmp_path / "recall.log"
    cfg = make_config(recall_log=recall_log_path, cooccurrence_min=3)
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="links to [[beta]]")
    write_memory(scope, "beta", body="second memory")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)
    _write_recall_log(recall_log_path, [["alpha", "beta"]] * 3)

    report = dream.run(cfg, scope, store)

    assert report.coretrieved == []


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


def test_dream_project_link_to_global_memory_resolves(
    make_config, write_memory
) -> None:
    """A project memory linking a global memory is not a broken link."""
    cfg = make_config(("global", "project"))
    global_scope, project_scope = cfg.scopes
    write_memory(global_scope, "use-tox", body="Always run tox before a PR.")
    write_memory(
        project_scope,
        "ci-checklist",
        body="See [[use-tox]] and [[does-not-exist]].",
    )
    store = Store(cfg, project_scope)
    index.sync(cfg, project_scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, project_scope, store)

    assert ("ci-checklist", "use-tox") not in report.broken_links
    assert ("ci-checklist", "does-not-exist") in report.broken_links


def test_dream_global_link_to_project_memory_is_broken(
    make_config, write_memory
) -> None:
    """A global memory cannot depend on one project's memory."""
    cfg = make_config(("global", "project"))
    global_scope, project_scope = cfg.scopes
    write_memory(project_scope, "snowdesk-only", body="Project fact.")
    write_memory(global_scope, "general-rule", body="See [[snowdesk-only]].")
    store = Store(cfg, global_scope)
    index.sync(cfg, global_scope, store, embeddings.build(cfg), rebuild=True)

    report = dream.run(cfg, global_scope, store)

    assert ("general-rule", "snowdesk-only") in report.broken_links
