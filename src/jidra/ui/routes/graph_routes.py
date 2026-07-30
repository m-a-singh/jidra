from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

router = APIRouter()

# Mirrors render_interactive_html color/shape logic exactly
_STEREO_COLORS: dict[str, str] = {
    "controller": "#2196f3",
    "service": "#34d399",
    "repository": "#f59e0b",
    "component": "#a78bfa",
    "configuration": "#67e8f9",
    "entity": "#fb7185",
    "http_handler": "#2196f3",
    "flask_route": "#34d399",
    "fastapi_route": "#34d399",
    "django_handler": "#34d399",
    "unknown": "#4d6173",
}
_HTTP_COLORS: dict[str, str] = {
    "GET": "#34d399",
    "POST": "#f59e0b",
    "PUT": "#38bdf8",
    "DELETE": "#fb7185",
    "PATCH": "#a78bfa",
}


def _vis_color(n: dict) -> str:
    if n.get("is_endpoint") and n.get("http_method"):
        return _HTTP_COLORS.get((n["http_method"] or "").upper(), "#38bdf8")
    return _STEREO_COLORS.get((n.get("group") or "unknown").lower(), "#4d6173")


def _vis_shape(n: dict) -> str:
    if n.get("is_endpoint"):
        return "diamond"
    grp = (n.get("group") or "").lower()
    if grp in (
        "controller",
        "http_handler",
        "flask_route",
        "fastapi_route",
        "django_handler",
    ):
        return "diamond"
    if grp == "service":
        return "ellipse"
    if grp == "repository":
        return "database"
    return "box"


def _enrich(n: dict) -> dict:
    """Add vis-network display fields to a raw build_graph_data node."""
    c = _vis_color(n)
    fname = (n.get("file_path") or "").split("/")[-1]
    line = n.get("line", "")
    title_parts = [n.get("signature") or n["label"]]
    if fname:
        title_parts.append(f"{fname}:{line}")
    if n.get("route"):
        title_parts.append(f"{n.get('http_method', '')} {n['route']}")
    return {
        **n,
        "title": "\n".join(title_parts),
        "shape": _vis_shape(n),
        "color": {
            "background": c + "33",
            "border": c,
            "highlight": {"background": c + "55", "border": "#ffffff"},
            "hover": {"background": c + "44", "border": c},
        },
        "font": {"color": "#cdd9e5", "size": 13, "face": "JetBrains Mono, monospace"},
        "borderWidth": 2,
    }


def _get_engine(repo_path: str, output_path: str | None = None):
    from ...engine.engine import get_engine
    from ...graph.graph_store import resolve_graph_db_path
    from .util_routes import resolve_out_dir

    out_dir = resolve_out_dir(repo_path, output_path)
    db = resolve_graph_db_path(out_dir)
    return get_engine(str(db), variant="main")


@router.get("/nodes")
async def get_nodes(
    repo_path: str,
    output_path: str | None = Query(None),
    method: str | None = Query(None),
    depth: int = Query(2),
    package: str | None = Query(None),
    language: str | None = Query(None),
    limit: int = Query(-1),  # -1 = no limit (full graph)
) -> dict:
    from ...graph.graph_visualizer import build_graph_data

    try:
        engine = _get_engine(repo_path, output_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    data = build_graph_data(
        engine.graph,
        method_selector=method,
        depth=depth,
        package_filter=package,
    )

    nodes: list[dict] = data.get("nodes", [])
    edges: list[dict] = data.get("edges", [])

    if language:
        nodes = [n for n in nodes if n.get("language") == language]

    total = len(nodes)
    if limit > 0:
        nodes = nodes[:limit]
    nodes = [_enrich(n) for n in nodes]
    node_ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e.get("from") in node_ids and e.get("to") in node_ids]

    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": limit > 0 and total > limit,
    }


