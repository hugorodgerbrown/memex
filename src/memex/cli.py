"""Command-line entry point for memex.

Subcommands operate across the active scopes (global + the project resolved from
the current directory), unless ``--scope`` narrows them:

* ``index`` — sync each scope's index with its memory files (``--rebuild`` redoes all).
* ``query`` — layered hybrid recall for an ad-hoc query, printed for a human.
* ``dream`` — run the consolidation pass per scope and write dated reports.
* ``stats`` — show index size and per-memory recall strength per scope.
* ``doctor`` — show resolved scopes and verify the embedder / sqlite-vec.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from . import config as config_module
from . import dream as dream_module
from . import embeddings, index, retrieve
from .config import Config, Scope
from .store import Store


def _active_scopes(cfg: Config, only: str | None) -> list[Scope]:
    """Return the scopes to act on, optionally narrowed to one by name."""
    if only is None:
        return cfg.scopes
    scope = cfg.scope(only)
    return [scope] if scope is not None else []


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="memex", description=__doc__)
    parser.add_argument(
        "--scope",
        choices=["global", "project"],
        default=None,
        help="limit to one scope (default: all active scopes)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="sync the indexes with the memory files")
    p_index.add_argument("--rebuild", action="store_true", help="re-embed every memory")

    p_query = sub.add_parser("query", help="layered hybrid recall for a query")
    p_query.add_argument("text", help="the query text")
    p_query.add_argument("-k", type=int, default=None, help="number of memories")
    p_query.add_argument(
        "--no-graph", action="store_true", help="disable graph expansion"
    )

    sub.add_parser("dream", help="run the consolidation pass and write reports")
    sub.add_parser("stats", help="show index size and recall strength")
    sub.add_parser("doctor", help="show scopes and verify embedder / sqlite-vec")
    return parser


def _cmd_index(cfg: Config, scopes: list[Scope], *, rebuild: bool) -> int:
    """Sync each scope's index and print a per-scope summary."""
    embedder = embeddings.build(cfg)
    for scope in scopes:
        store = Store(cfg, scope)
        result = index.sync(cfg, scope, store, embedder, rebuild=rebuild)
        store.close()
        print(
            f"[{scope.name}] +{len(result.added)} added, "
            f"~{len(result.updated)} updated, -{len(result.removed)} removed, "
            f"{result.unchanged} unchanged"
        )
    return 0


def _cmd_query(
    cfg: Config, scopes: list[Scope], text: str, k: int | None, no_graph: bool
) -> int:
    """Run layered hybrid recall and print the results for a human reader."""
    embedder = embeddings.build(cfg)
    stores = [Store(cfg, scope) for scope in scopes if scope.db_path.exists()]
    hits = retrieve.retrieve(
        cfg, stores, embedder, text, k=k, expand_graph=not no_graph
    )
    for store in stores:
        store.close()
    if not hits:
        print("no memories matched")
        return 0
    for hit in hits:
        print(f"\n### {hit.name}  [{hit.scope}/{hit.mtype}]  ({hit.via})")
        print(f"score={hit.score:.4f}  decay×{hit.multiplier:.2f}")
        print(hit.description)
        print(hit.path)
    return 0


def _cmd_dream(cfg: Config, scopes: list[Scope]) -> int:
    """Run the dream cycle for each scope and write dated reports."""
    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    for scope in scopes:
        if not scope.db_path.exists():
            print(f"[{scope.name}] no index; skipped")
            continue
        store = Store(cfg, scope)
        report = dream_module.run(cfg, scope, store)
        store.close()
        path = dream_module.write_report(scope, report, today=today)
        print(
            f"[{scope.name}] {report.total} memories, "
            f"{len(report.duplicates)} dup candidates, "
            f"{len(report.broken_links)} broken links → {path}"
        )
    return 0


def _cmd_stats(cfg: Config, scopes: list[Scope]) -> int:
    """Print index size and per-memory recall strength for each scope."""
    for scope in scopes:
        if not scope.db_path.exists():
            print(f"\n[{scope.name}] no index")
            continue
        store = Store(cfg, scope)
        print(
            f"\n[{scope.name}] memories indexed: {store.count()}  ({scope.memory_dir})"
        )
        for row in store.access_summary():
            last = row["last_accessed"] or "-"
            print(
                f"  {row['name'][:38]:38s} {row['mtype'][:9]:9s} "
                f"hits={row['access_count']:<4d} sal={row['salience']:<6.1f} {last}"
            )
        store.close()
    return 0


def _cmd_doctor(cfg: Config) -> int:
    """Show the resolved scopes and verify the embedder and sqlite-vec."""
    print(
        f"backend: {cfg.embed_backend}  model: {cfg.embed_model}  dim: {cfg.embed_dim}"
    )
    for scope in cfg.scopes:
        exists = "indexed" if scope.db_path.exists() else "no index yet"
        print(f"scope[{scope.name}]: {scope.memory_dir}  ({exists})")
    try:
        store = Store(cfg, cfg.scopes[0])
        store.close()
        print("sqlite-vec: ok")
    except Exception as exc:
        # Doctor exists to report any failure plainly, so it catches broadly.
        print(f"sqlite-vec: FAILED — {exc}")
        return 1
    try:
        embedder = embeddings.build(cfg)
        vec = embedder.embed_one("hello world")
        print(f"embedder:   ok (dim={len(vec)})")
    except Exception as exc:
        # Doctor exists to report any failure plainly, so it catches broadly.
        print(f"embedder:   FAILED — {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the chosen subcommand."""
    args = _build_parser().parse_args(argv)
    cfg = config_module.load(cwd=os.getcwd())
    scopes = _active_scopes(cfg, args.scope)

    if args.command == "index":
        return _cmd_index(cfg, scopes, rebuild=args.rebuild)
    if args.command == "query":
        return _cmd_query(cfg, scopes, args.text, args.k, args.no_graph)
    if args.command == "dream":
        return _cmd_dream(cfg, scopes)
    if args.command == "stats":
        return _cmd_stats(cfg, scopes)
    if args.command == "doctor":
        return _cmd_doctor(cfg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
