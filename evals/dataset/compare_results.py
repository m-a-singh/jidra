#!/usr/bin/env python3
"""
compare_results.py — compare two swebench_retrieval_eval JSON outputs.

Primary use case: JIDRA vs CodeGraph side-by-side on the same cases.
Also works for any two runs (v1 vs v2, different configs, etc).

Produces a combined JSON + printed summary with per-case breakdown,
win/loss/tie table, and aggregate metric deltas.

Usage:
    # JIDRA vs CodeGraph (primary use case)
    python evals/dataset/compare_results.py \
        --a evals/results_django_jidra.json \
        --b evals/results_django_cg.json \
        --label-a JIDRA \
        --label-b CodeGraph \
        --out evals/comparison_django.json

    # Any two runs
    python evals/dataset/compare_results.py \
        --a evals/results_v1.json \
        --b evals/results_v2.json \
        --label-a "v1" --label-b "v2" \
        --out evals/comparison_v1_v2.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> dict[str, dict]:
    """Load JSON results file → dict keyed by case_id."""
    raw = json.loads(path.read_text())
    return {r["case_id"]: r for r in raw}


def compare(a: dict[str, dict], b: dict[str, dict], label_a: str, label_b: str) -> list[dict]:
    all_ids = sorted(set(a) | set(b))
    rows = []
    for cid in all_ids:
        ra = a.get(cid)
        rb = b.get(cid)
        if ra is None:
            rows.append({"case_id": cid, "winner": label_b, "status": f"only_in_{label_b}", "a": None, "b": rb})
            continue
        if rb is None:
            rows.append({"case_id": cid, "winner": label_a, "status": f"only_in_{label_a}", "a": ra, "b": None})
            continue

        a_pass = ra["passed"]
        b_pass = rb["passed"]
        recall_delta = round(ra["file_recall"] - rb["file_recall"], 4)  # positive = A wins
        mrr_delta = round(ra["mrr"] - rb["mrr"], 4)

        # Winner per case
        if a_pass and not b_pass:
            winner = label_a
            status = f"{label_a}_only"
        elif b_pass and not a_pass:
            winner = label_b
            status = f"{label_b}_only"
        elif a_pass and b_pass:
            if recall_delta > 0.01:
                winner = label_a
                status = "both_pass_a_higher"
            elif recall_delta < -0.01:
                winner = label_b
                status = "both_pass_b_higher"
            else:
                winner = "tie"
                status = "both_pass_tied"
        else:
            winner = "neither"
            status = "both_fail"

        rows.append({
            "case_id": cid,
            "repo": ra.get("repo", ""),
            "winner": winner,
            "status": status,
            f"{label_a}_pass": a_pass,
            f"{label_b}_pass": b_pass,
            f"{label_a}_recall": ra["file_recall"],
            f"{label_b}_recall": rb["file_recall"],
            "recall_delta_a_minus_b": recall_delta,
            f"{label_a}_mrr": ra["mrr"],
            f"{label_b}_mrr": rb["mrr"],
            "mrr_delta_a_minus_b": mrr_delta,
            f"{label_a}_search": ra.get("search_recall", 0),
            f"{label_b}_search": rb.get("search_recall", 0),
            f"{label_a}_explore": ra.get("explore_recall", 0),
            f"{label_b}_explore": rb.get("explore_recall", 0),
            f"{label_a}_found": ra.get("found_files", []),
            f"{label_b}_found": rb.get("found_files", []),
            f"{label_a}_missed": ra.get("missed_files", []),
            f"{label_b}_missed": rb.get("missed_files", []),
        })
    return rows


def aggregate(results: dict[str, dict], label: str) -> dict:
    rs = list(results.values())
    if not rs:
        return {}
    n = len(rs)
    return {
        "label": label,
        "total": n,
        "passed": sum(1 for r in rs if r["passed"]),
        "pass_rate": round(sum(1 for r in rs if r["passed"]) / n, 4),
        "mean_recall": round(sum(r["file_recall"] for r in rs) / n, 4),
        "mean_mrr": round(sum(r["mrr"] for r in rs) / n, 4),
        "mean_search_recall": round(sum(r.get("search_recall", 0) for r in rs) / n, 4),
        "mean_explore_recall": round(sum(r.get("explore_recall", 0) for r in rs) / n, 4),
    }


def per_repo(rows: list[dict], label_a: str, label_b: str) -> dict[str, dict]:
    repos: dict[str, list[dict]] = {}
    for r in rows:
        repo = r.get("repo", "")
        if not repo or r["winner"] in (f"only_in_{label_a}", f"only_in_{label_b}"):
            continue
        repos.setdefault(repo, []).append(r)
    out = {}
    for repo, cases in sorted(repos.items()):
        n = len(cases)
        out[repo] = {
            "total": n,
            f"{label_a}_passed": sum(1 for c in cases if c.get(f"{label_a}_pass")),
            f"{label_b}_passed": sum(1 for c in cases if c.get(f"{label_b}_pass")),
            f"{label_a}_mean_recall": round(sum(c.get(f"{label_a}_recall", 0) for c in cases) / n, 4),
            f"{label_b}_mean_recall": round(sum(c.get(f"{label_b}_recall", 0) for c in cases) / n, 4),
            f"{label_a}_wins": sum(1 for c in cases if c["winner"] == label_a),
            f"{label_b}_wins": sum(1 for c in cases if c["winner"] == label_b),
            "ties": sum(1 for c in cases if c["winner"] == "tie"),
        }
    return out


def print_report(rows: list[dict], agg_a: dict, agg_b: dict,
                 label_a: str, label_b: str) -> None:
    sep = "─" * 72

    # Aggregate table
    print(f"\n{sep}")
    print(f"  {'Metric':<25}  {label_a:<22}  {label_b:<22}  {label_a} advantage")
    print(sep)
    metrics = [
        ("Pass rate", "pass_rate"),
        ("Mean recall", "mean_recall"),
        ("Mean MRR", "mean_mrr"),
        ("Search recall", "mean_search_recall"),
        ("Explore recall", "mean_explore_recall"),
    ]
    for label, key in metrics:
        av = agg_a.get(key, 0)
        bv = agg_b.get(key, 0)
        delta = av - bv
        sign = "+" if delta >= 0 else ""
        print(f"  {label:<25}  {av:<22.4f}  {bv:<22.4f}  {sign}{delta:.4f}")
    a_pass = f"{agg_a.get('passed',0)}/{agg_a.get('total',0)}"
    b_pass = f"{agg_b.get('passed',0)}/{agg_b.get('total',0)}"
    print(f"  {'Pass count':<25}  {a_pass:<22}  {b_pass:<22}")
    print(sep)

    # Win/loss/tie counts
    by_status: dict[str, list[dict]] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)

    a_only = by_status.get(f"{label_a}_only", [])
    b_only = by_status.get(f"{label_b}_only", [])
    both_a = by_status.get("both_pass_a_higher", [])
    both_b = by_status.get("both_pass_b_higher", [])
    tied = by_status.get("both_pass_tied", [])
    both_fail = by_status.get("both_fail", [])

    total_valid = len([r for r in rows if r["winner"] not in (f"only_in_{label_a}", f"only_in_{label_b}")])
    a_wins = len(a_only) + len(both_a)
    b_wins = len(b_only) + len(both_b)

    print(f"\n  Win/loss ({total_valid} shared cases):")
    print(f"    {label_a} wins:  {a_wins}  ({len(a_only)} exclusive + {len(both_a)} higher recall when both pass)")
    print(f"    {label_b} wins:  {b_wins}  ({len(b_only)} exclusive + {len(both_b)} higher recall when both pass)")
    print(f"    Tied:        {len(tied)}")
    print(f"    Both fail:   {len(both_fail)}")

    if a_only:
        print(f"\n  {label_a} passes, {label_b} fails ({len(a_only)} cases):")
        for r in a_only:
            print(f"    {r['case_id']:<58}  {label_a}={r.get(f'{label_a}_recall',0):.2f}  {label_b}={r.get(f'{label_b}_recall',0):.2f}")

    if b_only:
        print(f"\n  {label_b} passes, {label_a} fails ({len(b_only)} cases):")
        for r in b_only:
            print(f"    {r['case_id']:<58}  {label_a}={r.get(f'{label_a}_recall',0):.2f}  {label_b}={r.get(f'{label_b}_recall',0):.2f}")

    if both_a:
        print(f"\n  Both pass, {label_a} higher recall ({len(both_a)} cases):")
        for r in both_a:
            delta = r["recall_delta_a_minus_b"]
            print(f"    {r['case_id']:<58}  +{delta:.2f}")

    if both_b:
        print(f"\n  Both pass, {label_b} higher recall ({len(both_b)} cases):")
        for r in both_b:
            delta = r["recall_delta_a_minus_b"]
            print(f"    {r['case_id']:<58}  {delta:.2f}")

    if both_fail:
        print(f"\n  Both fail ({len(both_fail)} cases):")
        for r in both_fail:
            print(f"    {r['case_id']}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two retrieval eval JSON outputs (e.g. JIDRA vs CodeGraph)"
    )
    parser.add_argument("--a", required=True, metavar="JSON",
                        help="First results JSON (e.g. JIDRA output)")
    parser.add_argument("--b", required=True, metavar="JSON",
                        help="Second results JSON (e.g. CodeGraph output)")
    parser.add_argument("--label-a", default="JIDRA",
                        help="Label for --a (default: JIDRA)")
    parser.add_argument("--label-b", default="CodeGraph",
                        help="Label for --b (default: CodeGraph)")
    parser.add_argument("--out", default=None, metavar="JSON",
                        help="Write combined comparison JSON")
    args = parser.parse_args()

    a_path = Path(args.a)
    b_path = Path(args.b)
    for p in (a_path, b_path):
        if not p.exists():
            raise SystemExit(f"File not found: {p}")

    label_a = args.label_a
    label_b = args.label_b

    a = load(a_path)
    b = load(b_path)

    rows = compare(a, b, label_a, label_b)
    agg_a = aggregate(a, label_a)
    agg_b = aggregate(b, label_b)
    repo_data = per_repo(rows, label_a, label_b)

    print(f"\nComparing:")
    print(f"  {label_a:<12} {a_path}  ({len(a)} cases)")
    print(f"  {label_b:<12} {b_path}  ({len(b)} cases)")

    print_report(rows, agg_a, agg_b, label_a, label_b)

    # Per-repo table
    if len(repo_data) > 1:
        print(f"  Per-repo:")
        print(f"  {'repo':<35}  {label_a+' pass':<14}  {label_b+' pass':<14}  {label_a} recall  {label_b} recall  {label_a} wins  {label_b} wins")
        print("  " + "─" * 100)
        for repo, rd in sorted(repo_data.items()):
            n = rd["total"]
            a_p = f"{rd.get(f'{label_a}_passed',0)}/{n}"
            b_p = f"{rd.get(f'{label_b}_passed',0)}/{n}"
            a_r = rd.get(f"{label_a}_mean_recall", 0)
            b_r = rd.get(f"{label_b}_mean_recall", 0)
            a_w = rd.get(f"{label_a}_wins", 0)
            b_w = rd.get(f"{label_b}_wins", 0)
            print(f"  {repo:<35}  {a_p:<14}  {b_p:<14}  {a_r:.3f}       {b_r:.3f}       {a_w}       {b_w}")
        print()

    if args.out:
        out = {
            f"{label_a}_file": str(a_path),
            f"{label_b}_file": str(b_path),
            f"label_{label_a}": label_a,
            f"label_{label_b}": label_b,
            f"aggregate_{label_a}": agg_a,
            f"aggregate_{label_b}": agg_b,
            "per_repo": repo_data,
            "cases": rows,
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Comparison written to {args.out}")


if __name__ == "__main__":
    main()
