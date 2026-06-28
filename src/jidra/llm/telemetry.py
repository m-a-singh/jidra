from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_TELEMETRY_DIR = Path(__file__).resolve().parents[3] / "output" / "telemetry"
_TELEMETRY_DB = _TELEMETRY_DIR / "telemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_index_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    source_path     TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    chunks          INTEGER NOT NULL DEFAULT 0,
    linked_classes  INTEGER NOT NULL DEFAULT 0,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    elapsed_ms      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok',
    error           TEXT
);
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    repo        TEXT NOT NULL,
    languages   TEXT NOT NULL,
    classes     INTEGER NOT NULL,
    methods     INTEGER NOT NULL,
    lines       INTEGER NOT NULL,
    elapsed_ms  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reindex_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  INTEGER NOT NULL,
    repo                TEXT NOT NULL,
    changed_file        TEXT NOT NULL,
    language            TEXT,
    change_type         TEXT NOT NULL,
    classes_added       INTEGER NOT NULL DEFAULT 0,
    classes_modified    INTEGER NOT NULL DEFAULT 0,
    classes_deleted     INTEGER NOT NULL DEFAULT 0,
    methods_added       INTEGER NOT NULL DEFAULT 0,
    methods_modified    INTEGER NOT NULL DEFAULT 0,
    methods_deleted     INTEGER NOT NULL DEFAULT 0,
    lines_added         INTEGER NOT NULL DEFAULT 0,
    lines_deleted       INTEGER NOT NULL DEFAULT 0,
    elapsed_ms          INTEGER NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_TELEMETRY_DB))
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _estimate_lines(graph) -> int:
    total = 0
    for m in graph.methods:
        start = getattr(m, "start_line", None) or 0
        end = getattr(m, "end_line", None) or 0
        if end >= start > 0:
            total += end - start + 1
    return total


def _file_language(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return {
        ".java": "java",
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".scala": "scala",
        ".go": "go",
    }.get(ext, "unknown")


def record_index_event(
    repo: str,
    languages: list[str],
    graph,
    elapsed_ms: int,
) -> None:
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO index_events (ts, repo, languages, classes, methods, lines, elapsed_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                int(time.time() * 1000),
                repo,
                ",".join(sorted(languages)),
                len(graph.classes),
                len(graph.methods),
                _estimate_lines(graph),
                elapsed_ms,
            ),
        )
        conn.commit()
        conn.close()
        refresh_html()
    except Exception:
        pass  # telemetry must never crash the main flow


def record_reindex_event(
    repo: str,
    changed_file: str,
    change_type: str,
    summary: dict,
    elapsed_ms: int,
) -> None:
    try:
        lang = _file_language(changed_file)
        added_methods = summary.get("added_methods", 0)
        removed_methods = summary.get("removed_methods", 0)

        # Derive class counts from summary if available, else estimate from methods
        classes_added = summary.get("classes_added", 0)
        classes_modified = summary.get("classes_modified", 0)
        classes_deleted = summary.get("classes_deleted", 0)

        # Line estimates from method line deltas if provided
        lines_added = summary.get("lines_added", 0)
        lines_deleted = summary.get("lines_deleted", 0)

        conn = _connect()
        conn.execute(
            """INSERT INTO reindex_events (
                ts, repo, changed_file, language, change_type,
                classes_added, classes_modified, classes_deleted,
                methods_added, methods_modified, methods_deleted,
                lines_added, lines_deleted, elapsed_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(time.time() * 1000),
                repo,
                changed_file,
                lang,
                change_type,
                classes_added,
                classes_modified,
                classes_deleted,
                added_methods,
                0,  # methods_modified — not tracked by reindexer yet
                removed_methods,
                lines_added,
                lines_deleted,
                elapsed_ms,
            ),
        )
        conn.commit()
        conn.close()
        refresh_html()
    except Exception:
        pass


def fetch_index_history(repo: str | None = None, limit: int = 100) -> list[dict]:
    try:
        conn = _connect()
        if repo:
            rows = conn.execute(
                "SELECT ts, repo, languages, classes, methods, lines, elapsed_ms "
                "FROM index_events WHERE repo = ? ORDER BY ts DESC LIMIT ?",
                (repo, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, repo, languages, classes, methods, lines, elapsed_ms "
                "FROM index_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [
            dict(
                zip(
                    [
                        "ts",
                        "repo",
                        "languages",
                        "classes",
                        "methods",
                        "lines",
                        "elapsed_ms",
                    ],
                    r,
                )
            )
            for r in rows
        ]
    except Exception:
        return []


def fetch_reindex_history(repo: str | None = None, limit: int = 200) -> list[dict]:
    try:
        conn = _connect()
        cols = [
            "ts",
            "repo",
            "changed_file",
            "language",
            "change_type",
            "classes_added",
            "classes_modified",
            "classes_deleted",
            "methods_added",
            "methods_modified",
            "methods_deleted",
            "lines_added",
            "lines_deleted",
            "elapsed_ms",
        ]
        if repo:
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM reindex_events "
                "WHERE repo = ? ORDER BY ts DESC LIMIT ?",
                (repo, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM reindex_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def record_doc_index_event(
    source_path: str,
    source_type: str,
    chunks: int,
    linked_classes: int,
    file_size_bytes: int,
    elapsed_ms: int,
    status: str = "ok",
    error: str | None = None,
) -> None:
    try:
        conn = _connect()
        conn.execute(
            """INSERT INTO doc_index_events
               (ts, source_path, source_type, chunks, linked_classes, file_size_bytes, elapsed_ms, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(time.time() * 1000),
                source_path,
                source_type,
                chunks,
                linked_classes,
                file_size_bytes,
                elapsed_ms,
                status,
                error,
            ),
        )
        conn.commit()
        conn.close()
        refresh_html()
    except Exception:
        pass


def fetch_doc_index_history(limit: int = 200) -> list[dict]:
    try:
        conn = _connect()
        cols = [
            "ts",
            "source_path",
            "source_type",
            "chunks",
            "linked_classes",
            "file_size_bytes",
            "elapsed_ms",
            "status",
            "error",
        ]
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM doc_index_events ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def refresh_html() -> None:
    """Regenerate telemetry.html next to telemetry.db. Called after every record."""
    try:
        index_rows = fetch_index_history(limit=200)
        reindex_rows = fetch_reindex_history(limit=500)
        doc_rows = fetch_doc_index_history(limit=200)
        from jidra.cli import _render_history_html

        html = _render_history_html(index_rows, reindex_rows, doc_rows)
        out = _TELEMETRY_DIR / "telemetry.html"
        out.write_text(html, encoding="utf-8")
    except Exception:
        pass


def list_repos() -> list[str]:
    try:
        conn = _connect()
        index_repos = {
            r[0]
            for r in conn.execute("SELECT DISTINCT repo FROM index_events").fetchall()
        }
        reindex_repos = {
            r[0]
            for r in conn.execute("SELECT DISTINCT repo FROM reindex_events").fetchall()
        }
        conn.close()
        return sorted(index_repos | reindex_repos)
    except Exception:
        return []
