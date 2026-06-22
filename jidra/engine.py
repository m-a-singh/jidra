from __future__ import annotations

from collections import deque
from pathlib import Path

from .context_builder import build_method_context
from .flow_stitcher import stitch_flow
from .graph_io import load_graph_jsonl
from .selector import (
    _fuzzy_suggestions,
    _method_ambiguous_error,
    _resolve_method_selector,
)

# Default graph path used when no explicit graph is provided.
# Keep this relative so the project is portable and doesn't leak a developer machine path.
DEFAULT_MAIN_GRAPH = str(Path(__file__).resolve().parent / "output" / "graph.jsonl")


def _summarize_uncertain_edges(edges: list[dict], limit: int = 8) -> dict:
    grouped: dict[tuple[str, str], int] = {}
    total = 0
    for edge in edges:
        call = str(edge.get("call") or "unknown")
        reason = str(edge.get("reason") or "unknown")
        count = int(edge.get("count", 1) or 1)
        grouped[(call, reason)] = grouped.get((call, reason), 0) + count
        total += count

    ranked = sorted(grouped.items(), key=lambda item: item[1], reverse=True)
    top_calls = [
        {"call": call, "reason": reason, "count": count}
        for (call, reason), count in ranked[: max(1, limit)]
    ]
    omitted = max(0, len(ranked) - len(top_calls))
    return {
        "total": total,
        "top_calls": top_calls,
        "omitted": omitted,
    }


def _summarize_stopped_paths(paths: list[dict]) -> dict:
    by_reason: dict[str, int] = {}
    for path in paths:
        reason = str(path.get("reason") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "total": len(paths),
        "by_reason": by_reason,
    }


