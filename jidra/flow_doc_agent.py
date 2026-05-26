from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .engine import JidraEngine


def _tier_priority(tier: str | None) -> int:
    t = (tier or "").lower()
    if t == "primary":
        return 0
    if t == "supporting":
        return 1
    if t == "utility":
        return 2
    return 3


def _line_range(line_start, line_end) -> str:
    if line_start and line_end:
        return f"{line_start}-{line_end}"
    if line_start:
        return str(line_start)
    return "unknown"


NOISY_CALL_NAMES = {
    "debug",
    "info",
    "warn",
    "error",
    "trace",
    "append",
    "appendentries",
    "increment",
    "decrement",
    "recordexecutiontime",
    "toString".lower(),
    "hashCode".lower(),
    "getclass",
    "getmessage",
    "getsimplename",
    "addall",
    "orElse".lower(),
    "orElseGet".lower(),
    "orElseThrow".lower(),
    "map",
    "flatmap",
    "filter",
    "stream",
    "collect",
    "decrementCounter",
    "getMarker",
}

JDK_UTILITY_TYPES = {"Optional", "Mono", "Flux", "String", "UUID", "StringBuilder"}

NOISY_TYPE_PREFIXES = (
    "java.",
    "javax.",
    "jakarta.",
    "org.slf4j.",
    "ch.qos.logback.",
    "reactor.",
    "io.micrometer.",
    "net.logstash.",
    "org.apache.commons.",
    # Project-specific package prefixes should be configured externally.
    # "com.myco.metrics",   # example
)


@dataclass
class _ExpandCandidate:
    method_id: str
    signature: str
    tier: str
    rank_score: float
    rank_reason: list[str]


