"""Author memories and move them between scopes.

Scope is determined by *which directory* a memory file lives in: the global
directory (``~/.claude/memory/``) or a project's directory. So promoting a
project memory to global is a file move, and adding a global memory is a file
write. This module does both, edits and forgets existing memories, and keeps the
human-facing ``MEMORY.md`` index in step with every change. It holds no
embedding dependency — the caller re-indexes the affected scopes afterwards, or
leaves it to the scheduled run — and the interactive picker is driven through
injected ``ask``/``emit`` callables so the CLI wires ``input``/``print`` and the
tests script the responses.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Config, Scope
from .markdown import iter_memory_files, parse

_MEMORY_INDEX = "MEMORY.md"
_VALID_TYPES = ("user", "feedback", "project", "reference")
# A memory is addressed by its file stem; anything else (path separators, a
# leading dot) could reach outside the scope's directory or a hidden file.
_FILE_STEM = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass
class MemoryEntry:
    """One live memory: its name, description, and file path."""

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
class UpdateResult:
    """Outcome of editing one existing memory."""

    ok: bool
    reason: str = ""  # when not ok: not-found | bad-frontmatter | no-change
    path: Path | None = None
    index_updated: bool = False


@dataclass
class ForgetResult:
    """Outcome of forgetting one memory."""

    ok: bool
    reason: str = ""  # when not ok: not-found
    archived_to: Path | None = None
    index_removed: bool = False
    # Memories in the same scope whose ``[[wikilinks]]`` now point at nothing.
    linked_from: list[str] = field(default_factory=list)


@dataclass
class AddResult:
    """Outcome of authoring one new memory."""

    ok: bool
    reason: str = ""  # when not ok: no-such-scope | bad-name | exists
    path: Path | None = None


def list_memories(scope: Scope) -> list[MemoryEntry]:
    """Return ``scope``'s live memories, sorted by file name."""
    memories: list[MemoryEntry] = []
    for path in iter_memory_files(scope.memory_dir):
        memory = parse(path)
        memories.append(MemoryEntry(memory.name, memory.description, path))
    return memories


def list_project_memories(config: Config) -> list[MemoryEntry]:
    """Return the project scope's live memories, or empty if none is active."""
    scope = config.scope("project")
    if scope is None:
        return []
    return list_memories(scope)


def promote(config: Config, name: str) -> PromoteResult:
    """Move memory ``name`` from the active project scope into the global scope."""
    project = config.scope("project")
    global_ = config.scope("global")
    if project is None or global_ is None:
        return PromoteResult(ok=False, reason="no-project-scope")
    return move(project, global_, name)


def move(source: Scope, destination_scope: Scope, name: str) -> PromoteResult:
    """Move memory ``name`` from ``source`` into ``destination_scope``.

    Moves the Markdown file and transplants its ``MEMORY.md`` line. Re-indexing
    (so the memory leaves one index and enters the other) is left to the caller.
    """
    source_path = memory_path(source, name)
    if source_path is None:
        return PromoteResult(ok=False, reason="not-found")

    destination = destination_scope.memory_dir / f"{name}.md"
    if destination.exists():
        return PromoteResult(ok=False, reason="conflict")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    source_path.unlink()

    index_moved = _move_index_line(
        source.memory_dir / _MEMORY_INDEX,
        destination_scope.memory_dir / _MEMORY_INDEX,
        name,
    )
    return PromoteResult(ok=True, destination=destination, index_moved=index_moved)


def memory_path(scope: Scope, name: str) -> Path | None:
    """Return the file for memory ``name`` in ``scope``, or ``None`` if absent.

    ``name`` is the file stem. A stem that could escape the directory, or that
    names ``MEMORY.md`` or a hidden file, resolves to ``None``.
    """
    if not _FILE_STEM.match(name):
        return None
    path = scope.memory_dir / f"{name}.md"
    if path not in iter_memory_files(scope.memory_dir):
        return None
    return path


