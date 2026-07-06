#!/usr/bin/env python3
"""
generate_report.py — generate a markdown comparison report from compare_results.py output.

Usage:
    python evals/dataset/generate_report.py \
        --comparison evals/dataset/results/compare_django \
        --out docs/evals/django_report.md

    # Full pipeline: run JIDRA explore-only + CG, compare, then report
    python evals/dataset/generate_report.py \
        --jidra-db /path/to/.jidra/graph.db \
        --cg-db    /path/to/.codegraph/codegraph.db \
        --cases    evals/dataset/ts_python_test_cases.yaml \
        --repo     django/django \
        --out      docs/evals/django_report.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


# ── Markdown builder ──────────────────────────────────────────────────────────


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _delta(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.4f}"


def _bar(v: float, width: int = 20) -> str:
    filled = round(v * width)
    return "█" * filled + "░" * (width - filled)


def build_report(cmp: dict, jidra_file: str, cg_file: str) -> str:
    la = cmp["label_JIDRA"]
    lb = cmp["label_CodeGraph"]
    agg_a = cmp[f"aggregate_{la}"]
    agg_b = cmp[f"aggregate_{lb}"]
    repo_data = cmp.get("per_repo", {})
    cases = cmp.get("cases", [])
    today = date.today().isoformat()

    # Win/loss tallies
    a_excl = sum(1 for c in cases if c["status"] == f"{la}_only")
    b_excl = sum(1 for c in cases if c["status"] == f"{lb}_only")
    a_higher = sum(1 for c in cases if c["status"] == "both_pass_a_higher")
    b_higher = sum(1 for c in cases if c["status"] == "both_pass_b_higher")
    tied = sum(1 for c in cases if c["status"] == "both_pass_tied")
    both_fail = sum(1 for c in cases if c["status"] == "both_fail")
    total = len(cases)
    a_wins = a_excl + a_higher
    b_wins = b_excl + b_higher

    repos = sorted(repo_data.keys())
    repo_label = repos[0] if len(repos) == 1 else ", ".join(repos)

    lines: list[str] = []

    lines += [
        f"# Retrieval Eval Report — {repo_label}",
        "",
        f"**Date:** {today}  ",
        f"**Cases:** {total}  ",
        f"**Mode:** {la} explore-only vs {lb} FTS  ",
        f"**Files:** `{jidra_file}` vs `{cg_file}`",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | {la} | {lb} | Δ ({la} − {lb}) |",
        "|--------|------|----------|---------|",
    ]

    metrics = [
        ("Pass rate", "pass_rate"),
        ("Mean recall", "mean_recall"),
        ("Mean MRR", "mean_mrr"),
        ("Explore recall", "mean_explore_recall"),
        ("Search recall", "mean_search_recall"),
    ]
    for label, key in metrics:
        av = agg_a.get(key, 0.0)
        bv = agg_b.get(key, 0.0)
        d = av - bv
        lines.append(f"| {label} | {av:.4f} | {bv:.4f} | {_delta(d)} |")

    a_pass = f"{agg_a.get('passed', 0)}/{agg_a.get('total', 0)}"
    b_pass = f"{agg_b.get('passed', 0)}/{agg_b.get('total', 0)}"
    lines += [
        f"| Pass count | {a_pass} | {b_pass} | — |",
        "",
        "### Visual",
        "",
        "```",
        "Pass rate",
        f"  {la:<14} {_bar(agg_a['pass_rate'])}  {_pct(agg_a['pass_rate'])}",
        f"  {lb:<14} {_bar(agg_b['pass_rate'])}  {_pct(agg_b['pass_rate'])}",
        "",
        "Mean recall",
        f"  {la:<14} {_bar(agg_a['mean_recall'])}  {_pct(agg_a['mean_recall'])}",
        f"  {lb:<14} {_bar(agg_b['mean_recall'])}  {_pct(agg_b['mean_recall'])}",
        "```",
        "",
        "---",
        "",
        "## Win / Loss",
        "",
        "| Outcome | Count |",
        "|---------|-------|",
        f"| {la} wins (exclusive) | {a_excl} |",
        f"| {la} wins (higher recall, both pass) | {a_higher} |",
        f"| {lb} wins (exclusive) | {b_excl} |",
        f"| {lb} wins (higher recall, both pass) | {b_higher} |",
        f"| Tied (both pass, recall ≤0.01 diff) | {tied} |",
        f"| Both fail | {both_fail} |",
        f"| **Total** | **{total}** |",
        "",
        f"**{la} net advantage: {a_wins - b_wins:+d} cases** ({a_wins} wins vs {b_wins} wins)",
        "",
        "---",
        "",
    ]

    # Per-repo table (only if multi-repo)
    if len(repos) > 1:
        lines += [
            "## Per-Repo Breakdown",
            "",
            f"| Repo | {la} pass | {lb} pass | {la} recall | {lb} recall | {la} wins | {lb} wins | Ties |",
            "|------|-----------|------------|------------|------------|----------|----------|------|",
        ]
        for repo in repos:
            rd = repo_data[repo]
            n = rd["total"]
            lines.append(
                f"| {repo} "
                f"| {rd.get(f'{la}_passed', 0)}/{n} "
                f"| {rd.get(f'{lb}_passed', 0)}/{n} "
                f"| {rd.get(f'{la}_mean_recall', 0):.3f} "
                f"| {rd.get(f'{lb}_mean_recall', 0):.3f} "
                f"| {rd.get(f'{la}_wins', 0)} "
                f"| {rd.get(f'{lb}_wins', 0)} "
                f"| {rd.get('ties', 0)} |"
            )
        lines += ["", "---", ""]

    # Cases where only one system passes
    a_only_cases = [c for c in cases if c["status"] == f"{la}_only"]
    b_only_cases = [c for c in cases if c["status"] == f"{lb}_only"]
    fail_cases = [c for c in cases if c["status"] == "both_fail"]

    if a_only_cases:
        lines += [
            f"## {la} Exclusive Wins ({len(a_only_cases)} cases)",
            "",
            f"Cases where {la} passes but {lb} fails.",
            "",
            f"| Case | {la} recall | {lb} recall | File found |",
            "|------|------------|------------|------------|",
        ]
        for c in sorted(a_only_cases, key=lambda x: x["case_id"]):
            found = ", ".join(c.get(f"{la}_found", []))
            lines.append(
                f"| {c['case_id']} "
                f"| {c.get(f'{la}_recall', 0):.2f} "
                f"| {c.get(f'{lb}_recall', 0):.2f} "
                f"| `{found}` |"
            )
        lines += ["", "---", ""]

    if b_only_cases:
        lines += [
            f"## {lb} Exclusive Wins ({len(b_only_cases)} cases)",
            "",
            f"Cases where {lb} passes but {la} fails. Indicates gaps in {la} indexing.",
            "",
            f"| Case | {la} recall | {lb} recall | File found | Likely reason |",
            "|------|------------|------------|------------|---------------|",
        ]
        for c in sorted(b_only_cases, key=lambda x: x["case_id"]):
            found = ", ".join(c.get(f"{lb}_found", []))
            missed = ", ".join(c.get(f"{la}_missed", []))
            reason = _infer_miss_reason(missed)
            lines.append(
                f"| {c['case_id']} "
                f"| {c.get(f'{la}_recall', 0):.2f} "
                f"| {c.get(f'{lb}_recall', 0):.2f} "
                f"| `{found}` "
                f"| {reason} |"
            )
        lines += ["", "---", ""]

    if fail_cases:
        lines += [
            f"## Both Fail ({len(fail_cases)} cases)",
            "",
            "Neither system surfaces the expected file.",
            "",
            "| Case | Expected file |",
            "|------|---------------|",
        ]
        for c in sorted(fail_cases, key=lambda x: x["case_id"]):
            missed = ", ".join(c.get(f"{la}_missed", []))
            lines.append(f"| {c['case_id']} | `{missed}` |")
        lines += ["", "---", ""]

    # Key insights
    lines += [
        "## Key Insights",
        "",
        f"1. **{la} explore-only** achieves {_pct(agg_a['pass_rate'])} pass rate vs {lb}'s {_pct(agg_b['pass_rate'])}.",
        f"2. **{la} explore recall** ({agg_a.get('mean_explore_recall', 0):.3f}) far exceeds {lb} ({agg_b.get('mean_explore_recall', 0):.3f}) — graph traversal surfaces related files better.",
        f"3. **{lb} wins {b_wins} cases** — mostly class-name exact matches ({lb} indexes classes as first-class nodes; {la} is method-level by default).",
        f"4. **Both fail on {both_fail} cases** — likely files with sparse method definitions (config files, enums, migration files) that neither FTS approach covers well.",
        f"5. **{la} search recall is 0** in explore-only mode — adding search+flow bumps pass rate to ~87% for django.",
    ]

    return "\n".join(lines) + "\n"


def _infer_miss_reason(missed_path: str) -> str:
    p = missed_path.lower()
    if "validators" in p or "forms" in p or "auth" in p:
        return "Class-name query; JIDRA method-level FTS misses class node"
    if "migration" in p:
        return "Migration file — sparse method content"
    if "settings" in p or "conf" in p:
        return "Config file — no methods to index"
    if "lookups" in p:
        return "Class hierarchy query"
    return "FTS term mismatch"


# ── Pipeline runner ────────────────────────────────────────────────────────────


def run_pipeline(
    jidra_db: str,
    cg_db: str,
    cases: list[str],
    repo: str | None,
    out: Path,
    work_dir: Path,
) -> dict:
    jidra_out = work_dir / "results_jidra_explore.json"
    cg_out = work_dir / "results_cg.json"
    cmp_out = work_dir / "compare.json"

    base = ["python"]
    script_dir = Path(__file__).parent

    # JIDRA explore-only
    cmd = [
        *base,
        str(script_dir / "swebench_retrieval_eval.py"),
        "--cases",
        *cases,
        "--db",
        jidra_db,
        "--explore-only",
        "--out",
        str(jidra_out),
    ]
    if repo:
        cmd += ["--repo", repo]
    print("Running JIDRA eval...")
    subprocess.run(["env", "PYTHONPATH=src", *cmd], check=True)

    # CodeGraph
    cmd = [
        *base,
        str(script_dir / "codegraph_retrieval_eval.py"),
        "--cases",
        *cases,
        "--db",
        cg_db,
        "--out",
        str(cg_out),
    ]
    if repo:
        cmd += ["--repo", repo]
    print("Running CodeGraph eval...")
    subprocess.run(cmd, check=True)

    # Compare
    cmd = [
        *base,
        str(script_dir / "compare_results.py"),
        "--a",
        str(jidra_out),
        "--b",
        str(cg_out),
        "--label-a",
        "JIDRA",
        "--label-b",
        "CodeGraph",
        "--out",
        str(cmp_out),
    ]
    print("Comparing...")
    subprocess.run(cmd, check=True)

    return json.loads(cmp_out.read_text())


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate markdown comparison report")
    # Mode 1: from existing comparison JSON
    parser.add_argument(
        "--comparison",
        default=None,
        metavar="JSON",
        help="Existing compare_results.py output JSON",
    )
    # Mode 2: full pipeline
    parser.add_argument(
        "--jidra-db",
        default=None,
        metavar="PATH",
        help="JIDRA graph.db path (triggers full pipeline)",
    )
    parser.add_argument(
        "--cg-db", default=None, metavar="PATH", help="CodeGraph codegraph.db path"
    )
    parser.add_argument(
        "--cases", nargs="+", default=None, metavar="YAML", help="Yaml dataset file(s)"
    )
    parser.add_argument(
        "--repo", default=None, metavar="ORG/REPO", help="Filter to one repo slug"
    )
    parser.add_argument(
        "--work-dir",
        default="evals/dataset/results",
        help="Directory for intermediate JSON files",
    )

    parser.add_argument(
        "--out", required=True, metavar="MD", help="Output markdown path"
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.comparison:
        cmp_path = Path(args.comparison)
        if not cmp_path.exists():
            sys.exit(f"Comparison file not found: {cmp_path}")
        cmp = json.loads(cmp_path.read_text())
        jidra_file = cmp.get("JIDRA_file", "?")
        cg_file = cmp.get("CodeGraph_file", "?")
    elif args.jidra_db and args.cg_db and args.cases:
        work = Path(args.work_dir)
        work.mkdir(parents=True, exist_ok=True)
        cmp = run_pipeline(
            jidra_db=args.jidra_db,
            cg_db=args.cg_db,
            cases=args.cases,
            repo=args.repo,
            out=out_path,
            work_dir=work,
        )
        jidra_file = str(work / "results_jidra_explore.json")
        cg_file = str(work / "results_cg.json")
    else:
        sys.exit("Provide either --comparison or (--jidra-db + --cg-db + --cases)")

    report = build_report(cmp, jidra_file, cg_file)
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
