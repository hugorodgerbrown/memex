"""Tests for maintenance health reporting."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from memex import health


def test_read_last_run_success(tmp_path: Path) -> None:
    """A log ending in the completion marker reads as a successful run."""
    log = tmp_path / "m.log"
    log.write_text(
        "=== memex maintenance 2026-06-26T03:00:00Z ===\n"
        "[global] indexed\n"
        "=== maintenance complete ===\n"
        "=== memex maintenance 2026-06-27T03:00:00Z ===\n"
        "[global] indexed\n"
        "=== maintenance complete ===\n",
        encoding="utf-8",
    )
    status = health.read_last_run(log)
    assert status is not None
    assert status.succeeded is True
    # The newest header wins.
    assert status.started_at.day == 27


def test_read_last_run_failure(tmp_path: Path) -> None:
    """A last run with no completion marker reads as failed."""
    log = tmp_path / "m.log"
    log.write_text(
        "=== memex maintenance 2026-06-27T03:00:00Z ===\n"
        "Traceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'fastembed'\n",
        encoding="utf-8",
    )
    status = health.read_last_run(log)
    assert status is not None
    assert status.succeeded is False


def test_read_last_run_missing(tmp_path: Path) -> None:
    """A missing log yields no status."""
    assert health.read_last_run(tmp_path / "absent.log") is None


def test_scope_freshness_reads_newest_report(make_config) -> None:
    """The newest REPORT date is reported per scope."""
    cfg = make_config()
    scope = cfg.scopes[0]
    scope.reports_dir.mkdir(parents=True, exist_ok=True)
    (scope.reports_dir / "REPORT-2026-06-25.md").write_text("x", encoding="utf-8")
    (scope.reports_dir / "REPORT-2026-06-27.md").write_text("x", encoding="utf-8")
    fresh = health.scope_freshness(cfg)
    assert fresh[0].scope == "global"
    assert fresh[0].last_report == dt.date(2026, 6, 27)


def test_scope_freshness_no_reports(make_config) -> None:
    """A scope with no reports yields None."""
    fresh = health.scope_freshness(make_config())
    assert fresh[0].last_report is None


def test_humanise_age() -> None:
    """Durations render as compact relative strings."""
    assert health.humanise_age(dt.timedelta(seconds=30)) == "30s ago"
    assert health.humanise_age(dt.timedelta(minutes=10)) == "10m ago"
    assert health.humanise_age(dt.timedelta(hours=3)) == "3h ago"
    assert health.humanise_age(dt.timedelta(days=2)) == "2d ago"
