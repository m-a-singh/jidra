from __future__ import annotations

import sqlite3
import time

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_chunks (
    id              TEXT PRIMARY KEY,
    source_path     TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    title           TEXT,
    content         TEXT NOT NULL,
    linked_classes  TEXT NOT NULL DEFAULT '',
    chunk_index     INTEGER NOT NULL DEFAULT 0,
    ts              INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_sources (
    source_path     TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL,
    title           TEXT,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    indexed_at      INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
    title,
    content,
    linked_classes,
    content='doc_chunks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS doc_chunks_ai AFTER INSERT ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(rowid, title, content, linked_classes)
    VALUES (new.rowid, new.title, new.content, new.linked_classes);
END;

CREATE TRIGGER IF NOT EXISTS doc_chunks_ad AFTER DELETE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, title, content, linked_classes)
    VALUES ('delete', old.rowid, old.title, old.content, old.linked_classes);
END;

CREATE TRIGGER IF NOT EXISTS doc_chunks_au AFTER UPDATE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, title, content, linked_classes)
    VALUES ('delete', old.rowid, old.title, old.content, old.linked_classes);
    INSERT INTO doc_chunks_fts(rowid, title, content, linked_classes)
    VALUES (new.rowid, new.title, new.content, new.linked_classes);
END;
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── Write ─────────────────────────────────────────────────────────────────────


def upsert_chunks(conn: sqlite3.Connection, chunks: list[dict]) -> None:
    """Insert or replace doc chunks. Each dict must have keys matching doc_chunks columns."""
    conn.executemany(
        """INSERT OR REPLACE INTO doc_chunks
           (id, source_path, source_type, title, content, linked_classes, chunk_index, ts)
           VALUES (:id, :source_path, :source_type, :title, :content, :linked_classes, :chunk_index, :ts)""",
        chunks,
    )
    conn.commit()


def upsert_source(
    conn: sqlite3.Connection,
    source_path: str,
    source_type: str,
    title: str | None,
    chunk_count: int,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO doc_sources (source_path, source_type, title, chunk_count, indexed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (source_path, source_type, title, chunk_count, int(time.time() * 1000)),
    )
    conn.commit()


def delete_source(conn: sqlite3.Connection, source_path: str) -> None:
    conn.execute("DELETE FROM doc_chunks WHERE source_path = ?", (source_path,))
    conn.execute("DELETE FROM doc_sources WHERE source_path = ?", (source_path,))
    conn.commit()


# ── Query ─────────────────────────────────────────────────────────────────────


def query_by_class(
    conn: sqlite3.Connection, class_name: str, limit: int = 5
) -> list[dict]:
    """Return chunks explicitly linked to a class name."""
    rows = conn.execute(
        """SELECT id, source_path, source_type, title, content, linked_classes, chunk_index
           FROM doc_chunks
           WHERE (',' || linked_classes || ',') LIKE ?
           ORDER BY chunk_index
           LIMIT ?""",
        (f"%,{class_name},%", limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def query_fts(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[dict]:
    """Full-text search across title + content + linked_classes."""
    try:
        rows = conn.execute(
            """SELECT dc.id, dc.source_path, dc.source_type, dc.title, dc.content,
                      dc.linked_classes, dc.chunk_index,
                      rank
               FROM doc_chunks_fts
               JOIN doc_chunks dc ON doc_chunks_fts.rowid = dc.rowid
               WHERE doc_chunks_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def docs_available_for_class(conn: sqlite3.Connection, class_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM doc_chunks WHERE (',' || linked_classes || ',') LIKE ? LIMIT 1",
        (f"%,{class_name},%",),
    ).fetchone()
    return row is not None


def docs_available_for_query(conn: sqlite3.Connection, query: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM doc_chunks_fts WHERE doc_chunks_fts MATCH ? LIMIT 1",
            (query,),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def list_sources(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT source_path, source_type, title, chunk_count, indexed_at FROM doc_sources ORDER BY indexed_at DESC"
    ).fetchall()
    return [
        dict(
            zip(["source_path", "source_type", "title", "chunk_count", "indexed_at"], r)
        )
        for r in rows
    ]


def _row_to_dict(row) -> dict:
    keys = [
        "id",
        "source_path",
        "source_type",
        "title",
        "content",
        "linked_classes",
        "chunk_index",
    ]
    return dict(zip(keys, row[: len(keys)]))