@router.get("/methods-in-file")
async def methods_in_file(
    file_path: str,
    repo_path: str = "",
    output_path: str | None = Query(None),
) -> list[dict]:
    """Return all methods in a file (for CodeLens batch resolution)."""
    import sqlite3

    from ...graph.graph_store import resolve_graph_db_path
    from .util_routes import resolve_out_dir

    if not repo_path and not output_path:
        raise HTTPException(status_code=400, detail="repo_path or output_path required")
    out_dir = resolve_out_dir(repo_path, output_path)
    db = resolve_graph_db_path(out_dir)
    if not db.exists():
        return []
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            """SELECT id, method_name, signature, class_full_name, start_line, end_line
               FROM methods WHERE file_path = ? AND variant = 'main' ORDER BY start_line""",
            (file_path,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "method_name": r[1],
            "signature": r[2],
            "class_full_name": r[3],
            "start_line": r[4],
            "end_line": r[5],
        }
        for r in rows
    ]


@router.get("/callers")
async def get_callers(
    method_id: str,
    depth: int = Query(2),
    repo_path: str = "",
    output_path: str | None = Query(None),
) -> dict:
    """Return methods that call method_id (upstream callers), up to depth hops."""
    import sqlite3

    from ...graph.graph_store import resolve_graph_db_path
    from .util_routes import resolve_out_dir

    if not repo_path and not output_path:
        raise HTTPException(status_code=400, detail="repo_path or output_path required")
    out_dir = resolve_out_dir(repo_path, output_path)
    db = resolve_graph_db_path(out_dir)
    if not db.exists():
        raise HTTPException(status_code=404, detail=f"graph.db not found at {db}")

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        visited: set[str] = set()
        frontier = {method_id}
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"SELECT DISTINCT caller_method_id FROM resolved_call_edges WHERE callee_method_id IN ({placeholders})",
                list(frontier),
            ).fetchall()
            next_frontier: set[str] = set()
            for r in rows:
                cid = r[0]
                if cid != method_id and cid not in visited:
                    visited.add(cid)
                    next_frontier.add(cid)
            frontier = next_frontier

        nodes = []
        if visited:
            placeholders = ",".join("?" * len(visited))
            rows = conn.execute(
                f"""SELECT id, method_name, class_full_name, file_path, start_line
                    FROM methods WHERE id IN ({placeholders}) AND variant = 'main'""",
                list(visited),
            ).fetchall()
            for r in rows:
                short_class = (r["class_full_name"] or "").split(".")[-1]
                nodes.append(
                    {
                        "id": r["id"],
                        "label": f"{short_class}.{r['method_name']}",
                        "file_path": r["file_path"],
                        "line": r["start_line"],
                    }
                )
    return {"nodes": nodes}


@router.get("/subgraph")
async def get_subgraph(
    method_id: str,
    depth: int = Query(2),
    repo_path: str = "",
    output_path: str | None = Query(None),
) -> dict:
    """Return nodes+edges for the call subgraph centred on method_id (both directions)."""
    import sqlite3

    from ...graph.graph_store import resolve_graph_db_path
    from .util_routes import resolve_out_dir

    if not repo_path and not output_path:
        raise HTTPException(status_code=400, detail="repo_path or output_path required")
    out_dir = resolve_out_dir(repo_path, output_path)
    db = resolve_graph_db_path(out_dir)
    if not db.exists():
        raise HTTPException(status_code=404, detail=f"graph.db not found at {db}")

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        visited: set[str] = {method_id}
        frontier = {method_id}
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            args = list(frontier)
            rows = conn.execute(
                f"""SELECT callee_method_id AS id FROM resolved_call_edges WHERE caller_method_id IN ({placeholders})
                    UNION SELECT caller_method_id AS id FROM resolved_call_edges WHERE callee_method_id IN ({placeholders})""",
                args + args,
            ).fetchall()
            next_frontier: set[str] = set()
            for r in rows:
                nid = r[0]
                if nid not in visited:
                    visited.add(nid)
                    next_frontier.add(nid)
            frontier = next_frontier

        method_ids = list(visited)
        placeholders = ",".join("?" * len(method_ids))
        method_rows = conn.execute(
            f"""SELECT id, method_name, class_full_name, file_path, start_line
                FROM methods WHERE id IN ({placeholders}) AND variant = 'main'""",
            method_ids,
        ).fetchall()
        nodes = []
        for r in method_rows:
            short_class = (r["class_full_name"] or "").split(".")[-1]
            nodes.append(
                {
                    "id": r["id"],
                    "label": f"{short_class}.{r['method_name']}",
                    "file_path": r["file_path"],
                    "line": r["start_line"],
                }
            )

        node_id_set = {n["id"] for n in nodes}
        placeholders2 = ",".join("?" * len(node_id_set))
        edge_rows = conn.execute(
            f"""SELECT caller_method_id AS "from", callee_method_id AS "to"
                FROM resolved_call_edges
                WHERE caller_method_id IN ({placeholders2}) AND callee_method_id IN ({placeholders2})""",
            list(node_id_set) + list(node_id_set),
        ).fetchall()
        edges = [{"from": r["from"], "to": r["to"]} for r in edge_rows]

    return {"nodes": nodes, "edges": edges}


