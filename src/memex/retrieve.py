"""Layered hybrid recall across scopes: vector + keyword, decay, graph.

The retrieval pipeline, in order:

1. Embed the query once.
2. For each active scope (global, project), take the vector KNN neighbours and
   the BM25 keyword matches and fuse them with Reciprocal Rank Fusion, then
   multiply each fused score by its decay multiplier (recency/frequency). When
   ``config.adaptive_rrf`` is set, the vector/keyword split is weighted per query
   by mean inverse document frequency, rather than fixed at 50/50. When
   ``config.resolve_supersessions`` is set, a candidate that the dream cycle
   would flag as superseded (a near-duplicate with an older ``event_date``) is
   dropped from its scope's pool, so a stale fact does not spend a recall slot
   competing with the memory that replaced it.
3. Merge the per-scope candidate pools and take the top ``k`` overall, so a
   strongly-relevant global memory can outrank a weakly-relevant project one and
   vice versa. Each hit is tagged with the scope it came from.
4. Optionally pull in one hop of ``[[wikilink]]`` neighbours of the top hit
   (within its own scope) so structured recall returns a connected cluster.
5. Every scope's ``pinned: true`` memories are prepended ahead of the ranked
   hits, up to ``config.pinned_max`` total — a guaranteed core tier that never
   competes for a ranked slot (Letta/MemGPT's pinned core-memory blocks).
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass

from .config import Config
from .embeddings import Embedder
from .store import Store, tokenize

_CANDIDATES = 20
# Mean IDF at which the fusion is exactly 50/50 vector/keyword.
_IDF_MIDPOINT = 2.0
# A near-duplicate pair whose ``event_date`` values differ by more than this many
# days is a supersession rather than a redundant duplicate — the same threshold
# the dream cycle uses offline (see ``dream._SUPERSESSION_GAP_DAYS``).
_SUPERSESSION_GAP_DAYS = 30

# A scored retrieval candidate: (store, memory_id, score, multiplier, via-label).
Candidate = tuple["Store", int, float, float, str]


@dataclass
class Hit:
    """A single recalled memory with its scoring provenance and scope."""

    name: str
    path: str
    mtype: str
    description: str
    body: str
    links: list[str]
    score: float
    multiplier: float
    via: str
    scope: str


def _fts_weight(store: Store, query: str) -> float:
    """Return the keyword-channel share ``alpha`` of the RRF fusion, in (0, 1).

    Rare or technical query tokens (high mean IDF across the corpus) favour
    exact lexical matching, so ``alpha`` moves toward 1; common or conceptual
    tokens (low mean IDF) favour vector similarity, so it moves toward 0. A
    mean IDF of ``_IDF_MIDPOINT`` gives an even 0.5 split.
    """
    tokens = tokenize(query)
    corpus_size = store.count()
    if not tokens or corpus_size == 0:
        return 0.5
    mean_idf = sum(
        math.log(corpus_size / max(store.document_frequency(token), 1))
        for token in tokens
    ) / len(tokens)
    return 1.0 / (1.0 + math.exp(-(mean_idf - _IDF_MIDPOINT)))


def _parse_date(value: str | None) -> dt.date | None:
    """Parse a ``YYYY-MM-DD`` event date, tolerating ``None`` and bad input."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (already unit-norm)."""
    return sum(x * y for x, y in zip(a, b, strict=False))


def _suppress_superseded(
    store: Store, candidates: list[tuple[int, float, float]], threshold: float
) -> list[tuple[int, float, float]]:
    """Drop the older side of any near-duplicate pair that has been superseded.

    Reuses the dream cycle's offline signal — cosine similarity above the dedup
    threshold plus a gap in ``event_date`` — at query time. Only candidates that
    both carry an explicit ``event_date`` can ever be dropped, so a memory
    without one is never suppressed.
    """
    if len(candidates) < 2:
        return candidates
    ids = [memory_id for memory_id, _score, _mult in candidates]
    dates = {
        memory_id: _parse_date(store.hydrate(memory_id)["event_date"])
        for memory_id in ids
    }
    drop: set[int] = set()
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            id_a, id_b = ids[i], ids[j]
            date_a, date_b = dates[id_a], dates[id_b]
            if date_a is None or date_b is None:
                continue
            if abs((date_a - date_b).days) <= _SUPERSESSION_GAP_DAYS:
                continue
            if _cosine(store.embedding(id_a), store.embedding(id_b)) < threshold:
                continue
            drop.add(id_a if date_a < date_b else id_b)
    return [c for c in candidates if c[0] not in drop]


