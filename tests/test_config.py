"""Tests for scope resolution and path mangling."""

from __future__ import annotations

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
