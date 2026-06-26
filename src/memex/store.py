"""The derived index: a single SQLite file holding vectors, keywords and stats.

Three coordinated tables back the hybrid recall:

* ``memories`` — one row per memory file (the canonical fields + content hash).
* ``vec_memories`` — a ``sqlite-vec`` virtual table for cosine KNN search.
* ``fts_memories`` — an FTS5 virtual table for BM25 keyword search.
* ``memory_stats`` — access recency/frequency/salience driving decay re-ranking.

The file lives beside the Markdown it indexes and is disposable: delete it and
``memex index --rebuild`` reconstructs it from the memory files.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3

import numpy as np
import sqlite_vec

from .config import Config, Scope
from .markdown import MemoryFile

# FTS5 tokens: word characters only, lower-cased, used to build a MATCH query.
_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return dt.datetime.now(dt.UTC).isoformat()


def _parse_iso(value: str | None) -> dt.datetime | None:
    """Parse an ISO-8601 string, tolerating ``None``."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


class Store:
    """SQLite-backed index over the memory files."""

    def __init__(self, config: Config, scope: Scope) -> None:
        """Open (creating if needed) the index database for one ``scope``."""
        self._config = config
        self._scope = scope
        scope.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(scope.db_path)
        self._db.row_factory = sqlite3.Row
        self._db.enable_load_extension(True)
        sqlite_vec.load(self._db)
        self._db.enable_load_extension(False)
        self._create_schema()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()

    @property
    def scope_name(self) -> str:
        """The name of the scope this store indexes (``global`` / ``project``)."""
        return self._scope.name

    def _create_schema(self) -> None:
        """Create the tables and indexes if they do not yet exist."""
        self._db.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS memories (
                id           INTEGER PRIMARY KEY,
                name         TEXT UNIQUE NOT NULL,
                path         TEXT NOT NULL,
                mtype        TEXT NOT NULL,
                description  TEXT NOT NULL,
                body         TEXT NOT NULL,
                links        TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                indexed_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_stats (
                memory_id     INTEGER PRIMARY KEY
                              REFERENCES memories(id) ON DELETE CASCADE,
                created_at    TEXT NOT NULL,
                last_accessed TEXT,
                access_count  INTEGER NOT NULL DEFAULT 0,
                salience      REAL NOT NULL DEFAULT 0.0
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                memory_id INTEGER PRIMARY KEY,
                embedding FLOAT[{self._config.embed_dim}]
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_memories USING fts5(
                name, description, body, content=''
            );
            """
        )
        self._db.commit()

    # -- indexing -----------------------------------------------------------

    def existing_hashes(self) -> dict[str, str]:
        """Return a ``name -> content_hash`` map of currently indexed memories."""
        rows = self._db.execute("SELECT name, content_hash FROM memories").fetchall()
        return {row["name"]: row["content_hash"] for row in rows}

    def upsert(self, memory: MemoryFile, embedding: list[float]) -> None:
        """Insert or replace a memory and its vector/keyword/stats rows."""
        cur = self._db.execute("SELECT id FROM memories WHERE name = ?", (memory.name,))
        row = cur.fetchone()
        memory_id = row["id"] if row else None

        if memory_id is not None:
            self._db.execute(
                "UPDATE memories SET path=?, mtype=?, description=?, body=?, "
                "links=?, content_hash=?, indexed_at=? WHERE id=?",
                (
                    str(memory.path),
                    memory.mtype,
                    memory.description,
                    memory.body,
                    json.dumps(memory.links),
                    memory.content_hash,
                    _now(),
                    memory_id,
                ),
            )
            self._db.execute("DELETE FROM vec_memories WHERE memory_id=?", (memory_id,))
            self._db.execute("DELETE FROM fts_memories WHERE rowid=?", (memory_id,))
        else:
            cur = self._db.execute(
                "INSERT INTO memories (name, path, mtype, description, body, links, "
                "content_hash, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory.name,
                    str(memory.path),
                    memory.mtype,
                    memory.description,
                    memory.body,
                    json.dumps(memory.links),
                    memory.content_hash,
                    _now(),
                ),
            )
            memory_id = int(cur.lastrowid)
            self._db.execute(
                "INSERT INTO memory_stats (memory_id, created_at) VALUES (?, ?)",
                (memory_id, _now()),
            )

        self._db.execute(
            "INSERT INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
            (memory_id, sqlite_vec.serialize_float32(embedding)),
        )
        self._db.execute(
            "INSERT INTO fts_memories (rowid, name, description, body) "
            "VALUES (?, ?, ?, ?)",
            (memory_id, memory.name, memory.description, memory.body),
        )
        self._db.commit()

    def prune(self, live_names: set[str]) -> list[str]:
        """Soft-delete index rows whose source file no longer exists.

        Returns the names removed. Mirrors GBrain's "deletion in git becomes a
        soft-delete in the database" — the file is the source of truth.
        """
        rows = self._db.execute("SELECT id, name FROM memories").fetchall()
        removed: list[str] = []
        for row in rows:
            if row["name"] in live_names:
                continue
            self._db.execute("DELETE FROM vec_memories WHERE memory_id=?", (row["id"],))
            self._db.execute("DELETE FROM fts_memories WHERE rowid=?", (row["id"],))
            self._db.execute("DELETE FROM memory_stats WHERE memory_id=?", (row["id"],))
            self._db.execute("DELETE FROM memories WHERE id=?", (row["id"],))
            removed.append(row["name"])
        self._db.commit()
        return removed

    # -- retrieval ----------------------------------------------------------

    def knn(self, query_vec: list[float], n: int) -> list[tuple[int, float]]:
        """Return the ``n`` nearest memory ids and distances for ``query_vec``."""
        rows = self._db.execute(
            "SELECT memory_id, distance FROM vec_memories "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), n),
        ).fetchall()
        return [(row["memory_id"], row["distance"]) for row in rows]

    def fts(self, query: str, n: int) -> list[tuple[int, float]]:
        """Return up to ``n`` keyword (BM25) matches for ``query``."""
        tokens = sorted(set(_TOKEN.findall(query.lower())))
        if not tokens:
            return []
        match = " OR ".join(f'"{token}"' for token in tokens)
        try:
            rows = self._db.execute(
                "SELECT rowid, bm25(fts_memories) AS score FROM fts_memories "
                "WHERE fts_memories MATCH ? ORDER BY score LIMIT ?",
                (match, n),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(row["rowid"], row["score"]) for row in rows]

    def decay_multiplier(self, memory_id: int) -> float:
        """Compute the recency/frequency decay multiplier for a memory.

        Recently accessed *or* frequently accessed memories surface (toward the
        ceiling); long-unused ones dampen toward the floor. The underlying fact
        is never deleted — only its recall strength falls (Mem0's model).
        """
        row = self._db.execute(
            "SELECT created_at, last_accessed, access_count FROM memory_stats "
            "WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return 1.0

        reference = _parse_iso(row["last_accessed"]) or _parse_iso(row["created_at"])
        now = dt.datetime.now(dt.UTC)
        age_days = (now - reference).total_seconds() / 86400.0 if reference else 0.0

        recency = 0.5 ** (max(age_days, 0.0) / self._config.decay_half_life_days)
        frequency = min(row["access_count"] / 10.0, 1.0)
        strength = max(recency, frequency)

        span = self._config.decay_ceiling - self._config.decay_floor
        return self._config.decay_floor + span * strength

    def touch(self, memory_ids: list[int]) -> None:
        """Record an access for each id, reinforcing its recall strength."""
        for memory_id in memory_ids:
            self._db.execute(
                "UPDATE memory_stats SET last_accessed=?, access_count=access_count+1 "
                "WHERE memory_id=?",
                (_now(), memory_id),
            )
        self._db.commit()

    def id_for_name(self, name: str) -> int | None:
        """Resolve a memory name to its id within this scope, if present."""
        row = self._db.execute(
            "SELECT id FROM memories WHERE name=?", (name,)
        ).fetchone()
        return int(row["id"]) if row else None

    def hydrate(self, memory_id: int) -> dict:
        """Return the full stored record for a memory id."""
        row = self._db.execute(
            "SELECT name, path, mtype, description, body, links FROM memories "
            "WHERE id=?",
            (memory_id,),
        ).fetchone()
        return {
            "name": row["name"],
            "path": row["path"],
            "mtype": row["mtype"],
            "description": row["description"],
            "body": row["body"],
            "links": json.loads(row["links"]),
        }

    # -- maintenance --------------------------------------------------------

    def all_records(self) -> list[dict]:
        """Return every memory with its id, fields and embedding."""
        rows = self._db.execute(
            "SELECT m.id, m.name, m.mtype, m.description, m.links, "
            "v.embedding FROM memories m JOIN vec_memories v ON v.memory_id = m.id"
        ).fetchall()
        records: list[dict] = []
        for row in rows:
            records.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "mtype": row["mtype"],
                    "description": row["description"],
                    "links": json.loads(row["links"]),
                    "embedding": np.frombuffer(
                        row["embedding"], dtype=np.float32
                    ).tolist(),
                }
            )
        return records

    def known_names(self) -> set[str]:
        """Return the set of indexed memory names (for link validation)."""
        rows = self._db.execute("SELECT name FROM memories").fetchall()
        return {row["name"] for row in rows}

    def set_salience(self, memory_id: int, salience: float) -> None:
        """Persist a recomputed salience score for a memory."""
        self._db.execute(
            "UPDATE memory_stats SET salience=? WHERE memory_id=?",
            (salience, memory_id),
        )
        self._db.commit()

    def count(self) -> int:
        """Return the number of indexed memories."""
        return int(
            self._db.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        )

    def access_summary(self) -> list[dict]:
        """Return per-memory access stats ordered by recall strength."""
        rows = self._db.execute(
            "SELECT m.name, m.mtype, s.access_count, s.last_accessed, s.salience "
            "FROM memories m JOIN memory_stats s ON s.memory_id = m.id "
            "ORDER BY s.salience DESC, s.access_count DESC"
        ).fetchall()
        return [dict(row) for row in rows]
