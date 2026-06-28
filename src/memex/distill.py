"""Transcript distillation: turn a finished session into memory candidates.

This is the write side of memory. At session end the conversation transcript is
condensed, handed to a small model that extracts durable facts, and the results
are written as *proposed* memories into a per-scope staging area — never straight
into the live store. ``memex candidates`` lists them and ``memex accept`` promotes
one into its scope's memory directory.

Routing: the model assigns each candidate a scope — ``global`` for cross-project
facts (style, standards, preferences) and ``project`` for codebase-specific facts.

The model is invoked through the ``claude`` CLI in headless mode, so it reuses the
user's existing auth and needs no API key. If the CLI is absent or errors, nothing
is staged and the caller degrades silently.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config, Scope


def _log(message: str) -> None:
    """Append a timestamped line to the distill debug log, when one is set.

    Distillation is otherwise silent and degrades silently, so a session that
    stages nothing gives no way to tell an auth failure from an empty result.
    Opt in by pointing ``MEMEX_DISTILL_LOG`` at a file. Logging must never disrupt
    distillation, so any write error is swallowed.
    """
    target = os.environ.get("MEMEX_DISTILL_LOG")
    if not target:
        return
    try:
        path = Path(target).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        return


# Cap on transcript text sent to the model; the tail of a conversation carries
# the durable conclusions, so we keep the most recent characters.
_MAX_TRANSCRIPT_CHARS = 24_000
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_VALID_SCOPES = {"global", "project"}
_VALID_TYPES = {"user", "feedback", "project", "reference"}


@dataclass
class Candidate:
    """A proposed memory extracted from a transcript."""

    scope: str
    name: str
    description: str
    mtype: str
    body: str


def condense_transcript(path: Path, max_chars: int = _MAX_TRANSCRIPT_CHARS) -> str:
    """Reduce a Claude Code transcript JSONL to plain ``role: text`` lines.

    Tool calls and metadata are dropped; only user and assistant text remains.
    The result is truncated to its last ``max_chars`` characters.
    """
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        role = event.get("type")
        if role not in ("user", "assistant"):
            continue
        text = _message_text(event.get("message"))
        if text:
            lines.append(f"{role}: {text}")
    convo = "\n".join(lines)
    return convo[-max_chars:]


def _message_text(message: object) -> str:
    """Extract the plain text from a transcript message's content."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return " ".join(p.strip() for p in parts if p.strip())


def build_prompt(convo: str, *, has_project: bool) -> str:
    """Build the extraction prompt for the model."""
    scopes = "global or project" if has_project else "global"
    return (
        "You extract durable, reusable memories from a coding session for a "
        "long-term memory store. Read the conversation below and return ONLY a "
        "JSON array (no prose, no code fence) of memory objects worth keeping for "
        "future sessions.\n\n"
        'Each object: {"scope", "type", "name", "description", "body"}.\n'
        f"- scope: one of {scopes}. Use global for cross-project facts (style, "
        "standards, tooling preferences); project for facts specific to this "
        "codebase.\n"
        "- type: one of user, feedback, project, reference.\n"
        "- name: short kebab-case slug.\n"
        "- description: one line, <=120 chars.\n"
        "- body: a few sentences; for feedback/project include why and how to "
        "apply.\n\n"
        "Rules: only stable facts a future session would benefit from; skip "
        "transient task detail, secrets, tokens, and personal data. Return [] if "
        "nothing qualifies.\n\n"
        "=== CONVERSATION ===\n"
        f"{convo}\n"
        "=== END ===\n"
    )


def parse_candidates(text: str, *, has_project: bool) -> list[Candidate]:
    """Parse and validate the model's JSON output into candidates."""
    cleaned = _FENCE.sub("", text).strip()
    try:
        raw = json.loads(cleaned)
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []

    candidates: list[Candidate] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope", "")).lower()
        if scope not in _VALID_SCOPES or (scope == "project" and not has_project):
            scope = "global"
        mtype = str(item.get("type", "reference")).lower()
        if mtype not in _VALID_TYPES:
            mtype = "reference"
        name = _slug(str(item.get("name", "")))
        description = str(item.get("description", "")).strip()
        body = str(item.get("body", "")).strip()
        if not name or not body:
            continue
        candidates.append(
            Candidate(
                scope=scope, name=name, description=description, mtype=mtype, body=body
            )
        )
    return candidates


