"""Claude Code ``UserPromptSubmit`` hook: inject relevant memories into context.

Claude Code invokes this with the hook payload on stdin (including ``cwd``). We
resolve the active scopes for that directory, run layered hybrid recall across
the global and project stores, and print the top memories to stdout — Claude Code
appends that to the model's context for the turn (the "always-on context" feature).

The hook degrades silently: any failure prints nothing and exits 0, so a broken
index or a slow embedder never blocks the user from sending a prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a loose script (the hooks dir is not on the package path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memex import config as config_module  # noqa: E402
from memex import embeddings, retrieve  # noqa: E402
from memex.store import Store  # noqa: E402


def _render(hits: list[retrieve.Hit]) -> str:
    """Render recalled memories as a compact, scope-tagged context block."""
    lines = [
        "<memex-recall>",
        f"{len(hits)} long-term memories relevant to this prompt (retrieved "
        "automatically; verify any that name files or flags before relying on them):",
        "",
    ]
    for hit in hits:
        lines.append(f"### {hit.name} [{hit.scope}/{hit.mtype}]")
        if hit.description:
            lines.append(hit.description)
        snippet = hit.body.strip().splitlines()
        if snippet:
            lines.append(" ".join(snippet)[:400])
        lines.append("")
    lines.append("</memex-recall>")
    return "\n".join(lines)


def main() -> int:
    """Read the hook payload, recall memories, and print the context block."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:  # JSONDecodeError subclasses ValueError
        return 0

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return 0

    try:
        cfg = config_module.load(cwd=payload.get("cwd"))
        stores = [Store(cfg, scope) for scope in cfg.scopes if scope.db_path.exists()]
        if not stores:
            return 0
        embedder = embeddings.build(cfg)
        hits = retrieve.retrieve(cfg, stores, embedder, prompt)
        for store in stores:
            store.close()
    except Exception:
        # A hook failure must never block prompt submission; degrade silently.
        return 0

    if hits:
        print(_render(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
