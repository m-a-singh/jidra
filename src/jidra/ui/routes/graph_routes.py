from __future__ import annotations

from pathlib import Path

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
    from ...cli import _repo_output_dir
    from ...engine.engine import get_engine
    from ...graph.graph_store import resolve_graph_db_path

    out_dir = Path(output_path) if output_path else _repo_output_dir(Path(repo_path))
    import sqlite3 as _sq

    db = resolve_graph_db_path(out_dir)
    # prefer validated if it has records, else fall back to main
    conn = _sq.connect(str(db))
    validated_count = conn.execute(
        "SELECT COUNT(*) FROM methods WHERE variant='validated'"
    ).fetchone()[0]
    conn.close()
    variant = "validated" if validated_count > 0 else "main"
    return get_engine(str(db), variant=variant)


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


@router.get("/node/{node_id:path}")
async def get_node(
    node_id: str, repo_path: str, output_path: str | None = None
) -> dict:
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
    from ...cli import _repo_output_dir

    out_dir = Path(output_path) if output_path else _repo_output_dir(Path(repo_path))
    name = (
        "graph_visualization_raw.html"
        if variant == "raw"
        else "graph_visualization.html"
    )
    html_path = out_dir / name
    if not html_path.exists():
        raise HTTPException(
            status_code=404, detail=f"{name} not found — run the pipeline first"
        )
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
