from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from typing import Callable

from ..utils.context_builder import build_method_context

NOISY_UNRESOLVED_CALLS = {
    "get",
    "put",
    "add",
    "remove",
    "equals",
    "hashcode",
    "tostring",
    "size",
    "isempty",
    "contains",
    "containskey",
    "entryset",
    "values",
    "keyset",
    "containertype",
    "containers",
    "getapiversion",
}

NOISY_UNRESOLVED_RECEIVERS = {
    "headersmap",
    "dynamiccontainers",
    "postrankingrules",
    "sxmcontext",
}

RECEIVER_SOURCE_PRIORITY = {
    "field": 0,
    "this": 1,
    "local": 2,
    "param": 3,
    "unknown": 4,
}


def _matches_pattern(value: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, value):
                return True
        except re.error:
            continue
    return False


def _parse_signature(signature: str) -> tuple[str, str]:
    if "#" not in signature:
        return "", signature
    class_full, rest = signature.split("#", 1)
    method_name = rest.split("(", 1)[0]
    return class_full, method_name


def _selector_candidates(signature: str) -> tuple[str, str, str]:
    class_full, method_name = _parse_signature(signature)
    short_class = class_full.split(".")[-1] if class_full else ""
    return (
        f"{class_full}.{method_name}" if class_full else method_name,
        f"{short_class}.{method_name}".strip("."),
        method_name,
    )


def _matches_selector(
    selector: str, node_signature: str, bare_name_unique: bool
) -> bool:
    full, short, bare = _selector_candidates(node_signature)
    if selector == full or selector == short:
        return True
    if selector == bare and bare_name_unique:
        return True
    return False


def _base_match(node: dict, rules: dict) -> tuple[bool, str | None]:
    signature = node.get("signature", "")
    file_path = node.get("file_path", "")
    _, method_name = _parse_signature(signature)

    if signature in rules.get("signatures", []):
        return True, "signature"
    if any(p in file_path for p in rules.get("package_contains", [])):
        return True, "package"
    if _matches_pattern(method_name, rules.get("method_name_patterns", [])):
        return True, "method_pattern"
    return False, None


def _apply_selector_rules(
    nodes: list[dict], flow_config: dict
) -> tuple[set[str], dict[str, str], set[str], dict[str, str]]:
    include = (
        (flow_config or {}).get("include", {}) if isinstance(flow_config, dict) else {}
    )
    exclude = (
        (flow_config or {}).get("exclude", {}) if isinstance(flow_config, dict) else {}
    )

    include_ids: set[str] = set()
    include_reason: dict[str, str] = {}
    exclude_ids: set[str] = set()
    exclude_reason: dict[str, str] = {}

    method_counts = defaultdict(int)
    for n in nodes:
        _, m = _parse_signature(n.get("signature", ""))
        method_counts[m] += 1

    for n in nodes:
        nid = n["id"]
        signature = n.get("signature", "")
        _, method_name = _parse_signature(signature)
        bare_unique = method_counts[method_name] == 1

        matched, reason = _base_match(n, include)
        if matched:
            include_ids.add(nid)
            include_reason[nid] = f"include:{reason}"

        for selector in include.get("selectors", []) or []:
            if _matches_selector(selector, signature, bare_unique):
                include_ids.add(nid)
                include_reason[nid] = "include:selector"
                break

        matched, reason = _base_match(n, exclude)
        if matched:
            exclude_ids.add(nid)
            exclude_reason[nid] = f"exclude:{reason}"

        for selector in exclude.get("selectors", []) or []:
            if _matches_selector(selector, signature, bare_unique):
                exclude_ids.add(nid)
                exclude_reason[nid] = "exclude:selector"
                break

    # include overrides exclude
    for nid in list(exclude_ids):
        if nid in include_ids:
            exclude_ids.remove(nid)
            exclude_reason.pop(nid, None)

    return include_ids, include_reason, exclude_ids, exclude_reason


def _group_uncertain_edges(raw_uncertain: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], int] = {}
    for e in raw_uncertain:
        key = (
            str(e.get("from") or ""),
            str(e.get("call") or ""),
            str(e.get("reason") or ""),
        )
        grouped[key] = grouped.get(key, 0) + 1

    out = []
    for (from_id, call, reason), count in grouped.items():
        out.append({"from": from_id, "call": call, "reason": reason, "count": count})
    out.sort(key=lambda x: (x["from"], x["call"], x["reason"]))
    return out