class JidraEngine:
    def __init__(self, graph_path: str):
        self.graph_path = str(Path(graph_path).resolve())
        self.graph = load_graph_jsonl(Path(self.graph_path))

    def _resolve_single_method(self, selector: str) -> dict:
        candidates = _resolve_method_selector(self.graph, selector)
        if not candidates:
            suggestions = _fuzzy_suggestions(self.graph, selector)
            return {
                "error": f"No exact match for '{selector}' in the graph.",
                "action": "Pick the best match from suggestions and retry with that selector.",
                "suggestions": suggestions,
            }
        if len(candidates) > 1:
            return {"error": _method_ambiguous_error(selector, candidates)}
        return {"method": candidates[0]}

    def get_method_context(self, method: str, max_chars: int = 12000) -> dict:
        resolved = self._resolve_single_method(method)
        if "error" in resolved:
            # Auto-retry with top suggestion if available and high-confidence
            suggestions = resolved.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                top_selector = suggestions[0]["selector"]
                resolved = self._resolve_single_method(top_selector)
                if "error" in resolved:
                    return resolved
            else:
                return resolved
        selected = resolved["method"]
        if not hasattr(selected, "id"):
            return {"error": "invalid_method_selector_result"}
        return build_method_context(self.graph, selected.id, max_chars=max_chars)

    def get_flow(self, method: str, depth: int = 4, top_n: int = 4) -> dict:
        _ = top_n
        resolved = self._resolve_single_method(method)
        if "error" in resolved:
            suggestions = resolved.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                top_selector = suggestions[0]["selector"]
                resolved = self._resolve_single_method(top_selector)
                if "error" in resolved:
                    return resolved
            else:
                return resolved
        selected = resolved["method"]
        if not hasattr(selected, "id"):
            return {"error": "invalid_method_selector_result"}
        return stitch_flow(self.graph, selected, max_depth=depth)

    def get_agent_flow(self, method: str, depth: int = 4, top_n: int = 4) -> dict:
        flow = self.get_flow(method, depth=depth, top_n=top_n)
        if flow.get("error"):
            return flow

        agent_view = flow.get("agent_view", {}) if isinstance(flow, dict) else {}
        all_top_nodes = list(agent_view.get("top_nodes", []))
        non_utility = [n for n in all_top_nodes if n.get("tier") != "utility"]
        wanted = max(1, top_n)
        if len(non_utility) >= wanted:
            top_nodes = non_utility[:wanted]
        else:
            utility = [n for n in all_top_nodes if n.get("tier") == "utility"]
            top_nodes = (non_utility + utility)[:wanted]

        uncertain_edges = agent_view.get(
            "uncertain_edges", flow.get("uncertain_edges", [])
        )
        stopped_paths = agent_view.get("stopped_paths", flow.get("stopped_paths", []))
        selected_ids = {
            str(node.get("method_id") or "")
            for node in top_nodes
            if node.get("method_id")
        }
        top_edges: list[dict] = []
        for edge in flow.get("edges", []):
            from_id = edge.get("from") or edge.get("from_method_id")
            to_id = edge.get("to") or edge.get("to_method_id")
            if from_id not in selected_ids or to_id not in selected_ids:
                continue
            top_edges.append(
                {
                    "from": from_id,
                    "to": to_id,
                    "call": edge.get("call")
                    or edge.get("callee")
                    or edge.get("callee_name"),
                    "lines": edge.get("lines") or edge.get("line_numbers") or [],
                    "resolution": edge.get("resolution")
                    or edge.get("resolution_status"),
                }
            )
        summary = flow.get("summary", {}) if isinstance(flow, dict) else {}
        return {
            "entry": agent_view.get("entry", flow.get("entry", {})),
            "top_nodes": top_nodes,
            "top_edges": top_edges,
            "important_unresolved_calls": agent_view.get(
                "important_unresolved_calls", flow.get("important_unresolved_calls", [])
            ),
            "uncertain_edge_summary": _summarize_uncertain_edges(uncertain_edges),
            "stopped_path_summary": _summarize_stopped_paths(stopped_paths),
            "summary": summary,
            "notes": [
                "This is a compact agent view.",
                "Use jidra_get_flow for the full graph.",
                "Use jidra_get_method_source to fetch source for a selected method.",
            ],
        }

    def get_method_source(self, method: str) -> dict:
        resolved = self._resolve_single_method(method)
        if "error" in resolved:
            suggestions = resolved.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                top_selector = suggestions[0]["selector"]
                resolved = self._resolve_single_method(top_selector)
                if "error" in resolved:
                    return resolved
            else:
                return resolved
        selected = resolved["method"]
        if not hasattr(selected, "id"):
            return {"error": "invalid_method_selector_result"}
        return {
            "method_id": selected.id,
            "signature": selected.signature,
            "file_path": selected.file_path,
            "line_start": getattr(selected, "start_line", None),
            "line_end": getattr(selected, "end_line", None),
            "source": selected.source or "",
        }

    def get_call_chain(
        self, from_method: str, to_method: str, max_depth: int = 6
    ) -> dict:
        resolved_from = self._resolve_single_method(from_method)
        if "error" in resolved_from:
            suggestions = resolved_from.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                top_selector = suggestions[0]["selector"]
                resolved_from = self._resolve_single_method(top_selector)
                if "error" in resolved_from:
                    return resolved_from
            else:
                return resolved_from
        resolved_to = self._resolve_single_method(to_method)
        if "error" in resolved_to:
            suggestions = resolved_to.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                top_selector = suggestions[0]["selector"]
                resolved_to = self._resolve_single_method(top_selector)
                if "error" in resolved_to:
                    return resolved_to
            else:
                return resolved_to

        source = resolved_from["method"]
        target = resolved_to["method"]

        if not hasattr(source, "id") or not hasattr(target, "id"):
            return {"error": "invalid_method_selector_result"}
        method_by_id = {m.id: m for m in self.graph.methods}
        callsite_by_id = {c.id: c for c in self.graph.callsites}

        adjacency: dict[str, list[str]] = {}
        edge_lookup: dict[tuple[str, str], list[object]] = {}
        for edge in self.graph.resolved_call_edges:
            from_id = (
                getattr(edge, "from_method_id", None)
                or getattr(edge, "caller_method_id", None)
                or getattr(edge, "source_method_id", None)
                or getattr(edge, "from_id", None)
            )
            to_id = (
                getattr(edge, "to_method_id", None)
                or getattr(edge, "callee_method_id", None)
                or getattr(edge, "target_method_id", None)
                or getattr(edge, "to_id", None)
            )
            if not from_id or not to_id:
                continue
            adjacency.setdefault(from_id, []).append(to_id)
            edge_lookup.setdefault((from_id, to_id), []).append(edge)

        queue = deque([(source.id, 0)])
        visited = {source.id}
        parent: dict[str, str] = {}
        found = source.id == target.id

        while queue and not found:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for nxt in adjacency.get(current, []):
                if nxt in visited:
                    continue
                visited.add(nxt)
                parent[nxt] = current
                if nxt == target.id:
                    found = True
                    break
                queue.append((nxt, depth + 1))

        result = {
            "from": {"method_id": source.id, "signature": source.signature},
            "to": {"method_id": target.id, "signature": target.signature},
            "found": found,
            "max_depth": max_depth,
            "path": [],
            "edges": [],
            "stopped_reason": None if found else "max_depth_or_no_path",
        }
        if not found:
            return result

        node_ids = [target.id]
        cur = target.id
        while cur != source.id:
            prev = parent[cur]
            node_ids.append(prev)
            cur = prev
        node_ids.reverse()

        for method_id in node_ids:
            m = method_by_id.get(method_id)
            if not m:
                continue
            result["path"].append(
                {
                    "method_id": m.id,
                    "signature": m.signature,
                    "file_path": m.file_path,
                    "line_start": getattr(m, "start_line", None),
                    "line_end": getattr(m, "end_line", None),
                }
            )

        edges_out: list[dict] = []
        for idx in range(len(node_ids) - 1):
            from_id = node_ids[idx]
            to_id = node_ids[idx + 1]
            candidates = edge_lookup.get((from_id, to_id), [])
            edge_obj = candidates[0] if candidates else None

            call = getattr(edge_obj, "call", None) if edge_obj is not None else None
            if call is None and edge_obj is not None:
                call = getattr(edge_obj, "callee_name", None) or getattr(
                    edge_obj, "name", None
                )

            lines = getattr(edge_obj, "lines", None) if edge_obj is not None else None
            if lines is None and edge_obj is not None:
                lines = getattr(edge_obj, "line_numbers", None)

            resolution = (
                getattr(edge_obj, "resolution", None) if edge_obj is not None else None
            )
            if resolution is None and edge_obj is not None:
                resolution = getattr(edge_obj, "resolution_status", None)

            if lines is None and edge_obj is not None:
                line_single = getattr(edge_obj, "line", None)
                if line_single is not None:
                    lines = [line_single]

            if edge_obj is not None and call is None:
                callsite = callsite_by_id.get(getattr(edge_obj, "callsite_id", ""))
                if callsite is not None:
                    call = getattr(callsite, "callee_name", None)
                    if lines is None:
                        line_val = getattr(callsite, "line", None)
                        if line_val is not None:
                            lines = [line_val]
                    if resolution is None:
                        resolution = getattr(callsite, "resolution_status", None)

            if isinstance(lines, int):
                lines = [lines]
            if lines is None:
                lines = []

            edges_out.append(
                {
                    "from": from_id,
                    "to": to_id,
                    "call": call,
                    "lines": lines,
                    "resolution": resolution,
                }
            )

        result["edges"] = edges_out
        return result
