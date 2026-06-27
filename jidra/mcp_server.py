from __future__ import annotations
import argparse
from pathlib import Path
from .engine import DEFAULT_MAIN_GRAPH, get_engine
from .flow_doc_agent import FlowDocAgent
from .cli import (
    _parse_stack_trace,
    _match_stack_frames_to_methods,
    _is_meaningful_signature,
    _is_error_doc_noise_call,
    _extract_focused_map_sections,
    _no_stack_frame_error_payload,
    compute_graph_health,
)


def _log_session_call(
    codebase_path: str | None, tool_name: str, method_id: str | None = None
) -> None:
    """Best-effort session call log. Never raises — logging must not break tool responses."""
    try:
        import json
        from datetime import datetime, timezone

        root = Path(codebase_path) if codebase_path else Path.cwd()
        log_dir = root / ".jidra"
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "tool_name": tool_name,
            "method_id": method_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with (log_dir / "session_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _maybe_add_stale_hint(result: dict, graph_dir: Path) -> dict:
    """Add passive staleness hint if graph may be stale (O(1) check)."""
    try:
        from .reindexer import quick_stale_check

        if quick_stale_check(graph_dir):
            result["graph_may_be_stale"] = True
            result["staleness_hint"] = (
                "Source files changed since last index. Call jidra_check_staleness() or jidra_reindex()."
            )
    except Exception:
        pass
    return result


def check_staleness(
    graph_path: str | None = None,
    codebase: str | None = None,
) -> dict:
    """Check whether the graph is stale — call this at session start."""
    from .reindexer import check_staleness as check_staleness_impl

    resolved_graph = graph_path or DEFAULT_MAIN_GRAPH
    resolved_codebase = codebase or str(Path(resolved_graph).parent.parent)

    return check_staleness_impl(Path(resolved_codebase), Path(resolved_graph))


def jidra_reindex_impl(
    graph_path: str | None = None,
    codebase: str | None = None,
    changed_files: list[str] | None = None,
) -> dict:
    """Reindex the codebase incrementally."""
    from .reindexer import incremental_reindex

    resolved_graph = graph_path or DEFAULT_MAIN_GRAPH
    resolved_codebase = codebase or str(Path(resolved_graph).parent.parent)
    result = incremental_reindex(
        Path(resolved_codebase), Path(resolved_graph), hint_changed_files=changed_files
    )
    graph_dir = (
        Path(resolved_graph)
        if Path(resolved_graph).is_dir()
        else Path(resolved_graph).parent
    )
    return _maybe_add_stale_hint(result, graph_dir)


def graph_health(graph_path: str | None = None) -> dict:
    """Resolved/unresolved/external callsite breakdown for the local code graph."""
    resolved_graph = graph_path or DEFAULT_MAIN_GRAPH
    engine = get_engine(resolved_graph)
    return compute_graph_health(engine.graph)


def analyze_stack_trace(
    stack_trace: str,
    graph_path: str | None = None,
    depth: int = 6,
    max_nodes: int = 80,
    include_utility: bool = False,
) -> dict:
    resolved_graph = graph_path or DEFAULT_MAIN_GRAPH
    graph_dir = (
        Path(resolved_graph)
        if Path(resolved_graph).is_dir()
        else Path(resolved_graph).parent
    )
    engine = get_engine(resolved_graph)
    graph = engine.graph

    frames = _parse_stack_trace(stack_trace)
    matched_rows, anchor = _match_stack_frames_to_methods(graph, frames)
    if not frames:
        result = _no_stack_frame_error_payload(stack_trace)
        return _maybe_add_stale_hint(result, graph_dir)
    if anchor is None:
        result = {"error": "no_project_anchor_found", "stack_frames": matched_rows}
        return _maybe_add_stale_hint(result, graph_dir)

    anchor_method_id = (
        anchor["ambiguous_method_ids"][0]
        if anchor["match_status"] == "ambiguous"
        else anchor["matched_method_id"]
    )
    agent = FlowDocAgent(
        engine,
        flow_depth=depth,
        include_utility=include_utility,
        mind_map_mode=True,
        include_details=False,
        max_nodes=max_nodes,
    )
    flow_result = agent.build(anchor_method_id)
    if flow_result.get("error"):
        result = {
            "error": flow_result["error"],
            "stack_frames": matched_rows,
            "primary_anchor": anchor,
        }
        return _maybe_add_stale_hint(result, graph_dir)

    method_by_id = {m.id: m for m in graph.methods}
    caller_row = (
        matched_rows[anchor["frame_index"] - 1] if anchor["frame_index"] > 0 else None
    )
    unresolved_near = [
        c
        for c in (flow_result.get("mind_map", {}) or {}).get("unresolved_calls", [])
        if not _is_error_doc_noise_call(c)
    ]
    unresolved_near = unresolved_near[:10]

    neighbors = []
    for e in graph.resolved_call_edges:
        if e.caller_method_id == anchor_method_id:
            m = method_by_id.get(e.callee_method_id)
            if m:
                neighbors.append(m.signature)
        if e.callee_method_id == anchor_method_id:
            m = method_by_id.get(e.caller_method_id)
            if m:
                neighbors.append(m.signature)
    neighbors = sorted(set(neighbors))

    meaningful_downstream = []
    for src, dst in (flow_result.get("mind_map", {}) or {}).get("edges", []):
        if src != anchor_method_id:
            continue
        dm = method_by_id.get(dst)
        if dm and _is_meaningful_signature(dm.signature):
            meaningful_downstream.append(dm.signature)
    upstream_mode = len(meaningful_downstream) == 0

    suggested = []
    anchor_sig = None
    if anchor.get("matched_method_id"):
        mm = method_by_id.get(anchor["matched_method_id"])
        if mm:
            anchor_sig = mm.signature
    suggested.append(
        {
            "priority": 1,
            "location": anchor_sig or anchor_method_id,
            "reason": "failing project frame",
        }
    )
    if caller_row:
        suggested.append(
            {
                "priority": 2,
                "location": f"{caller_row['class_full_name']}#{caller_row['method_name']}:{caller_row['line']}",
                "reason": "caller frame above failure",
            }
        )
    for c in unresolved_near:
        receiver = str(c.get("receiver") or "").strip()
        call_name = str(c.get("call") or "").strip()
        if receiver and call_name:
            location = f"{receiver}.{call_name}"
        elif call_name:
            location = call_name
        else:
            continue
        suggested.append(
            {
                "priority": 3,
                "location": location,
                "reason": "unresolved external call near failure",
            }
        )
    if upstream_mode:
        for sig in neighbors[:10]:
            suggested.append(
                {
                    "priority": 4,
                    "location": sig,
                    "reason": "graph caller of failing method",
                }
            )
    else:
        for sig in neighbors[:10]:
            suggested.append(
                {
                    "priority": 4,
                    "location": sig,
                    "reason": "callee graph neighbor of failing method",
                }
            )

    focused_map_markdown = _extract_focused_map_sections(
        agent.render_markdown(flow_result)
    )
    match_summary = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for row in matched_rows:
        st = row.get("match_status", "unmatched")
        match_summary[st] = match_summary.get(st, 0) + 1

    result = {
        "stack_frames": matched_rows,
        "primary_anchor": anchor,
        "match_summary": match_summary,
        "suggested_debug_locations": suggested,
        "unresolved_calls_near_anchor": unresolved_near,
        "focused_flow_map": {
            "mind_map": flow_result.get("mind_map", {}),
            "upstream_mode": upstream_mode,
        },
        "focused_flow_map_markdown": focused_map_markdown,
        "limits": [
            "static_analysis_only",
            "runtime_dispatch_not_guaranteed",
            "external_libraries_may_be_unmatched",
            "graph_quality_affects_output",
        ],
    }
    return _maybe_add_stale_hint(result, graph_dir)


def _resolve_graph_dir(resolved_graph: str) -> Path:
    p = Path(resolved_graph)
    return p if p.is_dir() else p.parent


def _dispatch_get_docs(
    graph_path: str,
    query: str,
    linked_class: str | None,
    limit: int,
) -> dict:
    from . import doc_store, graph_store as gs

    conn = gs.connect(Path(graph_path))
    doc_store.migrate(conn)
    chunks: list[dict] = []
    # 1. If a specific class is given, prioritise linked chunks
    if linked_class:
        chunks = doc_store.query_by_class(conn, linked_class, limit=limit)
    # 2. FTS search — covers both the class-linked and free-text cases
    if not chunks or len(chunks) < limit:
        fts_chunks = doc_store.query_fts(conn, query, limit=limit)
        seen = {c["id"] for c in chunks}
        chunks += [c for c in fts_chunks if c["id"] not in seen]
    chunks = chunks[:limit]
    if not chunks:
        return {
            "docs_found": False,
            "message": "No relevant documentation found for this query.",
        }
    return {
        "docs_found": True,
        "count": len(chunks),
        "chunks": [
            {
                "source": c["source_path"],
                "source_type": c["source_type"],
                "title": c["title"],
                "content": c["content"],
                "linked_classes": [x for x in c["linked_classes"].split(",") if x],
            }
            for c in chunks
        ],
    }


def _dispatch_index_docs(graph_path: str, path: str) -> dict:
    from . import doc_store, graph_store as gs
    from .doc_indexer import index_document, index_directory, extract_graph_names

    conn = gs.connect(Path(graph_path))
    doc_store.migrate(conn)
    # Load graph for heuristic linking
    graph = gs.load_graph(conn, variant="main")
    class_names, method_names = extract_graph_names(graph)
    if path.startswith(("http://", "https://")):
        return {
            "error": "URL indexing is disabled. Download the document locally first to keep all processing offline."
        }
    p = Path(path)
    if p.is_dir():
        results = index_directory(
            conn, path, graph_class_names=class_names, graph_method_names=method_names
        )
        total_chunks = sum(v for v in results.values() if v >= 0)
        failed = [k for k, v in results.items() if v < 0]
        return {
            "indexed": len(results),
            "chunks": total_chunks,
            "failed": failed,
            "sources": list(results.keys()),
        }
    n = index_document(conn, path, class_names, method_names)
    return {"indexed": 1, "chunks": n, "source": path}


def _enrich_with_docs(
    conn, result: dict, class_name: str | None, query: str | None
) -> dict:
    """Add a docs_available flag to any tool result. No-op if doc tables don't
    exist yet, so this is always safe to call even on a graph that's never had
    `jidra_index_docs` run against it."""
    try:
        from . import doc_store

        doc_store.migrate(conn)
        available = False
        if class_name:
            available = doc_store.docs_available_for_class(conn, class_name)
        if not available and query:
            available = doc_store.docs_available_for_query(conn, query)
        if available:
            result["docs_available"] = True
            result["docs_hint"] = (
                "Call jidra_get_docs to retrieve relevant specification/design context."
            )
    except Exception:
        pass
    return result


def dispatch_tool(
    name: str,
    params: dict | None,
    *,
    default_graph_path: str | None,
    codebase_path: str | None,
) -> dict:
    """Single source of truth for every JIDRA MCP tool's behavior.

    Used directly by ``direct`` mode and by the daemon (Phase 5); the proxy
    forwards tool calls here over a socket. Returns a plain dict and raises
    KeyError only for an unknown tool name.
    """
    p = params or {}
    graph_path = p.get("graph_path") or default_graph_path or DEFAULT_MAIN_GRAPH
    graph_dir = _resolve_graph_dir(graph_path)

    def engine():
        return get_engine(graph_path)

    if name == "jidra_get_method_context":
        _log_session_call(codebase_path, name, p.get("method"))
        result = _maybe_add_stale_hint(
            engine().get_method_context(
                method=p["method"], max_chars=p.get("max_chars")
            ),
            graph_dir,
        )
        from . import graph_store as _gs

        class_name = (
            result.get("class_name") or (result.get("suggestions") or [None])[0]
        )
        return _enrich_with_docs(
            _gs.connect(Path(graph_path)), result, class_name, p.get("method")
        )
    if name == "jidra_get_flow":
        _log_session_call(codebase_path, name, p.get("method"))
        return _maybe_add_stale_hint(
            engine().get_flow(
                method=p["method"],
                depth=p.get("depth"),
                top_n=p.get("top_n"),
                detail=p.get("detail", "summary"),
            ),
            graph_dir,
        )
    if name == "jidra_get_agent_flow":
        _log_session_call(codebase_path, name, p.get("method"))
        return _maybe_add_stale_hint(
            engine().get_agent_flow(
                method=p["method"], depth=p.get("depth"), top_n=p.get("top_n")
            ),
            graph_dir,
        )
    if name == "jidra_get_method_source":
        _log_session_call(codebase_path, name, p.get("method"))
        result = _maybe_add_stale_hint(
            engine().get_method_source(method=p["method"]), graph_dir
        )
        from . import graph_store as _gs

        class_name = (
            result.get("class_name") or (result.get("suggestions") or [None])[0]
        )
        return _enrich_with_docs(
            _gs.connect(Path(graph_path)), result, class_name, p.get("method")
        )
    if name == "jidra_find_callers":
        _log_session_call(codebase_path, name, p.get("method"))
        return _maybe_add_stale_hint(
            engine().find_callers(method=p["method"], depth=p.get("depth", 1)),
            graph_dir,
        )
    if name == "jidra_get_call_chain":
        _log_session_call(codebase_path, name, p.get("from_method"))
        return _maybe_add_stale_hint(
            engine().get_call_chain(
                from_method=p["from_method"],
                to_method=p["to_method"],
                max_depth=p.get("max_depth", 6),
            ),
            graph_dir,
        )
    if name == "jidra_search":
        _log_session_call(codebase_path, name, p.get("query"))
        result = _maybe_add_stale_hint(
            engine().search(
                query=p["query"],
                limit=p.get("limit", 20),
                language=p.get("language"),
            ),
            graph_dir,
        )
        from . import graph_store as _gs

        return _enrich_with_docs(
            _gs.connect(Path(graph_path)), result, None, p.get("query")
        )
    if name == "jidra_explore":
        _log_session_call(codebase_path, name, p.get("query"))
        result = _maybe_add_stale_hint(
            engine().explore(query=p["query"], top_n=p.get("top_n", 10)), graph_dir
        )
        from . import graph_store as _gs

        return _enrich_with_docs(
            _gs.connect(Path(graph_path)), result, None, p.get("query")
        )
    if name == "jidra_get_file_dependents":
        _log_session_call(codebase_path, name, p.get("file_path"))
        return _maybe_add_stale_hint(
            engine().get_file_dependents(p["file_path"]), graph_dir
        )
    if name == "jidra_get_file_dependencies":
        _log_session_call(codebase_path, name, p.get("file_path"))
        return _maybe_add_stale_hint(
            engine().get_file_dependencies(p["file_path"]), graph_dir
        )
    if name == "jidra_get_endpoints":
        _log_session_call(codebase_path, name)
        return _maybe_add_stale_hint(
            engine().get_endpoints(framework=p.get("framework")), graph_dir
        )
    if name == "jidra_get_components":
        _log_session_call(codebase_path, name)
        return _maybe_add_stale_hint(
            engine().get_components(kind=p.get("kind")), graph_dir
        )
    if name == "jidra_get_framework_summary":
        _log_session_call(codebase_path, name)
        return _maybe_add_stale_hint(engine().get_framework_summary(), graph_dir)
    if name == "jidra_get_operation_graph":
        _log_session_call(codebase_path, name, p.get("operation"))
        return _maybe_add_stale_hint(
            engine().get_operation_graph(p["operation"]), graph_dir
        )
    if name == "jidra_list_operations":
        _log_session_call(codebase_path, name)
        return _maybe_add_stale_hint(
            engine().list_operations(service=p.get("service")), graph_dir
        )
    if name == "jidra_analyze_stack_trace":
        _log_session_call(codebase_path, name)
        return analyze_stack_trace(
            stack_trace=p["stack_trace"],
            graph_path=graph_path,
            depth=p.get("depth", 6),
            max_nodes=p.get("max_nodes", 80),
            include_utility=p.get("include_utility", False),
        )
    if name == "jidra_graph_health":
        _log_session_call(codebase_path, name)
        return graph_health(graph_path=graph_path)
    if name == "jidra_check_staleness":
        _log_session_call(codebase_path, name)
        return check_staleness(
            graph_path=p.get("graph_path"), codebase=p.get("codebase")
        )
    if name == "jidra_reindex":
        _log_session_call(codebase_path, name)
        return jidra_reindex_impl(
            graph_path=p.get("graph_path"),
            codebase=p.get("codebase"),
            changed_files=p.get("changed_files"),
        )
    if name == "jidra_get_docs":
        _log_session_call(codebase_path, name, p.get("query"))
        return _dispatch_get_docs(
            graph_path=graph_path,
            query=p.get("query", ""),
            linked_class=p.get("linked_class"),
            limit=p.get("limit", 5),
        )
    if name == "jidra_index_docs":
        _log_session_call(codebase_path, name, p.get("path"))
        return _dispatch_index_docs(
            graph_path=graph_path,
            path=p["path"],
        )
    if name == "jidra_get_implementations":
        _log_session_call(str(graph_dir), name, p.get("interface"))
        return _maybe_add_stale_hint(
            engine().get_implementations(
                p["interface"],
                transitive=p.get("transitive", False),
                limit=p.get("limit", 30),
                detail=p.get("detail", "summary"),
            ),
            graph_dir,
        )
    if name == "jidra_get_class_members":
        _log_session_call(str(graph_dir), name, p.get("class_selector"))
        return _maybe_add_stale_hint(
            engine().get_class_members(p["class_selector"]), graph_dir
        )
    if name == "jidra_find_callers":
        _log_session_call(str(graph_dir), name, p.get("method"))
        return _maybe_add_stale_hint(
            engine().find_callers(p["method"], depth=p.get("depth", 1)), graph_dir
        )
    if name == "jidra_query_by_annotation":
        _log_session_call(str(graph_dir), name, p.get("annotation"))
        return _maybe_add_stale_hint(
            engine().query_by_annotation(
                p["annotation"],
                kind=p.get("kind", "any"),
                limit=p.get("limit", 30),
                detail=p.get("detail", "summary"),
            ),
            graph_dir,
        )
    if name == "jidra_field_access":
        _log_session_call(str(graph_dir), name, p.get("field") or p.get("method"))
        return _maybe_add_stale_hint(
            engine().field_access(field=p.get("field"), method=p.get("method")),
            graph_dir,
        )
    raise KeyError(f"unknown tool: {name}")


# Primary tier: lean, high-confidence grounding tools. Always visible.
PRIMARY_TOOLS = [
    "jidra_explore",
    "jidra_get_method_source",
    "jidra_find_callers",
    "jidra_get_implementations",
    "jidra_analyze_stack_trace",
]

# Full tool set exposed by the server.
TOOL_NAMES = [
    "jidra_get_method_context",
    "jidra_get_flow",
    "jidra_get_agent_flow",
    "jidra_get_method_source",
    "jidra_find_callers",
    "jidra_get_call_chain",
    "jidra_search",
    "jidra_explore",
    "jidra_get_file_dependents",
    "jidra_get_file_dependencies",
    "jidra_get_endpoints",
    "jidra_get_components",
    "jidra_get_framework_summary",
    "jidra_get_operation_graph",
    "jidra_list_operations",
    "jidra_analyze_stack_trace",
    "jidra_graph_health",
    "jidra_check_staleness",
    "jidra_reindex",
    "jidra_get_docs",
    "jidra_index_docs",
]


def visible_tool_names() -> list[str]:
    """Return visible tools: primary tier by default, full set if JIDRA_FULL_TOOLS=1."""
    import os

    if os.environ.get("JIDRA_FULL_TOOLS") == "1":
        return TOOL_NAMES
    return PRIMARY_TOOLS


def build_mcp(
    default_graph_path: str | None = None,
    codebase_path: str | None = None,
    invoke=None,
):
    """Build the FastMCP server. `invoke(name, params) -> dict` does the work:
    in ``direct`` mode it dispatches locally; in ``proxy`` mode it forwards to
    the daemon over a socket. The tool surface (names, signatures, docstrings)
    is identical either way."""
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - runtime dependency gate
        raise RuntimeError(
            "MCP support requires installing jidra[mcp] or pip install mcp"
        ) from exc

    default_path = default_graph_path or DEFAULT_MAIN_GRAPH
    if invoke is None:

        def invoke(name, p):
            return dispatch_tool(
                name,
                p,
                default_graph_path=default_path,
                codebase_path=codebase_path,
            )

    mcp = FastMCP("JIDRA MCP")

    @mcp.tool()
    def jidra_get_method_context(
        method: str,
        graph_path: str | None = None,
        max_chars: int | None = None,
    ) -> dict:
        """Query a method or class from the local code graph. Returns the method source, call edges, callers, and class hierarchy.
        Output size auto-scales to the graph size (budget_tier in the response); pass max_chars to override."""
        return invoke(
            "jidra_get_method_context",
            {"method": method, "graph_path": graph_path, "max_chars": max_chars},
        )

    @mcp.tool()
    def jidra_get_flow(
        method: str,
        graph_path: str | None = None,
        depth: int | None = None,
        top_n: int | None = None,
        detail: str = "summary",
    ) -> dict:
        """Get ranked downstream call graph for a method from the local code graph.
        depth/top_n default to the graph's budget tier when omitted.
        detail: 'summary' (default, fast) or 'full' (includes all nodes/edges/flows)."""
        return invoke(
            "jidra_get_flow",
            {
                "method": method,
                "graph_path": graph_path,
                "depth": depth,
                "top_n": top_n,
                "detail": detail,
            },
        )

    @mcp.tool()
    def jidra_get_agent_flow(
        method: str,
        graph_path: str | None = None,
        depth: int | None = None,
        top_n: int | None = None,
    ) -> dict:
        """Get downstream call graph for a method from the local code graph.
        depth/top_n default to the graph's budget tier when omitted."""
        return invoke(
            "jidra_get_agent_flow",
            {
                "method": method,
                "graph_path": graph_path,
                "depth": depth,
                "top_n": top_n,
            },
        )

    @mcp.tool()
    def jidra_get_method_source(
        method: str,
        graph_path: str | None = None,
    ) -> dict:
        """TRIGGER: any request to see the implementation of a specific method or function. Call this BEFORE opening a file.
        Returns the source of just that method — no need to find or read the whole file.
        If selector returns suggestions, pick the best match and retry immediately."""
        return invoke(
            "jidra_get_method_source", {"method": method, "graph_path": graph_path}
        )

    @mcp.tool()
    def jidra_find_callers(
        method: str,
        depth: int = 1,
        graph_path: str | None = None,
    ) -> dict:
        """Find all methods that call the given method (reverse call lookup).
        Use this to answer "who calls X?" questions. `depth` controls how many
        levels up the call graph to walk (default 1 = direct callers only).
        If selector returns suggestions, pick the best match and retry immediately."""
        return invoke(
            "jidra_find_callers",
            {"method": method, "depth": depth, "graph_path": graph_path},
        )

    @mcp.tool()
    def jidra_get_call_chain(
        from_method: str,
        to_method: str,
        graph_path: str | None = None,
        max_depth: int = 6,
    ) -> dict:
        """Find the call chain between two methods in the local code graph."""
        return invoke(
            "jidra_get_call_chain",
            {
                "from_method": from_method,
                "to_method": to_method,
                "graph_path": graph_path,
                "max_depth": max_depth,
            },
        )

    @mcp.tool()
    def jidra_search(
        query: str,
        graph_path: str | None = None,
        limit: int = 20,
        language: str | None = None,
    ) -> dict:
        """Keyword/full-text search over method names, signatures, and source in
        the local code graph. Use this when you DON'T know the exact method name —
        e.g. jidra_search("token validation"). Returns ranked method hits; follow
        up with jidra_get_method_context on a hit. Optionally filter by language
        (java, python, typescript, ...)."""
        return invoke(
            "jidra_search",
            {
                "query": query,
                "graph_path": graph_path,
                "limit": limit,
                "language": language,
            },
        )

    @mcp.tool()
    def jidra_explore(
        query: str,
        graph_path: str | None = None,
        top_n: int = 10,
    ) -> dict:
        """Natural-language exploration of the codebase. Tokenizes the query
        (handles CamelCase/snake_case), searches the graph, ranks results by
        relevance, and attaches class/endpoint context. Use this as the FIRST
        step when starting from a vague description rather than a known symbol."""
        return invoke(
            "jidra_explore", {"query": query, "graph_path": graph_path, "top_n": top_n}
        )

    @mcp.tool()
    def jidra_get_file_dependents(
        file_path: str,
        graph_path: str | None = None,
    ) -> dict:
        """Blast radius: which files would break if you change `file_path`.
        Returns caller files ranked by number of call sites (most-coupled first)
        — the impact-analysis question to ask BEFORE refactoring a file."""
        return invoke(
            "jidra_get_file_dependents",
            {"file_path": file_path, "graph_path": graph_path},
        )

    @mcp.tool()
    def jidra_get_file_dependencies(
        file_path: str,
        graph_path: str | None = None,
    ) -> dict:
        """Forward dependencies: which files `file_path` depends on, via both
        resolved call edges and class inheritance (extends/implements)."""
        return invoke(
            "jidra_get_file_dependencies",
            {"file_path": file_path, "graph_path": graph_path},
        )

    @mcp.tool()
    def jidra_get_endpoints(
        framework: str | None = None,
        graph_path: str | None = None,
    ) -> dict:
        """List all HTTP endpoints in the codebase (Spring, NestJS, Flask,
        FastAPI, Django) with method, route, and framework role. Optionally
        filter by framework (e.g. "flask", "fastapi", "spring", "typescript")."""
        return invoke(
            "jidra_get_endpoints", {"framework": framework, "graph_path": graph_path}
        )

    @mcp.tool()
    def jidra_get_components(
        kind: str | None = None,
        graph_path: str | None = None,
    ) -> dict:
        """List UI/framework components and hooks (React, Vue, Angular, NestJS).
        Optionally filter by kind substring (e.g. "react", "angular", "hook")."""
        return invoke("jidra_get_components", {"kind": kind, "graph_path": graph_path})

    @mcp.tool()
    def jidra_get_framework_summary(
        graph_path: str | None = None,
    ) -> dict:
        """Discovery overview: counts of framework roles, class stereotypes, and
        languages in the graph. A good first call to understand a new codebase."""
        return invoke("jidra_get_framework_summary", {"graph_path": graph_path})

    @mcp.tool()
    def jidra_get_operation_graph(
        operation: str,
        graph_path: str | None = None,
    ) -> dict:
        """Smithy operation lookup: the operation's contract (service, HTTP
        binding, input/output shape ids, errors) plus the real handler class
        that implements it, if one was bridged via a known codegen toolchain
        (smithy-java, smithy4s). `operation` matches by simple name or full
        shape id (namespace#Name)."""
        return invoke(
            "jidra_get_operation_graph",
            {"operation": operation, "graph_path": graph_path},
        )

    @mcp.tool()
    def jidra_list_operations(
        service: str | None = None,
        graph_path: str | None = None,
    ) -> dict:
        """List all Smithy operations in the graph, optionally filtered to one
        service shape name. Use this to discover operation names before
        calling jidra_get_operation_graph."""
        return invoke(
            "jidra_list_operations", {"service": service, "graph_path": graph_path}
        )

    @mcp.tool()
    def jidra_analyze_stack_trace(
        stack_trace: str,
        graph_path: str | None = None,
        depth: int = 6,
        max_nodes: int = 80,
        include_utility: bool = False,
    ) -> dict:
        """Analyze a stack trace against the local code graph to find debug locations."""
        return invoke(
            "jidra_analyze_stack_trace",
            {
                "stack_trace": stack_trace,
                "graph_path": graph_path,
                "depth": depth,
                "max_nodes": max_nodes,
                "include_utility": include_utility,
            },
        )

    @mcp.tool()
    def jidra_graph_health(
        graph_path: str | None = None,
    ) -> dict:
        """Resolved/unresolved/external callsite breakdown for the local code graph."""
        return invoke("jidra_graph_health", {"graph_path": graph_path})

    @mcp.tool()
    def jidra_check_staleness(
        graph_path: str | None = None,
        codebase: str | None = None,
    ) -> dict:
        """Check if the local code graph is stale compared to source files."""
        return invoke(
            "jidra_check_staleness", {"graph_path": graph_path, "codebase": codebase}
        )

    @mcp.tool()
    def jidra_get_implementations(
        interface: str,
        transitive: bool = False,
        graph_path: str | None = None,
        limit: int = 30,
        detail: str = "summary",
    ) -> dict:
        """List ALL concrete implementations of interface/abstract class in ONE call.
        Use instead of repeated searches asking 'implements X', 'how many impls', 'what classes handle interface'.
        limit: max implementations to return (default 30); detail: 'summary' or 'full'.
        """
        return invoke(
            "jidra_get_implementations",
            {
                "interface": interface,
                "transitive": transitive,
                "graph_path": graph_path,
                "limit": limit,
                "detail": detail,
            },
        )

    @mcp.tool()
    def jidra_get_class_members(
        class_selector: str,
        graph_path: str | None = None,
    ) -> dict:
        """List field and method members of a class in one call.
        Use before calling get_method_source repeatedly on same class.
        """
        return invoke(
            "jidra_get_class_members",
            {"class_selector": class_selector, "graph_path": graph_path},
        )

    @mcp.tool()
    def jidra_query_by_annotation(
        annotation: str,
        kind: str = "any",
        graph_path: str | None = None,
        limit: int = 30,
        detail: str = "summary",
    ) -> dict:
        """Find classes/methods by annotation. kind: 'class', 'method', or 'any'.
        Example queries: query_by_annotation("RestController"), query_by_annotation("async_task", kind="method").
        limit: max results per kind to return (default 30); detail: 'summary' or 'full'.
        """
        return invoke(
            "jidra_query_by_annotation",
            {
                "annotation": annotation,
                "kind": kind,
                "graph_path": graph_path,
                "limit": limit,
                "detail": detail,
            },
        )

    @mcp.tool()
    def jidra_field_access(
        field: str | None = None,
        method: str | None = None,
        graph_path: str | None = None,
    ) -> dict:
        """Find field access patterns. Query by field name or method signature.
        Field format: "ClassName#fieldName" or just "fieldName" to search all classes.
        Example: field_access(field="Cache#config"), field_access(method="processData(String)")."""
        return invoke(
            "jidra_field_access",
            {"field": field, "method": method, "graph_path": graph_path},
        )

    @mcp.tool()
    def jidra_reindex(
        graph_path: str | None = None,
        codebase: str | None = None,
        changed_files: list[str] | None = None,
    ) -> dict:
        """Update the local code graph after file changes."""
        return invoke(
            "jidra_reindex",
            {
                "graph_path": graph_path,
                "codebase": codebase,
                "changed_files": changed_files,
            },
        )

    @mcp.tool()
    def jidra_get_docs(
        query: str,
        linked_class: str | None = None,
        limit: int = 5,
        graph_path: str | None = None,
    ) -> dict:
        """Search indexed spec/design documents for context relevant to a query or class.

        Call this when:
        - A code query returns `docs_available: true`
        - A suggestion list is returned and you want to disambiguate using spec docs
        - The user asks a question about design intent, spec compliance, or business rules
        - No code match is found — the concept may only exist in docs so far

        Returns ranked doc chunks with source, title, and content.
        """
        return invoke(
            "jidra_get_docs",
            {
                "query": query,
                "linked_class": linked_class,
                "limit": limit,
                "graph_path": graph_path,
            },
        )

    @mcp.tool()
    def jidra_index_docs(
        path: str,
        graph_path: str | None = None,
    ) -> dict:
        """Index a document or directory of documents (MD, PDF, DOCX, PPTX) into the doc store.

        Chunks content, extracts class/method name mentions for heuristic linking to the code graph,
        and stores in the graph DB for retrieval via jidra_get_docs.

        path: a local file path or directory path. URLs are not accepted — all
        processing is offline; download the document locally first.
        """
        return invoke(
            "jidra_index_docs",
            {"path": path, "graph_path": graph_path},
        )

    # Honor the tool-surface trim in direct mode too: FastMCP advertises every
    # @mcp.tool() decorator, so prune the ones hidden by the primary-tier gate
    # (set JIDRA_FULL_TOOLS=1 to expose all). Keeps direct mode in sync with the
    # daemon's tools/list (see visible_tool_names).
    visible = set(visible_tool_names())
    tool_mgr = mcp._tool_manager
    for tool_name in list(tool_mgr._tools):
        if tool_name not in visible:
            tool_mgr.remove_tool(tool_name)

    return mcp


def run_mcp_server(
    default_graph_path: str | None = None,
    codebase_path: str | None = None,
    invoke=None,
) -> None:
    """Run the JIDRA MCP server over stdio (``direct`` mode, or ``proxy`` when an
    `invoke` forwarder is supplied)."""
    mcp = build_mcp(default_graph_path, codebase_path, invoke)
    mcp.run(transport="stdio")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the JIDRA MCP server")
    parser.add_argument("--graph", default=None, help="Path to graph.db")
    parser.add_argument(
        "--codebase", default=None, help="Path to codebase root (for reindex tool)"
    )
    parser.add_argument(
        "--mode",
        choices=["direct", "proxy", "daemon"],
        default="direct",
        help=(
            "direct: load the graph in-process (default, fallback, Windows). "
            "proxy: thin stdio<->socket bridge to a shared daemon. "
            "daemon: run the detached shared-graph server."
        ),
    )
    args = parser.parse_args()

    if args.mode == "daemon":
        from .daemon import JidraDaemon

        JidraDaemon(graph_path=args.graph, codebase_path=args.codebase).start()
        return

    if args.mode == "proxy":
        from .proxy import JidraProxy

        proxy = JidraProxy(graph_path=args.graph, codebase_path=args.codebase)
        # Unix sockets aren't available everywhere (e.g. Windows) — degrade to
        # in-process direct mode rather than failing the MCP handshake.
        if not proxy.available():
            run_mcp_server(default_graph_path=args.graph, codebase_path=args.codebase)
            return
        run_mcp_server(
            default_graph_path=args.graph,
            codebase_path=args.codebase,
            invoke=proxy.call,
        )
        return

    run_mcp_server(default_graph_path=args.graph, codebase_path=args.codebase)


if __name__ == "__main__":
    main()
