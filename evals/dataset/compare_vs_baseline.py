#!/usr/bin/env python3
"""Compare a result JSON against a baseline (v2 by default, or --baseline path).

Usage:
    python evals/dataset/compare_vs_baseline.py --result path/to/compare_four_vN.json
    python evals/dataset/compare_vs_baseline.py --result vN.json --baseline vM.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_BASELINE = Path(__file__).parent / "results" / "compare_four_sympy_v2.json"

METRICS = [
    ("pass_rate", "Pass rate", ".4f", True),
    ("mean_recall", "Mean recall", ".4f", True),
    ("mean_mrr", "Mean MRR", ".4f", True),
    ("mean_latency_ms", "Latency mean ms", ".1f", False),
    ("p50_latency_ms", "Latency p50 ms", ".1f", False),
    ("p95_latency_ms", "Latency p95 ms", ".1f", False),
]

MODES = [
    ("aggregate_jidra_search", "JIDRA search"),
    ("aggregate_jidra_explore", "JIDRA explore"),
    ("aggregate_cg_search", "CG search"),
    ("aggregate_cg_explore", "CG explore"),
]


def load(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def delta_str(key: str, base_val: float, new_val: float, higher_better: bool) -> str:
    d = new_val - base_val
    if abs(d) < 1e-6:
        marker = "  ="
    elif (d > 0) == higher_better:
        marker = " ▲"
    else:
        marker = " ▼"
    sign = "+" if d >= 0 else ""
    fmt = ".4f" if isinstance(new_val, float) and new_val < 100 else ".1f"
    return f"{sign}{d:{fmt}}{marker}"


def compare(baseline: dict, result: dict) -> None:
    b_path = baseline.get("_path", "baseline")
    r_path = result.get("_path", "result")

    print(f"\nBaseline : {b_path}")
    print(f"Result   : {r_path}")

    for mode_key, mode_label in MODES:
        b = baseline.get(mode_key, {})
        r = result.get(mode_key, {})
        if not b or not r:
            continue

        b_passed = b.get("passed", "?")
        r_passed = r.get("passed", "?")
        total = b.get("total", r.get("total", "?"))

        print(f"\n{'─' * 60}")
        print(
            f"  {mode_label}  (baseline {b_passed}/{total} → result {r_passed}/{total})"
        )
        print(f"{'─' * 60}")
        print(f"  {'Metric':<20} {'Baseline':>10} {'Result':>10} {'Delta':>12}")
        print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 12}")

        for key, label, fmt, higher_better in METRICS:
            bv = b.get(key)
            rv = r.get(key)
            if bv is None or rv is None:
                continue
            d = delta_str(key, bv, rv, higher_better)
            print(f"  {label:<20} {bv:>10{fmt}} {rv:>10{fmt}} {d:>12}")

    # win/loss
    bwl = baseline.get("win_loss", {})
    rwl = result.get("win_loss", {})
    if bwl and rwl:
        print(f"\n{'─' * 60}")
        print("  Win / Loss (pass = search OR explore)")
        print(f"{'─' * 60}")
        for k in ("both_pass", "jidra_only", "cg_only", "both_fail"):
            label = k.replace("_", " ").title()
            bv = bwl.get(k, "?")
            rv = rwl.get(k, "?")
            if isinstance(bv, int) and isinstance(rv, int):
                d = rv - bv
                sign = f"+{d}" if d > 0 else str(d)
                marker = (
                    ""
                    if d == 0
                    else (
                        " ▲"
                        if k in ("both_pass", "jidra_only") and d > 0
                        else " ▼"
                        if d != 0
                        else ""
                    )
                )
                print(f"  {label:<20} {bv:>6}  →  {rv:>6}   ({sign}{marker})")
            else:
                print(f"  {label:<20} {bv!s:>6}  →  {rv!s:>6}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare eval result JSON vs baseline")
    parser.add_argument("--result", required=True, help="Path to new result JSON")
    parser.add_argument(
        "--baseline", default=None, help="Path to baseline JSON (default: v2)"
    )
    args = parser.parse_args()

    result_path = Path(args.result)
    baseline_path = Path(args.baseline) if args.baseline else DEFAULT_BASELINE

    if not result_path.exists():
        sys.exit(f"result not found: {result_path}")
    if not baseline_path.exists():
        sys.exit(f"baseline not found: {baseline_path}")

    baseline = load(baseline_path)
    baseline["_path"] = str(baseline_path)
    result = load(result_path)
    result["_path"] = str(result_path)

    compare(baseline, result)


if __name__ == "__main__":
    main()
