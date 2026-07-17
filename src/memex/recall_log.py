"""Recall audit log: what memex actually offered the model each turn.

The ``UserPromptSubmit`` hook injects the top-K hits into the prompt context but
gives the user no way to see what happened afterwards. Adding a citation
instruction to the injected block gets Claude to mark the memories it used —
but a memory can shape an answer without being cited. This log is the
ground-truth counterpart: every invocation appends a JSON record with the
prompt snippet and the hits, so ``memex recall-log`` can show what memex put in
front of Claude even when the answer never mentions a memory.

The log is on by default so an installed hook is observable without extra
setup; override the path with ``MEMEX_RECALL_LOG`` or silence it with
``off``/``none``/``0``/empty. The writer degrades silently — a hook failure
must never block the prompt.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .retrieve import Hit

# Cap on the prompt text recorded per turn. The log's job is to help the reader
# recognise which turn a record belongs to, not to be a full transcript, and a
# very long prompt bloats the log for no gain.
_PROMPT_SNIPPET_MAX = 240
# Cap on records returned by :func:`tail` — matches how much a terminal can
# comfortably show without paging.
_DEFAULT_TAIL = 20


def _snippet(text: str, limit: int = _PROMPT_SNIPPET_MAX) -> str:
    """Trim ``text`` to ``limit`` characters, marking any truncation."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def write(
    log_path: Path | None,
    *,
    cwd: str | None,
    prompt: str,
    hits: list[Hit],
) -> None:
    """Append one record for this retrieval to ``log_path``.

    ``log_path`` is ``None`` when logging is disabled — the caller passes the
    resolved path from :class:`~memex.config.Config`, so this module does not
    read environment variables itself. Silent on any write error: recall must
    not block a prompt.
    """
    if log_path is None:
        return
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cwd": cwd or "",
        "prompt": _snippet(prompt),
        "hits": [
            {
                "name": hit.name,
                "scope": hit.scope,
                "mtype": hit.mtype,
                "score": round(hit.score, 4),
                "via": hit.via,
            }
            for hit in hits
        ],
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


def tail(log_path: Path, n: int = _DEFAULT_TAIL) -> list[dict]:
    """Return the last ``n`` records from the log, oldest first.

    Malformed lines are skipped so a partial write from a crashed hook does not
    poison the reader. Returns an empty list if the log does not exist.
    """
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    records: list[dict] = []
    for line in lines[-n:] if n > 0 else lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records
