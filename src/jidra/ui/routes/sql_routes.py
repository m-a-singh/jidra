from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_MAX_ROWS = 2000


class QueryRequest(BaseModel):
    repo_path: str
    sql: str
    db: str = "graph"  # "graph" | "telemetry"


def _db_path(repo_path: str, db: str) -> Path:
    from ...cli import _repo_output_dir

    out_dir = _repo_output_dir(Path(repo_path))
    if db == "telemetry":
        from ...llm.telemetry import _TELEMETRY_DB
        return _TELEMETRY_DB
    return out_dir / "graph.db"


@router.post("/query")
async def run_query(req: QueryRequest) -> dict:
    db_file = _db_path(req.repo_path, req.db)
    if not db_file.exists():
        raise HTTPException(status_code=404, detail=f"DB not found: {db_file}")

    try:
        conn = sqlite3.connect(str(db_file), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(req.sql)
        rows = cur.fetchmany(_MAX_ROWS)
        columns = [d[0] for d in cur.description] if cur.description else []
        conn.close()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "truncated": len(rows) == _MAX_ROWS,
    }


@router.get("/schema")
async def get_schema(repo_path: str, db: str = "graph") -> list[dict]:
    db_file = _db_path(repo_path, db)
    if not db_file.exists():
        raise HTTPException(status_code=404, detail=f"DB not found: {db_file}")

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    result = []
    for (table,) in tables:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        result.append({
            "table": table,
            "columns": [{"name": c[1], "type": c[2]} for c in cols],
        })
    conn.close()
    return result
