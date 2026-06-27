"""Interactive review of staged distillation candidates.

Walks each staged candidate, shows its detail, and asks whether to accept it into
memory, discard it, or skip it. Kept free of any TUI dependency: it drives the
loop through injected ``ask``/``emit`` callables, so the CLI wires ``input``/``print``
and the tests script the responses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import distill
from .config import Config
from .markdown import parse

_MENU = "[a]ccept  [d]iscard  [s]kip  [q]uit > "


@dataclass
class ReviewResult:
    """Tally of one review session."""

    accepted: int = 0
    discarded: int = 0
    skipped: int = 0
    accepted_scopes: set[str] = field(default_factory=set)


def _render(scope: str, path: Path) -> str:
    """Render a staged candidate's detail for display."""
    memory = parse(path)
    lines = [
        f"=== {memory.name}  [{scope}/{memory.mtype}] ===",
    ]
    if memory.description:
        lines.append(memory.description)
    lines.append("")
    lines.append(memory.body)
    return "\n".join(lines)


def review(
    config: Config,
    *,
    ask: Callable[[str], str],
    emit: Callable[[str], None],
) -> ReviewResult:
    """Step through staged candidates, applying the reviewer's choice to each."""
    staged = distill.list_candidates(config)
    result = ReviewResult()
    if not staged:
        emit("no staged candidates")
        return result

    emit(f"{len(staged)} staged candidate(s) to review.\n")
    for item in staged:
        emit(_render(item.scope, item.path))
        if _handle_one(config, item, ask, emit, result):
            break  # the reviewer quit

    emit(
        f"\nreviewed: {result.accepted} accepted, {result.discarded} discarded, "
        f"{result.skipped} skipped"
    )
    return result


def _handle_one(
    config: Config,
    item: distill.StagedFile,
    ask: Callable[[str], str],
    emit: Callable[[str], None],
    result: ReviewResult,
) -> bool:
    """Prompt for one candidate; apply the choice. Returns True if quitting."""
    while True:
        choice = ask(_MENU).strip().lower()
        if choice in ("a", "accept"):
            destination = distill.accept(config, item.name)
            if destination is None:
                emit("  could not accept (a live memory may already use that name)")
            else:
                emit(f"  accepted → {destination}")
                result.accepted += 1
                result.accepted_scopes.add(item.scope)
            return False
        if choice in ("d", "discard"):
            distill.discard(config, item.name)
            emit("  discarded")
            result.discarded += 1
            return False
        if choice in ("s", "skip", ""):
            emit("  skipped")
            result.skipped += 1
            return False
        if choice in ("q", "quit"):
            return True
        emit("  (enter a, d, s, or q)")
