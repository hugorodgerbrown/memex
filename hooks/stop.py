"""Claude Code ``Stop`` hook: keep the index current after every turn.

When Claude finishes responding, this hook runs an incremental index sync so any
memory file written or edited during the turn is embedded and searchable on the
next prompt. It is deterministic and cheap (only changed files are re-embedded).

Transcript-based fact *distillation* — reading the conversation and writing new
memory files — is a deliberate extension point, not wired here: it needs an LLM
call and a human-review policy. The transcript path is available on the payload
(``transcript_path``) for whoever implements it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memex import config as config_module  # noqa: E402
from memex import embeddings, index  # noqa: E402
from memex.store import Store  # noqa: E402


def main() -> int:
    """Read the hook payload and run an incremental index sync per scope."""
    cwd = None
    try:
        payload = json.load(sys.stdin)
        cwd = payload.get("cwd")
    except ValueError:
        # JSONDecodeError subclasses ValueError; fall back to global scope only.
        pass

    try:
        cfg = config_module.load(cwd=cwd)
        embedder = embeddings.build(cfg)
        for scope in cfg.scopes:
            if not scope.memory_dir.exists():
                continue
            store = Store(cfg, scope)
            index.sync(cfg, scope, store, embedder)
            store.close()
    except Exception:
        # A hook failure must not disrupt the session; degrade silently.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
