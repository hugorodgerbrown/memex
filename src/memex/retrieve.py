"""Layered hybrid recall across scopes: vector + keyword, decay, graph.

The retrieval pipeline, in order:

1. Embed the query once.
2. For each active scope (global, project), take the vector KNN neighbours and
   the BM25 keyword matches and fuse them with Reciprocal Rank Fusion, then
   multiply each fused score by its decay multiplier (recency/frequency).
3. Merge the per-scope candidate pools and take the top ``k`` overall, so a
   strongly-relevant global memory can outrank a weakly-relevant project one and
   vice versa. Each hit is tagged with the scope it came from.
4. Optionally pull in one hop of ``[[wikilink]]`` neighbours of the top hit
   (within its own scope) so structured recall returns a connected cluster.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .config import Config
from .embeddings import Embedder
from .store import Store

_CANDIDATES = 20

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

    candidates: list[tuple[int, float, float]] = []
    for memory_id, positions in ranks.items():
        rrf = 0.0
        for source in ("vec", "fts"):
            if source in positions:
                rrf += 1.0 / (config.rrf_k + positions[source])
        multiplier = store.decay_multiplier(memory_id)
        candidates.append((memory_id, rrf * multiplier, multiplier))
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

    pool: list[tuple[Store, int, float, float]] = []
    for store in stores:
        for memory_id, score, multiplier in _fused_candidates(
            config, store, query_vec, query
        ):
            pool.append((store, memory_id, score, multiplier))

    pool.sort(key=lambda item: item[2], reverse=True)
    # Normalise to (store, id, score, multiplier, via) before optional expansion.
    selected: list[Candidate] = [(*item, "hybrid") for item in pool[:k]]
    if expand_graph and selected:
        selected = _expand(selected)

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
