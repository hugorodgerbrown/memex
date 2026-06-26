"""Synchronising a scope's index with its Markdown memory files.

Indexing is incremental: only files whose content hash changed since the last
run are re-embedded, and memories whose file disappeared are soft-deleted. This
keeps the ``Stop`` hook cheap enough to run after every Claude turn. Each scope
(global, project) is synced independently against its own directory and database.
"""

from __future__ import annotations

from dataclasses import dataclass

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
