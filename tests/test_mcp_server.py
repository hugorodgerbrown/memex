"""Tests for the memory-management MCP server.

The tool logic is exercised through its plain functions; one test drives the
registered server end to end so the tool wiring and error mapping are covered.
"""

from __future__ import annotations

import asyncio

import pytest

from memex import embeddings, index, mcp_server
from memex.config import Config, Scope
from memex.store import Store

_APP = "-Users-me-Projects-app"
_OTHER_APP = "-Users-me-Work-app"
_TOOLS = "-Users-me-Projects-tools"


def _scope(cfg: Config, name: str) -> Scope:
    scope = cfg.scope(name)
    assert scope is not None
    return scope


def _index(cfg: Config, scope: Scope) -> None:
    store = Store(cfg, scope)
    index.sync(cfg, scope, store, embeddings.build(cfg))
    store.close()


def test_resolve_scope_by_name_path_and_suffix(make_config) -> None:
    cfg = make_config(("global", _APP, _TOOLS))

    assert mcp_server.resolve_scope(cfg, "global").name == "global"
    assert mcp_server.resolve_scope(cfg, _TOOLS).name == _TOOLS
    assert mcp_server.resolve_scope(cfg, "tools").name == _TOOLS
    assert mcp_server.resolve_scope(cfg, "/Users/me/Projects/app").name == _APP
    # A worktree path resolves to its parent repository.
    worktree = "/Users/me/Projects/app/.claude/worktrees/feature-x"
    assert mcp_server.resolve_scope(cfg, worktree).name == _APP


def test_resolve_scope_rejects_unknown_and_ambiguous(make_config) -> None:
    cfg = make_config(("global", _APP, _OTHER_APP))

    with pytest.raises(mcp_server.ScopeError, match="several projects"):
        mcp_server.resolve_scope(cfg, "app")
    with pytest.raises(mcp_server.ScopeError, match="no scope named"):
        mcp_server.resolve_scope(cfg, "ghost")
    with pytest.raises(mcp_server.ScopeError, match="no project memories"):
        mcp_server.resolve_scope(cfg, "/Users/me/elsewhere")


def test_list_scopes_counts_memories(make_config, write_memory) -> None:
    cfg = make_config(("global", _APP))
    write_memory(_scope(cfg, _APP), "one")
    write_memory(_scope(cfg, _APP), "two")

    scopes = {row["scope"]: row for row in mcp_server.list_scopes(cfg)}

    assert scopes["global"]["memories"] == 0
    assert scopes[_APP]["memories"] == 2
    assert scopes[_APP]["indexed"] is False
    assert scopes[_APP]["latest_report"] is None


def test_list_memories_includes_recall_stats_once_indexed(
    make_config, write_memory
) -> None:
    cfg = make_config(("global",))
    scope = _scope(cfg, "global")
    write_memory(scope, "tone", description="grounded", mtype="feedback", pinned=True)

    before = mcp_server.list_memories(cfg, "global")["memories"]
    assert before == [
        {
            "name": "tone",
            "description": "grounded",
            "type": "feedback",
            "pinned": True,
            "links": [],
        }
    ]

    _index(cfg, scope)
    after = mcp_server.list_memories(cfg, "global")["memories"][0]
    assert after["recall_count"] == 0
    assert "salience" in after


def test_read_memory_returns_file_and_rejects_escape(make_config, write_memory) -> None:
    cfg = make_config(("global",))
    write_memory(_scope(cfg, "global"), "tone", body="be brief")

    result = mcp_server.read_memory(cfg, "global", "tone")
    assert result["content"].endswith("be brief\n")

    with pytest.raises(ValueError, match="no memory named"):
        mcp_server.read_memory(cfg, "global", "../tone")


def test_search_does_not_record_access(make_config, write_memory) -> None:
    cfg = make_config(("global",))
    scope = _scope(cfg, "global")
    write_memory(scope, "tox", description="run tox before a PR", body="tox tox")
    _index(cfg, scope)

    hits = mcp_server.search_memories(cfg, embeddings.build(cfg), "tox")

    assert hits[0]["name"] == "tox"
    assert hits[0]["scope"] == "global"
    stats = mcp_server.list_memories(cfg, "global")["memories"][0]
    assert stats["recall_count"] == 0


def test_latest_dream_report_picks_newest(make_config) -> None:
    cfg = make_config(("global",))
    scope = _scope(cfg, "global")
    scope.reports_dir.mkdir(parents=True)
    (scope.reports_dir / "REPORT-2026-09-01.md").write_text("old")
    (scope.reports_dir / "REPORT-2026-09-30.md").write_text("new")

    result = mcp_server.latest_dream_report(cfg, "global")

    assert result["report"] == "REPORT-2026-09-30.md"
    assert result["content"] == "new"


def test_update_and_forget_through_functions(make_config, write_memory) -> None:
    cfg = make_config(("global",))
    scope = _scope(cfg, "global")
    write_memory(scope, "fact", description="old")

    updated = mcp_server.update_memory(cfg, "global", "fact", description="new")
    assert updated["name"] == "fact"
    forgotten = mcp_server.forget_memory(cfg, "global", "fact")
    assert forgotten["archived_to"].endswith(".md")
    assert not (scope.memory_dir / "fact.md").exists()

    with pytest.raises(ValueError, match="no memory named"):
        mcp_server.forget_memory(cfg, "global", "fact")


def test_promote_memory_requires_a_project(make_config, write_memory) -> None:
    cfg = make_config(("global", _APP))
    write_memory(_scope(cfg, _APP), "fact")
    write_memory(_scope(cfg, "global"), "taken")

    result = mcp_server.promote_memory(cfg, "app", "fact")
    assert result == {
        "from": _APP,
        "to": "global",
        "path": str(_scope(cfg, "global").memory_dir / "fact.md"),
        "memory_index_moved": False,
    }

    with pytest.raises(ValueError, match="name a project"):
        mcp_server.promote_memory(cfg, "global", "taken")


def test_server_registers_tools_and_maps_errors(
    make_config, write_memory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    cfg = make_config(("global", _APP))
    write_memory(_scope(cfg, _APP), "fact", description="d")
    monkeypatch.setattr(mcp_server.config_module, "load_all", lambda: cfg)
    server = mcp_server.build_server()

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {
        "list_scopes",
        "list_memories",
        "read_memory",
        "search_memories",
        "latest_dream_report",
        "update_memory",
        "promote_memory",
        "forget_memory",
    }
    assert tools["read_memory"].annotations.read_only_hint is True
    assert tools["forget_memory"].annotations.destructive_hint is True

    result = asyncio.run(
        server.call_tool(
            "update_memory", {"scope": "app", "name": "fact", "type": "user"}
        )
    )
    assert not result.is_error
    assert "type: user" in (_scope(cfg, _APP).memory_dir / "fact.md").read_text()

    with pytest.raises(ToolError, match="no memory named 'ghost'"):
        asyncio.run(
            server.call_tool("forget_memory", {"scope": "app", "name": "ghost"})
        )
