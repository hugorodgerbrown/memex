"""Runtime configuration and scope resolution for memex.

Memex indexes two *scopes* of memory and recalls across both:

* **global** — durable, cross-project facts (style choices, coding standards,
  preferences). Lives in ``~/.claude/memory/`` and applies to every project.
* **project** — facts specific to one codebase. Lives in Claude Code's per-project
  memory directory (``~/.claude/projects/<mangled-path>/memory/``), resolved from
  the session's working directory.

The project directory is derived by reproducing Claude Code's path mangling
(``/`` and ``.`` become ``-``). Worktrees map back to their parent project, so a
session under ``<repo>/.claude/worktrees/<name>`` resolves to ``<repo>``'s memory
— matching how the harness itself loads memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Marker that identifies a Claude Code worktree path; everything before it is the
# parent project whose memory store the session shares.
_WORKTREE_MARKER = "/.claude/worktrees/"

_DEFAULT_GLOBAL_DIR = Path.home() / ".claude" / "memory"
_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class Scope:
    """One memory tier: a name, its Markdown directory, and its index database."""

    name: str
    memory_dir: Path
    db_path: Path

    @property
    def reports_dir(self) -> Path:
        """Directory where this scope's dream-cycle reports are written."""
        return self.db_path.parent / "reports"


@dataclass(frozen=True)
class Config:
    """Resolved configuration: the active scopes plus the shared tunables."""

    scopes: list[Scope]
    embed_backend: str
    embed_model: str
    embed_dim: int
    top_k: int
    rrf_k: int
    decay_half_life_days: float
    decay_floor: float
    decay_ceiling: float
    dedup_threshold: float
    distill_model: str

    def scope(self, name: str) -> Scope | None:
        """Return the scope with ``name``, or ``None`` if it is not active."""
        for scope in self.scopes:
            if scope.name == name:
                return scope
        return None


def mangle(project_root: str) -> str:
    """Reproduce Claude Code's project-directory mangling for ``project_root``."""
    return project_root.replace("/", "-").replace(".", "-")


def resolve_project_root(cwd: str | None) -> str | None:
    """Resolve a working directory to its parent project root.

    A worktree path resolves to the repository it was created from; any other
    path is returned unchanged. Returns ``None`` when no directory is known.
    """
    if not cwd:
        return None
    if _WORKTREE_MARKER in cwd:
        return cwd[: cwd.index(_WORKTREE_MARKER)]
    return cwd.rstrip("/")


def _project_memory_dir(cwd: str | None) -> Path | None:
    """Locate the project memory directory for the session's ``cwd``."""
    override = os.environ.get("MEMEX_PROJECT_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    project_root = resolve_project_root(cwd)
    if not project_root:
        return None
    return _PROJECTS_ROOT / mangle(project_root) / "memory"


def _global_scope() -> Scope:
    """Build the global scope from the environment or its default location."""
    global_dir = Path(
        os.environ.get("MEMEX_GLOBAL_MEMORY_DIR", str(_DEFAULT_GLOBAL_DIR))
    ).expanduser()
    return Scope("global", global_dir, global_dir / ".memex" / "index.db")


def _has_memories(directory: Path) -> bool:
    """Whether ``directory`` holds at least one indexable memory file."""
    if not directory.is_dir():
        return False
    return any(
        path.name != "MEMORY.md" and not path.name.startswith(".")
        for path in directory.glob("*.md")
    )


def discover_project_scopes() -> list[Scope]:
    """Return a scope for every project memory directory with memories.

    Used by the scheduled maintenance run, which has no single session cwd and so
    must sweep all known projects rather than resolve one.
    """
    if not _PROJECTS_ROOT.is_dir():
        return []
    scopes: list[Scope] = []
    for project_dir in sorted(_PROJECTS_ROOT.iterdir()):
        memory_dir = project_dir / "memory"
        if _has_memories(memory_dir):
            scopes.append(
                Scope(project_dir.name, memory_dir, memory_dir / ".memex" / "index.db")
            )
    return scopes


def load(cwd: str | None = None) -> Config:
    """Build a :class:`Config`, resolving the active scopes for ``cwd``.

    ``cwd`` is the session working directory (the hooks read it from their
    payload). When omitted, only the global scope is active.
    """
    scopes = [_global_scope()]

    project_dir = _project_memory_dir(cwd)
    if project_dir is not None:
        scopes.append(
            Scope("project", project_dir, project_dir / ".memex" / "index.db")
        )

    return _with_tunables(scopes)


def load_all() -> Config:
    """Build a :class:`Config` spanning the global scope and every project.

    This is the entry point for scheduled maintenance (``memex maintain``).
    """
    return _with_tunables([_global_scope(), *discover_project_scopes()])


def _with_tunables(scopes: list[Scope]) -> Config:
    """Assemble a :class:`Config` from ``scopes`` plus the shared tunables."""
    return Config(
        scopes=scopes,
        embed_backend=os.environ.get("MEMEX_EMBED_BACKEND", "fastembed"),
        embed_model=os.environ.get("MEMEX_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        embed_dim=int(os.environ.get("MEMEX_EMBED_DIM", "384")),
        top_k=int(os.environ.get("MEMEX_TOP_K", "3")),
        rrf_k=int(os.environ.get("MEMEX_RRF_K", "60")),
        decay_half_life_days=float(os.environ.get("MEMEX_DECAY_HALF_LIFE", "30")),
        decay_floor=float(os.environ.get("MEMEX_DECAY_FLOOR", "0.3")),
        decay_ceiling=float(os.environ.get("MEMEX_DECAY_CEILING", "1.5")),
        dedup_threshold=float(os.environ.get("MEMEX_DEDUP_THRESHOLD", "0.92")),
        distill_model=os.environ.get(
            "MEMEX_DISTILL_MODEL", "claude-haiku-4-5-20251001"
        ),
    )
