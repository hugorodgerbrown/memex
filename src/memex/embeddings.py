"""Text embedding backends.

Two backends are provided:

* ``fastembed`` — a local ONNX model (default ``BAAI/bge-small-en-v1.5``). No API
  key, no data leaves the machine. This is the backend for real use.
* ``hash`` — a deterministic feature-hashing embedder with no dependencies. It
  carries no semantic meaning but exercises the full vector pipeline offline, so
  tests and CI smoke runs need neither a network nor a model download.

The backend is selected by ``MEMEX_EMBED_BACKEND``. Both implement the same tiny
protocol: ``embed(texts) -> list[list[float]]`` returning L2-normalised vectors.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from .config import Config


class Embedder(Protocol):
    """The interface every embedding backend implements."""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into unit-length vectors."""
        ...

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text into a unit-length vector."""
        ...


def _l2_normalise(vec: list[float]) -> list[float]:
    """Scale ``vec`` to unit length, leaving a zero vector unchanged."""
    norm = math.sqrt(sum(component * component for component in vec))
    if norm == 0.0:
        return vec
    return [component / norm for component in vec]


class HashEmbedder:
    """Dependency-free deterministic embedder for offline use.

    Each token is hashed into a bucket with a signed contribution (the hashing
    trick). The result is stable across runs and machines, which is what tests
    need, but it models lexical overlap only — never use it in production.
    """

    def __init__(self, dim: int) -> None:
        """Create a hashing embedder producing ``dim``-dimensional vectors."""
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the hashing trick."""
        return [self._embed_one(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text via the hashing trick."""
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        """Hash the tokens of ``text`` into a normalised bucket vector."""
        vec = [0.0] * self.dim
        for token in text.lower().split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        return _l2_normalise(vec)


class FastEmbedEmbedder:
    """Local ONNX embedder backed by the ``fastembed`` package."""

    def __init__(self, model_name: str, dim: int) -> None:
        """Load the ``fastembed`` model named ``model_name``."""
        from fastembed import TextEmbedding  # imported lazily; heavy dependency

        self.dim = dim
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts with the local model."""
        return [
            _l2_normalise(list(map(float, vec))) for vec in self._model.embed(texts)
        ]

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text with the local model."""
        return self.embed([text])[0]


def build(config: Config) -> Embedder:
    """Construct the embedder selected by ``config.embed_backend``."""
    if config.embed_backend == "hash":
        return HashEmbedder(dim=config.embed_dim)
    if config.embed_backend == "fastembed":
        return FastEmbedEmbedder(model_name=config.embed_model, dim=config.embed_dim)
    raise ValueError(f"unknown embed backend: {config.embed_backend!r}")
