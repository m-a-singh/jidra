from __future__ import annotations

import re
import threading
from collections import Counter, deque
from pathlib import Path
from typing import Any

from .context_builder import build_method_context
from .flow_stitcher import stitch_flow
from . import graph_store
from .selector import (
    _fuzzy_suggestions,
    _method_ambiguous_error,
    _resolve_method_selector,
)

# Default graph path used when no explicit graph is provided.
# Keep this relative so the project is portable and doesn't leak a developer machine path.
DEFAULT_MAIN_GRAPH = str(Path(__file__).resolve().parent / "output" / "graph.db")


# Output-size budget tiers keyed on the number of methods in the graph (a proxy
# for repo size). A toy project and an enterprise monorepo should not get the
# same response shape — small graphs get fuller answers, large graphs get
# tighter, windowed output. Each entry is (exclusive_upper_bound, budget); the
# final None bound is the catch-all for the largest graphs.
_BUDGET_TIERS: list[tuple[int | None, dict]] = [
    (
        200,
        {
            "tier": "XS",
            "max_chars": 6_000,
            "max_source_lines": 60,
            "top_n": 6,
            "depth": 5,
        },
    ),
    (
        1_000,
        {
            "tier": "S",
            "max_chars": 10_000,
            "max_source_lines": 50,
            "top_n": 5,
            "depth": 4,
        },
    ),
    (
        5_000,
        {
            "tier": "M",
            "max_chars": 14_000,
            "max_source_lines": 40,
            "top_n": 4,
            "depth": 4,
        },
    ),
    (
        20_000,
        {
            "tier": "L",
            "max_chars": 18_000,
            "max_source_lines": 30,
            "top_n": 4,
            "depth": 3,
        },
    ),
    (
        None,
        {
            "tier": "XL",
            "max_chars": 22_000,
            "max_source_lines": 20,
            "top_n": 3,
            "depth": 3,
        },
    ),
]


# Split CamelCase / snake_case identifiers into their constituent words so a
# query like "AuthToken" or "validate_token" matches the same methods.
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
# Path markers that demote generated/mock code in explore ranking.
_GENERATED_MARKERS = (
    ".pb.",
    "_generated",
    "/generated/",
    "mock_",
    "/mocks/",
    "/build/generated",
    "generated-src",
    "/build/classes",
    "/out/production",
)


def _tokenize_query(text: str) -> set[str]:
    tokens: set[str] = set()
    for part in re.split(r"[^A-Za-z0-9]+", text or ""):
        if not part:
            continue
        tokens.add(part.lower())
        for sub in _CAMEL_RE.findall(part):
            if sub:
                tokens.add(sub.lower())
    return tokens


def _score_hit(row: dict, tokens: set[str]) -> float:
    """Heuristic relevance for explore ranking (higher = better).

    Mirrors CodeGraph: exact name +50, name substring +25, signature match +20,
    a base +10 for matching source (FTS already guarantees a hit), test penalty
    -15, generated/mock penalty -20. bm25 (lower = better) is folded in as a
    small tiebreaker.
    """
    name = (row.get("method_name") or "").lower()
    sig = (row.get("signature") or "").lower()
    path = (row.get("file_path") or "").lower()
    score = 10.0  # base: this row matched the FTS source/name/signature index
    for tok in tokens:
        if tok == name:
            score += 50.0
        elif tok in name:
            score += 25.0
        if tok in sig:
            score += 20.0
    if (
        "/test/" in path
        or "/tests/" in path
        or path.endswith(("test.java", "tests.java"))
    ):
        score -= 15.0
    if any(marker in path for marker in _GENERATED_MARKERS):
        score -= 200.0  # hard demote — generated files never outrank real source
    score += -float(row.get("score", 0.0))  # bm25 tiebreaker
    return score


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


def _call_neighbors_batch(
    conn: Any, method_ids: set[str], variant: str = "main"
) -> set[str]:
    """Return all direct callers + callees of a set of method IDs via resolved_call_edges."""
    if not method_ids:
        return set()
    import sqlite3 as _sq3

    placeholders = ",".join("?" * len(method_ids))
    ids = list(method_ids)
    out: set[str] = set()
    try:
        cur = conn.execute(
            f"SELECT callee_method_id FROM resolved_call_edges "
            f"WHERE caller_method_id IN ({placeholders}) AND variant=?",
            ids + [variant],
        )
        out |= {r[0] for r in cur.fetchall()}
        cur = conn.execute(
            f"SELECT caller_method_id FROM resolved_call_edges "
            f"WHERE callee_method_id IN ({placeholders}) AND variant=?",
            ids + [variant],
        )
        out |= {r[0] for r in cur.fetchall()}
    except _sq3.OperationalError:
        pass
    return out


