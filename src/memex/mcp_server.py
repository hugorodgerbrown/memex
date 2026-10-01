"""A local MCP server for managing existing memories from a chat client.

Run with ``memex mcp`` (stdio transport) and register it in the Claude Desktop
config. It manages what is already there — list, read, search, edit, promote,
forget — and deliberately offers no way to author a new memory.

A chat session has no working directory, so every tool names its scope
explicitly: ``global``, or a project. Scopes come from
:func:`config.load_all`, so a project is any directory under
``~/.claude/projects/`` that holds memories; it can be named by its full
mangled directory name, by the project path, or by the last segment of the
path when that is unambiguous (``memex`` for ``/Users/me/Projects/memex``).

Tools change the Markdown files only. They do not re-index: the scheduled
``memex maintain`` run (and the Code tab's ``Stop`` hook, for the scopes a
session touches) picks the changes up, so search results and recall lag edits
until then.

The tool logic lives in plain functions that take a :class:`Config`, so the
tests call them directly; :func:`build_server` wraps them as MCP tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import authoring, embeddings, retrieve
from . import config as config_module
from .config import Config, Scope
from .embeddings import Embedder
from .markdown import iter_memory_files, parse
from .store import Store

_INSTRUCTIONS = """\
Manages the user's Memex long-term memories: Markdown files in a global scope \
(cross-project facts) and one scope per project. Start with list_scopes. \
Address a memory by its scope and its name (the file stem). Changes reach \
recall at the next scheduled re-index, not at once. forget_memory archives the \
file rather than deleting it outright. Confirm with the user before calling \
update_memory, promote_memory or forget_memory."""


class ScopeError(LookupError):
    """A scope identifier that matches no scope, or more than one."""