def _bridge_edges(
    edges: list[dict], removed_ids: set[str], kept_ids: set[str]
) -> list[dict]:
    out_map = defaultdict(list)
    in_map = defaultdict(list)
    for e in edges:
        out_map[e["from"]].append(e)
        in_map[e["to"]].append(e)

    kept_edges = [e for e in edges if e["from"] in kept_ids and e["to"] in kept_ids]
    seen = {
        (
            e["from"],
            e["to"],
            e.get("call"),
            tuple(e.get("lines", [])),
            e.get("resolution"),
        )
        for e in kept_edges
    }

    for rid in removed_ids:
        incoming = [e for e in in_map.get(rid, []) if e["from"] in kept_ids]
        if not incoming:
            continue
        for inc in incoming:
            q = deque([(rid, set([rid]))])
            while q:
                cur, visited = q.popleft()
                for oe in out_map.get(cur, []):
                    dst = oe["to"]
                    if dst in visited:
                        continue
                    if dst in removed_ids:
                        nv = set(visited)
                        nv.add(dst)
                        q.append((dst, nv))
                        continue
                    if dst in kept_ids:
                        lines = sorted(
                            set((inc.get("lines") or []) + (oe.get("lines") or []))
                        )
                        bridged = {
                            "from": inc["from"],
                            "to": dst,
                            "call": oe.get("call") or inc.get("call"),
                            "lines": lines,
                            "resolution": "excluded_bridge",
                        }
                        key = (
                            bridged["from"],
                            bridged["to"],
                            bridged.get("call"),
                            tuple(bridged.get("lines", [])),
                            bridged.get("resolution"),
                        )
                        if key not in seen:
                            seen.add(key)
                            kept_edges.append(bridged)

    return kept_edges


