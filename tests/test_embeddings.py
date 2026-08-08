"""Tests for the embedding backends and their construction.

The fastembed backend is exercised against a stub module rather than the real
package: it is a heavy optional dependency, and what needs asserting here is the
wiring (which arguments reach ``TextEmbedding``), not the ONNX runtime.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from memex import embeddings


class _StubTextEmbedding:
    """Records the kwargs it was constructed with; embeds to fixed vectors."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        """Capture construction kwargs for later assertion."""
        type(self).last_kwargs = kwargs

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one non-normalised vector per text."""
        return [[3.0, 4.0] for _ in texts]


@pytest.fixture
def stub_fastembed(monkeypatch) -> type[_StubTextEmbedding]:
    """Install a stub ``fastembed`` module for the duration of a test."""
    _StubTextEmbedding.last_kwargs = {}
    module = types.ModuleType("fastembed")
    module.TextEmbedding = _StubTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return _StubTextEmbedding


def test_hash_backend_returns_unit_vectors(make_config) -> None:
    """The hash backend builds and produces L2-normalised vectors."""
    embedder = embeddings.build(make_config())
    vec = embedder.embed_one("some memory text")
    assert len(vec) == 64
    assert sum(component * component for component in vec) == pytest.approx(1.0)


def test_unknown_backend_raises(make_config) -> None:
    """An unrecognised backend name fails loudly rather than silently."""
    config = make_config()
    object.__setattr__(config, "embed_backend", "nonsense")
    with pytest.raises(ValueError, match="unknown embed backend"):
        embeddings.build(config)


def test_build_forwards_cache_dir(make_config, stub_fastembed, tmp_path: Path) -> None:
    """``build`` passes the configured cache dir through to ``TextEmbedding``.

    This is the regression guard: config resolving the directory correctly is
    useless if the value never reaches fastembed, which is precisely the bug —
    ``TextEmbedding`` was constructed with only ``model_name``, so it fell back
    to its ``$TMPDIR`` default.
    """
    config = make_config()
    object.__setattr__(config, "embed_backend", "fastembed")
    object.__setattr__(config, "embed_cache_dir", tmp_path / "models")

    embeddings.build(config)

    assert stub_fastembed.last_kwargs["cache_dir"] == str(tmp_path / "models")
    assert stub_fastembed.last_kwargs["model_name"] == "test"


def test_fastembed_normalises_output(stub_fastembed, tmp_path: Path) -> None:
    """Vectors from the model are L2-normalised before being returned."""
    embedder = embeddings.FastEmbedEmbedder(
        model_name="test", dim=2, cache_dir=tmp_path
    )
    assert embedder.embed_one("text") == pytest.approx([0.6, 0.8])