def _slug(value: str) -> str:
    """Reduce a string to a safe kebab-case slug."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:60]


def call_model(prompt: str, model: str) -> str | None:
    """Invoke the ``claude`` CLI headlessly and return its text, or ``None``."""
    binary = shutil.which("claude")
    if binary is None:
        _log("call_model: claude CLI not found on PATH")
        return None
    try:
        # The binary is resolved from PATH and the arguments are fixed plus a
        # config-controlled model name; no shell and no untrusted input.
        result = subprocess.run(  # noqa: S603
            [binary, "-p", "--model", model, "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            # Mark the spawned session so its SessionEnd hook skips distillation
            # instead of recursing back into this call.
            env={**os.environ, "MEMEX_IN_DISTILL": "1"},
        )
    except OSError, subprocess.SubprocessError:
        _log("call_model: claude CLI subprocess raised (OSError/SubprocessError)")
        return None
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        envelope = None
    # ``claude -p --output-format json`` returns a JSON envelope even on auth
    # failure, exiting non-zero with ``is_error`` set. Inspect it before treating
    # a non-zero exit as a silent miss, so 401s are distinguishable from an empty
    # extraction in the log.
    if isinstance(envelope, dict) and envelope.get("is_error"):
        status = envelope.get("api_error_status")
        detail = str(envelope.get("result", "")).strip()
        _log(f"call_model: claude reported error (status={status}): {detail}")
        return None
    if result.returncode != 0:
        _log(
            f"call_model: claude exited {result.returncode}; "
            f"stderr={result.stderr.strip()[:200]}"
        )
        return None
    if isinstance(envelope, dict):
        return envelope.get("result")
    return result.stdout


def extract(config: Config, transcript_path: Path, model: str) -> list[Candidate]:
    """Condense the transcript, call the model, and return parsed candidates."""
    if not transcript_path.exists():
        _log(f"extract: transcript not found: {transcript_path}")
        return []
    convo = condense_transcript(transcript_path)
    if not convo.strip():
        _log(f"extract: condensed transcript empty: {transcript_path}")
        return []
    has_project = config.scope("project") is not None
    _log(f"extract: condensed {len(convo)} chars; calling model {model}")
    text = call_model(build_prompt(convo, has_project=has_project), model)
    if text is None:
        _log("extract: no model output (see preceding call_model line); staged nothing")
        return []
    _log(f"extract: raw model output: {text.strip()[:1000]}")
    candidates = parse_candidates(text, has_project=has_project)
    names = [c.name for c in candidates]
    _log(f"extract: parsed {len(candidates)} candidate(s): {names}")
    return candidates


def _candidates_dir(scope: Scope) -> Path:
    """The staging directory for a scope's proposed memories."""
    return scope.db_path.parent / "candidates"


def stage(
    config: Config, candidates: list[Candidate], *, session_id: str
) -> list[Path]:
    """Write candidates as proposed memory files; return the paths written."""
    written: list[Path] = []
    for candidate in candidates:
        scope = config.scope(candidate.scope) or config.scope("global")
        if scope is None:
            continue
        directory = _candidates_dir(scope)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{candidate.name}.md"
        path.write_text(_render(candidate, session_id=session_id), encoding="utf-8")
        written.append(path)
    return written


def _render(candidate: Candidate, *, session_id: str) -> str:
    """Render a candidate to staged Markdown with frontmatter."""
    return (
        "---\n"
        f"name: {candidate.name}\n"
        f"description: {candidate.description}\n"
        "metadata:\n"
        "  node_type: memory\n"
        f"  type: {candidate.mtype}\n"
        f"  scope: {candidate.scope}\n"
        "  status: proposed\n"
        "  source: distill\n"
        f"  origin_session: {session_id}\n"
        "---\n\n"
        f"{candidate.body}\n"
    )


@dataclass
class StagedFile:
    """A staged candidate awaiting review."""

    scope: str
    name: str
    path: Path


def list_candidates(config: Config) -> list[StagedFile]:
    """Return every staged candidate across the active scopes."""
    staged: list[StagedFile] = []
    for scope in config.scopes:
        directory = _candidates_dir(scope)
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            staged.append(StagedFile(scope=scope.name, name=path.stem, path=path))
    return staged


def discard(config: Config, name: str) -> bool:
    """Delete a staged candidate by name. Returns whether one was removed."""
    for scope in config.scopes:
        source = _candidates_dir(scope) / f"{name}.md"
        if source.exists():
            source.unlink()
            return True
    return False


def accept(config: Config, name: str) -> Path | None:
    """Promote a staged candidate into its scope's live memory directory.

    Returns the destination path, or ``None`` if no such candidate exists or a
    live memory already uses that name.
    """
    for scope in config.scopes:
        source = _candidates_dir(scope) / f"{name}.md"
        if not source.exists():
            continue
        destination = scope.memory_dir / f"{name}.md"
        if destination.exists():
            return None
        text = source.read_text(encoding="utf-8").replace("  status: proposed\n", "")
        destination.write_text(text, encoding="utf-8")
        source.unlink()
        return destination
    return None
