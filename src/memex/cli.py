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
from pathlib import Path

from . import config as config_module
from . import distill as distill_module
from . import dream as dream_module
from . import embeddings, health, index, retrieve
from . import review as review_module
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
    sub.add_parser("health", help="report the last maintenance run's status and age")

    p_distill = sub.add_parser(
        "distill", help="extract memory candidates from a transcript into staging"
    )
    p_distill.add_argument("transcript", help="path to the session transcript JSONL")
    p_distill.add_argument(
        "--session-id", default="manual", help="origin session id for provenance"
    )

    sub.add_parser(
        "maintain", help="index + dream every scope and project (for cron/launchd)"
    )
    sub.add_parser("candidates", help="list staged memory candidates awaiting review")
    sub.add_parser("review", help="interactively accept/discard staged candidates")

    p_accept = sub.add_parser("accept", help="promote a staged candidate into memory")
    p_accept.add_argument("name", help="the candidate slug to accept")
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
            f"{len(report.supersessions)} supersessions, "
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


def _cmd_health(cfg: Config) -> int:
    """Report the last maintenance run's status/age and per-scope freshness."""
    now = dt.datetime.now(dt.UTC)
    print(f"maintenance log: {cfg.maintenance_log}")

    status = health.read_last_run(cfg.maintenance_log)
    if status is None:
        print("last run:        never (no maintenance log found)")
        exit_code = 1
    else:
        ts = status.started_at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = health.humanise_age(now - status.started_at)
        verdict = "OK" if status.succeeded else "FAILED"
        print(f"last run:        {ts} ({age}) — {verdict}")
        exit_code = 0 if status.succeeded else 1

    print("scopes (newest dream report):")
    today = now.date()
    for fresh in health.scope_freshness(config_module.load_all()):
        if fresh.last_report is None:
            print(f"  [{fresh.scope}] no report yet")
            continue
        days = (today - fresh.last_report).days
        when = "today" if days == 0 else f"{days}d ago"
        print(f"  [{fresh.scope}] {fresh.last_report.isoformat()} ({when})")
    return exit_code


def _cmd_maintain(cfg: Config) -> int:
    """Index and dream every scope and project; the scheduled entry point."""
    _cmd_index(cfg, cfg.scopes, rebuild=False)
    return _cmd_dream(cfg, cfg.scopes)


def _cmd_distill(cfg: Config, transcript: str, session_id: str) -> int:
    """Extract memory candidates from a transcript and stage them for review."""
    candidates = distill_module.extract(cfg, Path(transcript), cfg.distill_model)
    if not candidates:
        print("no candidates extracted (or the claude CLI was unavailable)")
        return 0
    written = distill_module.stage(cfg, candidates, session_id=session_id)
    for path in written:
        print(f"staged → {path}")
    print(f"\n{len(written)} candidate(s) staged. Review with: memex candidates")
    return 0


def _cmd_review(cfg: Config) -> int:
    """Interactively review staged candidates, re-indexing any accepted."""
    if not sys.stdin.isatty():
        print(
            "review is interactive; run it in a terminal "
            "(or use `memex candidates` + `memex accept <name>` non-interactively)"
        )
        return 1
    result = review_module.review(cfg, ask=input, emit=print)
    if result.accepted_scopes:
        embedder = embeddings.build(cfg)
        for name in result.accepted_scopes:
            scope = cfg.scope(name)
            if scope is None:
                continue
            store = Store(cfg, scope)
            index.sync(cfg, scope, store, embedder)
            store.close()
        print("re-indexed accepted memories")
    return 0


def _cmd_candidates(cfg: Config) -> int:
    """List staged candidates awaiting review."""
    staged = distill_module.list_candidates(cfg)
    if not staged:
        print("no staged candidates")
        return 0
    for item in staged:
        print(f"[{item.scope}] {item.name}  ({item.path})")
    print("\nPromote one with: memex accept <name>")
    return 0


def _cmd_accept(cfg: Config, name: str) -> int:
    """Promote a staged candidate into its scope's live memory directory."""
    destination = distill_module.accept(cfg, name)
    if destination is None:
        print(f"could not accept {name!r} (not staged, or a live memory exists)")
        return 1
    print(f"accepted → {destination}\nrun `memex index` to make it searchable")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the chosen subcommand."""
    args = _build_parser().parse_args(argv)
    if args.command == "maintain":
        return _cmd_maintain(config_module.load_all())

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
    if args.command == "health":
        return _cmd_health(cfg)
    if args.command == "distill":
        return _cmd_distill(cfg, args.transcript, args.session_id)
    if args.command == "review":
        return _cmd_review(cfg)
    if args.command == "candidates":
        return _cmd_candidates(cfg)
    if args.command == "accept":
        return _cmd_accept(cfg, args.name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
