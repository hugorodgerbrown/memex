"""Tests for the interactive candidate-review loop.

The loop is driven through injected ``ask``/``emit`` callables, so these tests
script the reviewer's keystrokes without a real terminal.
"""

from __future__ import annotations

from collections.abc import Callable

from memex import distill, review
from memex.config import Config


def _stage(cfg: Config, *names: str) -> None:
    """Stage one candidate per name in the global scope."""
    candidates = [
        distill.Candidate(
            scope="global",
            name=name,
            description=f"desc {name}",
            mtype="feedback",
            body=f"body for {name}",
        )
        for name in names
    ]
    distill.stage(cfg, candidates, session_id="t")


def _scripted(answers: list[str]) -> Callable[[str], str]:
    """Return an ``ask`` that yields the scripted answers in order."""
    it = iter(answers)
    return lambda _prompt: next(it)


def test_accept_then_discard(make_config) -> None:
    """Accepting promotes to the live dir; discarding deletes the staged file."""
    cfg = make_config()
    scope = cfg.scopes[0]
    _stage(cfg, "alpha", "beta")
    emitted: list[str] = []

    result = review.review(cfg, ask=_scripted(["a", "d"]), emit=emitted.append)

    assert result.accepted == 1
    assert result.discarded == 1
    assert result.accepted_scopes == {"global"}
    # alpha promoted, beta gone, neither remains staged.
    assert (scope.memory_dir / "alpha.md").exists()
    assert not (scope.memory_dir / "beta.md").exists()
    assert distill.list_candidates(cfg) == []


def test_skip_leaves_candidate_staged(make_config) -> None:
    """Skipping applies no change and leaves the candidate in staging."""
    cfg = make_config()
    _stage(cfg, "alpha")
    result = review.review(cfg, ask=_scripted(["s"]), emit=lambda _m: None)
    assert result.skipped == 1
    assert [s.name for s in distill.list_candidates(cfg)] == ["alpha"]


def test_quit_stops_without_touching_remaining(make_config) -> None:
    """Quitting on the first candidate leaves all candidates staged."""
    cfg = make_config()
    _stage(cfg, "alpha", "beta")
    result = review.review(cfg, ask=_scripted(["q"]), emit=lambda _m: None)
    assert result.accepted == 0
    assert len(distill.list_candidates(cfg)) == 2


def test_invalid_choice_reprompts(make_config) -> None:
    """An unrecognised key re-prompts rather than acting."""
    cfg = make_config()
    _stage(cfg, "alpha")
    result = review.review(cfg, ask=_scripted(["x", "s"]), emit=lambda _m: None)
    assert result.skipped == 1


def test_review_empty(make_config) -> None:
    """With nothing staged, review reports it and tallies zero."""
    cfg = make_config()
    messages: list[str] = []
    result = review.review(cfg, ask=_scripted([]), emit=messages.append)
    assert result == review.ReviewResult()
    assert any("no staged candidates" in m for m in messages)
