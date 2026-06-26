"""Claude Code ``SessionEnd`` hook: distil the finished session into candidates.

Once per session (not per turn), this reads the conversation transcript, extracts
durable memory candidates with a small model, and writes them to the per-scope
staging area for review. It writes nothing into the live store directly.

Gated by ``MEMEX_DISTILL_ENABLED``: distillation invokes the ``claude`` CLI, which
costs tokens, so it stays off until explicitly enabled. The hook always exits 0
and degrades silently, so it never disrupts session teardown.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memex import config as config_module  # noqa: E402
from memex import distill as distill_module  # noqa: E402


def _enabled() -> bool:
    """Whether distillation is switched on via the environment."""
    return os.environ.get("MEMEX_DISTILL_ENABLED", "").lower() in ("1", "true", "yes")


def main() -> int:
    """Read the payload and stage distilled candidates, if enabled."""
    if not _enabled():
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0

    transcript = payload.get("transcript_path")
    if not transcript:
        return 0

    try:
        cfg = config_module.load(cwd=payload.get("cwd"))
        candidates = distill_module.extract(cfg, Path(transcript), cfg.distill_model)
        if candidates:
            distill_module.stage(
                cfg, candidates, session_id=payload.get("session_id", "session")
            )
    except Exception:
        # Session teardown must not fail on a hook error; degrade silently.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
