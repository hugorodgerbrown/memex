"""Tests for indexing, hybrid retrieval, graph expansion, and decay."""

from __future__ import annotations

from collections.abc import Callable

from memex import embeddings, index, retrieve
from memex.config import Config
from memex.store import Store


def _index(cfg: Config, write_memory: Callable[..., object]) -> Store:
    """Populate the global scope with linked memories and index them."""
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="the quick brown fox jumps over [[beta]]")
    write_memory(scope, "beta", body="a lazy sleeping dog in the yard")
    write_memory(scope, "gamma", body="entirely unrelated content about teapots")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)
    return store


def test_retrieve_ranks_lexical_match_first(make_config, write_memory) -> None:
    """A keyword-overlapping memory is the top hit."""
    cfg = make_config()
    store = _index(cfg, write_memory)
    hits = retrieve.retrieve(
        cfg, [store], embeddings.build(cfg), "quick brown fox", k=1, expand_graph=False
    )
    assert hits[0].name == "alpha"
    assert hits[0].scope == "global"


def test_graph_expansion_pulls_linked_neighbour(make_config, write_memory) -> None:
    """The top hit's wikilink neighbour is appended via the graph."""
    cfg = make_config()
    store = _index(cfg, write_memory)
    hits = retrieve.retrieve(
        cfg, [store], embeddings.build(cfg), "quick brown fox", k=1, expand_graph=True
    )
    names = {h.name: h.via for h in hits}
    assert "beta" in names
    assert names["beta"].startswith("graph:")


def test_decay_multiplier_fresh_is_ceiling(make_config, write_memory) -> None:
    """A freshly indexed memory recalls at the decay ceiling."""
    cfg = make_config()
    store = _index(cfg, write_memory)
    multiplier = store.decay_multiplier(store.id_for_name("alpha"))
    assert abs(multiplier - cfg.decay_ceiling) < 0.05


def test_touch_increments_access_count(make_config, write_memory) -> None:
    """Recording access bumps the count used for decay/salience."""
    cfg = make_config()
    store = _index(cfg, write_memory)
    store.touch([store.id_for_name("alpha")])
    counts = {row["name"]: row["access_count"] for row in store.access_summary()}
    assert counts["alpha"] == 1


def test_sync_active_indexes_then_noops(make_config, write_memory) -> None:
    """sync_active indexes pending scopes, and does nothing when current."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="hello world")

    first = index.sync_active(cfg)
    assert [r.scope for r in first] == ["global"]
    assert first[0].added == ["alpha"]

    # Nothing changed → no scopes processed, no embedder built.
    assert index.sync_active(cfg) == []

    store = Store(cfg, scope)
    assert store.count() == 1
    store.close()


def test_prune_removes_deleted_files(make_config, write_memory) -> None:
    """Deleting a memory file soft-deletes it from the index on re-sync."""
    cfg = make_config()
    scope = cfg.scopes[0]
    store = _index(cfg, write_memory)
    (scope.memory_dir / "gamma.md").unlink()
    result = index.sync(cfg, scope, store, embeddings.build(cfg))
    assert result.removed == ["gamma"]
    assert store.id_for_name("gamma") is None