@router.get("/node/{node_id:path}")
async def get_node(node_id: str, repo_path: str, output_path: str | None = None) -> dict:
    try:
        engine = _get_engine(repo_path, output_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    method_by_id = {m.id: m for m in engine.graph.methods}
    node = method_by_id.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    return vars(node) if hasattr(node, "__dict__") else {"id": node_id}


@router.get("/html")
async def graph_html(
    repo_path: str,
    output_path: str | None = Query(None),
    variant: str = Query("visualization"),
) -> HTMLResponse:
    from .util_routes import resolve_out_dir

    out_dir = resolve_out_dir(repo_path, output_path)
    name = "graph_visualization_raw.html" if variant == "raw" else "graph_visualization.html"
    html_path = out_dir / name
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"{name} not found — run the pipeline first")
    html = html_path.read_text(encoding="utf-8")
    # vis-network requires a concrete pixel height on its container.
    # In an iframe the flex chain may not resolve, so we force it.
    style_fix = (
        "<style>"
        "html,body{height:100%!important;overflow:hidden!important;}"
        ".body{flex:1;min-height:0;height:100%;}"
        "#graph{height:100%!important;min-height:0;}"
        "</style>"
    )
    # Inject fit() call inside the same script block where `const network` lives.
    # Can't access it from an external script due to block scoping.
    fit_call = "\nsetTimeout(function(){network.fit({animation:false});},200);\n"
    # Replace the LAST </script> before </body> — that's the one containing network
    last_script_close = html.rfind("</script>")
    html = html[:last_script_close] + fit_call + html[last_script_close:]
    html = html.replace("</head>", style_fix + "</head>")
    return HTMLResponse(html)


@router.get("/search")
async def search_nodes(
    repo_path: str,
    q: str = Query(..., min_length=1),
    output_path: str | None = Query(None),
    language: str | None = Query(None),
    limit: int = Query(50),
) -> dict:
    try:
        engine = _get_engine(repo_path, output_path)
        return engine.search(q, limit=limit, language=language)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/method-at")
async def method_at(
    file_path: str,
    line: int,
    repo_path: str = "",
    output_path: str | None = Query(None),
) -> dict | None:
    import sqlite3

    from ...graph.graph_store import resolve_graph_db_path
    from .util_routes import resolve_out_dir

    if not repo_path and not output_path:
        raise HTTPException(status_code=400, detail="repo_path or output_path required")

    out_dir = resolve_out_dir(repo_path, output_path)
    db = resolve_graph_db_path(out_dir)

    if not db.exists():
        raise HTTPException(status_code=404, detail=f"graph.db not found at {db}")

    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            """SELECT id, method_name, signature, class_full_name, start_line, end_line
               FROM methods WHERE file_path = ? AND start_line <= ? AND end_line >= ?
               AND variant = 'main' ORDER BY (end_line - start_line) ASC LIMIT 1""",
            (file_path, line, line),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "method_name": row[1],
        "signature": row[2],
        "class_full_name": row[3],
        "start_line": row[4],
        "end_line": row[5],
    }