def update(
    scope: Scope,
    name: str,
    *,
    description: str | None = None,
    body: str | None = None,
    mtype: str | None = None,
    pinned: bool | None = None,
) -> UpdateResult:
    """Rewrite the given fields of memory ``name`` in place.

    Fields left as ``None`` keep their current value; other frontmatter keys are
    preserved. A changed description is mirrored into the ``MEMORY.md`` line.
    """
    path = memory_path(scope, name)
    if path is None:
        return UpdateResult(ok=False, reason="not-found")

    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(raw)
    front_text, old_body = (match.group(1), match.group(2)) if match else ("", raw)
    try:
        front = yaml.safe_load(front_text) or {}
    except yaml.YAMLError:
        return UpdateResult(ok=False, reason="bad-frontmatter")
    if not isinstance(front, dict):
        return UpdateResult(ok=False, reason="bad-frontmatter")

    original = dict(front)
    front.setdefault("name", name)
    if description is not None:
        front["description"] = description
    if mtype is not None:
        metadata = front.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata["type"] = mtype if mtype in _VALID_TYPES else "reference"
        front["metadata"] = metadata
        front.pop("type", None)
    if pinned is True:
        front["pinned"] = True
    elif pinned is False:
        front.pop("pinned", None)
    new_body = body.strip() if body is not None else old_body.strip()

    if front == original and new_body == old_body.strip():
        return UpdateResult(ok=False, reason="no-change", path=path)

    # A wide line limit stops PyYAML folding a long description over lines.
    rendered = yaml.safe_dump(
        front,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1_000_000,
    )
    path.write_text(f"---\n{rendered}---\n\n{new_body}\n", encoding="utf-8")

    index_updated = False
    if description is not None and description != original.get("description"):
        index_updated = _replace_index_hook(
            scope.memory_dir / _MEMORY_INDEX, name, description
        )
    return UpdateResult(ok=True, path=path, index_updated=index_updated)


def forget(scope: Scope, name: str, *, now: dt.datetime | None = None) -> ForgetResult:
    """Remove memory ``name`` from ``scope``, keeping a copy in its archive.

    The file moves to ``<scope>/.memex/forgotten/<name>-<UTC timestamp>.md`` —
    outside the indexed directory, so recall stops offering it at the next
    re-index, but recoverable by moving it back. Its ``MEMORY.md`` line goes.
    """
    path = memory_path(scope, name)
    if path is None:
        return ForgetResult(ok=False, reason="not-found")

    linked_from = [
        other.stem
        for other in iter_memory_files(scope.memory_dir)
        if other != path and name in parse(other).links
    ]

    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%SZ")
    archive_dir = scope.db_path.parent / "forgotten"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_to = archive_dir / f"{name}-{stamp}.md"
    path.rename(archived_to)

    index_removed = _remove_index_line(scope.memory_dir / _MEMORY_INDEX, name)
    return ForgetResult(
        ok=True,
        archived_to=archived_to,
        index_removed=index_removed,
        linked_from=linked_from,
    )


def add(
    config: Config,
    *,
    scope: str,
    name: str,
    description: str,
    mtype: str,
    body: str,
    pinned: bool = False,
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

    pinned_line = "pinned: true\n" if pinned else ""
    target.memory_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {slug}\n"
        f"description: {description}\n"
        f"{pinned_line}"
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


def _select(memories: list[MemoryEntry], choice: str) -> MemoryEntry | None:
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


def _remove_index_line(index_path: Path, name: str) -> bool:
    """Drop the ``MEMORY.md`` line for ``name``; return whether one was found."""
    if not index_path.exists():
        return False
    marker = f"({name}.md)"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if marker not in line]
    if len(kept) == len(lines):
        return False
    index_path.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    return True


def _replace_index_hook(index_path: Path, name: str, description: str) -> bool:
    """Swap the hook text on ``name``'s ``MEMORY.md`` line for ``description``.

    The link part (``- [Title](name.md)``) is kept; everything after it is
    replaced with `` — description``. Returns whether a line was found.
    """
    if not index_path.exists():
        return False
    marker = f"({name}.md)"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    found = False
    for number, line in enumerate(lines):
        if marker not in line:
            continue
        link = line[: line.index(marker) + len(marker)]
        lines[number] = f"{link} — {description}"
        found = True
    if found:
        index_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return found


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
