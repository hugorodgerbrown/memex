"""Deployment check for the ``/remember`` Claude Code skill.

The skill is a shipped artefact, not repo tooling: it has to fire in every
project, so it lives at ``<repo>/skills/remember/`` and is deployed by linking
that directory into ``~/.claude/skills/``. Nothing in the tool creates the link,
so ``memex doctor`` reports whether it is there and correct.

The check exists mainly to catch one failure mode. ``ln -s`` is not idempotent:
run it twice and the second run follows the existing symlink and drops the new
link *inside* the source directory, giving ``skills/remember/remember`` pointing
at its own parent. Editors and any recursive walk then descend forever. The fix
is ``ln -sfn``, and this reports the loop if an earlier ``ln -s`` already made one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

SKILL_NAME = "remember"

_SKILLS_DIR = Path.home() / ".claude" / "skills"


class SkillLink(Enum):
    """The state of the deployed skill link."""

    OK = "ok"
    MISSING = "missing"
    LOOPED = "looped"
    WRONG_TARGET = "wrong target"
    COPY = "copy"
    NO_SOURCE = "no source"


@dataclass
class SkillStatus:
    """Where the skill is deployed from, where to, and whether that worked."""

    state: SkillLink
    source: Path
    link: Path

    @property
    def ok(self) -> bool:
        """True when the skill is deployed and Claude Code will load it."""
        return self.state in (SkillLink.OK, SkillLink.COPY)

    @property
    def remedy(self) -> str | None:
        """The shell command that fixes this state, or ``None`` if fine."""
        relink = f"ln -sfn {self.source} {self.link}"
        match self.state:
            case SkillLink.MISSING | SkillLink.WRONG_TARGET:
                return relink
            case SkillLink.LOOPED:
                # Remove the self-referential link before relinking, or the loop
                # survives the fix.
                return f"rm {self.source / SKILL_NAME} && {relink}"
            case _:
                return None


def source_dir() -> Path:
    """Return the in-repo skill directory (``<repo>/skills/remember``).

    Resolved relative to this file, which holds for the editable install the
    README prescribes. A non-editable install puts the package under
    site-packages, where ``skills/`` does not exist — reported as ``NO_SOURCE``
    rather than guessed at.
    """
    return Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME


def check(source: Path | None = None, skills_dir: Path | None = None) -> SkillStatus:
    """Report whether the ``/remember`` skill is deployed to ``~/.claude/skills``."""
    src = source_dir() if source is None else source
    link = (_SKILLS_DIR if skills_dir is None else skills_dir) / SKILL_NAME

    if not (src / "SKILL.md").is_file():
        return SkillStatus(SkillLink.NO_SOURCE, src, link)
    # Checked before the link itself: a loop lives in the source tree and breaks
    # recursive walks whatever state the link is in.
    if (src / SKILL_NAME).is_symlink():
        return SkillStatus(SkillLink.LOOPED, src, link)
    if link.is_symlink():
        target = link.resolve()
        state = SkillLink.OK if target == src.resolve() else SkillLink.WRONG_TARGET
        return SkillStatus(state, src, link)
    if (link / "SKILL.md").is_file():
        # A copy loads, but drifts from the repo on every edit.
        return SkillStatus(SkillLink.COPY, src, link)
    return SkillStatus(SkillLink.MISSING, src, link)
