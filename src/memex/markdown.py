"""Parsing of Claude Code memory files.

A memory file is Markdown with a YAML frontmatter block. This module turns one
into a structured record: the frontmatter fields, the body, the ``[[wikilink]]``
targets that form the entity graph, and a content hash used for incremental
indexing. Markdown stays the system of record — nothing here mutates the file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Matches ``[[target]]`` references; the target becomes a graph edge. We strip a
# trailing ``|alias`` and any ``#anchor`` so the edge points at the memory name.
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Files that are not memories: the human-facing index and anything hidden.
SKIP_NAMES = {"MEMORY.md"}


@dataclass
class MemoryFile:
    """A parsed memory file ready to be indexed."""

    name: str
    path: Path
    mtype: str
    description: str
    body: str
    links: list[str] = field(default_factory=list)
    content_hash: str = ""

    @property
    def searchable_text(self) -> str:
        """The text used for both embedding and keyword indexing."""
        return f"{self.name}\n{self.description}\n{self.body}".strip()


def _normalise_link(target: str) -> str:
    """Reduce a raw wikilink target to a bare memory name."""
    target = target.split("|", 1)[0]
    target = target.split("#", 1)[0]
    return target.strip().strip("/").split("/")[-1]


def parse(path: Path) -> MemoryFile:
    """Parse a single memory file at ``path`` into a :class:`MemoryFile`."""
    raw = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    match = _FRONTMATTER.match(raw)
    if match:
        front = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
    else:
        front = {}
        body = raw.strip()

    metadata = front.get("metadata") or {}
    name = str(front.get("name") or path.stem)
    description = str(front.get("description") or "")
    mtype = str(metadata.get("type") or front.get("type") or "note")

    links = sorted({_normalise_link(t) for t in _WIKILINK.findall(body)})

    return MemoryFile(
        name=name,
        path=path,
        mtype=mtype,
        description=description,
        body=body,
        links=links,
        content_hash=content_hash,
    )


def iter_memory_files(memory_dir: Path) -> list[Path]:
    """Return the memory files under ``memory_dir`` worth indexing."""
    files: list[Path] = []
    for path in sorted(memory_dir.glob("*.md")):
        if path.name in SKIP_NAMES or path.name.startswith("."):
            continue
        files.append(path)
    return files
