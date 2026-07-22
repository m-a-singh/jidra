from __future__ import annotations

from collections import deque

from ..utils.selector import _resolve_method_selector


OBS_PATTERNS = (
    "log.",
    "logger.",
    "markers.",
    "metricsclient.",
    "statsdclient.",
    "counter.",
    "metrics.",
    "recordexecutiontime",
    "logsetnames",
)
UTILITY_NAMES = {
    "orelse",
    "map",
    "stream",
    "collect",
    "get",
    "ispresent",
    "filter",
    "flatmap",
    "emptylist",
    "emptyset",
    "emptymap",
    "currenttimemillis",
    "getclass",
    "getsimplename",
    "and",
}
UTILITY_TYPES = (
    "optional",
    "collections",
    "list",
    "map",
    "set",
    "stringutils",
    "collectionutils",
    "stream",
)


def _is_observability(call) -> bool:
    text = f"{call.receiver or ''}.{call.callee_name}".lower().strip(".")
    if any(p in text for p in OBS_PATTERNS):
        return True
    return ("log" in call.callee_name.lower()) or ("metric" in call.callee_name.lower())


def _is_utility(call) -> bool:
    n = (call.callee_name or "").lower()
    if n in UTILITY_NAMES:
        return True
    rt = f"{call.receiver_type_normalized or ''} {call.receiver or ''}".lower()
    if any(t in rt for t in UTILITY_TYPES):
        return True
    return False


def _looks_business(call) -> bool:
    t = f"{call.receiver_type_normalized or ''} {call.receiver or ''} {call.callee_name}".lower()
    return any(
        k in t
        for k in (
            "controller",
            "service",
            "component",
            "repository",
            "processor",
            "client",
            "container",
            "ranking",
        )
    )


def _classify(call) -> str:
    if _is_observability(call):
        return "observability"
    if _is_utility(call):
        return "utility"
    status = call.resolution_status or ""
    if status.startswith("resolved"):
        return "business_internal" if _looks_business(call) else "probable_internal"
    if status == "ambiguous_type" and call.resolved_candidates:
        return "probable_internal"
    if status == "external_library":
        return "external_library"
    if status.startswith("unresolved"):
        return "unresolved_business" if _looks_business(call) else "unknown"
    return "framework"


def _kind(call) -> str:
    status = call.resolution_status or ""
    if status.startswith("resolved"):
        return "internal"
    if status == "external_library":
        return "external"
    return "unresolved"


def _priority(call) -> int:
    k = _kind(call)
    text = f"{call.receiver_type_normalized or ''} {call.receiver or ''}".lower()
    if k == "internal":
        if any(
            t in text
            for t in ("service", "repository", "controller", "component", "processor")
        ):
            return 0
        return 1
    if k == "unresolved":
        if any(
            t in text
            for t in ("service", "repository", "controller", "component", "processor")
        ):
            return 2
        return 3
    return 4


