"""Tests for promoting project memories to global and authoring new ones."""

from __future__ import annotations

from memex import authoring
from memex.config import Config, Scope


def _global(cfg: Config) -> Scope:
    scope = cfg.scope("global")
    assert scope is not None
    return scope


def _project(cfg: Config) -> Scope:
    scope = cfg.scope("project")
    assert scope is not None
    return scope


def test_promote_moves_file_and_index_line(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    project, global_ = _project(cfg), _global(cfg)
    write_memory(project, "use-tox", description="run tox in CI", mtype="feedback")
    (project.memory_dir / "MEMORY.md").write_text(
        "# Project index\n\n- [Use tox](use-tox.md) — run tox in CI\n",
        encoding="utf-8",
    )
    (global_.memory_dir / "MEMORY.md").write_text(
        "# Global index\n\n- [Style](style.md) — grounded tone\n", encoding="utf-8"
    )

    result = authoring.promote(cfg, "use-tox")

    assert result.ok
    assert result.index_moved
    assert (global_.memory_dir / "use-tox.md").exists()
    assert not (project.memory_dir / "use-tox.md").exists()
    # The index line left the project index and joined the global one.
    assert "use-tox.md" not in (project.memory_dir / "MEMORY.md").read_text()
    assert "use-tox.md" in (global_.memory_dir / "MEMORY.md").read_text()
    assert "style.md" in (global_.memory_dir / "MEMORY.md").read_text()


def test_promote_rejects_global_name_collision(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    write_memory(_project(cfg), "dupe", body="project copy")
    write_memory(_global(cfg), "dupe", body="global copy")

    result = authoring.promote(cfg, "dupe")

    assert not result.ok
    assert result.reason == "conflict"
    # Neither file was touched.
    assert (_project(cfg).memory_dir / "dupe.md").exists()


def test_promote_unknown_name_is_not_found(make_config) -> None:
    cfg = make_config(("global", "project"))
    result = authoring.promote(cfg, "ghost")
    assert not result.ok
    assert result.reason == "not-found"


def test_promote_without_project_scope(make_config, write_memory) -> None:
    cfg = make_config(("global",))
    result = authoring.promote(cfg, "anything")
    assert not result.ok
    assert result.reason == "no-project-scope"


def test_promote_without_index_line_still_moves_file(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    write_memory(_project(cfg), "no-line", body="body")

    result = authoring.promote(cfg, "no-line")

    assert result.ok
    assert not result.index_moved
    assert (_global(cfg).memory_dir / "no-line.md").exists()


def test_list_project_memories_sorted(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    write_memory(_project(cfg), "beta", description="second")
    write_memory(_project(cfg), "alpha", description="first")

    names = [m.name for m in authoring.list_project_memories(cfg)]

    assert names == ["alpha", "beta"]


def test_list_project_memories_empty_without_scope(make_config) -> None:
    cfg = make_config(("global",))
    assert authoring.list_project_memories(cfg) == []


def test_list_memories_reads_a_scope(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    write_memory(_global(cfg), "style", description="grounded tone")
    write_memory(_global(cfg), "tox", description="run tox")

    entries = authoring.list_memories(_global(cfg))

    assert [(e.name, e.description) for e in entries] == [
        ("style", "grounded tone"),
        ("tox", "run tox"),
    ]


def test_list_memories_empty_scope(make_config) -> None:
    cfg = make_config(("global",))
    assert authoring.list_memories(_global(cfg)) == []


def test_promote_interactively_picks_by_number(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    write_memory(_project(cfg), "alpha", description="first")
    write_memory(_project(cfg), "beta", description="second")
    answers = iter(["1", "q"])

    touched = authoring.promote_interactively(
        cfg, ask=lambda _prompt: next(answers), emit=lambda _msg: None
    )

    assert touched == {"project", "global"}
    assert (_global(cfg).memory_dir / "alpha.md").exists()
    assert not (_project(cfg).memory_dir / "alpha.md").exists()
    assert (_project(cfg).memory_dir / "beta.md").exists()


def test_promote_interactively_quit_promotes_nothing(make_config, write_memory) -> None:
    cfg = make_config(("global", "project"))
    write_memory(_project(cfg), "alpha")

    touched = authoring.promote_interactively(
        cfg, ask=lambda _prompt: "q", emit=lambda _msg: None
    )

    assert touched == set()
    assert (_project(cfg).memory_dir / "alpha.md").exists()


def test_add_writes_file_and_index_line(make_config) -> None:
    cfg = make_config(("global",))
    result = authoring.add(
        cfg,
        scope="global",
        name="Always Use Tox",
        description="tox runs CI",
        mtype="feedback",
        body="Use tox everywhere.",
    )

    assert result.ok
    path = _global(cfg).memory_dir / "always-use-tox.md"
    assert path == result.path
    text = path.read_text(encoding="utf-8")
    assert "name: always-use-tox" in text
    assert "type: feedback" in text
    assert "Use tox everywhere." in text
    index = (_global(cfg).memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "always-use-tox.md" in index


def test_add_rejects_duplicate(make_config, write_memory) -> None:
    cfg = make_config(("global",))
    write_memory(_global(cfg), "taken", body="existing")
    result = authoring.add(
        cfg, scope="global", name="taken", description="", mtype="reference", body="x"
    )
    assert not result.ok
    assert result.reason == "exists"


def test_add_rejects_empty_slug(make_config) -> None:
    cfg = make_config(("global",))
    result = authoring.add(
        cfg, scope="global", name="!!!", description="", mtype="reference", body="x"
    )
    assert not result.ok
    assert result.reason == "bad-name"


def test_add_unknown_scope(make_config) -> None:
    cfg = make_config(("global",))
    result = authoring.add(
        cfg, scope="project", name="x", description="", mtype="reference", body="y"
    )
    assert not result.ok
    assert result.reason == "no-such-scope"


def test_add_invalid_type_falls_back_to_reference(make_config) -> None:
    cfg = make_config(("global",))
    result = authoring.add(
        cfg, scope="global", name="x", description="", mtype="bogus", body="y"
    )
    assert result.ok
    assert "type: reference" in result.path.read_text(encoding="utf-8")


def test_promoted_memory_is_searchable_after_reindex(make_config, write_memory) -> None:
    """End-to-end: promote then index, and the memory recalls from global."""
    from memex import embeddings, index, retrieve
    from memex.store import Store

    cfg = make_config(("global", "project"))
    write_memory(
        _project(cfg),
        "tox-rule",
        description="always run tox",
        body="tox drives CI",
        mtype="feedback",
    )

    assert authoring.promote(cfg, "tox-rule").ok

    embedder = embeddings.build(cfg)
    stores = []
    for name in ("global", "project"):
        scope = cfg.scope(name)
        store = Store(cfg, scope)
        index.sync(cfg, scope, store, embedder)
        stores.append((scope, store))

    open_stores = [s for _scope, s in stores if _scope.db_path.exists()]
    hits = retrieve.retrieve(cfg, open_stores, embedder, "tox", k=5)
    for _scope, store in stores:
        store.close()

    scopes_with_hit = {hit.scope for hit in hits if hit.name == "tox-rule"}
    assert scopes_with_hit == {"global"}
