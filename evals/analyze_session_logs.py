"""Offline analysis of JIDRA MCP session logs (.jidra/session_log.jsonl).

Standalone analysis tool — not wired into the MCP server or CLI.

For each `jidra_get_agent_flow` call, finds the `jidra_get_method_source`
calls that follow it (within the same session, up to the next
`jidra_get_agent_flow` call) and checks whether the expanded method was
ranked "utility" tier by flow_stitcher.py's static ranking heuristic.

A high rate of agents expanding low-ranked ("utility") nodes is a signal
that the ranking heuristic may need retuning — the agent is reaching past
what the heuristic considered the important nodes.

Usage:
    python scripts/analyze_session_logs.py --graph path/to/graph.db \\
        path/to/session_log.jsonl [more_session_logs.jsonl ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jidra.engine.engine import JidraEngine


def _read_log(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _tier_for_method(engine: JidraEngine, entry_method_id: str, method_id: str) -> str:
    try:
        flow = engine.get_agent_flow(method=entry_method_id)
    except Exception as exc:
        return f"error:{exc}"
    if flow.get("error"):
        return "error:flow_lookup_failed"
    for node in flow.get("top_nodes", []):
        if node.get("method_id") == method_id:
            return str(node.get("tier") or "unknown")
    return "not_in_flow"


def analyze(log_paths: list[Path], graph_path: str) -> dict:
    engine = JidraEngine(graph_path)

    expansions = []  # list of (entry_method_id, expanded_method_id, tier)

    for log_path in log_paths:
        entries = _read_log(log_path)
        current_entry_method_id: str | None = None
        for entry in entries:
            tool_name = entry.get("tool_name")
            if tool_name == "jidra_get_agent_flow":
                current_entry_method_id = entry.get("method_id")
                continue
            if tool_name == "jidra_get_method_source" and current_entry_method_id:
                method_id = entry.get("method_id")
                if not method_id:
                    continue
                tier = _tier_for_method(engine, current_entry_method_id, method_id)
                expansions.append((current_entry_method_id, method_id, tier))

    tier_counts: dict[str, int] = {}
    for _, _, tier in expansions:
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    return {
        "total_expansions": len(expansions),
        "tier_counts": tier_counts,
        "expansions": expansions,
    }


def format_report(result: dict) -> str:
    total = result["total_expansions"]
    if total == 0:
        return "No jidra_get_agent_flow -> jidra_get_method_source expansions found in the provided logs."

    lines = [
        "JIDRA Session Log Analysis",
        "=" * 40,
        f"Total expansions analyzed: {total}",
        "",
        "Breakdown by ranking tier:",
    ]
    for tier, count in sorted(result["tier_counts"].items(), key=lambda kv: -kv[1]):
        pct = 100 * count / total
        lines.append(f"  {tier:<20} {count:>4}  ({pct:5.1f}%)")

    utility_count = result["tier_counts"].get("utility", 0)
    utility_pct = 100 * utility_count / total
    lines.append("")
    lines.append(
        f"Agents expanded 'utility'-tier nodes in {utility_pct:.1f}% of cases."
    )
    if utility_pct > 25:
        lines.append(
            "This is a meaningful share — consider retuning the static ranking heuristic."
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", help="Path(s) to session_log.jsonl files")
    parser.add_argument(
        "--graph", required=True, help="Path to the graph.db used during the session"
    )
    args = parser.parse_args()

    log_paths = [Path(p) for p in args.logs]
    result = analyze(log_paths, args.graph)
    print(format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
