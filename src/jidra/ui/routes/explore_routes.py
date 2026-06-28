from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _get_graph(repo_path: str, output_path: str | None, graph_type: str = "main"):
    from ...cli import _repo_output_dir
    from ...graph import graph_store

    out_dir = Path(output_path) if output_path else _repo_output_dir(Path(repo_path))
    db_path = graph_store.resolve_graph_db_path(out_dir)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Repository not indexed. Run the pipeline first.")
    conn = graph_store.connect(db_path)
    graph = graph_store.load_graph(conn, variant=graph_type)
    return graph, db_path


def _resolve_method(graph, selector: str):
    from ...cli import _method_ambiguous_error, _method_not_found_error
    from ...utils.selector import _resolve_method_selector

    candidates = _resolve_method_selector(graph, selector)
    if not candidates:
        raise HTTPException(status_code=404, detail=_method_not_found_error(selector))
    if len(candidates) > 1:
        raise HTTPException(status_code=409, detail=_method_ambiguous_error(selector, candidates))
    return candidates[0]


class TraceRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    method: str
    max_depth: int = 5
    business_only: bool = True


@router.post("/trace")
async def trace(req: TraceRequest) -> dict:
    from ...cli import _apply_business_only_trace
    from ...llm.trace_engine import trace_method

    graph, _ = _get_graph(req.repo_path, req.output_path)
    method = _resolve_method(graph, req.method)
    result = trace_method(graph, method.id, max_depth=req.max_depth)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    if req.business_only:
        removed = _apply_business_only_trace(result)
        result["filters"] = {"business_only": True, "removed_count": removed}
    return result


class ContextRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    method: str
    max_chars: int = 12000
    business_only: bool = True


@router.post("/context")
async def context(req: ContextRequest) -> dict:
    from ...cli import _apply_business_only_context
    from ...utils.context_builder import build_method_context

    graph, _ = _get_graph(req.repo_path, req.output_path)
    method = _resolve_method(graph, req.method)
    result = build_method_context(graph, method.id, max_chars=req.max_chars)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    if req.business_only:
        removed = _apply_business_only_context(result)
        result["filters"] = {"business_only": True, "removed_count": removed}
    return result


class FlowRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    method: str
    depth: int = 4
    business_only: bool = True


@router.post("/flow")
async def flow(req: FlowRequest) -> dict:
    from ...cli import _load_cli_config, is_business_entry
    from ...flow.flow_stitcher import stitch_flow

    graph, _ = _get_graph(req.repo_path, req.output_path)
    method = _resolve_method(graph, req.method)
    config = _load_cli_config()
    flow_config = config.get("flow", {}) if isinstance(config, dict) else {}
    result = stitch_flow(
        graph,
        method,
        max_depth=req.depth,
        business_only=req.business_only,
        is_business_entry=is_business_entry,
        flow_config=flow_config,
    )
    return result


class RouteRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    route: str
    max_depth: int = 5


@router.post("/trace-route")
async def trace_route_endpoint(req: RouteRequest) -> dict:
    from ...llm.trace_engine import trace_route

    graph, _ = _get_graph(req.repo_path, req.output_path)
    result = trace_route(graph, req.route, max_depth=req.max_depth)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class FlowDocRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    method: str
    depth: int = 4
    top_n: int = 8
    max_subflows: int = 8
    include_utility: bool = False
    mind_map: bool = False


@router.post("/flow-doc")
async def flow_doc(req: FlowDocRequest) -> dict:
    from ...engine.engine import JidraEngine
    from ...flow.flow_doc_agent import FlowDocAgent

    _, db_path = _get_graph(req.repo_path, req.output_path)
    engine = JidraEngine(str(db_path), variant="main")
    agent = FlowDocAgent(
        engine,
        max_subflows=req.max_subflows,
        flow_depth=req.depth,
        top_n=req.top_n,
        include_utility=req.include_utility,
        mind_map_mode=req.mind_map,
        include_details=not req.mind_map,
    )
    result = agent.build(req.method)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"markdown": agent.render_markdown(result)}


class ErrorDocRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    stack_trace: str
    depth: int = 6
    max_nodes: int = 200
    include_utility: bool = False


