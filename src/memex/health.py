"""Health reporting for the scheduled maintenance run.

``memex health`` answers "did the dream/maintenance schedule run, and did it
succeed?" without any platform-specific tooling. It reads two artefacts the tool
writes itself:

* the maintenance log (``MEMEX_LOG``) — its last ``=== memex maintenance <ts> ===``
  header is the last attempt; a following ``=== maintenance complete ===`` marker
  means it succeeded;
* each scope's newest dream report — its date is when that scope was last swept.

Deriving status from the tool's own output keeps this portable (no ``launchctl``)
and honest (it reports what actually happened, not what was scheduled).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, Scope

_HEADER = re.compile(r"=== memex maintenance (\S+) ===")
_COMPLETE = "=== maintenance complete ==="


@dataclass
class RunStatus:
    """The outcome of the most recent maintenance attempt."""

    started_at: dt.datetime
    succeeded: bool


@dataclass
class ScopeFreshness:
    """When a scope was last swept, per its newest dream report."""

    scope: str
    last_report: dt.date | None


def read_last_run(log_path: Path) -> RunStatus | None:
    """Parse the maintenance log; return the last run's status, or ``None``."""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8")
    matches = list(_HEADER.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    started = _parse_timestamp(last.group(1))
    if started is None:
        return None
    tail = text[last.end() :]
    return RunStatus(started_at=started, succeeded=_COMPLETE in tail)


def _parse_timestamp(value: str) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``)."""
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def scope_freshness(config: Config) -> list[ScopeFreshness]:
    """Return the newest dream-report date for each active scope."""
    return [
        ScopeFreshness(scope.name, _latest_report(scope)) for scope in config.scopes
    ]


def _latest_report(scope: Scope) -> dt.date | None:
    """Return the date of the newest ``REPORT-<date>.md`` in a scope."""
    if not scope.reports_dir.is_dir():
        return None
    dates: list[dt.date] = []
    for path in scope.reports_dir.glob("REPORT-*.md"):
        try:
            dates.append(dt.date.fromisoformat(path.stem.removeprefix("REPORT-")))
        except ValueError:
            continue
    return max(dates) if dates else None


def humanise_age(delta: dt.timedelta) -> str:
    """Render a duration as a compact ``"3h ago"``-style string."""
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 90:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 36:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
