"""The nightly "dream" cycle: idle-time consolidation of the memory store.

Borrowed from GBrain's dream cycle and Mem0's consolidation, this pass runs off
the critical path (cron / a scheduled routine) and:

* flags near-duplicate memories that should probably be merged;
* recomputes salience (access frequency + inbound graph links);
* reports broken ``[[wikilinks]]`` and memories missing from ``MEMORY.md``.

It is deliberately advisory: it writes a dated report and updates salience
scores, but it never edits or deletes a memory file. A human (or a gated
routine) decides what to merge. This keeps the "never silently destroy memory"
guarantee that every system surveyed learned the hard way.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, Scope
from .store import Store


@dataclass
class DreamReport:
    """The findings of one dream cycle."""

    scope: str
    generated_at: str
    total: int
    duplicates: list[tuple[str, str, float]] = field(default_factory=list)
    broken_links: list[tuple[str, str]] = field(default_factory=list)
    unindexed_in_memory_md: list[str] = field(default_factory=list)
    salience: list[tuple[str, float]] = field(default_factory=list)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (already unit-norm)."""
    return sum(x * y for x, y in zip(a, b, strict=False))


def run(config: Config, scope: Scope, store: Store) -> DreamReport:
    """Execute the consolidation pass for one ``scope`` and return its report."""
    records = store.all_records()
    known = store.known_names()

    report = DreamReport(
        scope=scope.name,
        generated_at=dt.datetime.now(dt.UTC).isoformat(),
        total=len(records),
    )

    # Near-duplicate detection over every pair (memory stores are small).
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            sim = _cosine(records[i]["embedding"], records[j]["embedding"])
            if sim >= config.dedup_threshold:
                report.duplicates.append(
                    (records[i]["name"], records[j]["name"], round(sim, 3))
                )

    # Inbound link counts for salience, and broken-link detection.
    inbound: dict[str, int] = {record["name"]: 0 for record in records}
    for record in records:
        for link in record["links"]:
            if link in inbound:
                inbound[link] += 1
            elif link not in known:
                report.broken_links.append((record["name"], link))

    # Salience = access frequency + inbound graph links. Persist and report.
    summary = {row["name"]: row for row in store.access_summary()}
    for record in records:
        access_count = summary.get(record["name"], {}).get("access_count", 0) or 0
        salience = float(access_count) + float(inbound.get(record["name"], 0))
        store.set_salience(record["id"], salience)
        report.salience.append((record["name"], salience))
    report.salience.sort(key=lambda item: item[1], reverse=True)

    report.unindexed_in_memory_md = _missing_from_index_file(scope, known)
    return report


def _missing_from_index_file(scope: Scope, known: set[str]) -> list[str]:
    """Return indexed memories that are absent from the ``MEMORY.md`` index."""
    index_file = scope.memory_dir / "MEMORY.md"
    if not index_file.exists():
        return sorted(known)
    text = index_file.read_text(encoding="utf-8")
    return sorted(name for name in known if name not in text)


def write_report(scope: Scope, report: DreamReport, *, today: str) -> Path:
    """Render the report to ``reports/REPORT-<today>.md`` and return its path."""
    scope.reports_dir.mkdir(parents=True, exist_ok=True)
    path = scope.reports_dir / f"REPORT-{today}.md"

    lines = [
        f"# Memex dream cycle — {report.scope} — {today}",
        "",
        f"Generated: {report.generated_at}",
        f"Memories indexed: {report.total}",
        "",
        "## Candidate duplicates (consider merging)",
        "",
    ]
    if report.duplicates:
        lines += [f"- `{a}` ↔ `{b}` (cosine {sim})" for a, b, sim in report.duplicates]
    else:
        lines.append("_None above threshold._")

    lines += ["", "## Broken `[[wikilinks]]`", ""]
    if report.broken_links:
        lines += [
            f"- `{source}` → `[[{target}]]` (no such memory)"
            for source, target in report.broken_links
        ]
    else:
        lines.append("_None._")

    lines += ["", "## Missing from MEMORY.md index", ""]
    if report.unindexed_in_memory_md:
        lines += [f"- `{name}`" for name in report.unindexed_in_memory_md]
    else:
        lines.append("_All indexed memories are listed._")

    lines += ["", "## Salience ranking", ""]
    lines += [f"- `{name}` — {score:g}" for name, score in report.salience]
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
