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


def test_document_frequency_counts_matching_memories(make_config, write_memory) -> None:
    """document_frequency counts how many indexed memories contain a token."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="rrf_k tuning notes")
    write_memory(scope, "beta", body="general style guidance")
    write_memory(scope, "gamma", body="general formatting rules")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    assert store.document_frequency("rrf_k") == 1
    assert store.document_frequency("general") == 2
    assert store.document_frequency("nonexistent") == 0


def test_fts_weight_rises_as_the_query_token_gets_rarer(
    make_config, write_memory
) -> None:
    """A rarer query token yields a higher keyword-channel weight than a common one."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "alpha", body="rrf_k tuning notes")
    write_memory(scope, "beta", body="general style guidance")
    write_memory(scope, "gamma", body="general formatting rules")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    assert retrieve._fts_weight(store, "rrf_k") > retrieve._fts_weight(store, "general")


class _FakeStore:
    """A minimal stand-in exposing the subset of Store used by _fused_candidates."""

    def __init__(
        self,
        knn_hits: list[tuple[int, float]],
        fts_hits: list[tuple[int, float]],
        corpus_size: int,
        doc_freqs: dict[str, int],
    ) -> None:
        self._knn_hits = knn_hits
        self._fts_hits = fts_hits
        self._corpus_size = corpus_size
        self._doc_freqs = doc_freqs

    def knn(self, query_vec: list[float], n: int) -> list[tuple[int, float]]:
        return self._knn_hits

    def fts(self, query: str, n: int) -> list[tuple[int, float]]:
        return self._fts_hits

    def decay_multiplier(self, memory_id: int) -> float:
        return 1.0

    def count(self) -> int:
        return self._corpus_size

    def document_frequency(self, token: str) -> int:
        return self._doc_freqs.get(token, 0)


def test_adaptive_rrf_raises_score_of_a_keyword_only_hit(make_config) -> None:
    """A memory found only by the keyword channel scores higher once the rare
    query token pushes alpha above the disabled fusion's fixed 50/50 split."""
    cfg_off = make_config(adaptive_rrf=False)
    cfg_on = make_config(adaptive_rrf=True)
    store = _FakeStore(
        knn_hits=[],
        fts_hits=[(1, 0.0)],
        corpus_size=10,
        doc_freqs={"rrf_k": 1},
    )

    off_score = retrieve._fused_candidates(cfg_off, store, [0.0], "rrf_k")[0][1]
    on_score = retrieve._fused_candidates(cfg_on, store, [0.0], "rrf_k")[0][1]
    assert on_score > off_score


# A long shared body so two memories differing only by name embed near-identically
# under the hash backend, exceeding the dedup threshold (mirrors test_dream.py).
_SHARED = " ".join(["consolidation", "memory", "vector", "index", "recall"] * 8)


def test_resolve_supersessions_drops_the_older_near_duplicate(
    make_config, write_memory
) -> None:
    """With resolution on, a stale near-duplicate loses its recall slot to the
    memory that superseded it; with it off (the default), both surface."""
    cfg_off = make_config(resolve_supersessions=False)
    cfg_on = make_config(resolve_supersessions=True)
    scope = cfg_off.scopes[0]
    write_memory(scope, "old-fact", body=_SHARED, event_date="2025-01-01")
    write_memory(scope, "new-fact", body=_SHARED, event_date="2026-01-01")
    store = Store(cfg_off, scope)
    index.sync(cfg_off, scope, store, embeddings.build(cfg_off), rebuild=True)

    hits_off = retrieve.retrieve(
        cfg_off, [store], embeddings.build(cfg_off), _SHARED, k=2, expand_graph=False
    )
    hits_on = retrieve.retrieve(
        cfg_on, [store], embeddings.build(cfg_on), _SHARED, k=2, expand_graph=False
    )

    assert {h.name for h in hits_off} == {"old-fact", "new-fact"}
    assert {h.name for h in hits_on} == {"new-fact"}


def test_pinned_memory_recalls_regardless_of_relevance(
    make_config, write_memory
) -> None:
    """A pinned memory surfaces even when the query is entirely unrelated to it."""
    cfg = make_config()
    scope = cfg.scopes[0]
    write_memory(scope, "core-rule", body="always run tox before a PR", pinned=True)
    write_memory(scope, "unrelated", body="notes about teapots and kettles")
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    hits = retrieve.retrieve(
        cfg, [store], embeddings.build(cfg), "teapots", k=1, expand_graph=False
    )

    names = {h.name: h.via for h in hits}
    assert names["core-rule"] == "pinned"
    assert "unrelated" in names


def test_pinned_max_caps_the_pinned_pool(make_config, write_memory) -> None:
    """No more than ``pinned_max`` pinned memories are guaranteed a slot."""
    cfg = make_config(pinned_max=1)
    scope = cfg.scopes[0]
    write_memory(scope, "pin-one", body="first pinned rule", pinned=True)
    write_memory(scope, "pin-two", body="second pinned rule", pinned=True)
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    pinned = retrieve._pinned_candidates([store], cfg.pinned_max)
    assert len(pinned) == 1


def test_resolve_supersessions_ignores_pairs_without_event_dates(
    make_config, write_memory
) -> None:
    """Near-duplicates with no ``event_date`` are never suppressed, even when on."""
    cfg = make_config(resolve_supersessions=True)
    scope = cfg.scopes[0]
    write_memory(scope, "dup-one", body=_SHARED)
    write_memory(scope, "dup-two", body=_SHARED)
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)

    hits = retrieve.retrieve(
        cfg, [store], embeddings.build(cfg), _SHARED, k=2, expand_graph=False
    )

    assert {h.name for h in hits} == {"dup-one", "dup-two"}


def _two_scopes(
    cfg: Config, write_memory: Callable[..., object], *, project_body: str
) -> list[Store]:
    """Index a global and a project scope; return their stores in that order."""
    global_scope, project_scope = cfg.scopes
    write_memory(global_scope, "beta", body="a lazy sleeping dog in the yard")
    write_memory(global_scope, "gamma", body="entirely unrelated content about teapots")
    write_memory(project_scope, "alpha", body=project_body)
    stores = []
    for scope in (global_scope, project_scope):
        store = Store(cfg, scope)
        index.sync(cfg, scope, store, embeddings.build(cfg), rebuild=True)
        stores.append(store)
    return stores


def test_graph_expansion_follows_project_link_into_global(
    make_config, write_memory
) -> None:
    """A project top hit's link to a global memory pulls that memory in."""
    cfg = make_config(("global", "project"))
    stores = _two_scopes(
        cfg, write_memory, project_body="the quick brown fox jumps over [[beta]]"
    )
    hits = retrieve.retrieve(
        cfg, stores, embeddings.build(cfg), "quick brown fox", k=1, expand_graph=True
    )
    by_name = {h.name: h for h in hits}
    assert hits[0].name == "alpha"
    assert by_name["beta"].scope == "global"
    assert by_name["beta"].via == "graph:alpha"


def test_graph_expansion_prefers_own_scope(make_config, write_memory) -> None:
    """A link naming a memory in both scopes resolves in the top hit's scope."""
    cfg = make_config(("global", "project"))
    global_scope, project_scope = cfg.scopes
    write_memory(project_scope, "beta", body="project-local beta about kettles")
    stores = _two_scopes(
        cfg, write_memory, project_body="the quick brown fox jumps over [[beta]]"
    )
    hits = retrieve.retrieve(
        cfg, stores, embeddings.build(cfg), "quick brown fox", k=1, expand_graph=True
    )
    betas = [h for h in hits if h.name == "beta"]
    assert [h.scope for h in betas] == ["project"]
