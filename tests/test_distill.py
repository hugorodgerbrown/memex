"""Tests for transcript distillation (the deterministic seams)."""

from __future__ import annotations

import json
from pathlib import Path

from memex import distill


def _write_transcript(path: Path) -> None:
    """Write a minimal Claude Code transcript JSONL."""
    events = [
        {"type": "user", "message": {"content": "always use ruff format"}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "noted, will do"}]},
        },
        {"type": "summary", "message": {"content": "ignored"}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def test_condense_transcript_keeps_only_text_turns(tmp_path: Path) -> None:
    """Only user/assistant text survives condensation."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path)
    convo = distill.condense_transcript(path)
    assert "user: always use ruff format" in convo
    assert "assistant: noted, will do" in convo
    assert "ignored" not in convo


def test_parse_candidates_handles_fence_and_normalises(make_config) -> None:
    """Code fences are stripped; invalid scope/type fall back; bad rows drop."""
    text = (
        "```json\n"
        '[{"scope":"bogus","type":"weird","name":"Use Ruff","description":"d",'
        '"body":"run ruff"},'
        '{"scope":"global","type":"feedback","name":"x","description":"d","body":""}]'
        "\n```"
    )
    candidates = distill.parse_candidates(text, has_project=False)
    assert len(candidates) == 1
    only = candidates[0]
    assert only.scope == "global"
    assert only.mtype == "reference"
    assert only.name == "use-ruff"


def test_stage_list_accept_round_trip(make_config) -> None:
    """Staging writes proposals; accept promotes one into the live directory."""
    cfg = make_config()
    candidate = distill.Candidate(
        scope="global",
        name="use-ruff",
        description="run ruff format",
        mtype="feedback",
        body="Always run ruff format before commit.",
    )
    written = distill.stage(cfg, [candidate], session_id="t")
    assert written and written[0].exists()

    staged = distill.list_candidates(cfg)
    assert [s.name for s in staged] == ["use-ruff"]

    destination = distill.accept(cfg, "use-ruff")
    assert destination is not None and destination.exists()
    assert "status: proposed" not in destination.read_text(encoding="utf-8")
    # The staged copy is consumed on accept.
    assert not distill.list_candidates(cfg)


def test_extract_uses_model_output(make_config, tmp_path, monkeypatch) -> None:
    """extract() condenses, calls the model, and parses its output."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path)
    monkeypatch.setattr(
        distill,
        "call_model",
        lambda prompt, model: (
            '[{"scope":"global","type":"feedback",'
            '"name":"ruff","description":"d","body":"run ruff"}]'
        ),
    )
    candidates = distill.extract(make_config(), path, "test-model")
    assert [c.name for c in candidates] == ["ruff"]


def test_extract_returns_empty_when_model_unavailable(
    make_config, tmp_path, monkeypatch
) -> None:
    """A missing model (None) yields no candidates, not an error."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path)
    monkeypatch.setattr(distill, "call_model", lambda prompt, model: None)
    assert distill.extract(make_config(), path, "test-model") == []


def test_log_writes_only_when_enabled(tmp_path, monkeypatch) -> None:
    """``_log`` is a no-op unless ``MEMEX_DISTILL_LOG`` points somewhere."""
    target = tmp_path / "distill.log"
    monkeypatch.delenv("MEMEX_DISTILL_LOG", raising=False)
    distill._log("should not appear")
    assert not target.exists()

    monkeypatch.setenv("MEMEX_DISTILL_LOG", str(target))
    distill._log("hello")
    assert "hello" in target.read_text(encoding="utf-8")


def test_call_model_distinguishes_auth_error(tmp_path, monkeypatch) -> None:
    """An auth-failure envelope is logged as an error, not a silent empty miss."""
    target = tmp_path / "distill.log"
    monkeypatch.setenv("MEMEX_DISTILL_LOG", str(target))
    monkeypatch.setattr(distill.shutil, "which", lambda _name: "/usr/bin/claude")

    class _Result:
        returncode = 1
        stdout = json.dumps(
            {"is_error": True, "api_error_status": 401, "result": "bad creds"}
        )
        stderr = ""

    monkeypatch.setattr(distill.subprocess, "run", lambda *a, **k: _Result())
    assert distill.call_model("prompt", "model") is None
    logged = target.read_text(encoding="utf-8")
    assert "status=401" in logged and "bad creds" in logged


def test_call_model_returns_result_on_success(tmp_path, monkeypatch) -> None:
    """A successful envelope yields its ``result`` text."""
    monkeypatch.delenv("MEMEX_DISTILL_LOG", raising=False)
    monkeypatch.setattr(distill.shutil, "which", lambda _name: "/usr/bin/claude")

    class _Result:
        returncode = 0
        stdout = json.dumps({"is_error": False, "result": "[]"})
        stderr = ""

    monkeypatch.setattr(distill.subprocess, "run", lambda *a, **k: _Result())
    assert distill.call_model("prompt", "model") == "[]"
