"""Tests for memory-file parsing."""

from __future__ import annotations

from pathlib import Path

from memex.markdown import iter_memory_files, parse


def test_parse_extracts_frontmatter_and_links(tmp_path: Path) -> None:
    """Frontmatter fields, body, and wikilinks are parsed."""
    path = tmp_path / "example.md"
    path.write_text(
        "---\nname: example\ndescription: a desc\n"
        "metadata:\n  type: feedback\n---\n\n"
        "Body referring to [[other-memory]] and [[wiki/people/bob|Bob]].\n",
        encoding="utf-8",
    )
    memory = parse(path)
    assert memory.name == "example"
    assert memory.description == "a desc"
    assert memory.mtype == "feedback"
    assert memory.links == ["bob", "other-memory"]
    assert memory.content_hash


def test_parse_without_frontmatter_falls_back_to_stem(tmp_path: Path) -> None:
    """A file with no frontmatter uses its filename stem as the name."""
    path = tmp_path / "bare.md"
    path.write_text("just a body\n", encoding="utf-8")
    memory = parse(path)
    assert memory.name == "bare"
    assert memory.mtype == "note"
    assert memory.links == []


def test_iter_memory_files_skips_index_and_hidden(tmp_path: Path) -> None:
    """MEMORY.md and dot-files are excluded from indexable files."""
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("index", encoding="utf-8")
    (tmp_path / ".hidden.md").write_text("h", encoding="utf-8")
    names = [p.name for p in iter_memory_files(tmp_path)]
    assert names == ["a.md"]
