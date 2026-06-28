#!/usr/bin/env python3
"""
JIDRA Token & Cost Validation

Measures real token savings via Claude API — traditional (raw source) vs
JIDRA (graph context). Proves the 87-95% token reduction claim on your
own codebase.

Usage:
    # Pass methods inline
    ANTHROPIC_API_KEY=... python validations/run_validation.py \
        --graph /path/to/.jidra/graph.db \
        --codebase /path/to/your-repo \
        --methods "OrderController.createOrder,PaymentService.charge"

    # Pass methods via file (one per line or JSON array)
    ANTHROPIC_API_KEY=... python validations/run_validation.py \
        --graph /path/to/.jidra/graph.db \
        --codebase /path/to/your-repo \
        --methods-file my_methods.txt

    # Auto-discover endpoints from the graph
    ANTHROPIC_API_KEY=... python validations/run_validation.py \
        --graph /path/to/.jidra/graph.db \
        --codebase /path/to/your-repo \
        --auto-discover --discover-limit 5

Methods file format (one per line):
    ClassName.methodName
    com.example.ClassName.methodName
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jidra.cost_calculator import analyze_method_online, format_method_proof


def _load_methods_from_file(path: str) -> list[str]:
    text = Path(path).read_text().strip()
    if text.startswith("["):
        return [m.strip() for m in json.loads(text) if m.strip()]
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _auto_discover_methods(graph_path: Path, limit: int) -> list[str]:
    nodes = [
        json.loads(line) for line in graph_path.read_text().splitlines() if line.strip()
    ]
    method_nodes = [n for n in nodes if n.get("node_type") == "method"]
    endpoints = [n for n in method_nodes if n.get("is_endpoint")]
    candidates = sorted(
        endpoints or method_nodes, key=lambda n: len(n.get("calls", [])), reverse=True
    )
    results = []
    for node in candidates[: limit * 3]:
        qn = node.get("qualified_name", "")
        if "#" in qn:
            class_name = qn.split("#")[0].split(".")[-1]
            method_name = qn.split("#")[1].split("(")[0]
            selector = f"{class_name}.{method_name}"
            if selector not in results:
                results.append(selector)
        if len(results) >= limit:
            break
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JIDRA token & cost validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--graph", required=True, help="Path to graph.db")
    parser.add_argument("--codebase", required=True, help="Path to Java repo root")

    method_group = parser.add_mutually_exclusive_group()
    method_group.add_argument(
        "--methods",
        help="Comma-separated method selectors, e.g. 'OrderController.createOrder,PaymentService.charge'",
    )
    method_group.add_argument(
        "--methods-file",
        metavar="FILE",
        help="Path to file with method selectors (one per line or JSON array)",
    )
    method_group.add_argument(
        "--auto-discover",
        action="store_true",
        help="Auto-discover methods from the graph (prefers REST endpoints)",
    )

    parser.add_argument("--discover-limit", type=int, default=5)
    parser.add_argument(
        "--model", default="claude-opus-4-7", help="Claude model to use"
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=500,
        help="Annual query estimate for savings projection",
    )
    parser.add_argument("--output", help="Write JSON results to this file")
    args = parser.parse_args()

    graph_path = Path(args.graph).resolve()
    codebase_path = Path(args.codebase).resolve()

    if not graph_path.exists():
        sys.exit(f"Graph not found: {graph_path}")
    if not codebase_path.exists():
        sys.exit(f"Codebase not found: {codebase_path}")

    if args.methods_file:
        methods = _load_methods_from_file(args.methods_file)
    elif args.auto_discover:
        methods = _auto_discover_methods(graph_path, args.discover_limit)
        print(f"Auto-discovered {len(methods)} methods from graph")
    elif args.methods:
        methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    else:
        sys.exit(
            "No methods specified. Use --methods, --methods-file, or --auto-discover.\n"
            "Example: --methods 'OrderController.createOrder,PaymentService.charge'"
        )

    if not methods:
        sys.exit("Method list is empty.")

    print(f"\n{'=' * 70}")
    print("JIDRA Token & Cost Validation")
    print(f"{'=' * 70}")
    print(f"Graph:    {graph_path}")
    print(f"Codebase: {codebase_path}")
    print(f"Model:    {args.model}")
    print(f"Methods:  {len(methods)}")
    for m in methods:
        print(f"  • {m}")
    print(f"{'=' * 70}\n")

    results = []
    failed = []

    for i, method_selector in enumerate(methods, 1):
        print(f"[{i}/{len(methods)}] {method_selector}")
        try:
            proof = analyze_method_online(
                graph_path=graph_path,
                method_selector=method_selector,
                model=args.model,
                num_queries=args.queries,
                codebase=codebase_path,
            )
            print(format_method_proof(proof))
            results.append(proof)
        except Exception as e:
            print(f"  ✗ Failed: {e}\n")
            failed.append({"method": method_selector, "error": str(e)})

    if results:
        avg_reduction = sum(r.api_token_reduction_pct for r in results) / len(results)
        total_annual = sum(r.api_annual_savings for r in results)
        print(f"\n{'=' * 70}")
        print("Summary")
        print(f"{'=' * 70}")
        print(f"Methods validated:   {len(results)}/{len(methods)}")
        print(f"Avg token reduction: {avg_reduction:.1f}%")
        print(f"Annual savings ({args.queries} queries): ${total_annual:.2f}")
        if failed:
            print(f"Failed: {[f['method'] for f in failed]}")
        print(f"{'=' * 70}\n")

    if args.output:
        import dataclasses

        avg_reduction = (
            sum(r.api_token_reduction_pct for r in results) / len(results)
            if results
            else 0
        )
        total_annual = sum(r.api_annual_savings for r in results) if results else 0
        out = {
            "model": args.model,
            "queries": args.queries,
            "graph": str(graph_path),
            "codebase": str(codebase_path),
            "results": [dataclasses.asdict(r) for r in results],
            "failed": failed,
            "summary": {
                "methods_validated": len(results),
                "avg_token_reduction_pct": round(avg_reduction, 1),
                "total_annual_savings": round(total_annual, 4),
            },
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f"Results written to: {args.output}")


if __name__ == "__main__":
    main()
