"""Tests for scope resolution and path mangling."""

from __future__ import annotations

import tempfile
from pathlib import Path

from memex import config as config_module
from memex.config import mangle, resolve_project_root


def test_mangle_matches_claude_code_convention() -> None:
    """Slashes and dots both become hyphens."""
    assert mangle("/Users/hugo/Projects/ambassadeurs") == (
        "-Users-hugo-Projects-ambassadeurs"
    )
    assert mangle("/a/.claude/b") == "-a--claude-b"


def test_resolve_project_root_strips_worktree() -> None:
    """A worktree path resolves to its parent repository."""
    cwd = "/Users/hugo/Projects/ambassadeurs/.claude/worktrees/quirky-austin-dd8437"
    assert resolve_project_root(cwd) == "/Users/hugo/Projects/ambassadeurs"


def test_resolve_project_root_passthrough() -> None:
    """A non-worktree path is returned unchanged; None stays None."""
    assert (
        resolve_project_root("/Users/hugo/Projects/foo") == "/Users/hugo/Projects/foo"
    )
    assert resolve_project_root(None) is None


def test_load_without_cwd_has_global_scope_only(monkeypatch) -> None:
    """With no cwd, only the global scope is active."""
    monkeypatch.delenv("MEMEX_PROJECT_MEMORY_DIR", raising=False)
    cfg = config_module.load(cwd=None)
    assert [s.name for s in cfg.scopes] == ["global"]


def test_load_with_cwd_adds_project_scope(monkeypatch, tmp_path: Path) -> None:
    """A cwd override resolves a second, project scope."""
    monkeypatch.setenv("MEMEX_PROJECT_MEMORY_DIR", str(tmp_path / "proj"))
    cfg = config_module.load(cwd="/whatever")
    assert [s.name for s in cfg.scopes] == ["global", "project"]
    assert cfg.scope("project").memory_dir == tmp_path / "proj"


def _clear_cache_env(monkeypatch) -> None:
    """Unset both cache-dir env vars so a test sees a clean environment."""
    monkeypatch.delenv("MEMEX_EMBED_CACHE_DIR", raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)


def test_embed_cache_dir_defaults_outside_tmpdir(monkeypatch) -> None:
    """The default cache lives under ~/.cache, never under $TMPDIR.

    A $TMPDIR default is what let the OS reap the model between runs, so this
    asserts the property that matters rather than the literal path.
    """
    _clear_cache_env(monkeypatch)
    cache_dir = config_module.load(cwd=None).embed_cache_dir
    assert cache_dir == Path.home() / ".cache" / "fastembed"
    assert tempfile.gettempdir() not in str(cache_dir)


def test_embed_cache_dir_honours_fastembed_env(monkeypatch, tmp_path: Path) -> None:
    """``FASTEMBED_CACHE_PATH`` is respected even though we pass cache_dir.

    Passing ``cache_dir`` to ``TextEmbedding`` stops fastembed reading this var
    itself, so memex has to forward it or an already-set value would silently
    stop working.
    """
    _clear_cache_env(monkeypatch)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "fe"))
    assert config_module.load(cwd=None).embed_cache_dir == tmp_path / "fe"


def test_memex_cache_env_beats_fastembed_env(monkeypatch, tmp_path: Path) -> None:
    """memex's own knob outranks fastembed's when both are set."""
    _clear_cache_env(monkeypatch)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "fe"))
    monkeypatch.setenv("MEMEX_EMBED_CACHE_DIR", str(tmp_path / "memex"))
    assert config_module.load(cwd=None).embed_cache_dir == tmp_path / "memex"


def test_blank_cache_env_falls_through_to_default(monkeypatch) -> None:
    """An empty or whitespace value is ignored rather than becoming Path('')."""
    _clear_cache_env(monkeypatch)
    monkeypatch.setenv("MEMEX_EMBED_CACHE_DIR", "   ")
    assert config_module.load(cwd=None).embed_cache_dir == (
        Path.home() / ".cache" / "fastembed"
    )


def test_cache_dir_expands_user(monkeypatch) -> None:
    """A ``~`` in the override is expanded, not taken literally."""
    _clear_cache_env(monkeypatch)
    monkeypatch.setenv("MEMEX_EMBED_CACHE_DIR", "~/somewhere/models")
    assert config_module.load(cwd=None).embed_cache_dir == (
        Path.home() / "somewhere" / "models"
    )
