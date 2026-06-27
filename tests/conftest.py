"""Shared pytest fixtures for the Memex test suite.

Every test runs on the offline ``hash`` embedding backend, so the suite needs no
network and no model download. Memory directories and index databases are built
under ``tmp_path`` and discarded after each test.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from memex.config import Config, Scope


def _scope(name: str, root: Path) -> Scope:
    """Create an empty memory directory and return its scope."""
    memory_dir = root / name
    memory_dir.mkdir(parents=True, exist_ok=True)
    return Scope(name, memory_dir, memory_dir / ".memex" / "index.db")


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., Config]:
    """Return a factory building a hash-backend :class:`Config` over tmp dirs."""

    def _make(scope_names: Sequence[str] = ("global",)) -> Config:
        scopes = [_scope(name, tmp_path) for name in scope_names]
        return Config(
            scopes=scopes,
            embed_backend="hash",
            embed_model="test",
            embed_dim=64,
            top_k=3,
            rrf_k=60,
            decay_half_life_days=30.0,
            decay_floor=0.3,
            decay_ceiling=1.5,
            dedup_threshold=0.92,
            distill_model="test",
            maintenance_log=tmp_path / "maintenance.log",
        )

    return _make


@pytest.fixture
def write_memory() -> Callable[..., Path]:
    """Return a helper that writes a memory Markdown file into a scope."""

    def _write(
        scope: Scope,
        name: str,
        *,
        description: str = "",
        body: str = "",
        mtype: str = "reference",
        event_date: str | None = None,
    ) -> Path:
        path = scope.memory_dir / f"{name}.md"
        event_line = f"event_date: {event_date}\n" if event_date is not None else ""
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\n{event_line}"
            f"metadata:\n  type: {mtype}\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return path

    return _write