@router.post("/error-doc")
async def error_doc(req: ErrorDocRequest) -> dict:
    from ...cli import _is_error_doc_noise_call, _is_meaningful_signature, _match_stack_frames_to_methods, _parse_stack_trace
    from ...engine.engine import JidraEngine
    from ...flow.flow_doc_agent import FlowDocAgent

    graph, db_path = _get_graph(req.repo_path, req.output_path)
    frames = _parse_stack_trace(req.stack_trace)
    if not frames:
        raise HTTPException(status_code=400, detail="No Java stack frames parsed from stack trace input.")
    matched_rows, anchor = _match_stack_frames_to_methods(graph, frames)
    if anchor is None:
        raise HTTPException(status_code=400, detail="No project frame matched/ambiguous for primary failure anchor.")

    method_selector = (
        anchor["ambiguous_method_ids"][0]
        if anchor["match_status"] == "ambiguous"
        else anchor["matched_method_id"]
    )

    engine = JidraEngine(str(db_path), variant="main")
    agent = FlowDocAgent(
        engine,
        flow_depth=req.depth,
        include_utility=req.include_utility,
        mind_map_mode=True,
        include_details=False,
        max_nodes=req.max_nodes,
    )
    flow_result = agent.build(method_selector)
    if flow_result.get("error"):
        raise HTTPException(status_code=400, detail=flow_result["error"])

    method_by_id = {m.id: m for m in graph.methods}
    failing_row = anchor
    caller_row = matched_rows[anchor["frame_index"] - 1] if anchor["frame_index"] > 0 else None

    neighbors = []
    if failing_row.get("matched_method_id"):
        mid = failing_row["matched_method_id"]
        for e in graph.resolved_call_edges:
            if e.caller_method_id == mid:
                tgt = method_by_id.get(e.callee_method_id)
                if tgt:
                    neighbors.append(tgt.signature)
            if e.callee_method_id == mid:
                src = method_by_id.get(e.caller_method_id)
                if src:
                    neighbors.append(src.signature)
    neighbors = sorted(set(neighbors))[:10]

    unresolved_near_all = (flow_result.get("mind_map", {}) or {}).get("unresolved_calls", [])
    unresolved_near = [c for c in unresolved_near_all if not _is_error_doc_noise_call(c)][:10]

    anchor_id = failing_row.get("matched_method_id")
    meaningful_downstream = []
    for src, dst in (flow_result.get("mind_map", {}) or {}).get("edges", []):
        if src != anchor_id:
            continue
        dm = method_by_id.get(dst)
        if dm and _is_meaningful_signature(dm.signature):
            meaningful_downstream.append(dm.signature)
    upstream_mode = len(meaningful_downstream) == 0

    caller_signatures = []
    if anchor_id:
        for e in graph.resolved_call_edges:
            if e.callee_method_id == anchor_id:
                sm = method_by_id.get(e.caller_method_id)
                if sm:
                    caller_signatures.append(sm.signature)
    caller_signatures = sorted(set(caller_signatures))[:10]

    lines = [
        "# Error Investigation",
        "",
        f"- anchor_frame_index: {failing_row['frame_index']}",
        f"- anchor_match_status: `{failing_row['match_status']}`",
        "",
        "## Stack Frames",
        "| frame index | class | method | file | line | matched_method_id | match_status |",
        "|---:|---|---|---|---:|---|---|",
    ]
    for r in matched_rows:
        lines.append(
            f"| {r['frame_index']} | `{r['class_full_name']}` | `{r['method_name']}` | `{r['file_name']}` | {r['line']} | `{r.get('matched_method_id', '')}` | `{r['match_status']}` |"
        )
        if r["match_status"] == "ambiguous":
            lines.append(
                f"| {r['frame_index']} | `ambiguous_candidates` |  |  |  | `{', '.join(r.get('ambiguous_method_ids', []))}` | `ambiguous` |"
            )
    lines.append("")
    lines.append("## Suggested Debug Locations")
    lines.append("| priority | location | reason |")
    lines.append("|---:|---|---|")
    failing_location = failing_row.get("matched_method_id") or ",".join(failing_row.get("ambiguous_method_ids", []))
    if failing_row.get("matched_method_id"):
        m = method_by_id.get(failing_row["matched_method_id"])
        if m:
            failing_location = m.signature
    lines.append(f"| 1 | `{failing_location}` | failing project frame |")
    if caller_row:
        caller_loc = f"{caller_row['class_full_name']}#{caller_row['method_name']}:{caller_row['line']}"
        lines.append(f"| 2 | `{caller_loc}` | caller frame above failure |")
    for c in unresolved_near:
        receiver = str(c.get("receiver") or "").strip()
        call_name = str(c.get("call") or "").strip()
        location = f"{receiver}.{call_name}" if receiver and call_name else call_name
        if not location:
            continue
        lines.append(f"| 3 | `{location}` | unresolved external call near failure |")
    for sig in caller_signatures:
        lines.append(f"| 4 | `{sig}` | graph caller of failing method |")
    if not upstream_mode:
        for sig in neighbors[:10]:
            lines.append(f"| 4 | `{sig}` | callee graph neighbor of failing method |")

    return {"markdown": "\n".join(lines) + "\n", "anchor_method": method_selector, "frames": len(matched_rows)}