class JidraEngine:
    def __init__(self, graph_path: str, variant: str = "validated"):
        self.graph_path = str(Path(graph_path).resolve())
        # Keep the connection alive so FTS-backed search (Phase 1) can query the
        # DB directly instead of scanning the in-memory graph. A lock serializes
        # access since the daemon (Phase 5) shares one cached engine across
        # handler threads.
        self.conn = graph_store.connect(Path(self.graph_path))
        self._conn_lock = threading.Lock()
        self.graph = graph_store.load_graph(self.conn, variant=variant)

        # Resolve repo root: read from meta if available, else compute from class paths
        codebase_root = graph_store.get_meta(self.conn, "codebase_root")
        if codebase_root:
            self.repo_root = Path(codebase_root)
        else:
            # Fallback: longest common prefix of all class file paths, anchored by markers
            if self.graph.classes:
                paths = [Path(c.file_path).resolve() for c in self.graph.classes]
                try:
                    candidate = Path(*[p.parts[0] for p in paths if p.parts])
                    for i in range(1, min(len(p.parts) for p in paths)):
                        if all(p.parts[i] == paths[0].parts[i] for p in paths):
                            candidate = Path(*paths[0].parts[: i + 1])
                        else:
                            break

                    # Anchor candidate on a real repo marker (.git)
                    self.repo_root = self._find_repo_root_with_marker()
                    if not self.repo_root:
                        self.repo_root = candidate
                except (IndexError, ValueError):
                    self.repo_root = None
            else:
                self.repo_root = None

    def _rel(self, path: str | None) -> str | None:
        """Make path repo-relative if possible, else return absolute."""
        if not path or not self.repo_root:
            return path
        try:
            p = Path(path).resolve()
            rel = p.relative_to(self.repo_root)
            return str(rel)
        except (ValueError, RuntimeError):
            return str(Path(path).resolve())

    def _find_repo_root_with_marker(self) -> Path | None:
        """Find the deepest directory with a .git marker that is an ancestor of most file paths."""
        try:
            if self.graph.classes:
                paths = [Path(c.file_path).resolve() for c in self.graph.classes]

                # For each path, walk up to find the deepest dir with .git (project root)
                possible_roots = []
                for p in paths:
                    current = p.parent
                    while current != current.parent:
                        if (current / ".git").exists():
                            possible_roots.append(current)
                            break
                        current = current.parent

                # Find the most common root among all paths
                if possible_roots:
                    root_counts = Counter(possible_roots)
                    most_common_root, count = root_counts.most_common(1)[0]
                    # Return only if it's the root for most paths
                    if count > len(paths) * 0.5:
                        return most_common_root

                return None
        except (IndexError, ValueError, AttributeError):
            pass
        return None

    def _resolve_single_method(self, selector: str) -> dict:
        candidates = _resolve_method_selector(self.graph, selector)
        if not candidates:
            # If the selector names a CLASS that exists but a method that does
            # not, say so definitively + list the real methods — otherwise the
            # agent keeps fuzzy-searching a method that isn't there (the T2 spiral).
            if "#" in selector or "." in selector:
                sep = "#" if "#" in selector else "."
                class_part, _, method_part = selector.rpartition(sep)
                cls_short = class_part.rsplit(".", 1)[-1]
                cls_methods = [
                    m
                    for m in self.graph.methods
                    if m.class_full_name == class_part
                    or m.class_full_name.rsplit(".", 1)[-1] == cls_short
                ]
                if class_part and method_part and cls_methods:
                    names = sorted({m.method_name for m in cls_methods})
                    if method_part not in names:
                        owner = cls_methods[0].class_full_name
                        return {
                            "error": "method_not_found_on_class",
                            "definitive": True,
                            "message": (
                                f"Class '{owner}' exists but declares no method "
                                f"named '{method_part}'. Do not keep searching — "
                                f"it is not on this class."
                            ),
                            "class": owner,
                            "available_methods": names,
                        }
            suggestions = _fuzzy_suggestions(self.graph, selector)
            return {
                "error": f"No exact match for '{selector}' in the graph.",
                "action": "Pick the best match from suggestions and retry with that selector.",
                "suggestions": suggestions,
            }
        if len(candidates) > 1:
            return {"error": _method_ambiguous_error(selector, candidates)}
        return {"method": candidates[0]}

    def _get_budget(self) -> dict:
        """Pick the output budget for this graph's size (see _BUDGET_TIERS)."""
        n = len(self.graph.methods)
        for threshold, budget in _BUDGET_TIERS:
            if threshold is None or n < threshold:
                return budget
        return _BUDGET_TIERS[-1][1]

    def _budget_meta(self) -> dict:
        budget = self._get_budget()
        return {
            "budget_tier": budget["tier"],
            "graph_size": {"methods": len(self.graph.methods)},
        }

    def _with_budget(self, result: dict) -> dict:
        """Annotate a tool response with the budget tier so callers can see why
        (and how much) output was truncated."""
        if isinstance(result, dict):
            result.setdefault("budget_tier", self._budget_meta()["budget_tier"])
            result.setdefault("graph_size", self._budget_meta()["graph_size"])
        return result

    def get_method_context(self, method: str, max_chars: int | None = None) -> dict:
        budget = self._get_budget()
        if max_chars is None:
            max_chars = budget["max_chars"]
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
        result = build_method_context(
            self.graph,
            selected.id,
            max_chars=max_chars,
            max_source_lines=budget["max_source_lines"],
        )
        result.update(
            self._smithy_linkage_for_class(getattr(selected, "class_id", None))
        )
        return self._with_budget(result)

    def _smithy_linkage_for_class(self, class_id: str | None) -> dict:
        """Surface whether this class is a known Smithy operation handler.

        Distinguishes "we looked and found no link" (smithy_operation: null,
        smithy_note explains why) from callers who never call this at all —
        the dict is always present so agents can tell the two apart."""
        if not class_id:
            return {
                "smithy_operation": None,
                "smithy_note": "no class context for this method",
            }
        links = graph_store.load_smithy_operation_links(self.conn, class_id=class_id)
        if not links:
            return {
                "smithy_operation": None,
                "smithy_note": "no matching Smithy codegen profile found for this class",
            }
        link = links[0]
        operations = graph_store.load_smithy_operations(self.conn)
        op = next((o for o in operations if o.id == link.operation_id), None)
        return {
            "smithy_operation": op.name if op else link.operation_id,
            "smithy_operation_id": link.operation_id,
            "smithy_codegen_profile": link.codegen_profile,
            "smithy_link_type": link.link_type,
            "smithy_note": None,
        }

    def get_flow(
        self,
        method: str,
        depth: int | None = None,
        top_n: int | None = None,
        detail: str = "summary",
    ) -> dict:
        _ = top_n
        if depth is None:
            depth = self._get_budget()["depth"]
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
        result = self._with_budget(
            stitch_flow(self.graph, selected, max_depth=depth, detail=detail)
        )
        if self.repo_root:
            result["repo_root"] = str(self.repo_root)
        return result

    def get_agent_flow(
        self, method: str, depth: int | None = None, top_n: int | None = None
    ) -> dict:
        budget = self._get_budget()
        if depth is None:
            depth = budget["depth"]
        if top_n is None:
            top_n = budget["top_n"]
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
        return self._with_budget(
            {
                "entry": agent_view.get("entry", flow.get("entry", {})),
                "top_nodes": top_nodes,
                "top_edges": top_edges,
                "important_unresolved_calls": agent_view.get(
                    "important_unresolved_calls",
                    flow.get("important_unresolved_calls", []),
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
        )

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

    def find_callers(self, method: str, depth: int = 1) -> dict:
        """Reverse call lookup: who calls `method`, walked up to `depth` levels
        of the call graph (BFS). Complements `get_file_dependents` (file-level)
        with a method-level answer to "what calls this, and what calls those.\""""
        resolved = self._resolve_single_method(method)
        if "error" in resolved:
            suggestions = resolved.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                resolved = self._resolve_single_method(suggestions[0]["selector"])
                if "error" in resolved:
                    return resolved
            else:
                return resolved
        selected = resolved["method"]
        if not hasattr(selected, "id"):
            return {"error": "invalid_method_selector_result"}

        method_by_id = {m.id: m for m in self.graph.methods}

        # Build reverse adjacency: callee_id → [caller_id, ...]
        reverse: dict[str, list[str]] = {}
        for edge in self.graph.resolved_call_edges:
            caller_id = getattr(edge, "caller_method_id", None)
            callee_id = getattr(edge, "callee_method_id", None)
            if caller_id and callee_id:
                reverse.setdefault(callee_id, []).append(caller_id)

        # BFS up the call graph up to `depth` levels
        visited: set[str] = {selected.id}
        frontier = [selected.id]
        callers_by_depth: list[list[dict]] = []
        for _ in range(max(1, depth)):
            next_frontier = []
            level = []
            for callee_id in frontier:
                for caller_id in reverse.get(callee_id, []):
                    if caller_id in visited:
                        continue
                    visited.add(caller_id)
                    next_frontier.append(caller_id)
                    m = method_by_id.get(caller_id)
                    level.append(
                        {
                            "method_id": caller_id,
                            "signature": m.signature if m else caller_id,
                            "file_path": getattr(m, "file_path", None) if m else None,
                            "start_line": getattr(m, "start_line", None) if m else None,
                        }
                    )
            if level:
                callers_by_depth.append(level)
            frontier = next_frontier
            if not frontier:
                break

        flat = [c for level in callers_by_depth for c in level]
        return {
            "method_id": selected.id,
            "signature": selected.signature,
            "caller_count": len(flat),
            "callers": flat,
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

    def get_implementations(
        self,
        interface: str,
        *,
        transitive: bool = False,
        limit: int = 30,
        detail: str = "summary",
    ) -> dict:
        """Resolve interface to full name, walk inheritance_edges where target matches,
        return direct (or transitive) implementers with signatures, file paths, stereotypes."""
        class_by_full_name = {c.full_name: c for c in self.graph.classes}

        # Resolve the interface SELECTOR to a class. inheritance_edges store the
        # target as written in the `implements`/`extends` clause — almost always a
        # SHORT name — so we resolve by short name and match the graph on short
        # name too. Prefer an exact short-name class, favouring interface/abstract
        # stereotypes over a same-named *Impl/*Service when both exist.
        sel_short = interface.split("#")[0].split("(")[0].rsplit(".", 1)[-1]
        candidates = [
            c
            for c in self.graph.classes
            if c.full_name.rsplit(".", 1)[-1] == sel_short or c.full_name == interface
        ]
        if not candidates:
            return {
                "error": "interface_class_not_found",
                "selector": interface,
                "hint": "no class with that name; pass the interface's simple or full name",
            }

        def _iface_rank(c) -> int:
            st = set(getattr(c, "stereotypes", []) or [])
            if {"interface"} & st:
                return 0
            if {"abstract"} & st:
                return 1
            return 2

        interface_class = sorted(candidates, key=_iface_rank)[0]
        target_short = interface_class.full_name.rsplit(".", 1)[-1]

        # Build forward adjacency keyed by SHORT target name (matches how the
        # extractor records implements/extends clauses).
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in self.graph.inheritance_edges:
            if edge.relation in ("implements", "extends"):
                key = edge.target_class.rsplit(".", 1)[-1]
                adjacency.setdefault(key, []).append((edge.source_class, edge.relation))

        implementers = []
        visited: set[str] = set()
        frontier = [target_short]

        while frontier:
            current = frontier.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for source_full_name, _relation in adjacency.get(current, []):
                impl_class = class_by_full_name.get(source_full_name)
                if impl_class:
                    methods = [
                        m
                        for m in self.graph.methods
                        if m.class_full_name == source_full_name
                    ]
                    item = {
                        "class_full_name": impl_class.full_name,
                        "file_path": self._rel(impl_class.file_path),
                        "stereotypes": getattr(impl_class, "stereotypes", []),
                    }
                    if detail == "full":
                        item["methods"] = [
                            {
                                "signature": m.signature,
                                "start_line": getattr(m, "start_line", None),
                            }
                            for m in methods
                        ]
                    else:
                        item["methods"] = [m.signature for m in methods]
                    implementers.append(item)
                    if transitive:
                        frontier.append(source_full_name.rsplit(".", 1)[-1])

        result: dict = {
            "interface": interface_class.full_name,
        }
        if self.repo_root:
            result["repo_root"] = str(self.repo_root)

        total = len(implementers)
        if limit > 0:
            capped = implementers[:limit]
            omitted = total - len(capped)
            result["implementations"] = capped
            result["count"] = total
            result["omitted"] = omitted
        else:
            result["implementations"] = implementers
            result["count"] = total

        return result

    def get_class_members(self, class_selector: str) -> dict:
        """Resolve class, return fields + methods with signatures, file paths."""
        resolved = self._resolve_single_method(class_selector)
        if "error" in resolved:
            suggestions = resolved.get("suggestions", [])
            if suggestions and suggestions[0].get("score", 0) >= 100:
                resolved = self._resolve_single_method(suggestions[0]["selector"])
                if "error" in resolved:
                    return resolved
            else:
                return resolved
        selected = resolved["method"]
        class_full_name = getattr(selected, "class_full_name", None)
        if not class_full_name:
            return {"error": "method_has_no_class_context"}

        class_by_full_name = {c.full_name: c for c in self.graph.classes}
        cls = class_by_full_name.get(class_full_name)
        if not cls:
            return {"error": "class_not_found"}

        methods = [
            m for m in self.graph.methods if m.class_full_name == class_full_name
        ]
        fields = [
            f
            for f in self.graph.fields
            if getattr(f, "class_full_name", None) == class_full_name
            or f.class_id == cls.id
        ]

        return {
            "class_full_name": cls.full_name,
            "file_path": cls.file_path,
            "fields": [
                {
                    "name": f.name,
                    "type_name": getattr(f, "type_name", None),
                    "line": getattr(f, "line", None),
                }
                for f in fields
            ],
            "methods": [
                {
                    "signature": m.signature,
                    "file_path": m.file_path,
                    "start_line": getattr(m, "start_line", None),
                }
                for m in methods
            ],
        }

    def query_by_annotation(
        self,
        annotation: str,
        kind: str = "any",
        limit: int = 30,
        detail: str = "summary",
    ) -> dict:
        """Find classes/methods by annotation or framework_role.
        kind: 'class', 'method', or 'any'. Matching is lenient: leading '@',
        annotation parameters (``@RequestMapping("/x")``), and case are ignored,
        so ``RestController`` matches ``@RestController``."""

        def _norm(s: str) -> str:
            s = (s or "").strip()
            if s.startswith("@"):
                s = s[1:]
            s = s.split("(", 1)[0]  # drop annotation params
            return s.rsplit(".", 1)[-1].strip().lower()  # bare name, lowercased

        target = _norm(annotation)

        def _ann_match(values) -> bool:
            return any(_norm(v) == target for v in (values or []))

        class_matches = []
        method_matches = []

        if kind in ("class", "any"):
            for cls in self.graph.classes:
                if _ann_match(cls.annotations):
                    item = {
                        "full_name": cls.full_name,
                        "file_path": self._rel(cls.file_path),
                        "line": cls.start_line,
                    }
                    stereotypes = cls.stereotypes or []
                    if detail == "full" or (stereotypes and stereotypes != ["unknown"]):
                        if stereotypes and stereotypes != ["unknown"]:
                            item["stereotypes"] = stereotypes
                    class_matches.append(item)

        if kind in ("method", "any"):
            for method in self.graph.methods:
                role = getattr(method, "framework_role", None)
                if _ann_match(method.annotations) or (role and _norm(role) == target):
                    item = {
                        "signature": method.signature,
                        "class": method.class_full_name,
                        "file_path": self._rel(method.file_path),
                        "line": method.start_line,
                    }
                    if detail == "full" or (role and role != "unknown"):
                        if role and role != "unknown":
                            item["framework_role"] = role
                    method_matches.append(item)

        result: dict = {}
        total_omitted = 0
        if class_matches:
            total_classes = len(class_matches)
            capped_classes = class_matches[:limit] if limit > 0 else class_matches
            result["classes"] = capped_classes
            if limit > 0 and total_classes > limit:
                result["classes_omitted"] = total_classes - limit
                total_omitted += total_classes - limit
        if method_matches:
            total_methods = len(method_matches)
            capped_methods = method_matches[:limit] if limit > 0 else method_matches
            result["methods"] = capped_methods
            if limit > 0 and total_methods > limit:
                result["methods_omitted"] = total_methods - limit
                total_omitted += total_methods - limit

        if total_omitted > 0:
            result["omitted"] = total_omitted

        if self.repo_root:
            result["repo_root"] = str(self.repo_root)

        if not result:
            suggestions = []
            seen = set()
            for cls in self.graph.classes:
                for ann in cls.annotations:
                    if ann not in seen:
                        suggestions.append({"annotation": ann, "kind": "class"})
                        seen.add(ann)
                if len(suggestions) >= 5:
                    break
            for method in self.graph.methods:
                for ann in method.annotations:
                    if ann not in seen:
                        suggestions.append({"annotation": ann, "kind": "method"})
                        seen.add(ann)
                if len(suggestions) >= 10:
                    break
            if suggestions:
                result["suggestions"] = suggestions
            else:
                result["message"] = "no matches found"

        return result

    def field_access(self, field: str | None = None, method: str | None = None) -> dict:
        """Find field access patterns. Query by field name or method signature."""
        if not field and not method:
            return {"error": "specify_field_or_method"}
        if field and method:
            return {"error": "specify_field_or_method"}

        if method:
            resolved = self._resolve_single_method(method)
            if "error" in resolved:
                return resolved
            m = resolved["method"]
            result = {
                "method": m.signature,
                "class": m.class_full_name,
            }
            if m.field_reads:
                result["reads"] = list(m.field_reads)
            if m.field_writes:
                result["writes"] = list(m.field_writes)
            if self.repo_root:
                result["repo_root"] = str(self.repo_root)
            return result

        # By field: field format is "ClassName#fieldName"
        class_name: str | None = None
        if not field:
            return {"error": "specify_field_or_method"}

        field_name: str = field
        if "#" in field:
            class_name, field_name = field.rsplit("#", 1)

        readers = []
        writers = []
        for m in self.graph.methods:
            if class_name and m.class_full_name != class_name:
                continue
            if field_name in m.field_reads:
                readers.append(m.signature)
            if field_name in m.field_writes:
                writers.append(m.signature)

        if not readers and not writers:
            return {"message": f"no access found for field '{field}'"}

        result: dict = {"field": field}
        if readers:
            result["readers"] = readers
        if writers:
            result["writers"] = writers
        if self.repo_root:
            result["repo_root"] = str(self.repo_root)
        return result

    def _search_fallback(
        self, query: str, *, limit: int, language: str | None = None
    ) -> list[dict]:
        """In-memory substring scan used when the FTS index is unavailable or
        returns nothing (e.g. a very old DB the migration couldn't index)."""
        tokens = _tokenize_query(query)
        if not tokens:
            return []
        hits: list[tuple[int, dict]] = []
        for m in self.graph.methods:
            if language and m.language != language:
                continue
            haystack = f"{m.method_name} {m.signature} {m.source or ''}".lower()
            matched = sum(1 for tok in tokens if tok in haystack)
            if not matched:
                continue
            hits.append(
                (
                    matched,
                    {
                        "id": m.id,
                        "method_name": m.method_name,
                        "signature": m.signature,
                        "class_full_name": m.class_full_name,
                        "file_path": m.file_path,
                        "language": m.language,
                        "score": 0.0,
                    },
                )
            )
        hits.sort(key=lambda h: h[0], reverse=True)
        return [row for _matched, row in hits[:limit]]

    def search(self, query: str, limit: int = 20, language: str | None = None) -> dict:
        """FTS5 keyword search over method names, signatures, and source.

        After FTS, expands results with 1-hop call-graph neighbors so that
        callers/callees of matched methods surface even when not directly
        indexed under the query terms.
        """
        with self._conn_lock:
            rows = graph_store.search_methods(
                self.conn, query, limit=limit, language=language
            )
        if not rows:
            rows = self._search_fallback(query, limit=limit, language=language)

        # Re-sort FTS rows: demote generated/build paths so real source ranks first.
        # BM25 scores are negative — more negative = better match, so sort ascending.
        # Generated files go into bucket 1 (after real source in bucket 0).
        def _fts_sort_key(r: dict) -> tuple:
            path = r.get("file_path", "")
            is_generated = any(m in path for m in _GENERATED_MARKERS)
            return (1 if is_generated else 0, float(r.get("score", 0.0)))

        rows = sorted(rows, key=_fts_sort_key)

        # If all top results are generated, fetch more rows to find real source
        if rows and all(
            any(m in r.get("file_path", "") for m in _GENERATED_MARKERS) for r in rows
        ):
            with self._conn_lock:
                extended = graph_store.search_methods(
                    self.conn, query, limit=limit * 5, language=language
                )
            rows = sorted(extended, key=_fts_sort_key)

        seed_ids = {r["id"] for r in rows}

        # 1-hop call expansion — callers + callees of every seed
        neighbor_ids = _call_neighbors_batch(self.conn, seed_ids) - seed_ids
        neighbor_rows: list = []
        if neighbor_ids:
            with self._conn_lock:
                neighbor_rows = graph_store.fetch_methods_by_ids(
                    self.conn, list(neighbor_ids)
                )

        tokens = _tokenize_query(query)

        # Seeds first — preserve BM25 order, never interleaved with neighbors
        results = []
        seen: set[str] = set()
        for r in rows:
            seen.add(r["id"])
            results.append(
                {
                    "method_id": r["id"],
                    "method_name": r["method_name"],
                    "signature": r["signature"],
                    "class_full_name": r["class_full_name"],
                    "file_path": r["file_path"],
                    "language": r["language"],
                    "score": round(-float(r.get("score", 0.0)), 6),
                    "source": "fts",
                }
            )

        # Neighbors appended after all seeds, sorted by heuristic score
        scored_neighbors = sorted(
            (
                (_score_hit(dict(r), tokens), r)
                for r in neighbor_rows
                if r["id"] not in seen
            ),
            key=lambda p: p[0],
            reverse=True,
        )
        for score, r in scored_neighbors:
            seen.add(r["id"])
            results.append(
                {
                    "method_id": r["id"],
                    "method_name": r["method_name"],
                    "signature": r["signature"],
                    "class_full_name": r["class_full_name"],
                    "file_path": r["file_path"],
                    "language": r["language"],
                    "score": round(score, 6),
                    "source": "graph",
                }
            )

        return {"query": query, "count": len(results), "results": results}

    def explore(self, query: str, top_n: int = 10) -> dict:
        """Natural-language exploration: tokenize the query, retrieve FTS
        candidates, re-rank with kind/path heuristics, and attach class context."""
        tokens = _tokenize_query(query)
        fetch = max(top_n * 4, 40)
        with self._conn_lock:
            candidates = graph_store.search_methods(self.conn, query, limit=fetch)
        if not candidates:
            candidates = self._search_fallback(query, limit=fetch)
        method_by_id = {m.id: m for m in self.graph.methods}
        class_by_full = {c.full_name: c for c in self.graph.classes}

        scored = sorted(
            ((_score_hit(r, tokens), r) for r in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )

        # top_n seeds — these are the primary results
        top_seeds = scored[:top_n]
        seed_ids = {r["id"] for _, r in top_seeds}

        # 1-2 hop graph expansion — find neighbors, re-score, append without
        # displacing seeds (they always come first)
        neighbor_ids = _call_neighbors_batch(self.conn, seed_ids)
        hop2_ids = (
            _call_neighbors_batch(self.conn, neighbor_ids - seed_ids)
            - seed_ids
            - neighbor_ids
        )
        new_ids = (neighbor_ids | hop2_ids) - seed_ids
        neighbor_rows: list = []
        if new_ids:
            with self._conn_lock:
                neighbor_rows = graph_store.fetch_methods_by_ids(
                    self.conn, list(new_ids)
                )

        # re-score neighbors; keep top_n worth of them
        scored_neighbors = sorted(
            ((_score_hit(r, tokens), r) for r in neighbor_rows),
            key=lambda p: p[0],
            reverse=True,
        )[:top_n]

        results: list[dict] = []
        seen_ids: set[str] = set()

        def _make_entry(score: float, r: dict) -> dict:
            entry: dict = {
                "method_id": r["id"],
                "method_name": r["method_name"],
                "signature": r["signature"],
                "class_full_name": r["class_full_name"],
                "file_path": r["file_path"],
                "language": r["language"],
                "score": round(score, 2),
            }
            method = method_by_id.get(r["id"])
            if method is not None and method.is_endpoint:
                entry["endpoint"] = {
                    "http_method": method.http_method,
                    "route": method.full_route or method.route,
                }
            cls = class_by_full.get(r["class_full_name"])
            if cls is not None and cls.stereotypes:
                entry["class_stereotypes"] = cls.stereotypes
            return entry

        for score, r in top_seeds:
            seen_ids.add(r["id"])
            results.append(_make_entry(score, r))

        for score, r in scored_neighbors:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                results.append(_make_entry(score, r))

        return {
            "query": query,
            "tokens": sorted(tokens),
            "count": len(results),
            "results": results,
            "hint": "Call jidra_get_method_context on a result's method_id to drill in.",
        }

    def _methods_in_file(self, file_path: str) -> list:
        """Methods whose file matches `file_path`, tolerating absolute vs.
        repo-relative paths (one being a path-suffix of the other)."""
        target = _norm_path(file_path)
        return [m for m in self.graph.methods if _path_matches(m.file_path, target)]

    def _classes_in_file(self, file_path: str) -> list:
        target = _norm_path(file_path)
        return [c for c in self.graph.classes if _path_matches(c.file_path, target)]

    def get_file_dependents(self, file_path: str) -> dict:
        """Reverse dependencies: which files would break if this file changes.

        Walks resolved call edges whose callee lives in `file_path` back to the
        caller's file, ranked by number of call sites (most-coupled first)."""
        local = self._methods_in_file(file_path)
        local_ids = {m.id for m in local}
        method_by_id = {m.id: m for m in self.graph.methods}
        if not local:
            return {
                "file": file_path,
                "dependents": [],
                "total_dependent_files": 0,
                "total_call_sites": 0,
                "note": "No methods found for that file_path in the graph.",
            }
        by_file: dict[str, dict] = {}
        total_calls = 0
        for edge in self.graph.resolved_call_edges:
            if edge.callee_method_id not in local_ids:
                continue
            caller = method_by_id.get(edge.caller_method_id)
            if caller is None or caller.file_path in (None, ""):
                continue
            callee = method_by_id.get(edge.callee_method_id)
            entry = by_file.setdefault(
                caller.file_path, {"call_count": 0, "methods_called": set()}
            )
            entry["call_count"] += 1
            if callee is not None:
                entry["methods_called"].add(callee.method_name)
            total_calls += 1
        dependents = sorted(
            (
                {
                    "file": fp,
                    "call_count": data["call_count"],
                    "methods_called": sorted(data["methods_called"]),
                }
                for fp, data in by_file.items()
            ),
            key=lambda d: d["call_count"],
            reverse=True,
        )
        return {
            "file": file_path,
            "dependents": dependents,
            "total_dependent_files": len(dependents),
            "total_call_sites": total_calls,
        }

    def get_file_dependencies(self, file_path: str) -> dict:
        """Forward dependencies: which files this file depends on.

        Combines outgoing resolved call edges (call-level) with inheritance
        edges from classes defined in this file (extends/implements targets)."""
        local = self._methods_in_file(file_path)
        local_ids = {m.id for m in local}
        method_by_id = {m.id: m for m in self.graph.methods}
        class_by_full = {c.full_name: c for c in self.graph.classes}
        local_classes = self._classes_in_file(file_path)
        if not local and not local_classes:
            return {
                "file": file_path,
                "dependencies": [],
                "inheritance": [],
                "total_dependency_files": 0,
                "total_call_sites": 0,
                "note": "No methods or classes found for that file_path in the graph.",
            }
        self_files = {_norm_path(m.file_path) for m in local}
        by_file: dict[str, dict] = {}
        total_calls = 0
        for edge in self.graph.resolved_call_edges:
            if edge.caller_method_id not in local_ids:
                continue
            callee = method_by_id.get(edge.callee_method_id)
            if callee is None or callee.file_path in (None, ""):
                continue
            if _norm_path(callee.file_path) in self_files:
                continue  # skip intra-file calls
            entry = by_file.setdefault(
                callee.file_path, {"call_count": 0, "methods_called": set()}
            )
            entry["call_count"] += 1
            entry["methods_called"].add(callee.method_name)
            total_calls += 1
        dependencies = sorted(
            (
                {
                    "file": fp,
                    "call_count": data["call_count"],
                    "methods_called": sorted(data["methods_called"]),
                }
                for fp, data in by_file.items()
            ),
            key=lambda d: d["call_count"],
            reverse=True,
        )
        local_class_ids = {c.id for c in local_classes}
        inheritance: list[dict] = []
        seen_inh: set[tuple[str, str]] = set()
        for edge in self.graph.inheritance_edges:
            if edge.source_class_id not in local_class_ids:
                continue
            target_cls = class_by_full.get(edge.target_class)
            target_file = target_cls.file_path if target_cls else None
            key = (edge.target_class, edge.relation)
            if key in seen_inh:
                continue
            seen_inh.add(key)
            inheritance.append(
                {
                    "file": target_file,
                    "target_class": edge.target_class,
                    "relation": edge.relation,
                    "resolved": target_file is not None,
                }
            )
        dep_files = {d["file"] for d in dependencies}
        dep_files |= {i["file"] for i in inheritance if i["file"]}
        return {
            "file": file_path,
            "dependencies": dependencies,
            "inheritance": inheritance,
            "total_dependency_files": len(dep_files),
            "total_call_sites": total_calls,
        }

    def get_operation_graph(self, operation: str) -> dict:
        """Smithy operation lookup (Phase A/B): the operation's contract
        (service, http binding, input/output shape ids, errors) plus any real
        handler class bridged to it via a known codegen toolchain's naming
        convention (smithy-java, smithy4s). `operation` matches by simple
        name or full shape id (namespace#Name)."""
        operations = graph_store.load_smithy_operations(self.conn)
        match = next(
            (o for o in operations if o.id == operation or o.name == operation), None
        )
        if match is None:
            return {
                "operation": operation,
                "found": False,
                "note": "No Smithy operation matched that name/shape id.",
            }
        links = graph_store.load_smithy_operation_links(
            self.conn, operation_id=match.id
        )
        handlers = [
            {
                "class_full_name": link.class_full_name,
                "file_path": link.file_path,
                "line": link.line,
                "language": link.language,
                "codegen_profile": link.codegen_profile,
            }
            for link in links
        ]
        return {
            "operation": match.name,
            "operation_id": match.id,
            "found": True,
            "service": match.service_name,
            "http_method": match.http_method,
            "http_uri": match.http_uri,
            "input_shape_id": match.input_shape_id,
            "output_shape_id": match.output_shape_id,
            "errors": match.errors,
            "handlers": handlers,
            "handler_count": len(handlers),
        }

    def list_operations(self, service: str | None = None) -> dict:
        """All Smithy operations in the graph, optionally filtered to one
        `service` shape name."""
        operations = graph_store.load_smithy_operations(self.conn)
        if service:
            operations = [o for o in operations if o.service_name == service]
        return {
            "count": len(operations),
            "operations": [
                {
                    "name": o.name,
                    "operation_id": o.id,
                    "service": o.service_name,
                    "http_method": o.http_method,
                    "http_uri": o.http_uri,
                }
                for o in operations
            ],
        }

    def get_endpoints(self, framework: str | None = None) -> dict:
        """All HTTP endpoints in the graph (Spring/NestJS/Flask/FastAPI/Django).

        `framework` filters case-insensitively against the endpoint's framework
        role or language (e.g. "flask", "fastapi", "django", "spring", "java",
        "typescript")."""
        fw = (framework or "").lower()
        endpoints: list[dict] = []
        for m in self.graph.methods:
            if not (m.is_endpoint or m.framework_role in _ENDPOINT_ROLES):
                continue
            if (
                fw
                and fw not in (m.framework_role or "").lower()
                and fw not in (m.language or "").lower()
            ):
                continue
            endpoints.append(
                {
                    "method_id": m.id,
                    "signature": m.signature,
                    "class_full_name": m.class_full_name,
                    "http_method": m.http_method,
                    "route": m.full_route or m.route,
                    "framework_role": m.framework_role,
                    "language": m.language,
                    "file_path": m.file_path,
                }
            )
        endpoints.sort(key=lambda e: (e["route"] or "", e["http_method"] or ""))
        return {
            "framework_filter": framework,
            "count": len(endpoints),
            "endpoints": endpoints,
        }

    def get_components(self, kind: str | None = None) -> dict:
        """UI/framework components and hooks. Pulls class-level component
        stereotypes (Angular/Vue/React/NestJS) and method-level React
        components/hooks. `kind` filters by substring (e.g. "react", "angular",
        "hook", "component")."""
        k = (kind or "").lower()
        items: list[dict] = []
        for c in self.graph.classes:
            roles = [s for s in (c.stereotypes or []) if s in _COMPONENT_STEREOTYPES]
            if not roles:
                continue
            if k and not any(k in r for r in roles):
                continue
            items.append(
                {
                    "source": "class",
                    "name": c.full_name,
                    "stereotypes": roles,
                    "file_path": c.file_path,
                    "language": c.language,
                }
            )
        for m in self.graph.methods:
            role = m.framework_role
            if role not in ("component", "hook"):
                continue
            if k and k not in role and k != "react":
                continue
            items.append(
                {
                    "source": "method",
                    "name": m.method_name,
                    "framework_role": role,
                    "file_path": m.file_path,
                    "language": m.language,
                }
            )
        return {"kind_filter": kind, "count": len(items), "components": items}

    def get_framework_summary(self) -> dict:
        """Discovery counts: framework roles, class stereotypes, languages."""
        from collections import Counter

        roles = Counter(
            m.framework_role for m in self.graph.methods if m.framework_role
        )
        stereotypes: Counter = Counter()
        for c in self.graph.classes:
            stereotypes.update(c.stereotypes or [])
        languages = Counter(m.language for m in self.graph.methods)
        endpoint_total = sum(
            1
            for m in self.graph.methods
            if m.is_endpoint or m.framework_role in _ENDPOINT_ROLES
        )
        return {
            "endpoints_total": endpoint_total,
            "framework_roles": dict(roles.most_common()),
            "class_stereotypes": dict(stereotypes.most_common()),
            "languages": dict(languages.most_common()),
        }


# Method-level framework roles that mark an HTTP endpoint across languages.
_ENDPOINT_ROLES = {"http_handler", "flask_route", "fastapi_route", "django_handler"}
# Class stereotypes that denote a UI/framework component.
_COMPONENT_STEREOTYPES = {
    "react_component",
    "vue_component",
    "angular_component",
    "component",
    "react_context",
    "vue_store",
    "vue_composable",
}


def _norm_path(path: str | None) -> str:
    return (path or "").replace("\\", "/").strip()


def _path_matches(stored: str | None, target_norm: str) -> bool:
    """True if `stored` refers to the same file as the (normalized) target,
    allowing either to be a path-suffix of the other (absolute vs. relative)."""
    s = _norm_path(stored)
    if not s or not target_norm:
        return False
    if s == target_norm:
        return True
    return s.endswith("/" + target_norm) or target_norm.endswith("/" + s)


_engine_cache: dict[tuple[str, str], tuple["JidraEngine", float]] = {}


def _db_fingerprint(db_path: Path) -> float:
    """Latest mtime across the main db file and its WAL sidecar.

    In WAL mode (which `graph_store.connect` always enables), writes land in
    `<db>-wal` and the main file's mtime doesn't change until a checkpoint —
    so the main file alone is not a reliable change signal.
    """
    best = -1.0
    for path in (db_path, Path(str(db_path) + "-wal")):
        try:
            best = max(best, path.stat().st_mtime)
        except OSError:
            pass
    return best


def get_engine(graph_path: str, variant: str = "validated") -> "JidraEngine":
    """Return a cached `JidraEngine` for `(graph_path, variant)`, reloading
    only if the underlying graph.db has changed since it was cached.

    `JidraEngine.__init__` materializes the entire graph into Python objects
    — fine once, wasteful if repeated on every MCP tool call within the same
    long-lived server session. This makes that load happen once per session
    and only again after a real write (e.g. `jidra_reindex`), not per call.
    """
    resolved = str(Path(graph_path).resolve())
    db_path = graph_store.resolve_graph_db_path(Path(resolved))
    fingerprint = _db_fingerprint(db_path)
    key = (resolved, variant)
    cached = _engine_cache.get(key)
    if cached is not None and cached[1] == fingerprint:
        return cached[0]
    engine = JidraEngine(resolved, variant=variant)
    _engine_cache[key] = (engine, fingerprint)
    return engine