def _fused_candidates(
    config: Config, store: Store, query_vec: list[float], query: str
) -> list[tuple[int, float, float]]:
    """Return ``(memory_id, fused_score, multiplier)`` candidates for one store."""
    vec_hits = store.knn(query_vec, n=_CANDIDATES)
    fts_hits = store.fts(query, n=_CANDIDATES)

    ranks: dict[int, dict[str, int]] = {}
    for rank, (memory_id, _distance) in enumerate(vec_hits, start=1):
        ranks.setdefault(memory_id, {})["vec"] = rank
    for rank, (memory_id, _score) in enumerate(fts_hits, start=1):
        ranks.setdefault(memory_id, {})["fts"] = rank

    # At alpha=0.5 these weights are both 1.0, matching the unweighted fusion.
    alpha = _fts_weight(store, query) if config.adaptive_rrf else 0.5
    weights = {"fts": 2.0 * alpha, "vec": 2.0 * (1.0 - alpha)}

    candidates: list[tuple[int, float, float]] = []
    for memory_id, positions in ranks.items():
        rrf = 0.0
        for source in ("vec", "fts"):
            if source in positions:
                rrf += weights[source] / (config.rrf_k + positions[source])
        multiplier = store.decay_multiplier(memory_id)
        candidates.append((memory_id, rrf * multiplier, multiplier))
    return candidates


def _pinned_candidates(stores: list[Store], limit: int) -> list[Candidate]:
    """Return up to ``limit`` pinned memories across ``stores``, tagged ``pinned``.

    Pinned memories bypass ranking entirely, so they carry no fused score (``0.0``)
    — ordering among themselves is by scope order, then creation time within a
    scope, and ``limit`` is a hard cap so an install cannot pin its way to an
    unbounded prompt.
    """
    candidates: list[Candidate] = []
    for store in stores:
        for memory_id in store.pinned_ids():
            if len(candidates) >= limit:
                return candidates
            multiplier = store.decay_multiplier(memory_id)
            candidates.append((store, memory_id, 0.0, multiplier, "pinned"))
    return candidates


def retrieve(
    config: Config,
    stores: list[Store],
    embedder: Embedder,
    query: str,
    *,
    k: int | None = None,
    expand_graph: bool = True,
    record_access: bool = True,
) -> list[Hit]:
    """Return the top ``k`` memories for ``query`` across all ``stores``."""
    k = k or config.top_k
    query_vec = embedder.embed_one(query)

    pinned = (
        _pinned_candidates(stores, config.pinned_max) if config.pinned_max > 0 else []
    )
    pinned_ids = {(store, memory_id) for store, memory_id, *_ in pinned}

    pool: list[tuple[Store, int, float, float]] = []
    for store in stores:
        candidates = _fused_candidates(config, store, query_vec, query)
        if config.resolve_supersessions:
            candidates = _suppress_superseded(store, candidates, config.dedup_threshold)
        for memory_id, score, multiplier in candidates:
            if (store, memory_id) in pinned_ids:
                continue
            pool.append((store, memory_id, score, multiplier))

    pool.sort(key=lambda item: item[2], reverse=True)
    # Normalise to (store, id, score, multiplier, via) before optional expansion.
    selected: list[Candidate] = [(*item, "hybrid") for item in pool[:k]]
    if expand_graph and selected:
        selected = _expand(selected)
    # Pinned memories are guaranteed a slot, ahead of the ranked/expanded hits.
    selected = pinned + selected

    hits: list[Hit] = []
    touch: dict[Store, list[int]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for store, memory_id, score, multiplier, via in selected:
        record = store.hydrate(memory_id)
        key = (store.scope_name, record["name"])
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            Hit(
                name=record["name"],
                path=record["path"],
                mtype=record["mtype"],
                description=record["description"],
                body=record["body"],
                links=record["links"],
                score=score,
                multiplier=multiplier,
                via=via,
                scope=store.scope_name,
            )
        )
        touch[store].append(memory_id)

    if record_access:
        for store, ids in touch.items():
            store.touch(ids)

    return hits


def _expand(selected: list[Candidate]) -> list[Candidate]:
    """Append one hop of graph neighbours of the top hit, within its scope."""
    store, top_id, _score, _multiplier, _via = selected[0]
    top = store.hydrate(top_id)
    present = {st.hydrate(mid)["name"] for st, mid, *_ in selected if st is store}

    expanded = list(selected)
    for link in top["links"]:
        if link in present:
            continue
        neighbour_id = store.id_for_name(link)
        if neighbour_id is None:
            continue
        multiplier = store.decay_multiplier(neighbour_id)
        expanded.append((store, neighbour_id, 0.0, multiplier, f"graph:{top['name']}"))
    return expanded
