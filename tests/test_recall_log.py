"""Tests for the recall audit log and its CLI viewer."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from memex import cli, recall_log
from memex import config as config_module
from memex.retrieve import Hit


def _hit(name: str = "use-tox", scope: str = "global", score: float = 0.5) -> Hit:
    """Build a plain :class:`Hit` for logging tests."""
    return Hit(
        name=name,
        path=f"/tmp/{name}.md",  # noqa: S108
        mtype="feedback",
        description="run tox before a PR",
        body="Always run `tox` before opening a PR.",
        links=[],
        score=score,
        multiplier=1.0,
        via="vec+fts",
        scope=scope,
    )


def test_write_appends_json_lines_and_creates_parent(tmp_path: Path) -> None:
    """Two ``write`` calls yield two JSON records; the parent directory is created."""
    log = tmp_path / "sub" / "recall.log"

    recall_log.write(log, cwd="/repo", prompt="how do we test?", hits=[_hit()])
    recall_log.write(log, cwd="/repo", prompt="another", hits=[])

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["cwd"] == "/repo"
    assert first["prompt"] == "how do we test?"
    assert first["hits"] == [
        {
            "name": "use-tox",
            "scope": "global",
            "mtype": "feedback",
            "score": 0.5,
            "via": "vec+fts",
        }
    ]
    # Timestamp is present and in ISO Zulu shape; we do not pin the exact value.
    assert first["ts"].endswith("Z")

    second = json.loads(lines[1])
    assert second["hits"] == []


def test_write_disabled_is_a_noop(tmp_path: Path) -> None:
    """Passing ``None`` means logging is off; no file is created."""
    recall_log.write(None, cwd="/repo", prompt="q", hits=[_hit()])
    assert list(tmp_path.iterdir()) == []


def test_prompt_snippet_is_truncated(tmp_path: Path) -> None:
    """Very long prompts are trimmed so the log stays manageable."""
    log = tmp_path / "recall.log"
    recall_log.write(log, cwd="/repo", prompt="a" * 5000, hits=[])
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert len(record["prompt"]) < 300
    assert record["prompt"].endswith("…")


def test_tail_skips_malformed_lines(tmp_path: Path) -> None:
    """A partial write from a crashed hook does not stop the reader."""
    log = tmp_path / "recall.log"
    log.write_text(
        '{"ts": "2026-06-27T00:00:00Z", "cwd": "/a", "prompt": "q", "hits": []}\n'
        "not json\n"
        '{"ts": "2026-06-27T00:00:01Z", "cwd": "/b", "prompt": "q2", "hits": []}\n',
        encoding="utf-8",
    )
    records = recall_log.tail(log, n=10)
    assert [r["cwd"] for r in records] == ["/a", "/b"]


def test_tail_returns_most_recent_n_oldest_first(tmp_path: Path) -> None:
    """``tail`` keeps the last ``n`` records in write order."""
    log = tmp_path / "recall.log"
    for i in range(5):
        recall_log.write(log, cwd=f"/r{i}", prompt=f"p{i}", hits=[])
    records = recall_log.tail(log, n=3)
    assert [r["cwd"] for r in records] == ["/r2", "/r3", "/r4"]


def test_tail_missing_log_returns_empty(tmp_path: Path) -> None:
    """Reading a nonexistent log yields no records rather than raising."""
    assert recall_log.tail(tmp_path / "absent.log") == []


def test_config_resolves_recall_log_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no override the config resolves to the shipped default path."""
    monkeypatch.delenv("MEMEX_RECALL_LOG", raising=False)
    cfg = config_module.load(cwd=None)
    assert cfg.recall_log is not None
    assert str(cfg.recall_log).endswith("recall.log")


def test_config_recall_log_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``off`` (any case) resolves to ``None`` — the disabled signal."""
    monkeypatch.setenv("MEMEX_RECALL_LOG", "off")
    assert config_module.load(cwd=None).recall_log is None


def test_config_recall_log_override_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path in ``MEMEX_RECALL_LOG`` is honoured verbatim (with ``~`` expansion)."""
    target = tmp_path / "custom.log"
    monkeypatch.setenv("MEMEX_RECALL_LOG", str(target))
    assert config_module.load(cwd=None).recall_log == target


def test_cli_recall_log_prints_records(
    tmp_path: Path, make_config, capsys: pytest.CaptureFixture[str]
) -> None:
    """``memex recall-log`` prints each record with its hits."""
    log = tmp_path / "recall.log"
    recall_log.write(log, cwd="/repo/one", prompt="hello", hits=[_hit()])
    cfg = make_config()
    cfg = _replace_recall_log(cfg, log)

    assert cli._cmd_recall_log(cfg, tail_n=5) == 0
    out = capsys.readouterr().out
    assert str(log) in out
    assert "hello" in out
    assert "use-tox" in out
    assert "global/feedback" in out


def test_cli_recall_log_off_says_so(
    make_config, capsys: pytest.CaptureFixture[str]
) -> None:
    """Disabled log tells the user rather than pretending there is nothing."""
    cfg = _replace_recall_log(make_config(), None)
    assert cli._cmd_recall_log(cfg, tail_n=5) == 0
    assert "recall log is off" in capsys.readouterr().out


def test_cli_recall_log_empty_hint(
    tmp_path: Path, make_config, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing log file prints a hint about running the hook."""
    cfg = _replace_recall_log(make_config(), tmp_path / "not-yet.log")
    assert cli._cmd_recall_log(cfg, tail_n=5) == 0
    assert "no records yet" in capsys.readouterr().out


def _replace_recall_log(cfg, path: Path | None):
    """Return ``cfg`` with a new ``recall_log`` field (dataclass is frozen)."""
    import dataclasses

    return dataclasses.replace(cfg, recall_log=path)


def test_hook_writes_a_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_config,
    write_memory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: running the hook against an indexed store writes one record."""
    # Point the recall log at a temp file and stand up a real global scope with
    # one memory + a fresh index — the hook should recall it and log the write.
    log = tmp_path / "recall.log"
    monkeypatch.setenv("MEMEX_RECALL_LOG", str(log))
    monkeypatch.setenv("MEMEX_GLOBAL_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("MEMEX_EMBED_BACKEND", "hash")
    monkeypatch.setenv("MEMEX_EMBED_DIM", "64")

    from memex import embeddings
    from memex import index as index_module
    from memex.store import Store

    cfg = config_module.load(cwd=None)
    scope = cfg.scopes[0]
    scope.memory_dir.mkdir(parents=True, exist_ok=True)
    (scope.memory_dir / "use-tox.md").write_text(
        "---\nname: use-tox\ndescription: run tox before a PR\n"
        "metadata:\n  type: feedback\n---\n\nAlways run tox before opening a PR.\n",
        encoding="utf-8",
    )
    embedder = embeddings.build(cfg)
    store = Store(cfg, scope)
    index_module.sync(cfg, scope, store, embedder)
    store.close()

    import runpy
    import sys

    stdin_backup = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"prompt": "run tox please", "cwd": None}))
    try:
        # Load and execute the hook script; it exits via sys.exit(main()).
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(
                str(
                    Path(__file__).resolve().parent.parent
                    / "hooks"
                    / "user_prompt_submit.py"
                ),
                run_name="__main__",
            )
    finally:
        sys.stdin = stdin_backup

    assert excinfo.value.code == 0
    assert log.exists()
    records = recall_log.tail(log)
    assert len(records) == 1
    assert records[0]["prompt"] == "run tox please"
    # The injected block also mentions the citation instruction.
    assert "[memex:" in capsys.readouterr().out