class FlowDocAgent:
    def __init__(
        self,
        engine: JidraEngine,
        *,
        max_subflows: int = 8,
        flow_depth: int = 4,
        top_n: int = 8,
        max_context_chars: int = 12000,
        include_utility: bool = False,
        mind_map_mode: bool = False,
        include_details: bool = False,
        max_nodes: int = 200,
    ):
        self.engine = engine
        self.max_subflows = max_subflows
        self.flow_depth = flow_depth
        self.top_n = top_n
        self.max_context_chars = max_context_chars
        self.include_utility = include_utility
        self.mind_map_mode = mind_map_mode
        self.include_details = include_details
        self.max_nodes = max(1, int(max_nodes))
        self.progress_ui = None  # type: ignore[assignment]
        self.flow_exclude = self._load_flow_exclude_config()
        self.use_fallback_excludes = not bool(self.flow_exclude)

    def _load_flow_exclude_config(self) -> dict:
        candidates = [
            Path(__file__).resolve().parent / "config.yaml",
            Path(__file__).resolve().parent.parent / "config.yaml",
        ]
        for p in candidates:
            if not p.exists():
                continue
            try:
                import yaml

                raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                flow = raw.get("flow", {}) if isinstance(raw, dict) else {}
                exclude = flow.get("exclude", {}) if isinstance(flow, dict) else {}
                if isinstance(exclude, dict):
                    return exclude
            except Exception:
                continue
        return {}

    def _config_exclude_match(
        self, signature: str, file_path: str = ""
    ) -> tuple[bool, str]:
        rules = self.flow_exclude or {}
        if signature in (rules.get("signatures") or []):
            return True, "signature"
        for pkg in rules.get("package_contains") or []:
            if pkg and (pkg in signature or pkg in file_path):
                return True, f"package:{pkg}"
        class_part = signature.split("#", 1)[0]
        class_name = class_part.rsplit(".", 1)[-1]
        for pat in rules.get("class_name_patterns") or []:
            try:
                if (
                    re.search(pat, class_name)
                    or re.search(pat, class_part)
                    or re.search(pat, signature)
                ):
                    return True, f"class_pattern:{pat}"
            except re.error:
                continue
        method_name = (
            signature.split("#", 1)[-1].split("(", 1)[0]
            if "#" in signature
            else signature
        )
        for pat in rules.get("method_name_patterns") or []:
            try:
                if re.search(pat, method_name) or re.search(pat, signature):
                    return True, f"method_pattern:{pat}"
            except re.error:
                continue
        return False, ""

    def _group_for_signature(self, signature: str) -> str:
        s = (signature or "").lower()
        if ".controller." in s:
            return "controller"
        if ".service." in s:
            return "service"
        if ".processor." in s:
            return "processor"
        if ".cache." in s:
            return "cache"
        if ".repository." in s:
            return "repository"
        if ".response." in s or ".factory." in s:
            return "response"
        if ".utils." in s or ".util." in s:
            return "utils"
        if ".events." in s:
            return "events"
        return "other"

    def _build_mind_map(self, entry_id: str) -> dict:
        method_map = {m.id: m for m in self.engine.graph.methods}
        outgoing: dict[str, list[str]] = {}
        for edge in self.engine.graph.resolved_call_edges:
            outgoing.setdefault(edge.caller_method_id, []).append(edge.callee_method_id)
        for src, targets in outgoing.items():

            def _sort_key(method_id: str) -> str:
                m = method_map.get(method_id)
                sig = getattr(m, "signature", "") if m is not None else ""
                return sig or method_id

            outgoing[src] = sorted(set(targets), key=_sort_key)

        nodes: list[dict] = []
        edges: list[tuple[str, str]] = []
        unresolved_rows: list[dict] = []
        excluded_summary: dict[str, int] = {}
        depth_by_id: dict[str, int] = {}
        queue: list[tuple[str, int]] = [(entry_id, 0)]
        seen: set[str] = set()

        callsites_by_method: dict[str, list] = {}
        for c in self.engine.graph.callsites:
            callsites_by_method.setdefault(c.caller_method_id, []).append(c)

        while queue and len(seen) < self.max_nodes:
            current, depth = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            depth_by_id[current] = depth
            m = method_map.get(current)
            if not m:
                continue
            cfg_excluded, cfg_reason = self._config_exclude_match(
                m.signature, m.file_path or ""
            )
            if cfg_excluded and current != entry_id:
                key = f"method:{cfg_reason}"
                excluded_summary[key] = excluded_summary.get(key, 0) + 1
                continue
            tier = "utility"
            sig_l = m.signature.lower()
            if ".controller." in sig_l or ".service." in sig_l:
                tier = "primary"
            elif ".components." in sig_l or ".processor." in sig_l:
                tier = "supporting"
            if (not self.include_utility) and tier == "utility" and current != entry_id:
                excluded_summary["method:tier_utility"] = (
                    excluded_summary.get("method:tier_utility", 0) + 1
                )
                continue
            nodes.append(
                {
                    "method_id": m.id,
                    "signature": m.signature,
                    "file_path": m.file_path,
                    "line_start": m.start_line,
                    "line_end": m.end_line,
                    "depth": depth,
                    "tier": tier,
                    "group": self._group_for_signature(m.signature),
                }
            )
            resolved_targets = outgoing.get(current, [])
            if depth < self.flow_depth:
                for target in resolved_targets:
                    tm = method_map.get(target)
                    if not tm:
                        continue
                    edges.append((current, target))
                    if target not in seen and len(seen) + len(queue) < self.max_nodes:
                        queue.append((target, depth + 1))

            for c in sorted(
                callsites_by_method.get(current, []),
                key=lambda x: (x.line, x.column, x.id),
            ):
                if c.resolution_status == "resolved":
                    continue
                unresolved_rows.append(
                    {
                        "from_method_id": current,
                        "receiver": c.receiver or "",
                        "receiver_type": c.receiver_type
                        or c.receiver_type_normalized
                        or "",
                        "call": c.callee_name,
                        "line": c.line,
                        "reason": c.resolution_reason or c.resolution_status,
                    }
                )

        nodes.sort(key=lambda n: (n["depth"], n["group"], n["signature"]))
        unique_edges = sorted(
            set(edges), key=lambda e: (depth_by_id.get(e[0], 999), e[0], e[1])
        )
        id_map: dict[str, str] = {}
        for i, n in enumerate(nodes, start=1):
            id_map[n["method_id"]] = f"M{i}"
        # apply config-based unresolved exclusion if config exists; fallback to hardcoded behavior otherwise
        if self.flow_exclude:
            kept = []
            for row in unresolved_rows:
                probe = str(row.get("receiver_type") or row.get("receiver") or "")
                ex, reason = self._config_exclude_match(probe, "")
                if ex:
                    key = f"unresolved:{reason}"
                    excluded_summary[key] = excluded_summary.get(key, 0) + 1
                else:
                    kept.append(row)
            unresolved_rows = kept
        return {
            "nodes": nodes,
            "edges": unique_edges,
            "id_map": id_map,
            "unresolved_calls": unresolved_rows,
            "excluded_summary": excluded_summary,
        }

    def prepare(self, method: str) -> dict:
        entry = self._resolve_entry(method)
        if entry.get("error"):
            return {"error": entry["error"]}
        root_flow = self.engine.get_agent_flow(
            method, depth=self.flow_depth, top_n=self.top_n
        )
        if root_flow.get("error"):
            return {"error": root_flow["error"]}
        queue = self._build_expansion_queue(
            root_flow, str(entry.get("method_id") or "")
        )
        return {"entry": entry, "root_flow": root_flow, "queue": queue}

    def _ui_update(
        self, name: str, state: str, phase: str = "", error: str | None = None
    ) -> None:
        if self.progress_ui is None:
            return
        self.progress_ui.update(name=name, state=state, phase=phase, error=error)

    def _resolve_entry(self, method: str) -> dict:
        src = self.engine.get_method_source(method)
        if src.get("error"):
            return {"error": src["error"]}
        return src

    def _build_expansion_queue(
        self, root_flow: dict, root_id: str
    ) -> list[_ExpandCandidate]:
        top_nodes = list(root_flow.get("top_nodes", []))
        items: list[_ExpandCandidate] = []
        seen: set[str] = set()
        for node in top_nodes:
            method_id = str(node.get("method_id") or "")
            if not method_id or method_id == root_id or method_id in seen:
                continue
            seen.add(method_id)
            tier = str(node.get("tier") or "unknown")
            if (not self.include_utility) and tier == "utility":
                continue
            items.append(
                _ExpandCandidate(
                    method_id=method_id,
                    signature=str(node.get("signature") or method_id),
                    tier=tier,
                    rank_score=float(node.get("rank_score", 0.0) or 0.0),
                    rank_reason=list(node.get("rank_reason") or []),
                )
            )
        items.sort(key=lambda x: (_tier_priority(x.tier), -x.rank_score, x.signature))
        return items[: self.max_subflows]

    def _merge_important_unresolved(
        self, root_flow: dict, expanded_methods: list[dict]
    ) -> list[dict]:
        merged: list[dict] = []
        seen = set()
        sources = [root_flow.get("important_unresolved_calls", [])]
        sources.extend(
            item.get("important_unresolved_calls", []) for item in expanded_methods
        )
        for src in sources:
            for item in src:
                key = (
                    item.get("from_method_id"),
                    item.get("receiver"),
                    item.get("call"),
                    item.get("line"),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= 30:
                    return merged
        return merged

    def _is_project_type(self, receiver_type: str | None) -> bool:
        if not receiver_type:
            return False
        text = str(receiver_type).strip()
        if not text:
            return False
        lower = text.lower()
        if any(lower.startswith(prefix) for prefix in NOISY_TYPE_PREFIXES):
            return False
        simple = text.split("<", 1)[0].split(".")[-1]
        if simple in JDK_UTILITY_TYPES:
            return False
        return "." in text

    def _is_noise_important_unresolved(self, item: dict) -> tuple[bool, str]:
        call = str(item.get("call") or "").strip()
        call_l = call.lower()
        receiver = str(item.get("receiver") or "")
        receiver_type = str(item.get("receiver_type") or "")

        if not call:
            return True, "empty_call"
        if call_l in NOISY_CALL_NAMES:
            return True, f"call:{call_l}"

        receiver_blob = f"{receiver} {receiver_type}".lower()
        if (
            "marker" in receiver_blob
            or "metrics" in receiver_blob
            or "logger" in receiver_blob
            or "log" in receiver_blob
        ):
            return True, "receiver_noise"

        simple_type = (
            receiver_type.split("<", 1)[0].split(".")[-1] if receiver_type else ""
        )
        if simple_type in JDK_UTILITY_TYPES:
            return True, f"utility_type:{simple_type}"
        if any(
            receiver_type.lower().startswith(prefix) for prefix in NOISY_TYPE_PREFIXES
        ):
            return True, f"type_prefix:{receiver_type}"

        # builder-chain setters: hide unless likely project class and we have possible targets
        is_setter = call.startswith("set") and len(call) > 3 and call[3].isupper()
        if is_setter:
            possible = item.get("possible_targets") or []
            if not (self._is_project_type(receiver_type) and len(possible) > 0):
                return True, "setter_chain"

        return False, ""

    def _filter_important_unresolved(
        self, items: list[dict]
    ) -> tuple[list[dict], int, dict]:
        out = []
        suppressed = 0
        by_reason: dict[str, int] = {}
        for item in items:
            if self.flow_exclude:
                probe = str(item.get("receiver_type") or item.get("receiver") or "")
                ex, reason = self._config_exclude_match(probe, "")
                if ex:
                    suppressed += 1
                    by_reason[f"config:{reason}"] = (
                        by_reason.get(f"config:{reason}", 0) + 1
                    )
                    continue
            else:
                ex, reason = self._is_noise_important_unresolved(item)
                if ex:
                    suppressed += 1
                    by_reason[f"fallback:{reason}"] = (
                        by_reason.get(f"fallback:{reason}", 0) + 1
                    )
                    continue
            out.append(item)
            if len(out) >= 30:
                break
        return out, suppressed, by_reason

    def _build_suggested_next_methods(
        self, root_flow: dict, expanded_methods: list[dict]
    ) -> list[dict]:
        out = []
        seen = set()

        def add_node(node: dict, reason: str) -> None:
            method_id = node.get("method_id")
            signature = node.get("signature")
            if not method_id and not signature:
                return
            key = (method_id, signature)
            if key in seen:
                return
            seen.add(key)
            out.append(
                {
                    "method_id": method_id,
                    "signature": signature,
                    "tier": node.get("tier", "unknown"),
                    "rank_score": float(node.get("rank_score", 0.0) or 0.0),
                    "file_path": node.get("file_path"),
                    "line_start": node.get("line_start"),
                    "line_end": node.get("line_end"),
                    "reason": reason,
                }
            )

        for node in root_flow.get("top_nodes", []):
            add_node(node, "root_top_node")
        for item in expanded_methods:
            for node in item.get("subflow_top_nodes", []):
                add_node(node, f"subflow_top_node:{item.get('method_id')}")
        for item in [root_flow] + expanded_methods:
            for unresolved in item.get("important_unresolved_calls", []):
                for target in unresolved.get("possible_targets", [])[:5]:
                    add_node(
                        {
                            "method_id": target.get("method_id"),
                            "signature": target.get("signature"),
                            "tier": "supporting",
                            "rank_score": 0.0,
                            "file_path": target.get("file_path"),
                            "line_start": target.get("line_start"),
                            "line_end": target.get("line_end"),
                        },
                        "possible_target_from_unresolved",
                    )

        out.sort(
            key=lambda x: (
                _tier_priority(x.get("tier")),
                -x.get("rank_score", 0.0),
                x.get("signature") or "",
            )
        )
        return out[:15]

    def build(self, method: str) -> dict:
        prepared = self.prepare(method)
        if prepared.get("error"):
            return {"error": prepared["error"]}
        entry = prepared["entry"]

        self._ui_update("root", "extracting", phase="root_flow")
        root_flow = prepared["root_flow"]
        if root_flow.get("error"):
            self._ui_update("root", "failed", error=str(root_flow["error"]))
            return {"error": root_flow["error"]}
        self._ui_update("root", "enriched", phase="root_flow_done")

        queue = prepared["queue"]
        expanded: list[dict] = []
        mind_map = None
        if self.mind_map_mode:
            mind_map = self._build_mind_map(str(entry.get("method_id") or ""))

        method_map = {m.id: m for m in self.engine.graph.methods}

        for candidate in queue:
            if self.mind_map_mode and (not self.include_details):
                break
            slot_name = (
                candidate.signature.split("#", 1)[-1]
                if "#" in candidate.signature
                else candidate.method_id
            )
            self._ui_update(slot_name, "extracting", phase="subflow")
            subflow = self.engine.get_agent_flow(
                candidate.method_id, depth=self.flow_depth, top_n=self.top_n
            )
            context = self.engine.get_method_context(
                candidate.method_id, max_chars=self.max_context_chars
            )
            if subflow.get("error") or context.get("error"):
                self._ui_update(
                    slot_name,
                    "failed",
                    error=str(subflow.get("error") or context.get("error")),
                )
                continue
            subflow_important, _, _ = self._filter_important_unresolved(
                list(subflow.get("important_unresolved_calls", []))
            )

            src = method_map.get(candidate.method_id)
            signature = candidate.signature
            file_path = None
            line_start = None
            line_end = None
            if src:
                signature = src.signature
                file_path = src.file_path
                line_start = src.start_line
                line_end = src.end_line
            elif subflow.get("entry"):
                signature = subflow["entry"].get("signature") or signature
                file_path = (
                    subflow.get("top_nodes", [{}])[0].get("file_path")
                    if subflow.get("top_nodes")
                    else None
                )
                line_start = (
                    subflow.get("top_nodes", [{}])[0].get("line_start")
                    if subflow.get("top_nodes")
                    else None
                )
                line_end = (
                    subflow.get("top_nodes", [{}])[0].get("line_end")
                    if subflow.get("top_nodes")
                    else None
                )

            expanded.append(
                {
                    "method_id": candidate.method_id,
                    "signature": signature,
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "tier": candidate.tier,
                    "rank_score": candidate.rank_score,
                    "rank_reason": candidate.rank_reason,
                    "resolved_callees": list(context.get("resolved_callees", [])),
                    "unresolved_calls": list(context.get("unresolved_calls", [])),
                    "referenced_fields": list(context.get("referenced_fields", [])),
                    "relevant_imports": list(context.get("relevant_imports", [])),
                    "important_unresolved_calls": subflow_important,
                    "stopped_path_summary": dict(
                        subflow.get("stopped_path_summary", {})
                    ),
                    "uncertain_edge_summary": dict(
                        subflow.get("uncertain_edge_summary", {})
                    ),
                    "subflow_top_nodes": list(subflow.get("top_nodes", [])),
                }
            )
            self._ui_update(slot_name, "enriched", phase="subflow_done")

        merged_unresolved = self._merge_important_unresolved(root_flow, expanded)
        important_unresolved, suppressed_count, suppressed_by_reason = (
            self._filter_important_unresolved(merged_unresolved)
        )
        suggested_next = self._build_suggested_next_methods(root_flow, expanded)

        return {
            "entry": {
                "method_id": entry.get("method_id"),
                "signature": entry.get("signature"),
                "file_path": entry.get("file_path"),
                "line_start": entry.get("line_start"),
                "line_end": entry.get("line_end"),
            },
            "root_flow": root_flow,
            "expanded_methods": expanded,
            "important_unresolved_calls": important_unresolved,
            "excluded_noise_summary": {
                "important_unresolved_suppressed": suppressed_count,
                "suppressed_by_reason": suppressed_by_reason,
                "mind_map_excluded_summary": (mind_map or {}).get(
                    "excluded_summary", {}
                ),
            },
            "suggested_next_methods": suggested_next,
            "limits": [
                "static_analysis_only",
                "runtime_dispatch_not_verified",
                "config_and_template_context_not_included",
                f"max_subflows={self.max_subflows}",
                f"flow_depth={self.flow_depth}",
                f"mind_map_mode={self.mind_map_mode}",
                f"max_nodes={self.max_nodes}",
            ],
            "mind_map": mind_map,
        }

    def render_markdown(self, result: dict) -> str:
        entry = result.get("entry", {})
        root_flow = result.get("root_flow", {})
        expanded = result.get("expanded_methods", [])
        unresolved = result.get("important_unresolved_calls", [])
        excluded_noise_summary = result.get("excluded_noise_summary", {})
        suggested = result.get("suggested_next_methods", [])
        limits = result.get("limits", [])
        mind_map = result.get("mind_map")

        lines: list[str] = []
        lines.append(f"# Flow Investigation: {entry.get('signature', 'unknown')}")
        lines.append("")
        lines.append("## Entry")
        lines.append(f"- method_id: {entry.get('method_id', '')}")
        lines.append(f"- signature: `{entry.get('signature', '')}`")
        lines.append(
            f"- file/lines: `{entry.get('file_path', '')}:{_line_range(entry.get('line_start'), entry.get('line_end'))}`"
        )
        lines.append("")
        if self.mind_map_mode and mind_map:
            lines.append("## Mind Map (Mermaid)")
            lines.append("```mermaid")
            lines.append("flowchart TD")
            for node in mind_map.get("nodes", []):
                mid = mind_map.get("id_map", {}).get(node.get("method_id"), "")
                lines.append(
                    f'  {mid}["{mid} d{node.get("depth")} {node.get("tier")}"]'
                )
            for src, dst in mind_map.get("edges", []):
                sid = mind_map.get("id_map", {}).get(src, "")
                did = mind_map.get("id_map", {}).get(dst, "")
                if sid and did:
                    lines.append(f"  {sid} --> {did}")
            lines.append("```")
            lines.append("")
            lines.append("## Mind Map (Tree)")
            by_depth = {}
            for node in mind_map.get("nodes", []):
                by_depth.setdefault(node.get("depth", 0), []).append(node)
            for depth in sorted(by_depth):
                for node in sorted(
                    by_depth[depth],
                    key=lambda n: (n.get("group", ""), n.get("signature", "")),
                ):
                    mid = mind_map.get("id_map", {}).get(node.get("method_id"), "")
                    indent = "  " * int(depth)
                    lines.append(
                        f"{indent}- `{mid}` `{node.get('signature', '')}` `{node.get('file_path', '')}:{_line_range(node.get('line_start'), node.get('line_end'))}` depth={node.get('depth')} tier={node.get('tier')}"
                    )
            lines.append("")
            lines.append("## Mind Map Legend")
            lines.append("| node_id | signature | file:lines | depth | tier |")
            lines.append("|---|---|---|---:|---|")
            for node in mind_map.get("nodes", []):
                mid = mind_map.get("id_map", {}).get(node.get("method_id"), "")
                lines.append(
                    f"| `{mid}` | `{node.get('signature', '')}` | `{node.get('file_path', '')}:{_line_range(node.get('line_start'), node.get('line_end'))}` | {node.get('depth')} | {node.get('tier')} |"
                )
            lines.append("")
            lines.append("## Unresolved Calls")
            lines.append(
                "| from method | receiver | receiver_type | call | line | reason |"
            )
            lines.append("|---|---|---|---|---:|---|")
            for c in mind_map.get("unresolved_calls", []):
                lines.append(
                    f"| `{c.get('from_method_id', '')}` | `{c.get('receiver', '')}` | `{c.get('receiver_type', '')}` | `{c.get('call', '')}` | {c.get('line') or ''} | `{c.get('reason', '')}` |"
                )
            if not mind_map.get("unresolved_calls"):
                lines.append("| - | - | - | - | - | - |")
            lines.append("")
        lines.append("## Root Flow")
        lines.append("| depth | tier | method | file:lines | rank |")
        lines.append("|---:|---|---|---|---:|")
        for n in root_flow.get("top_nodes", []):
            lines.append(
                f"| {n.get('depth', '')} | {n.get('tier', '')} | `{n.get('signature', '')}` | "
                f"`{n.get('file_path', '')}:{_line_range(n.get('line_start'), n.get('line_end'))}` | {n.get('rank_score', 0)} |"
            )
        if not root_flow.get("top_nodes"):
            lines.append("| - | - | - | - | - |")
        lines.append("")
        if (not self.mind_map_mode) or self.include_details:
            lines.append("## Expanded Subflows")
            if not expanded:
                lines.append("_No expanded methods selected._")
                lines.append("")
        for item in (
            expanded if ((not self.mind_map_mode) or self.include_details) else []
        ):
            lines.append(f"### {item.get('signature', '')}")
            lines.append(f"- method_id: {item.get('method_id', '')}")
            lines.append(f"- file: `{item.get('file_path', '')}`")
            lines.append(
                f"- lines: `{_line_range(item.get('line_start'), item.get('line_end'))}`"
            )
            lines.append(f"- tier: {item.get('tier', '')}")
            lines.append(f"- rank: {item.get('rank_score', 0)}")
            lines.append(f"- why selected: {'; '.join(item.get('rank_reason', []))}")
            lines.append(
                f"- referenced fields: {', '.join(item.get('referenced_fields', [])) or '(none)'}"
            )
            lines.append(
                f"- relevant imports: {', '.join(item.get('relevant_imports', [])) or '(none)'}"
            )
            lines.append("")
            lines.append("#### Resolved Callees")
            lines.append("| call | target | lines |")
            lines.append("|---|---|---|")
            resolved = item.get("resolved_callees", [])
            for c in resolved:
                lines.append(
                    f"| `{c.get('call', '')}` | `{c.get('target_signature') or c.get('target_id') or ''}` | `{c.get('lines', [])}` |"
                )
            if not resolved:
                lines.append("| - | - | - |")
            lines.append("")
            lines.append("#### Unresolved Calls")
            lines.append("| receiver | call | reason | count |")
            lines.append("|---|---|---|---:|")
            unresolved_calls = item.get("unresolved_calls", [])
            for c in unresolved_calls:
                lines.append(
                    f"| `{c.get('receiver', '')}` | `{c.get('call', '')}` | `{c.get('reason', '')}` | {c.get('count', 1)} |"
                )
            if not unresolved_calls:
                lines.append("| - | - | - | - |")
            lines.append("")
            lines.append("#### Important Unresolved Calls")
            lines.append(
                "| receiver | receiver_type | call | line | possible targets |"
            )
            lines.append("|---|---|---|---:|---|")
            imp = item.get("important_unresolved_calls", [])
            for c in imp:
                targets = (
                    ", ".join(
                        t.get("signature", "")
                        for t in c.get("possible_targets", [])[:3]
                    )
                    or "-"
                )
                lines.append(
                    f"| `{c.get('receiver', '')}` | `{c.get('receiver_type') or ''}` | `{c.get('call', '')}` | "
                    f"{c.get('line') or ''} | {targets} |"
                )
            if not imp:
                lines.append("| - | - | - | - | - |")
            lines.append("")
            lines.append("#### Subflow Uncertainty")
            lines.append(
                f"- uncertain edge summary: `{item.get('uncertain_edge_summary', {})}`"
            )
            lines.append(
                f"- stopped path summary: `{item.get('stopped_path_summary', {})}`"
            )
            lines.append("")

        lines.append("## Important Unresolved Calls")
        lines.append(
            f"_Excluded Noise Summary: suppressed={excluded_noise_summary.get('important_unresolved_suppressed', 0)}_"
        )
        by_reason = excluded_noise_summary.get("suppressed_by_reason", {}) or {}
        mm_summary = excluded_noise_summary.get("mind_map_excluded_summary", {}) or {}
        if by_reason:
            lines.append(f"- suppressed_by_reason: `{by_reason}`")
        if mm_summary:
            lines.append(f"- mind_map_excluded_summary: `{mm_summary}`")
        lines.append(
            "| from method | receiver | receiver_type | call | line | possible_targets |"
        )
        lines.append("|---|---|---|---|---:|---|")
        for c in unresolved:
            targets = (
                ", ".join(
                    t.get("signature", "") for t in c.get("possible_targets", [])[:3]
                )
                or "-"
            )
            lines.append(
                f"| `{c.get('from_method_id', '')}` | `{c.get('receiver', '')}` | `{c.get('receiver_type') or ''}` | "
                f"`{c.get('call', '')}` | {c.get('line') or ''} | {targets} |"
            )
        if not unresolved:
            lines.append("| - | - | - | - | - | - |")
        lines.append("")

        lines.append("## Suggested Next Debug Locations")
        lines.append("| method | reason | file:lines |")
        lines.append("|---|---|---|")
        for s in suggested:
            lines.append(
                f"| `{s.get('signature', '')}` | `{s.get('reason', '')}` | "
                f"`{s.get('file_path', '')}:{_line_range(s.get('line_start'), s.get('line_end'))}` |"
            )
        if not suggested:
            lines.append("| - | - | - |")
        lines.append("")
        lines.append("## Limits / Uncertainty")
        for limit in limits:
            lines.append(f"- {limit}")
        return "\n".join(lines) + "\n"