def stitch_flow(
    graph,
    entry_method,
    *,
    max_depth: int = 4,
    business_only: bool = True,
    is_business_entry: Callable[[dict], bool] | None = None,
    flow_config: dict | None = None,
    detail: str = "summary",
) -> dict:
    flow_config = flow_config or {}
    method_by_id = {m.id: m for m in graph.methods}
    method_by_signature = {m.signature: m for m in graph.methods}
    method_lookup_full: dict[tuple[str, str], list] = {}
    method_lookup_short: dict[tuple[str, str], list] = {}
    for m in graph.methods:
        call_name = str(getattr(m, "method_name", "") or "")
        class_full = str(getattr(m, "class_full_name", "") or "")
        class_short = class_full.split(".")[-1] if class_full else ""
        if call_name and class_full:
            method_lookup_full.setdefault((class_full, call_name), []).append(m)
        if call_name and class_short:
            method_lookup_short.setdefault((class_short, call_name), []).append(m)

    nodes_map: dict[str, dict] = {}
    edges: list[dict] = []
    uncertain_raw: list[dict] = []
    stopped_paths: list[dict] = []

    edges_seen: set[tuple[str, str, str, tuple[int, ...], str]] = set()
    stopped_seen: set[tuple[str, str]] = set()
    min_depth_seen: dict[str, int] = {}
    context_cache: dict[str, dict] = {}
    important_unresolved_candidates: list[dict] = []

    def add_stopped(method_id: str, reason: str) -> None:
        key = (method_id, reason)
        if key in stopped_seen:
            return
        stopped_seen.add(key)
        stopped_paths.append({"method_id": method_id, "reason": reason})

    def load_context(method_id: str) -> dict:
        if method_id not in context_cache:
            context_cache[method_id] = build_method_context(graph, method_id)
        return context_cache[method_id]

    def resolve_target(resolved_item: dict):
        tid = resolved_item.get("target_id")
        if tid and tid in method_by_id:
            return method_by_id[tid]
        sig = resolved_item.get("target_signature")
        if sig and sig in method_by_signature:
            return method_by_signature[sig]
        return None

    def walk(method_id: str, depth: int, path: set[str]) -> None:
        method_obj = method_by_id.get(method_id)
        if not method_obj:
            add_stopped(method_id, "unresolved")
            return

        if method_id in min_depth_seen and min_depth_seen[method_id] <= depth:
            return
        min_depth_seen[method_id] = depth

        context = load_context(method_id)
        if context.get("error"):
            add_stopped(method_id, "unresolved")
            return

        resolved = list(
            context.get("business_flow") or context.get("resolved_callees", [])
        )
        if business_only and is_business_entry is not None:
            resolved = [item for item in resolved if is_business_entry(item)]
        unresolved = list(context.get("unresolved_calls", []))
        raw_unresolved = [
            c
            for c in graph.callsites
            if c.caller_method_id == method_id
            and not c.resolved_candidates
            and (c.receiver or "").strip()
            and (c.callee_name or "").strip()
        ]

        existing = nodes_map.get(method_id)
        if not existing or depth < existing["depth"]:
            nodes_map[method_id] = {
                "id": method_obj.id,
                "signature": method_obj.signature,
                "file_path": method_obj.file_path,
                "line_start": getattr(method_obj, "start_line", None),
                "line_end": getattr(method_obj, "end_line", None),
                "depth": depth,
                "business_call_count": len(resolved),
                "unresolved_call_count": len(unresolved),
            }

        for u in unresolved:
            uncertain_raw.append(
                {
                    "from": method_id,
                    "call": u.get("call"),
                    "reason": u.get("reason"),
                }
            )
        for c in raw_unresolved:
            call_name = str(c.callee_name or "").strip()
            receiver = str(c.receiver or "").strip()
            if not call_name or not receiver:
                continue

            call_lower = call_name.lower()
            receiver_lower = receiver.lower()
            if call_lower in NOISY_UNRESOLVED_CALLS:
                continue
            if receiver_lower in NOISY_UNRESOLVED_RECEIVERS:
                continue
            if "(" in receiver or ")" in receiver:
                continue

            receiver_type = (
                getattr(c, "receiver_type_normalized", None)
                or getattr(c, "receiver_type", None)
                or getattr(c, "receiver_type_raw", None)
            )
            receiver_type_text = str(receiver_type or "").strip()
            receiver_type_simple = (
                receiver_type_text.split(".")[-1] if receiver_type_text else ""
            )
            source_kind = str(
                getattr(c, "receiver_resolution_source", None) or "unknown"
            )

            possible = []
            if receiver_type_text:
                candidates = []
                candidates.extend(
                    method_lookup_full.get((receiver_type_text, call_name), [])
                )
                candidates.extend(
                    method_lookup_short.get((receiver_type_simple, call_name), [])
                )
                seen_targets = set()
                for m in candidates:
                    if m.id in seen_targets:
                        continue
                    seen_targets.add(m.id)
                    possible.append(
                        {
                            "method_id": m.id,
                            "signature": m.signature,
                            "file_path": m.file_path,
                            "line_start": getattr(m, "start_line", None),
                            "line_end": getattr(m, "end_line", None),
                        }
                    )
                    if len(possible) >= 5:
                        break

            important_unresolved_candidates.append(
                {
                    "from_method_id": method_id,
                    "receiver": receiver,
                    "receiver_type": receiver_type_text or None,
                    "receiver_resolution_source": source_kind,
                    "call": call_name,
                    "line": getattr(c, "line", None),
                    "resolution": source_kind
                    if source_kind != "unknown"
                    else (getattr(c, "resolution_status", None) or "unknown"),
                    "confidence": 0.5,
                    "reason": "unresolved_receiver_call",
                    "uncertainty": (
                        "receiver_type_known_but_not_resolved"
                        if receiver_type_text
                        else "receiver_type_unknown"
                    ),
                    "possible_targets": possible,
                }
            )

        if depth >= max_depth:
            add_stopped(method_id, "max_depth")
            return

        if not resolved:
            add_stopped(method_id, "no_business_callees")
            return

        next_path = set(path)
        next_path.add(method_id)
        traversed_child = False

        for item in resolved:
            target = resolve_target(item)
            call = item.get("call")
            lines = sorted(set(item.get("lines") or []))
            resolution = item.get("resolution") or "resolved_context"

            if not target:
                uncertain_raw.append(
                    {"from": method_id, "call": call, "reason": "unresolved"}
                )
                continue

            edge_key = (
                method_id,
                target.id,
                str(call or ""),
                tuple(lines),
                str(resolution),
            )
            if edge_key not in edges_seen:
                edges_seen.add(edge_key)
                edges.append(
                    {
                        "from": method_id,
                        "to": target.id,
                        "call": call,
                        "lines": lines,
                        "resolution": resolution,
                    }
                )

            if target.id in next_path:
                add_stopped(target.id, "cycle")
                continue

            traversed_child = True
            walk(target.id, depth + 1, next_path)

        if not traversed_child:
            add_stopped(method_id, "unresolved")

    walk(entry_method.id, 0, set())

    nodes = list(nodes_map.values())
    include_ids, include_reason, exclude_ids, exclude_reason = _apply_selector_rules(
        nodes, flow_config
    )

    for node in nodes:
        nid = node["id"]
        node["flow_filter"] = {
            "included": nid in include_ids,
            "excluded": nid in exclude_ids,
            "reason": include_reason.get(nid) or exclude_reason.get(nid),
        }

    kept_nodes = [n for n in nodes if n["id"] not in exclude_ids]
    kept_ids = {n["id"] for n in kept_nodes}
    filtered_edges = _bridge_edges(edges, exclude_ids, kept_ids)

    in_deg = defaultdict(int)
    out_deg = defaultdict(int)
    adjacency = defaultdict(set)
    for e in filtered_edges:
        out_deg[e["from"]] += 1
        in_deg[e["to"]] += 1
        adjacency[e["from"]].add(e["to"])

    sinks = {n["id"] for n in kept_nodes if out_deg.get(n["id"], 0) == 0}
    sink_cache: dict[str, set[str]] = {}

    def reachable_sinks(node_id: str, trail: set[str] | None = None) -> set[str]:
        if node_id in sink_cache:
            return sink_cache[node_id]
        trail = set() if trail is None else set(trail)
        if node_id in trail:
            return set()
        trail.add(node_id)
        children = adjacency.get(node_id, set())
        if not children:
            sink_cache[node_id] = {node_id} if node_id in sinks else set()
            return sink_cache[node_id]
        acc: set[str] = set()
        for child in children:
            acc.update(reachable_sinks(child, trail))
        sink_cache[node_id] = acc
        return acc

    for n in kept_nodes:
        nid = n["id"]
        sink_count = len(reachable_sinks(nid))
        entropy_score = math.log2(sink_count + 1)
        n["path_entropy_score"] = round(entropy_score, 4)
        n["path_entropy_reason"] = [f"reachable_sinks={sink_count}"]

        rank = 0.0
        reasons: list[str] = []
        if nid == entry_method.id:
            rank += 100
            reasons.append("entry:+100")
        d = int(n.get("depth", 0))
        if d == 1:
            rank += 30
            reasons.append("depth1:+30")
        elif d == 2:
            rank += 20
            reasons.append("depth2:+20")
        elif d == 3:
            rank += 10
            reasons.append("depth3:+10")
        if out_deg.get(nid, 0) > 0:
            rank += 15
            reasons.append("downstream:+15")
        if out_deg.get(nid, 0) >= 2:
            rank += 10
            reasons.append("multi_out:+10")
        if in_deg.get(nid, 0) >= 2:
            rank += 10
            reasons.append("multi_path:+10")
        if out_deg.get(nid, 0) == 0:
            rank -= 10
            reasons.append("leaf:-10")
        if out_deg.get(nid, 0) >= 5:
            rank -= 10
            reasons.append("high_fanout:-10")
        if nid in include_ids:
            rank += 100
            reasons.append("config_include:+100")
        rank += entropy_score * 10
        reasons.append(f"path_entropy:+{round(entropy_score * 10, 2)}")

        n["rank_score"] = round(rank, 4)
        n["rank_reason"] = reasons

        if nid in include_ids:
            n["tier"] = "primary"
            n["confidence"] = "high"
            n["tier_reason"] = "config:include"
        elif d <= 2 and out_deg.get(nid, 0) > 0 and in_deg.get(nid, 0) <= 2:
            n["tier"] = "primary"
            n["confidence"] = "high"
            n["tier_reason"] = "structural:shallow_with_downstream"
        elif (
            out_deg.get(nid, 0) >= 5
            or in_deg.get(nid, 0) >= 3
            or out_deg.get(nid, 0) == 0
        ):
            n["tier"] = "utility"
            n["confidence"] = "medium"
            n["tier_reason"] = "structural:fan_or_leaf"
        elif d <= 3:
            n["tier"] = "supporting"
            n["confidence"] = "medium"
            n["tier_reason"] = "structural:reachable_mid_depth"
        else:
            n["tier"] = "supporting"
            n["confidence"] = "low"
            n["tier_reason"] = "structural:fallback"

    kept_nodes.sort(key=lambda n: (n["depth"], n["signature"]))

    likely_primary = [
        {
            "method_id": n["id"],
            "signature": n["signature"],
            "file_path": n["file_path"],
            "line_start": n.get("line_start"),
            "line_end": n.get("line_end"),
            "depth": n["depth"],
            "tier": n["tier"],
            "confidence": n["confidence"],
            "tier_reason": n["tier_reason"],
        }
        for n in kept_nodes
        if n["tier"] == "primary"
    ]
    supporting = [
        {
            "method_id": n["id"],
            "signature": n["signature"],
            "file_path": n["file_path"],
            "line_start": n.get("line_start"),
            "line_end": n.get("line_end"),
            "depth": n["depth"],
            "tier": n["tier"],
            "confidence": n["confidence"],
            "tier_reason": n["tier_reason"],
        }
        for n in kept_nodes
        if n["tier"] == "supporting"
    ]
    low_priority = [
        {
            "method_id": n["id"],
            "signature": n["signature"],
            "file_path": n["file_path"],
            "line_start": n.get("line_start"),
            "line_end": n.get("line_end"),
            "depth": n["depth"],
            "tier": n["tier"],
            "confidence": n["confidence"],
            "tier_reason": n["tier_reason"],
        }
        for n in kept_nodes
        if n["tier"] == "utility"
    ]

    uncertain_edges = _group_uncertain_edges(uncertain_raw)

    top_nodes = sorted(
        [
            {
                "method_id": n["id"],
                "signature": n["signature"],
                "file_path": n["file_path"],
                "line_start": n.get("line_start"),
                "line_end": n.get("line_end"),
                "depth": n["depth"],
                "tier": n["tier"],
                "confidence": n["confidence"],
                "tier_reason": n["tier_reason"],
                "rank_score": n["rank_score"],
                "rank_reason": n["rank_reason"],
                "path_entropy_score": n.get("path_entropy_score", 0.0),
                "path_entropy_reason": n.get("path_entropy_reason", []),
            }
            for n in kept_nodes
        ],
        key=lambda x: x["rank_score"],
        reverse=True,
    )[:20]

    seen_unresolved = set()
    rank_by_id = {n["id"]: n["rank_score"] for n in kept_nodes}
    important_unresolved = []
    for item in important_unresolved_candidates:
        key = (
            item.get("from_method_id"),
            item.get("receiver"),
            item.get("call"),
            item.get("line"),
        )
        if key in seen_unresolved:
            continue
        seen_unresolved.add(key)
        important_unresolved.append(item)
    important_unresolved.sort(
        key=lambda item: (
            0 if item.get("from_method_id") == entry_method.id else 1,
            RECEIVER_SOURCE_PRIORITY.get(
                str(item.get("receiver_resolution_source") or "unknown"), 4
            ),
            -float(rank_by_id.get(item.get("from_method_id"), 0.0)),
            str(item.get("call") or ""),
        )
    )
    important_unresolved = important_unresolved[:10]

    result = {
        "entry": {
            "method_id": entry_method.id,
            "signature": entry_method.signature,
        },
        "depth": max_depth,
        "agent_view": {
            "entry": {
                "method_id": entry_method.id,
                "signature": entry_method.signature,
            },
            "top_nodes": top_nodes,
            "important_unresolved_calls": important_unresolved,
            "uncertain_edges": uncertain_edges,
            "stopped_paths": stopped_paths,
        },
        "summary": {
            "node_count": len(kept_nodes),
            "edge_count": len(filtered_edges),
            "top_node_count": len(top_nodes),
            "uncertain_edge_count": len(uncertain_edges),
            "stopped_path_count": len(stopped_paths),
            "excluded_count": len(exclude_ids),
        },
    }
    if detail == "full":
        result["nodes"] = kept_nodes
        result["edges"] = filtered_edges
        result["uncertain_edges"] = uncertain_edges
        result["stopped_paths"] = stopped_paths
        result["likely_primary"] = likely_primary
        result["supporting"] = supporting
        result["low_priority"] = low_priority
        # backward-compatible aliases
        result["primary_flow"] = likely_primary
        result["supporting_flow"] = supporting
        result["utility_flow"] = low_priority
    return result