def resolve_scope(cfg: Config, ident: str) -> Scope:
    """Return the scope named by ``ident``.

    Accepts ``global``, a scope's exact (mangled) name, a project path, or the
    final path segment of a project when exactly one project ends with it.
    """
    ident = ident.strip()
    for scope in cfg.scopes:
        if scope.name == ident:
            return scope

    if ident.startswith(("/", "~")):
        root = config_module.resolve_project_root(str(Path(ident).expanduser()))
        mangled = config_module.mangle(root or "")
        for scope in cfg.scopes:
            if scope.name == mangled:
                return scope
        raise ScopeError(f"no project memories for path {ident!r}")

    suffix = "-" + config_module.mangle(ident)
    matches = [scope for scope in cfg.scopes if scope.name.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(scope.name for scope in matches)
        raise ScopeError(f"{ident!r} matches several projects: {names}")
    raise ScopeError(f"no scope named {ident!r}; call list_scopes for the names")


def list_scopes(cfg: Config) -> list[dict[str, Any]]:
    """Return every scope with its directory and memory count."""
    return [
        {
            "scope": scope.name,
            "memory_dir": str(scope.memory_dir),
            "memories": len(iter_memory_files(scope.memory_dir)),
            "indexed": scope.db_path.exists(),
            "latest_report": _latest_report_name(scope),
        }
        for scope in cfg.scopes
    ]


def list_memories(cfg: Config, scope_ident: str) -> dict[str, Any]:
    """Return a scope's memories with the recall stats that inform pruning."""
    scope = resolve_scope(cfg, scope_ident)
    stats = _access_stats(cfg, scope)
    memories = []
    for path in iter_memory_files(scope.memory_dir):
        memory = parse(path)
        entry: dict[str, Any] = {
            "name": path.stem,
            "description": memory.description,
            "type": memory.mtype,
            "pinned": memory.pinned,
            "links": memory.links,
        }
        row = stats.get(memory.name)
        if row is not None:
            entry["recall_count"] = row["access_count"]
            entry["last_recalled"] = row["last_accessed"]
            entry["salience"] = row["salience"]
        memories.append(entry)
    return {"scope": scope.name, "memories": memories}


def read_memory(cfg: Config, scope_ident: str, name: str) -> dict[str, Any]:
    """Return one memory's full file text."""
    scope = resolve_scope(cfg, scope_ident)
    path = _require_path(scope, name)
    return {
        "scope": scope.name,
        "name": name,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


def search_memories(
    cfg: Config,
    embedder: Embedder,
    query: str,
    scope_ident: str | None = None,
    k: int = 10,
) -> list[dict[str, Any]]:
    """Hybrid search over the indexes, without counting it as a recall."""
    scopes = [resolve_scope(cfg, scope_ident)] if scope_ident else cfg.scopes
    stores = [Store(cfg, scope) for scope in scopes if scope.db_path.exists()]
    try:
        hits = retrieve.retrieve(
            cfg, stores, embedder, query, k=k, expand_graph=False, record_access=False
        )
    finally:
        for store in stores:
            store.close()
    return [
        {
            "scope": hit.scope,
            "name": Path(hit.path).stem,
            "description": hit.description,
            "type": hit.mtype,
            "score": round(hit.score, 4),
        }
        for hit in hits
    ]


def latest_dream_report(cfg: Config, scope_ident: str) -> dict[str, Any]:
    """Return the newest dream-cycle report: duplicates, broken links, salience."""
    scope = resolve_scope(cfg, scope_ident)
    reports = _reports(scope)
    if not reports:
        raise ScopeError(f"no dream report for {scope.name} yet")
    return {
        "scope": scope.name,
        "report": reports[-1].name,
        "content": reports[-1].read_text(encoding="utf-8"),
    }


def update_memory(
    cfg: Config,
    scope_ident: str,
    name: str,
    *,
    description: str | None = None,
    body: str | None = None,
    mtype: str | None = None,
    pinned: bool | None = None,
) -> dict[str, Any]:
    """Edit the given fields of an existing memory."""
    scope = resolve_scope(cfg, scope_ident)
    result = authoring.update(
        scope, name, description=description, body=body, mtype=mtype, pinned=pinned
    )
    if not result.ok:
        reasons = {
            "not-found": f"no memory named {name!r} in {scope.name}",
            "bad-frontmatter": f"{name!r} has frontmatter that is not a YAML mapping",
            "no-change": "nothing to change: the values match the file",
        }
        raise ValueError(reasons.get(result.reason, result.reason))
    return {
        "scope": scope.name,
        "name": name,
        "path": str(result.path),
        "memory_index_updated": result.index_updated,
    }


def promote_memory(cfg: Config, project_ident: str, name: str) -> dict[str, Any]:
    """Move a project memory into the global scope."""
    source = resolve_scope(cfg, project_ident)
    global_ = cfg.scope("global")
    if global_ is None or source.name == "global":
        raise ValueError("promote moves a project memory into global; name a project")
    result = authoring.move(source, global_, name)
    if not result.ok:
        reasons = {
            "not-found": f"no memory named {name!r} in {source.name}",
            "conflict": f"a global memory named {name!r} already exists",
        }
        raise ValueError(reasons.get(result.reason, result.reason))
    return {
        "from": source.name,
        "to": "global",
        "path": str(result.destination),
        "memory_index_moved": result.index_moved,
    }


def forget_memory(cfg: Config, scope_ident: str, name: str) -> dict[str, Any]:
    """Archive a memory out of its scope and drop its ``MEMORY.md`` line."""
    scope = resolve_scope(cfg, scope_ident)
    result = authoring.forget(scope, name)
    if not result.ok:
        raise ValueError(f"no memory named {name!r} in {scope.name}")
    return {
        "scope": scope.name,
        "name": name,
        "archived_to": str(result.archived_to),
        "memory_index_updated": result.index_removed,
        "now_broken_links_from": result.linked_from,
    }


def _require_path(scope: Scope, name: str) -> Path:
    """Return the file for ``name`` in ``scope`` or raise a readable error."""
    path = authoring.memory_path(scope, name)
    if path is None:
        raise ValueError(f"no memory named {name!r} in {scope.name}")
    return path


def _access_stats(cfg: Config, scope: Scope) -> dict[str, dict[str, Any]]:
    """Return per-memory recall stats keyed by frontmatter name (empty if unindexed)."""
    if not scope.db_path.exists():
        return {}
    store = Store(cfg, scope)
    try:
        return {row["name"]: row for row in store.access_summary()}
    finally:
        store.close()


def _reports(scope: Scope) -> list[Path]:
    """Return the scope's dream reports, oldest first."""
    if not scope.reports_dir.is_dir():
        return []
    return sorted(scope.reports_dir.glob("REPORT-*.md"))


def _latest_report_name(scope: Scope) -> str | None:
    """Return the newest report's file name, or ``None``."""
    reports = _reports(scope)
    return reports[-1].name if reports else None


def build_server() -> Any:
    """Construct the MCP server with every management tool registered."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    server = MCPServer("memex", instructions=_INSTRUCTIONS)
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    editing = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, open_world_hint=False
    )
    embedder_cache: list[Embedder] = []

    def cfg() -> Config:
        # Reloaded per call so a project that gains memories mid-session appears.
        return config_module.load_all()

    def guarded(call: Any) -> Any:
        try:
            return call()
        except (ScopeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="list_scopes", annotations=read_only)
    def _list_scopes() -> list[dict[str, Any]]:
        """List the global scope and every project scope, with memory counts."""
        return list_scopes(cfg())

    @server.tool(name="list_memories", annotations=read_only)
    def _list_memories(scope: str) -> dict[str, Any]:
        """List a scope's memories: name, description, type, pinned, wikilinks,
        and, once indexed, recall_count / last_recalled / salience. Low recall
        counts and old last_recalled dates mark pruning candidates."""
        return guarded(lambda: list_memories(cfg(), scope))

    @server.tool(name="read_memory", annotations=read_only)
    def _read_memory(scope: str, name: str) -> dict[str, Any]:
        """Return one memory's full Markdown, frontmatter included."""
        return guarded(lambda: read_memory(cfg(), scope, name))

    @server.tool(name="search_memories", annotations=read_only)
    def _search_memories(
        query: str, scope: str | None = None, k: int = 10
    ) -> list[dict[str, Any]]:
        """Search memories by meaning and keyword. Omit scope to search all.
        Reads the index, so edits since the last re-index are not reflected.
        Does not count as a recall."""

        def run() -> list[dict[str, Any]]:
            config = cfg()
            if not embedder_cache:
                embedder_cache.append(embeddings.build(config))
            return search_memories(config, embedder_cache[0], query, scope, k)

        return guarded(run)

    @server.tool(name="latest_dream_report", annotations=read_only)
    def _latest_dream_report(scope: str) -> dict[str, Any]:
        """Return the newest consolidation report for a scope: candidate
        duplicates, supersessions, broken wikilinks, and salience ranking."""
        return guarded(lambda: latest_dream_report(cfg(), scope))

    @server.tool(name="update_memory", annotations=editing)
    def _update_memory(
        scope: str,
        name: str,
        description: str | None = None,
        body: str | None = None,
        type: str | None = None,  # the frontmatter key's own name
        pinned: bool | None = None,
    ) -> dict[str, Any]:
        """Edit an existing memory. Only the fields passed change; body replaces
        the whole body. type is one of user, feedback, project, reference.
        A new description is mirrored into the scope's MEMORY.md line."""
        return guarded(
            lambda: update_memory(
                cfg(),
                scope,
                name,
                description=description,
                body=body,
                mtype=type,
                pinned=pinned,
            )
        )

    @server.tool(name="promote_memory", annotations=editing)
    def _promote_memory(project: str, name: str) -> dict[str, Any]:
        """Move a project memory into the global scope, with its MEMORY.md line."""
        return guarded(lambda: promote_memory(cfg(), project, name))

    @server.tool(name="forget_memory", annotations=editing)
    def _forget_memory(scope: str, name: str) -> dict[str, Any]:
        """Remove a memory from its scope. The file moves to the scope's
        .memex/forgotten/ archive (recoverable) and its MEMORY.md line is
        dropped. Reports other memories whose wikilinks now point at nothing."""
        return guarded(lambda: forget_memory(cfg(), scope, name))

    return server


def serve() -> None:
    """Run the server on stdio until the client disconnects."""
    build_server().run()
