#!/usr/bin/env python3
"""
compare_four.py — 4-way comparison: JIDRA search, JIDRA explore, CG search, CG explore.
Prints a terminal table AND writes a markdown report.

Usage — full pipeline (runs all 4 evals, compares, writes report):
    python evals/dataset/compare_four.py \
        --jidra-db /path/to/.jidra/graph.db \
        --cg-db    /path/to/.codegraph/codegraph.db \
        --cases    evals/dataset/ts_python_test_cases.yaml \
        --repo     django/django \
        --work-dir evals/dataset/results \
        --report   docs/evals/django_four_way.md

Usage — from pre-computed JSONs:
    python evals/dataset/compare_four.py \
        --jidra-search  evals/dataset/results/results_jidra_search.json \
        --jidra-explore evals/dataset/results/results_jidra_explore.json \
        --cg-search     evals/dataset/results/results_cg_search.json \
        --cg-explore    evals/dataset/results/results_cg_explore.json \
        --report        docs/evals/django_four_way.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

# Use venv python if available, so eval subprocesses have yaml/jidra deps
_venv_py = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
_PYTHON = str(_venv_py) if _venv_py.exists() else sys.executable


# ── Data helpers ──────────────────────────────────────────────────────────────


def load(path: Path) -> dict[str, dict]:
    return {r["case_id"]: r for r in json.loads(path.read_text())}


def agg(results: dict[str, dict]) -> dict:
    rs = list(results.values())
    n = len(rs)
    if not n:
        return {}
    lats = sorted(r.get("latency_ms", 0) for r in rs)
    return {
        "total": n,
        "passed": sum(1 for r in rs if r["passed"]),
        "pass_rate": round(sum(1 for r in rs if r["passed"]) / n, 4),
        "mean_recall": round(sum(r["file_recall"] for r in rs) / n, 4),
        "mean_mrr": round(sum(r["mrr"] for r in rs) / n, 4),
        "mean_latency_ms": round(sum(lats) / n, 1),
        "p50_latency_ms": round(lats[n // 2], 1),
        "p95_latency_ms": round(lats[int(n * 0.95)], 1),
    }


def _win_loss(js, je, cs, ce):
    all_ids = sorted(set(js) | set(je) | set(cs) | set(ce))
    both_pass = jidra_only = cg_only = both_fail = 0
    jidra_only_ids = []
    cg_only_ids = []
    both_fail_ids = []
    for cid in all_ids:
        j = js.get(cid, {}).get("passed", False) or je.get(cid, {}).get("passed", False)
        c = cs.get(cid, {}).get("passed", False) or ce.get(cid, {}).get("passed", False)
        if j and c:
            both_pass += 1
        elif j:
            jidra_only += 1
            jidra_only_ids.append(cid)
        elif c:
            cg_only += 1
            cg_only_ids.append(cid)
        else:
            both_fail += 1
            both_fail_ids.append(cid)
    return {
        "total": len(all_ids),
        "both_pass": both_pass,
        "jidra_only": jidra_only,
        "cg_only": cg_only,
        "both_fail": both_fail,
        "jidra_only_ids": jidra_only_ids,
        "cg_only_ids": cg_only_ids,
        "both_fail_ids": both_fail_ids,
    }


# ── Terminal output ───────────────────────────────────────────────────────────


def _bar(v: float, w: int = 16) -> str:
    filled = round(v * w)
    return "█" * filled + "░" * (w - filled)


def print_report(js, je, cs, ce) -> None:
    ajs, aje, acs, ace = agg(js), agg(je), agg(cs), agg(ce)
    cols = [
        ("JIDRA search", ajs),
        ("JIDRA explore", aje),
        ("CG search", acs),
        ("CG explore*", ace),
    ]
    sep = "─" * 92

    print(f"\n{sep}")
    print(
        f"  {'Metric':<22}  {'JIDRA search':<16}  {'JIDRA explore':<16}  {'CG search':<16}  {'CG explore*'}"
    )
    print(
        "  (* CG explore = 1-hop edge approximation, not CG's native semantic explore)"
    )
    print(sep)

    for label, key in [
        ("Pass rate", "pass_rate"),
        ("Mean recall", "mean_recall"),
        ("Mean MRR", "mean_mrr"),
        ("Latency mean", "mean_latency_ms"),
        ("Latency p50", "p50_latency_ms"),
        ("Latency p95", "p95_latency_ms"),
    ]:
        vals = [a.get(key, 0) for _, a in cols]
        fmt = "{:<16.0f}" if "latency" in key.lower() else "{:<16.4f}"
        row = "  ".join(fmt.format(v) for v in vals)
        print(f"  {label:<22}  {row}")

    row = "  ".join(f"{a.get('passed', 0)}/{a.get('total', 0):<14}" for _, a in cols)
    print(f"  {'Pass count':<22}  {row}")
    print(sep)

    print("\n  Pass rate")
    for name, a in cols:
        print(f"    {name:<16} {_bar(a['pass_rate'])}  {a['pass_rate'] * 100:.1f}%")

    print("\n  Mean recall")
    for name, a in cols:
        print(f"    {name:<16} {_bar(a['mean_recall'])}  {a['mean_recall'] * 100:.1f}%")

    max_lat = max(a.get("mean_latency_ms", 1) for _, a in cols) or 1
    print("\n  Latency (mean ms)")
    for name, a in cols:
        lat = a.get("mean_latency_ms", 0)
        filled = round((lat / max_lat) * 16)
        print(f"    {name:<16} {'█' * filled + '░' * (16 - filled)}  {lat:.0f}ms")

    wl = _win_loss(js, je, cs, ce)
    print(f"\n  System win/loss ({wl['total']} cases — pass = search OR explore):")
    print(f"    Both pass:   {wl['both_pass']}")
    print(f"    JIDRA only:  {wl['jidra_only']}")
    print(f"    CG only:     {wl['cg_only']}")
    print(f"    Both fail:   {wl['both_fail']}")
    print(sep)


# ── Markdown report ───────────────────────────────────────────────────────────


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _delta(a: float, b: float) -> str:
    d = a - b
    return f"{'+' if d >= 0 else ''}{d:.4f}"


def build_markdown(
    js, je, cs, ce, repo: str | None, jidra_s_path, jidra_e_path, cg_s_path, cg_e_path
) -> str:
    ajs, aje, acs, ace = agg(js), agg(je), agg(cs), agg(ce)
    wl = _win_loss(js, je, cs, ce)
    today = date.today().isoformat()
    title = repo or "aggregate"

    lines: list[str] = [
        f"# Retrieval Eval — {title}",
        "",
        f"**Date:** {today}  ",
        f"**Cases:** {wl['total']}  ",
        "**Inputs:**",
        f"- JIDRA search: `{jidra_s_path}`",
        f"- JIDRA explore: `{jidra_e_path}`",
        f"- CG search: `{cg_s_path}`",
        f"- CG explore: `{cg_e_path}`",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | JIDRA search | JIDRA explore | CG search | CG explore† |",
        "|--------|-------------|--------------|-----------|-------------|",
        "",
        "> † CG explore = 1-hop edge expansion approximation — not CodeGraph's native semantic explore. Underestimates real CG explore performance.",
        "",
    ]

    for label, key in [
        ("Pass rate", "pass_rate"),
        ("Mean recall", "mean_recall"),
        ("Mean MRR", "mean_mrr"),
        ("Latency mean", "mean_latency_ms"),
        ("Latency p50", "p50_latency_ms"),
        ("Latency p95", "p95_latency_ms"),
    ]:
        lat = "latency" in label.lower()
        fmt = "{:.0f}ms" if lat else "{:.4f}"
        vals = [fmt.format(a.get(key, 0)) for a in (ajs, aje, acs, ace)]
        lines.append(f"| {label} | {' | '.join(vals)} |")

    lines += [
        f"| Pass count | {ajs['passed']}/{ajs['total']} | {aje['passed']}/{aje['total']} | {acs['passed']}/{acs['total']} | {ace['passed']}/{ace['total']} |",
        "",
        "### JIDRA vs CG — best mode delta",
        "",
        "| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |",
        "|--------|-----------|---------|----------------|",
    ]

    for label, key in [
        ("Pass rate", "pass_rate"),
        ("Mean recall", "mean_recall"),
        ("Mean MRR", "mean_mrr"),
    ]:
        bj = max(ajs.get(key, 0), aje.get(key, 0))
        bc = max(acs.get(key, 0), ace.get(key, 0))
        lines.append(f"| {label} | {bj:.4f} | {bc:.4f} | {_delta(bj, bc)} |")

    lines += [
        "",
        "### Visual",
        "",
        "```",
        "Pass rate",
        f"  JIDRA search   {_bar(ajs['pass_rate'])}  {_pct(ajs['pass_rate'])}",
        f"  JIDRA explore  {_bar(aje['pass_rate'])}  {_pct(aje['pass_rate'])}",
        f"  CG search      {_bar(acs['pass_rate'])}  {_pct(acs['pass_rate'])}",
        f"  CG explore     {_bar(ace['pass_rate'])}  {_pct(ace['pass_rate'])}",
        "",
        "Mean recall",
        f"  JIDRA search   {_bar(ajs['mean_recall'])}  {_pct(ajs['mean_recall'])}",
        f"  JIDRA explore  {_bar(aje['mean_recall'])}  {_pct(aje['mean_recall'])}",
        f"  CG search      {_bar(acs['mean_recall'])}  {_pct(acs['mean_recall'])}",
        f"  CG explore     {_bar(ace['mean_recall'])}  {_pct(ace['mean_recall'])}",
        "",
        "Latency (mean ms — lower is better)",
    ]
    max_lat = max(a.get("mean_latency_ms", 1) for a in (ajs, aje, acs, ace)) or 1
    for name, a in [
        ("JIDRA search", ajs),
        ("JIDRA explore", aje),
        ("CG search", acs),
        ("CG explore†", ace),
    ]:
        lat = a.get("mean_latency_ms", 0)
        filled = round((lat / max_lat) * 20)
        lines.append(f"  {name:<16} {'█' * filled + '░' * (20 - filled)}  {lat:.0f}ms")
    lines += ["```", "", "---", ""]

    # System win/loss
    lines += [
        "## System Win / Loss",
        "",
        "> Pass = either search **or** explore passes for that system.",
        "",
        "| Outcome | Count |",
        "|---------|-------|",
        f"| Both pass | {wl['both_pass']} |",
        f"| JIDRA only | {wl['jidra_only']} |",
        f"| CG only | {wl['cg_only']} |",
        f"| Both fail | {wl['both_fail']} |",
        f"| **Total** | **{wl['total']}** |",
        "",
        f"**JIDRA net: {wl['jidra_only'] - wl['cg_only']:+d} cases** ({wl['jidra_only']} exclusive wins vs {wl['cg_only']})",
        "",
        "---",
        "",
    ]

    # JIDRA-only wins detail
    if wl["jidra_only_ids"]:
        lines += [
            f"## JIDRA Exclusive Wins ({wl['jidra_only']} cases)",
            "",
            "JIDRA (search or explore) passes; CG (search and explore) both fail.",
            "",
            "| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |",
            "|------|-------------|--------------|-----------|------------|------|",
        ]
        for cid in sorted(wl["jidra_only_ids"]):
            rjs = js.get(cid, {})
            rje = je.get(cid, {})
            rcs = cs.get(cid, {})
            rce = ce.get(cid, {})
            found = rjs.get("found_files") or rje.get("found_files") or []
            f_str = ", ".join(found) if found else "—"
            lines.append(
                f"| {cid} "
                f"| {rjs.get('file_recall', 0):.2f} "
                f"| {rje.get('file_recall', 0):.2f} "
                f"| {rcs.get('file_recall', 0):.2f} "
                f"| {rce.get('file_recall', 0):.2f} "
                f"| `{f_str}` |"
            )
        lines += ["", "---", ""]

    # CG-only wins detail
    if wl["cg_only_ids"]:
        lines += [
            f"## CG Exclusive Wins ({wl['cg_only']} cases)",
            "",
            "CG (search or explore) passes; JIDRA (search and explore) both fail.",
            "",
            "| Case | JIDRA search | JIDRA explore | CG search | CG explore | File | Gap |",
            "|------|-------------|--------------|-----------|------------|------|-----|",
        ]
        for cid in sorted(wl["cg_only_ids"]):
            rjs = js.get(cid, {})
            rje = je.get(cid, {})
            rcs = cs.get(cid, {})
            rce = ce.get(cid, {})
            found = rcs.get("found_files") or rce.get("found_files") or []
            missed = rjs.get("missed_files") or rje.get("missed_files") or []
            gap = _infer_gap(", ".join(missed))
            lines.append(
                f"| {cid} "
                f"| {rjs.get('file_recall', 0):.2f} "
                f"| {rje.get('file_recall', 0):.2f} "
                f"| {rcs.get('file_recall', 0):.2f} "
                f"| {rce.get('file_recall', 0):.2f} "
                f"| `{', '.join(found)}` "
                f"| {gap} |"
            )
        lines += ["", "---", ""]

    # Both fail
    if wl["both_fail_ids"]:
        lines += [
            f"## Both Fail ({wl['both_fail']} cases)",
            "",
            "Neither system finds the expected file with search or explore.",
            "",
            "| Case | Expected file |",
            "|------|---------------|",
        ]
        for cid in sorted(wl["both_fail_ids"]):
            missed = (
                js.get(cid, {}).get("missed_files")
                or je.get(cid, {}).get("missed_files")
                or []
            )
            lines.append(f"| {cid} | `{', '.join(missed)}` |")
        lines += ["", "---", ""]

    # Key insights
    best_j_pass = max(ajs["pass_rate"], aje["pass_rate"])
    best_c_pass = max(acs["pass_rate"], ace["pass_rate"])
    best_j_lat = min(ajs["mean_latency_ms"], aje["mean_latency_ms"])
    best_c_lat = min(acs["mean_latency_ms"], ace["mean_latency_ms"])

    lines += [
        "## Key Insights",
        "",
        f"1. **Best JIDRA mode** ({_pct(best_j_pass)}) vs **best CG mode** ({_pct(best_c_pass)}) — JIDRA advantage: {_delta(best_j_pass, best_c_pass)}.",
        f"2. **JIDRA explore** ({_pct(aje['pass_rate'])}) is the strongest single mode; **CG search** ({_pct(acs['pass_rate'])}) is CG's strongest.",
        f"3. **Latency** — JIDRA best: {best_j_lat:.0f}ms mean, CG best: {best_c_lat:.0f}ms mean ({best_c_lat / best_j_lat * 100:.0f}% of JIDRA).",
        f"4. **CG explore** ({_pct(ace['pass_rate'])}) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.",
        f"5. **Both fail {wl['both_fail']} cases** — config files, enums, migration files with sparse method content.",
    ]

    return "\n".join(lines) + "\n"


def _infer_gap(missed: str) -> str:
    p = missed.lower()
    if "validator" in p or "forms" in p:
        return "Class-name query"
    if "migration" in p:
        return "Sparse migration file"
    if "settings" in p or "conf/" in p:
        return "Config file"
    if "lookup" in p:
        return "Class hierarchy"
    return "FTS term mismatch"


# ── Pipeline ──────────────────────────────────────────────────────────────────


def run_pipeline(args: argparse.Namespace, work: Path) -> tuple[Path, Path, Path, Path]:
    script_dir = Path(__file__).parent
    cases = args.cases

    def _run(label: str, cmd: list[str]) -> None:
        print(f"\n{'─' * 50}\n{label}\n{'─' * 50}")
        subprocess.run(cmd, check=True)

    jidra_s = work / "results_jidra_search.json"
    jidra_e = work / "results_jidra_explore.json"
    cg_s = work / "results_cg_search.json"
    cg_e = work / "results_cg_explore.json"

    base_jidra = [
        _PYTHON,
        str(script_dir / "swebench_retrieval_eval.py"),
        "--cases",
        *cases,
        "--db",
        args.jidra_db,
    ]
    base_cg = [
        _PYTHON,
        str(script_dir / "codegraph_retrieval_eval.py"),
        "--cases",
        *cases,
        "--db",
        args.cg_db,
    ]
    if args.repo:
        base_jidra += ["--repo", args.repo]
        base_cg += ["--repo", args.repo]

    _run("JIDRA search", ["env", "PYTHONPATH=src", *base_jidra, "--out", str(jidra_s)])
    _run(
        "JIDRA explore",
        ["env", "PYTHONPATH=src", *base_jidra, "--explore-only", "--out", str(jidra_e)],
    )
    _run("CG search", [*base_cg, "--search-only", "--out", str(cg_s)])
    _run("CG explore", [*base_cg, "--explore-only", "--out", str(cg_e)])

    return jidra_s, jidra_e, cg_s, cg_e


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="4-way retrieval comparison + markdown report"
    )

    # Pre-computed JSONs
    parser.add_argument("--jidra-search", default=None, metavar="JSON")
    parser.add_argument("--jidra-explore", default=None, metavar="JSON")
    parser.add_argument("--cg-search", default=None, metavar="JSON")
    parser.add_argument("--cg-explore", default=None, metavar="JSON")

    # Full pipeline
    parser.add_argument("--jidra-db", default=None, metavar="PATH")
    parser.add_argument("--cg-db", default=None, metavar="PATH")
    parser.add_argument("--cases", nargs="+", default=None, metavar="YAML")
    parser.add_argument("--repo", default=None, metavar="ORG/REPO")
    parser.add_argument("--work-dir", default="evals/dataset/results", metavar="DIR")

    parser.add_argument(
        "--out", default=None, metavar="JSON", help="Write summary JSON"
    )
    parser.add_argument(
        "--report", default=None, metavar="MD", help="Write markdown report"
    )

    args = parser.parse_args()

    if args.jidra_db and args.cg_db and args.cases:
        work = Path(args.work_dir)
        work.mkdir(parents=True, exist_ok=True)
        jidra_s, jidra_e, cg_s, cg_e = run_pipeline(args, work)
    elif all([args.jidra_search, args.jidra_explore, args.cg_search, args.cg_explore]):
        jidra_s = Path(args.jidra_search)
        jidra_e = Path(args.jidra_explore)
        cg_s = Path(args.cg_search)
        cg_e = Path(args.cg_explore)
    else:
        sys.exit(
            "Provide (--jidra-db + --cg-db + --cases) OR all 4 --*-search/explore JSONs"
        )

    for p in (jidra_s, jidra_e, cg_s, cg_e):
        if not p.exists():
            sys.exit(f"File not found: {p}")

    js = load(jidra_s)
    je = load(jidra_e)
    cs = load(cg_s)
    ce = load(cg_e)

    print_report(js, je, cs, ce)

    if args.out:
        summary = {
            "jidra_search_file": str(jidra_s),
            "jidra_explore_file": str(jidra_e),
            "cg_search_file": str(cg_s),
            "cg_explore_file": str(cg_e),
            "aggregate_jidra_search": agg(js),
            "aggregate_jidra_explore": agg(je),
            "aggregate_cg_search": agg(cs),
            "aggregate_cg_explore": agg(ce),
            "win_loss": _win_loss(js, je, cs, ce),
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nJSON written to {args.out}")

    if args.report:
        md = build_markdown(js, je, cs, ce, args.repo, jidra_s, jidra_e, cg_s, cg_e)
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(md, encoding="utf-8")
        print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
