"""Synchronising a scope's index with its Markdown memory files.

Indexing is incremental: only files whose content hash changed since the last
run are re-embedded, and memories whose file disappeared are soft-deleted. This
keeps the ``Stop`` hook cheap enough to run after every Claude turn. Each scope
(global, project) is synced independently against its own directory and database.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import embeddings
from .config import Config, Scope
from .embeddings import Embedder
from .markdown import iter_memory_files, parse
from .store import Store


@dataclass
class IndexResult:
    """Summary of one scope's index run."""

    scope: str
    added: list[str]
    updated: list[str]
    removed: list[str]
    unchanged: int

    @property
    def changed(self) -> bool:
        """Whether the run altered the index."""
        return bool(self.added or self.updated or self.removed)


def sync(
    config: Config,
    scope: Scope,
    store: Store,
    embedder: Embedder,
    *,
    rebuild: bool = False,
) -> IndexResult:
    """Bring ``scope``'s index in line with its memory directory.

    With ``rebuild=True`` every file is re-embedded regardless of its hash;
    otherwise only changed files are processed.
    """
    existing = {} if rebuild else store.existing_hashes()
    added: list[str] = []
    updated: list[str] = []
    live_names: set[str] = set()

    paths = iter_memory_files(scope.memory_dir)
    pending = [parse(path) for path in paths]
    changed = [m for m in pending if existing.get(m.name) != m.content_hash]

    if changed:
        embeddings = embedder.embed([m.searchable_text for m in changed])
        for memory, embedding in zip(changed, embeddings, strict=True):
            is_new = memory.name not in existing
            store.upsert(memory, embedding)
            (added if is_new else updated).append(memory.name)

    for memory in pending:
        live_names.add(memory.name)

    removed = store.prune(live_names)
    unchanged = len(pending) - len(changed)
    return IndexResult(
        scope=scope.name,
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
    )


def _has_changes(scope: Scope, store: Store) -> bool:
    """Whether ``scope``'s files differ from what the index already holds."""
    existing = store.existing_hashes()
    current = {
        memory.name: memory.content_hash
        for memory in (parse(path) for path in iter_memory_files(scope.memory_dir))
    }
    return existing != current


def sync_active(config: Config) -> list[IndexResult]:
    """Incrementally index every active scope that has pending changes.

    The embedding model is loaded only when at least one scope changed, so a
    no-op call (the common case at session start or end) stays cheap. This is the
    entry point for the ``SessionStart`` and ``Stop`` hooks.
    """
    pairs = [
        (scope, Store(config, scope))
        for scope in config.scopes
        if scope.memory_dir.exists()
    ]
    results: list[IndexResult] = []
    try:
        pending = [
            (scope, store) for scope, store in pairs if _has_changes(scope, store)
        ]
        if pending:
            embedder = embeddings.build(config)
            for scope, store in pending:
                results.append(sync(config, scope, store, embedder))
    finally:
        for _scope, store in pairs:
            store.close()
    return results
