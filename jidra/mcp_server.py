from __future__ import annotations
import argparse
from pathlib import Path
from .engine import DEFAULT_MAIN_GRAPH, JidraEngine
from .flow_doc_agent import FlowDocAgent
from .cli import (
    _parse_stack_trace,
    _match_stack_frames_to_methods,
    _is_meaningful_signature,
    _is_error_doc_noise_call,
    _extract_focused_map_sections,
    _no_stack_frame_error_payload,
)


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
    engine = JidraEngine(resolved_graph)
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


def run_mcp_server(
    default_graph_path: str | None = None, codebase_path: str | None = None
) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - runtime dependency gate
        raise RuntimeError(
            "MCP support requires installing jidra[mcp] or pip install mcp"
        ) from exc

    default_path = default_graph_path or DEFAULT_MAIN_GRAPH
    mcp = FastMCP("JIDRA MCP")

    @mcp.tool()
    def jidra_get_method_context(
        method: str,
        graph_path: str | None = None,
        max_chars: int = 12000,
    ) -> dict:
        """Query a method or class from the local code graph. Returns the method source, call edges, callers, and class hierarchy."""
        resolved_graph = graph_path or default_path
        graph_dir = (
            Path(resolved_graph)
            if Path(resolved_graph).is_dir()
            else Path(resolved_graph).parent
        )
        engine = JidraEngine(resolved_graph)
        result = engine.get_method_context(method=method, max_chars=max_chars)
        return _maybe_add_stale_hint(result, graph_dir)

    @mcp.tool()
    def jidra_get_flow(
        method: str,
        graph_path: str | None = None,
        depth: int = 4,
        top_n: int = 4,
    ) -> dict:
        """Get ranked downstream call graph for a method from the local code graph."""
        resolved_graph = graph_path or default_path
        graph_dir = (
            Path(resolved_graph)
            if Path(resolved_graph).is_dir()
            else Path(resolved_graph).parent
        )
        engine = JidraEngine(resolved_graph)
        result = engine.get_flow(method=method, depth=depth, top_n=top_n)
        return _maybe_add_stale_hint(result, graph_dir)

    @mcp.tool()
    def jidra_get_agent_flow(
        method: str,
        graph_path: str | None = None,
        depth: int = 4,
        top_n: int = 4,
    ) -> dict:
        """Get downstream call graph for a method from the local code graph."""
        resolved_graph = graph_path or default_path
        graph_dir = (
            Path(resolved_graph)
            if Path(resolved_graph).is_dir()
            else Path(resolved_graph).parent
        )
        engine = JidraEngine(resolved_graph)
        result = engine.get_agent_flow(method=method, depth=depth, top_n=top_n)
        return _maybe_add_stale_hint(result, graph_dir)

    @mcp.tool()
    def jidra_get_method_source(
        method: str,
        graph_path: str | None = None,
    ) -> dict:
        """TRIGGER: any request to see the implementation of a specific method or function. Call this BEFORE opening a file.
        Returns the source of just that method — no need to find or read the whole file.
        If selector returns suggestions, pick the best match and retry immediately."""
        resolved_graph = graph_path or default_path
        graph_dir = (
            Path(resolved_graph)
            if Path(resolved_graph).is_dir()
            else Path(resolved_graph).parent
        )
        engine = JidraEngine(resolved_graph)
        result = engine.get_method_source(method=method)
        return _maybe_add_stale_hint(result, graph_dir)

    @mcp.tool()
    def jidra_get_call_chain(
        from_method: str,
        to_method: str,
        graph_path: str | None = None,
        max_depth: int = 6,
    ) -> dict:
        """Find the call chain between two methods in the local code graph."""
        resolved_graph = graph_path or default_path
        graph_dir = (
            Path(resolved_graph)
            if Path(resolved_graph).is_dir()
            else Path(resolved_graph).parent
        )
        engine = JidraEngine(resolved_graph)
        result = engine.get_call_chain(
            from_method=from_method, to_method=to_method, max_depth=max_depth
        )
        return _maybe_add_stale_hint(result, graph_dir)

    @mcp.tool()
    def jidra_analyze_stack_trace(
        stack_trace: str,
        graph_path: str | None = None,
        depth: int = 6,
        max_nodes: int = 80,
        include_utility: bool = False,
    ) -> dict:
        """Analyze a stack trace against the local code graph to find debug locations."""
        return analyze_stack_trace(
            stack_trace=stack_trace,
            graph_path=graph_path or default_path,
            depth=depth,
            max_nodes=max_nodes,
            include_utility=include_utility,
        )

    @mcp.tool()
    def jidra_check_staleness(
        graph_path: str | None = None,
        codebase: str | None = None,
    ) -> dict:
        """Check if the local code graph is stale compared to source files."""
        return check_staleness(graph_path=graph_path, codebase=codebase)

    @mcp.tool()
    def jidra_reindex(
        graph_path: str | None = None,
        codebase: str | None = None,
        changed_files: list[str] | None = None,
    ) -> dict:
        """Update the local code graph after file changes."""
        return jidra_reindex_impl(
            graph_path=graph_path, codebase=codebase, changed_files=changed_files
        )

    mcp.run(transport="stdio")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the JIDRA MCP server")
    parser.add_argument("--graph", default=None, help="Path to graph.jsonl")
    parser.add_argument(
        "--codebase", default=None, help="Path to codebase root (for reindex tool)"
    )
    args = parser.parse_args()
    run_mcp_server(default_graph_path=args.graph, codebase_path=args.codebase)


if __name__ == "__main__":
    main()
