"""Author memories and move them between scopes.

Scope is determined by *which directory* a memory file lives in: the global
directory (``~/.claude/memory/``) or a project's directory. So promoting a
project memory to global is a file move, and adding a global memory is a file
write. This module does both, and keeps the human-facing ``MEMORY.md`` index in
step with the move. It holds no embedding dependency — the caller re-indexes the
affected scopes afterwards — and the interactive picker is driven through
injected ``ask``/``emit`` callables so the CLI wires ``input``/``print`` and the
tests script the responses.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .markdown import iter_memory_files, parse

_MEMORY_INDEX = "MEMORY.md"
_VALID_TYPES = ("user", "feedback", "project", "reference")


@dataclass
class ProjectMemory:
    """A live project memory eligible for promotion."""

    name: str
    description: str
    path: Path


@dataclass
class PromoteResult:
    """Outcome of one promotion attempt."""

    ok: bool
    reason: str = ""  # when not ok: no-project-scope | not-found | conflict
    destination: Path | None = None
    index_moved: bool = False


@dataclass
class AddResult:
    """Outcome of authoring one new memory."""

    ok: bool
    reason: str = ""  # when not ok: no-such-scope | bad-name | exists
    path: Path | None = None


def list_project_memories(config: Config) -> list[ProjectMemory]:
    """Return the project scope's live memories, sorted by name.

    Empty when no project scope is active (e.g. the cwd is not inside a project).
    """
    scope = config.scope("project")
    if scope is None:
        return []
    memories: list[ProjectMemory] = []
    for path in iter_memory_files(scope.memory_dir):
        memory = parse(path)
        memories.append(ProjectMemory(memory.name, memory.description, path))
    return memories


def promote(config: Config, name: str) -> PromoteResult:
    """Move project memory ``name`` into the global scope.

    Moves the Markdown file and transplants its ``MEMORY.md`` line. Re-indexing
    (so the memory leaves the project index and enters the global one) is left to
    the caller.
    """
    project = config.scope("project")
    global_ = config.scope("global")
    if project is None or global_ is None:
        return PromoteResult(ok=False, reason="no-project-scope")

    source = project.memory_dir / f"{name}.md"
    if not source.exists():
        return PromoteResult(ok=False, reason="not-found")

    destination = global_.memory_dir / f"{name}.md"
    if destination.exists():
        return PromoteResult(ok=False, reason="conflict")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    source.unlink()

    index_moved = _move_index_line(
        project.memory_dir / _MEMORY_INDEX,
        global_.memory_dir / _MEMORY_INDEX,
        name,
    )
    return PromoteResult(ok=True, destination=destination, index_moved=index_moved)


def add(
    config: Config,
    *,
    scope: str,
    name: str,
    description: str,
    mtype: str,
    body: str,
) -> AddResult:
    """Author a new memory file in ``scope`` and append it to that ``MEMORY.md``."""
    target = config.scope(scope)
    if target is None:
        return AddResult(ok=False, reason="no-such-scope")

    slug = _slugify(name)
    if not slug:
        return AddResult(ok=False, reason="bad-name")
    mtype = mtype if mtype in _VALID_TYPES else "reference"

    path = target.memory_dir / f"{slug}.md"
    if path.exists():
        return AddResult(ok=False, reason="exists")

    target.memory_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {slug}\n"
        f"description: {description}\n"
        "metadata:\n"
        f"  type: {mtype}\n"
        "---\n\n"
        f"{body.strip()}\n",
        encoding="utf-8",
    )
    _append_index_line(target.memory_dir / _MEMORY_INDEX, slug, description)
    return AddResult(ok=True, path=path)


def promote_interactively(
    config: Config,
    *,
    ask: Callable[[str], str],
    emit: Callable[[str], None],
) -> set[str]:
    """List project memories and promote the ones the user picks.

    Returns the set of scope names whose indexes need refreshing (empty if
    nothing was promoted).
    """
    if config.scope("project") is None:
        emit("no project scope for this directory; nothing to promote")
        return set()

    touched: set[str] = set()
    while True:
        memories = list_project_memories(config)
        if not memories:
            emit("no project memories left to promote")
            break

        emit("\nProject memories:")
        for number, memory in enumerate(memories, start=1):
            summary = memory.description or "(no description)"
            emit(f"  {number}. {memory.name} — {summary}")

        choice = ask("\npromote which? [number, or q to quit] > ").strip().lower()
        if choice in ("q", "quit", ""):
            break

        selected = _select(memories, choice)
        if selected is None:
            emit("  (enter a listed number, or q)")
            continue

        result = promote(config, selected.name)
        if result.ok:
            emit(f"  promoted → {result.destination}")
            if not result.index_moved:
                emit(
                    f"  (no MEMORY.md line found for {selected.name}; index untouched)"
                )
            touched.update({"project", "global"})
        elif result.reason == "conflict":
            emit(f"  a global memory named {selected.name!r} already exists; skipped")
        else:
            emit(f"  could not promote {selected.name!r} ({result.reason})")

    return touched


def _select(memories: list[ProjectMemory], choice: str) -> ProjectMemory | None:
    """Resolve a menu ``choice`` (a 1-based number) to a memory, or ``None``."""
    if not choice.isdigit():
        return None
    position = int(choice)
    if 1 <= position <= len(memories):
        return memories[position - 1]
    return None


def _slugify(name: str) -> str:
    """Reduce ``name`` to a kebab-case slug, or '' if nothing usable remains."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:60]


def _move_index_line(src_index: Path, dst_index: Path, name: str) -> bool:
    """Transplant the ``MEMORY.md`` line for ``name`` from one index to another.

    Matches the line by its ``(<name>.md)`` link target. Returns whether a line
    was found and moved.
    """
    if not src_index.exists():
        return False
    marker = f"({name}.md)"
    lines = src_index.read_text(encoding="utf-8").splitlines()
    moved = [line for line in lines if marker in line]
    if not moved:
        return False

    kept = [line for line in lines if marker not in line]
    src_index.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")

    existing = (
        dst_index.read_text(encoding="utf-8").splitlines() if dst_index.exists() else []
    )
    dst_index.write_text(
        "\n".join(existing + moved).strip("\n") + "\n", encoding="utf-8"
    )
    return True


def _append_index_line(index_path: Path, slug: str, description: str) -> None:
    """Append a one-line pointer for ``slug`` to ``index_path`` (creating it)."""
    hook = description or slug
    entry = f"- [{slug}]({slug}.md) — {hook}"
    existing = (
        index_path.read_text(encoding="utf-8").splitlines()
        if index_path.exists()
        else []
    )
    index_path.write_text(
        "\n".join(existing + [entry]).strip("\n") + "\n", encoding="utf-8"
    )
