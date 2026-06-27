"""Claude Code ``SessionStart`` hook: index the project before the first prompt.

Recall resolves the project scope from the session's working directory, but a
brand-new project has no index until something builds it. Running an incremental
sync at session start closes that gap: project memories are searchable from the
very first prompt, with no one-turn warm-up and no manual ``memex index``.

The sync only loads the embedding model when a scope actually changed, so for an
already-current project this is close to free. The hook always exits 0 and
degrades silently, so it never delays or blocks session start.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memex import config as config_module  # noqa: E402
from memex import index  # noqa: E402


def main() -> int:
    """Read the hook payload and index the active scopes for the session."""
    cwd = None
    try:
        payload = json.load(sys.stdin)
        cwd = payload.get("cwd")
    except ValueError:
        # JSONDecodeError subclasses ValueError; fall back to global scope only.
        pass

    try:
        index.sync_active(config_module.load(cwd=cwd))
    except Exception:
        # Session start must not be delayed or blocked by a hook error.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
