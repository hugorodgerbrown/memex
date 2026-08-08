"""Tests for the ``/remember`` skill deployment check."""

from __future__ import annotations

from pathlib import Path

import pytest

from memex import skills


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """An in-repo skill directory, as shipped."""
    src = tmp_path / "repo" / "skills" / "remember"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: remember\n---\n", encoding="utf-8")
    return src


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """A stand-in for ``~/.claude/skills``."""
    d = tmp_path / "home" / ".claude" / "skills"
    d.mkdir(parents=True)
    return d


def test_missing_link_reports_the_ln_command(source: Path, skills_dir: Path) -> None:
    status = skills.check(source, skills_dir)
    assert status.state is skills.SkillLink.MISSING
    assert not status.ok
    assert status.remedy == f"ln -sfn {source} {skills_dir / 'remember'}"


def test_correct_link_is_ok(source: Path, skills_dir: Path) -> None:
    (skills_dir / "remember").symlink_to(source)
    status = skills.check(source, skills_dir)
    assert status.state is skills.SkillLink.OK
    assert status.ok
    assert status.remedy is None


def test_link_to_another_directory_is_wrong_target(
    source: Path, skills_dir: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere" / "remember"
    other.mkdir(parents=True)
    (skills_dir / "remember").symlink_to(other)
    status = skills.check(source, skills_dir)
    assert status.state is skills.SkillLink.WRONG_TARGET
    assert not status.ok
    assert status.remedy is not None
    assert status.remedy.startswith("ln -sfn")


def test_repeated_ln_s_creates_a_loop_and_is_detected(
    source: Path, skills_dir: Path
) -> None:
    """A second ``ln -s`` follows the existing link and nests inside the source."""
    link = skills_dir / "remember"
    link.symlink_to(source)
    # What `ln -s <source> <link>` does when <link> already points at a directory.
    (link.resolve() / "remember").symlink_to(source)

    status = skills.check(source, skills_dir)
    assert status.state is skills.SkillLink.LOOPED
    assert not status.ok
    assert status.remedy == (f"rm {source / 'remember'} && ln -sfn {source} {link}")


def test_copy_loads_but_is_flagged_as_unlinked(source: Path, skills_dir: Path) -> None:
    copied = skills_dir / "remember"
    copied.mkdir()
    (copied / "SKILL.md").write_text("---\nname: remember\n---\n", encoding="utf-8")
    status = skills.check(source, skills_dir)
    assert status.state is skills.SkillLink.COPY
    assert status.ok
    assert status.remedy is None


def test_no_source_when_skill_is_not_on_disk(tmp_path: Path, skills_dir: Path) -> None:
    """A non-editable install has no ``skills/`` directory to link from."""
    status = skills.check(tmp_path / "absent", skills_dir)
    assert status.state is skills.SkillLink.NO_SOURCE
    assert not status.ok
    assert status.remedy is None


def test_source_dir_points_at_the_shipped_skill() -> None:
    assert skills.source_dir().name == "remember"
    assert skills.source_dir().parent.name == "skills"
