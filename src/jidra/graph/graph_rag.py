"""
graph_rag.py — FTS seed + BFS graph walk retrieval. Zero API calls.

Flow:
  1. FTS5 BM25 seeds (reuse existing search_methods)
  2. BFS over resolved_call_edges (both directions) + inheritance_edges (N hops)
  3. Collect methods/files in subgraph
  4. Rank by hop distance, tiebreak on bm25 score

Returns same dict shape as engine.search() so compare_chat / eval_chat
can treat it as a drop-in third backend.
"""
from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from .graph_store import search_methods, connect


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def graph_rag_query(
    query: str,
    graph_path: str,
    *,
    seed_limit: int = 10,
    hops: int = 2,
    max_nodes: int = 150,
    variant: str = "main",
) -> dict:
    """FTS seeds → BFS subgraph → ranked result list.

    Args:
        query       : free-text query
        graph_path  : path to graph.db
        seed_limit  : how many FTS hits to use as BFS roots
        hops        : BFS depth (2 = callers + their callers)
        max_nodes   : cap on total methods collected before cutoff
        variant     : graph variant (usually "main")

    Returns dict with keys: query, seed_count, node_count, results
    Each result: method_id, method_name, signature, class_full_name,
                 file_path, language, hop, bm25_score
    """
    conn = connect(Path(graph_path))
    conn.row_factory = sqlite3.Row

    # --- Step 1: FTS seeds ---------------------------------------------------
    seed_rows = search_methods(conn, query, limit=seed_limit, variant=variant)
    if not seed_rows:
        return {"query": query, "seed_count": 0, "node_count": 0, "results": []}

    seed_ids: set[str] = {r["id"] for r in seed_rows}
    seed_score: dict[str, float] = {r["id"]: float(r.get("score", 0.0)) for r in seed_rows}

    # --- Step 2: BFS over call + inheritance edges ---------------------------
    # visited: method_id → (hop_distance, bm25_score)
    visited: dict[str, tuple[int, float]] = {
        mid: (0, seed_score[mid]) for mid in seed_ids
    }
    queue: deque[tuple[str, int]] = deque((mid, 0) for mid in seed_ids)

    # pre-load class → method_ids for inheritance expansion
    class_methods = _load_class_methods(conn, variant)

    while queue and len(visited) < max_nodes:
        method_id, depth = queue.popleft()
        if depth >= hops:
            continue

        neighbors = _call_neighbors(conn, method_id, variant)
        neighbors |= _inheritance_neighbors(conn, method_id, class_methods, variant)

        for nbr_id in neighbors:
            if nbr_id not in visited and len(visited) < max_nodes:
                visited[nbr_id] = (depth + 1, 0.0)
                queue.append((nbr_id, depth + 1))

    # --- Step 3: fetch method metadata for all visited nodes -----------------
    all_ids = list(visited.keys())
    method_meta = _fetch_methods(conn, all_ids, variant)

    # --- Step 4: rank by hop ASC, bm25 DESC (bm25 negative = lower = better) -
    results: list[dict] = []
    for row in method_meta:
        mid = row["id"]
        hop, bm25 = visited.get(mid, (hops + 1, 0.0))
        results.append({
            "method_id": mid,
            "method_name": row["method_name"],
            "signature": row["signature"],
            "class_full_name": row["class_full_name"],
            "file_path": row["file_path"],
            "language": row["language"],
            "hop": hop,
            "bm25_score": round(bm25, 4),
        })

    results.sort(key=lambda r: (r["hop"], r["bm25_score"]))

    return {
        "query": query,
        "seed_count": len(seed_ids),
        "node_count": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Graph traversal helpers
# ---------------------------------------------------------------------------

def _call_neighbors(conn: sqlite3.Connection, method_id: str, variant: str) -> set[str]:
    """Callers + callees of method_id via resolved_call_edges."""
    cur = conn.execute(
        "SELECT callee_method_id FROM resolved_call_edges WHERE caller_method_id = ? AND variant = ?",
        (method_id, variant),
    )
    neighbors = {row[0] for row in cur.fetchall()}
    cur = conn.execute(
        "SELECT caller_method_id FROM resolved_call_edges WHERE callee_method_id = ? AND variant = ?",
        (method_id, variant),
    )
    neighbors |= {row[0] for row in cur.fetchall()}
    return neighbors


def _load_class_methods(conn: sqlite3.Connection, variant: str) -> dict[str, list[str]]:
    """Map class_full_name → list of method IDs for inheritance expansion."""
    cur = conn.execute(
        "SELECT id, class_full_name FROM methods WHERE variant = ?", (variant,)
    )
    result: dict[str, list[str]] = {}
    for row in cur.fetchall():
        result.setdefault(row["class_full_name"], []).append(row["id"])
    return result


def _inheritance_neighbors(
    conn: sqlite3.Connection,
    method_id: str,
    class_methods: dict[str, list[str]],
    variant: str,
) -> set[str]:
    """Methods in classes that inherit from / are implemented by the method's class."""
    cur = conn.execute(
        "SELECT m.class_full_name FROM methods m WHERE m.id = ? AND m.variant = ?",
        (method_id, variant),
    )
    row = cur.fetchone()
    if not row:
        return set()
    class_name = row["class_full_name"] if isinstance(row, sqlite3.Row) else row[0]

    # classes that extend/implement this class
    cur = conn.execute(
        "SELECT source_class FROM inheritance_edges WHERE target_class = ? AND variant = ?",
        (class_name, variant),
    )
    related_classes = {r[0] for r in cur.fetchall()}

    # classes this class extends/implements
    cur = conn.execute(
        "SELECT target_class FROM inheritance_edges WHERE source_class = ? AND variant = ?",
        (class_name, variant),
    )
    related_classes |= {r[0] for r in cur.fetchall()}

    neighbors: set[str] = set()
    for cls in related_classes:
        neighbors |= set(class_methods.get(cls, []))
    return neighbors


def _fetch_methods(
    conn: sqlite3.Connection, method_ids: list[str], variant: str
) -> list[Any]:
    """Batch-fetch method rows for a list of IDs."""
    if not method_ids:
        return []
    placeholders = ",".join("?" * len(method_ids))
    cur = conn.execute(
        f"SELECT id, method_name, signature, class_full_name, file_path, language "
        f"FROM methods WHERE id IN ({placeholders}) AND variant = ?",
        method_ids + [variant],
    )
    return cur.fetchall()