def build_flow(
    graph,
    selector_or_route: str,
    *,
    max_depth: int = 5,
    include_observability: bool = False,
    mode: str = "compact",
) -> dict:
    roots = _resolve_method_selector(graph, selector_or_route)
    if not roots and selector_or_route.startswith("/"):
        roots = [
            m
            for m in graph.methods
            if m.is_endpoint
            and (m.full_route == selector_or_route or m.route == selector_or_route)
        ]
    if not roots:
        return {"error": f"no_flow_root:{selector_or_route}"}

    root = roots[0]
    method_by_id = {m.id: m for m in graph.methods}
    calls_by_caller: dict[str, list] = {}
    for c in graph.callsites:
        calls_by_caller.setdefault(c.caller_method_id, []).append(c)

    q = deque([(root.id, 0)])
    seen_nodes = {root.id}
    flow = [
        {
            "depth": 0,
            "id": root.id,
            "signature": root.signature,
            "kind": "internal",
            "resolution": "root",
            "source_lines": [],
        }
    ]
    external_calls = []
    unresolved_calls = []
    observability_calls = []
    utility_calls = []
    dedupe_index = {}
    dedup_count = 0

    while q:
        mid, depth = q.popleft()
        if depth >= max_depth:
            continue
        calls = sorted(
            calls_by_caller.get(mid, []),
            key=lambda c: (_priority(c), c.line, c.column, c.id),
        )
        for call in calls:
            category = _classify(call)
            if category == "observability":
                observability_calls.append(
                    {
                        "from_id": mid,
                        "call": call.callee_name,
                        "receiver": call.receiver,
                        "source_line": call.line,
                    }
                )
                if not include_observability:
                    continue
            if category in {"utility", "collection_operation"}:
                utility_calls.append(
                    {
                        "from_id": mid,
                        "call": call.callee_name,
                        "receiver": call.receiver,
                        "source_line": call.line,
                    }
                )
                if mode != "debug":
                    continue
            if call.resolved_candidates:
                target_id = call.resolved_candidates[0]
                target = method_by_id.get(target_id)
                resolution = call.resolution_status
                cand_id = None
                cand_sig = None
                confidence = 1.0
                if (
                    resolution == "ambiguous_type"
                    and len(call.resolved_candidates) == 1
                    and target
                ):
                    resolution = "resolved_probable"
                    cand_id = target.id
                    cand_sig = target.signature
                    confidence = 0.75
                include_in_main = (
                    (
                        mode == "compact"
                        and category in {"business_internal", "probable_internal"}
                    )
                    or (
                        mode == "full"
                        and category
                        in {
                            "business_internal",
                            "probable_internal",
                            "unresolved_business",
                        }
                    )
                    or (mode == "debug")
                    or (include_observability and category == "observability")
                )
                if not include_in_main:
                    if category == "external_library":
                        external_calls.append(
                            {
                                "from_id": mid,
                                "call": call.callee_name,
                                "receiver": call.receiver,
                                "resolution": call.resolution_status,
                                "source_line": call.line,
                            }
                        )
                    continue
                edge_key = (mid, target_id, call.callee_name)
                if edge_key in dedupe_index:
                    flow[dedupe_index[edge_key]]["source_lines"].append(call.line)
                    dedup_count += 1
                else:
                    dedupe_index[edge_key] = len(flow)
                    flow.append(
                        {
                            "depth": depth + 1,
                            "id": target_id,
                            "signature": target.signature if target else None,
                            "call": call.callee_name,
                            "kind": "internal",
                            "category": category,
                            "resolution": resolution,
                            "from_id": mid,
                            "source_lines": [call.line],
                            "candidate_target_id": cand_id,
                            "candidate_signature": cand_sig,
                            "confidence": confidence,
                        }
                    )
                if target_id not in seen_nodes:
                    seen_nodes.add(target_id)
                    q.append((target_id, depth + 1))
            else:
                kind = _kind(call)
                item = {
                    "from_id": mid,
                    "call": call.callee_name,
                    "receiver": call.receiver,
                    "receiver_type": call.receiver_type_normalized,
                    "resolution": call.resolution_status,
                    "kind": kind,
                    "source_line": call.line,
                }
                if kind == "external":
                    external_calls.append(item)
                else:
                    unresolved_calls.append(item)
                if (
                    mode in {"full", "debug"} and category == "unresolved_business"
                ) or (include_observability and category == "observability"):
                    flow.append(
                        {
                            "depth": depth + 1,
                            "id": None,
                            "signature": None,
                            "call": call.callee_name,
                            "kind": kind,
                            "category": category,
                            "resolution": call.resolution_status,
                            "from_id": mid,
                            "source_lines": [call.line],
                        }
                    )

    return {
        "root": {"id": root.id, "signature": root.signature},
        "flow": flow,
        "external_calls": external_calls,
        "unresolved_calls": unresolved_calls,
        "observability_calls": observability_calls,
        "utility_calls": utility_calls,
        "stats": {
            "flow_steps": len(flow),
            "external_calls": len(external_calls),
            "unresolved_calls": len(unresolved_calls),
            "observability_calls": len(observability_calls),
            "utility_calls": len(utility_calls),
            "deduplicated_calls": dedup_count,
        },
    }


def render_flow_text(flow_result: dict) -> str:
    if flow_result.get("error"):
        return flow_result["error"]
    lines = [f"Flow: {flow_result['root']['signature']}", ""]
    root_id = flow_result["root"]["id"]
    lines.append(f"[0] {flow_result['root']['signature']}")
    lines.append(f"    id: {root_id}")
    for step in flow_result.get("flow", [])[1:]:
        call = step.get("call", "")
        if step.get("id"):
            lines.append("")
            lines.append(f"  -> {step.get('signature')}")
            lines.append(f"     id: {step.get('id')}")
            lines.append(f"     resolution: {step.get('resolution')}")
            if step.get("source_lines"):
                lines.append(
                    f"     source_lines: {sorted(set(step.get('source_lines', [])))}"
                )
        else:
            lines.append("")
            lines.append(f"  -> {call}")
            lines.append("     external/unresolved")
    return "\n".join(lines)


def trace_method(graph, selector: str, max_depth: int = 5) -> dict:
    return build_flow(graph, selector, max_depth=max_depth, include_observability=False)


def trace_route(graph, route: str, max_depth: int = 5) -> dict:
    return build_flow(graph, route, max_depth=max_depth, include_observability=False)
