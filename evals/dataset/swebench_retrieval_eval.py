#!/usr/bin/env python3
"""
swebench_retrieval_eval.py — file-level retrieval eval against SWE-bench style yaml datasets.

Each case: NL bug report query → expected file(s) that need to change.
Scores whether JIDRA search/explore surfaces methods in those files.

Usage:
    # All cases in a yaml against one JIDRA db
    PYTHONPATH=src python evals/dataset/swebench_retrieval_eval.py \
        --cases evals/dataset/ts_python_test_cases.yaml \
        --db /path/to/repo/.jidra/graph.db

    # Filter to one repo
    PYTHONPATH=src python evals/dataset/swebench_retrieval_eval.py \
        --cases evals/dataset/ts_python_test_cases.yaml \
        --db /path/to/django/.jidra/graph.db \
        --repo django/django

    # Multiple yaml files (all languages)
    PYTHONPATH=src python evals/dataset/swebench_retrieval_eval.py \
        --cases evals/dataset/ts_python_test_cases.yaml evals/dataset/ts_go_test_cases.yaml \
        --db /path/to/repo/.jidra/graph.db \
        --repo gin-gonic/gin

    # Limit cases, write JSON output
    PYTHONPATH=src python evals/dataset/swebench_retrieval_eval.py \
        --cases evals/dataset/ts_python_test_cases.yaml \
        --db /path/to/repo/.jidra/graph.db \
        --repo sympy/sympy \
        --limit 20 \
        --out evals/results_sympy.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from jidra.engine.engine import JidraEngine

PASS_THRESHOLD = 0.5
SEARCH_LIMIT = 20
EXPLORE_TOP_N = 20
FLOW_TOP_N = 10
FLOW_DEPTH = 3


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class Case:
    id: str
    repo: str
    commit: str
    query: str
    expected_files: list[str]  # e.g. ["django/db/models/query.py"]


@dataclass
class Result:
    case_id: str
    repo: str
    passed: bool
    file_recall: float  # fraction of expected files hit
    mrr: float  # rank of first expected-file hit in search results
    search_recall: float
    explore_recall: float
    combined_recall: float
    found_files: list[str]
    missed_files: list[str]
    latency_ms: float
    n_results: int


# ── Yaml loading ───────────────────────────────────────────────────────────────


def load_cases(
    yaml_paths: list[Path], repo_filter: str | None, limit: int | None
) -> list[Case]:
    cases: list[Case] = []
    for p in yaml_paths:
        raw: list[dict] = yaml.safe_load(p.read_text())
        for item in raw:
            repo = item.get("repo", "")
            if repo_filter and repo != repo_filter:
                continue
            cases.append(
                Case(
                    id=item["id"],
                    repo=repo,
                    commit=item.get("commit", ""),
                    query=item["query"].strip(),
                    expected_files=[f.strip() for f in (item.get("expected") or [])],
                )
            )
    if limit:
        cases = cases[:limit]
    return cases


# ── Scoring ────────────────────────────────────────────────────────────────────


def _file_from_result(r: dict) -> str | None:
    """Extract file path from a JIDRA result dict."""
    return (
        r.get("file_path")
        or r.get("file")
        or r.get("source_file")
        or r.get("path")
        or None
    )


def _normalise(path: str) -> str:
    """Normalise to forward-slash, lowercase, strip leading ./."""
    return path.replace("\\", "/").lstrip("./").lower()


def _file_hit(result_files: list[str], expected: str) -> bool:
    """True if expected file appears (suffix match) in any result file."""
    norm_exp = _normalise(expected)
    # basename match first, then suffix
    exp_base = norm_exp.split("/")[-1]
    for rf in result_files:
        nrf = _normalise(rf)
        if nrf.endswith(norm_exp) or nrf == norm_exp:
            return True
        # loose: just basename
        if nrf.split("/")[-1] == exp_base:
            return True
    return False


def _score_files(
    expected_files: list[str],
    result_dicts: list[dict],
) -> tuple[float, list[str], list[str], float]:
    """Returns (recall, found, missed, mrr)."""
    result_files = [_file_from_result(r) for r in result_dicts]
    result_files = [f for f in result_files if f]

    found, missed = [], []
    first_rank = 0
    for exp in expected_files:
        hit = _file_hit(result_files, exp)
        if hit:
            found.append(exp)
            if first_rank == 0:
                # find rank of first result that matched
                for i, r in enumerate(result_dicts):
                    rf = _file_from_result(r)
                    if rf and _file_hit([rf], exp):
                        first_rank = i + 1
                        break
        else:
            missed.append(exp)

    recall = len(found) / len(expected_files) if expected_files else 0.0
    mrr = 1.0 / first_rank if first_rank else 0.0
    return recall, found, missed, mrr


# ── Engine calls ───────────────────────────────────────────────────────────────


def _run_flow_from_explore(engine: JidraEngine, query: str) -> list[dict]:
    seeds = engine.explore(query, top_n=5).get("results", [])
    nodes: list[dict] = []
    for seed in seeds[:3]:
        mid = seed.get("method_id") or seed.get("method_name", "")
        if not mid:
            continue
        try:
            raw = engine.get_agent_flow(mid, depth=FLOW_DEPTH, top_n=FLOW_TOP_N)
            nodes.extend(raw.get("top_nodes", []))
        except Exception:
            pass
    return nodes


def run_case(case: Case, engine: JidraEngine, explore_only: bool = False) -> Result:
    if not case.expected_files:
        return Result(
            case_id=case.id,
            repo=case.repo,
            passed=False,
            file_recall=0.0,
            mrr=0.0,
            search_recall=0.0,
            explore_recall=0.0,
            combined_recall=0.0,
            found_files=[],
            missed_files=[],
            latency_ms=0.0,
            n_results=0,
        )

    t0 = time.perf_counter()

    if explore_only:
        search_hits = []
        explore_hits = engine.explore(case.query, top_n=EXPLORE_TOP_N).get(
            "results", []
        )
        flow_hits = []
    else:
        search_hits = engine.search(case.query, limit=SEARCH_LIMIT).get("results", [])
        explore_hits = engine.explore(case.query, top_n=EXPLORE_TOP_N).get(
            "results", []
        )
        flow_hits = _run_flow_from_explore(engine, case.query)

    latency_ms = (time.perf_counter() - t0) * 1000

    s_recall, s_found, s_missed, s_mrr = _score_files(case.expected_files, search_hits)
    e_recall, e_found, _, e_mrr = _score_files(case.expected_files, explore_hits)
    combined = search_hits + explore_hits + flow_hits
    c_recall, c_found, c_missed, _ = _score_files(case.expected_files, combined)

    if explore_only:
        # report explore as the primary signal
        return Result(
            case_id=case.id,
            repo=case.repo,
            passed=e_recall >= PASS_THRESHOLD,
            file_recall=e_recall,
            mrr=e_mrr,
            search_recall=0.0,
            explore_recall=e_recall,
            combined_recall=e_recall,
            found_files=e_found,
            missed_files=[f for f in case.expected_files if f not in e_found],
            latency_ms=latency_ms,
            n_results=len(explore_hits),
        )

    return Result(
        case_id=case.id,
        repo=case.repo,
        passed=c_recall >= PASS_THRESHOLD,
        file_recall=c_recall,
        mrr=s_mrr,
        search_recall=s_recall,
        explore_recall=e_recall,
        combined_recall=c_recall,
        found_files=c_found,
        missed_files=c_missed,
        latency_ms=latency_ms,
        n_results=len(combined),
    )


# ── Output ─────────────────────────────────────────────────────────────────────


def _print_result(r: Result) -> None:
    status = "PASS" if r.passed else "FAIL"
    case_id = r.case_id[:52]
    print(
        f"  {case_id:<52} {status}  "
        f"recall={r.file_recall:.2f}  mrr={r.mrr:.2f}  "
        f"search={r.search_recall:.2f}  explore={r.explore_recall:.2f}  "
        f"{r.latency_ms:.0f}ms"
    )
    if r.missed_files:
        print(f"  {'':52}       missed={r.missed_files}")


def _print_summary(results: list[Result], label: str) -> None:
    if not results:
        return
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    mean_recall = sum(r.file_recall for r in results) / total
    search_mrr = sum(r.mrr for r in results) / total
    mean_search = sum(r.search_recall for r in results) / total
    mean_explore = sum(r.explore_recall for r in results) / total
    mean_latency = sum(r.latency_ms for r in results) / total
    p50 = sorted(r.latency_ms for r in results)[total // 2]
    p95 = sorted(r.latency_ms for r in results)[int(total * 0.95)]

    sep = "─" * 70
    print(f"\n{sep}")
    print(f"  {label} — {passed}/{total} passed ({100 * passed // total}%)")
    print(f"  File recall:     {mean_recall:.3f}")
    print(f"  Search MRR:      {search_mrr:.3f}")
    print(f"  Search recall:   {mean_search:.3f}")
    print(f"  Explore recall:  {mean_explore:.3f}")
    print(f"  Latency (mean):  {mean_latency:.0f}ms  p50={p50:.0f}ms  p95={p95:.0f}ms")
    print(sep)


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="File-level retrieval eval against SWE-bench yaml datasets"
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        required=True,
        metavar="YAML",
        help="One or more yaml dataset files (e.g. evals/dataset/ts_python_test_cases.yaml)",
    )
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="Path to JIDRA graph.db for the target repo",
    )
    parser.add_argument(
        "--repo",
        default=None,
        metavar="ORG/REPO",
        help="Filter to a single repo slug (e.g. django/django)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max number of cases to run",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="JSON",
        help="Write per-case results to JSON file",
    )
    parser.add_argument(
        "--explore-only",
        action="store_true",
        help="Use only explore (mirrors agent behavior); skip search+flow",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}")

    yaml_paths = [Path(p) for p in args.cases]
    for p in yaml_paths:
        if not p.exists():
            sys.exit(f"Cases file not found: {p}")

    cases = load_cases(yaml_paths, args.repo, args.limit)
    if not cases:
        sys.exit(f"No cases found (repo filter: {args.repo!r})")

    mode = "explore-only" if args.explore_only else "search+explore+flow"
    print(f"\nSWE-bench Retrieval Eval  [{mode}]")
    print(f"DB:     {db_path}")
    print(f"Cases:  {len(cases)}")
    if args.repo:
        print(f"Repo:   {args.repo}")
    print()

    engine = JidraEngine(str(db_path), variant="main")

    results: list[Result] = []
    repos_seen: dict[str, list[Result]] = {}

    for case in cases:
        r = run_case(case, engine, explore_only=args.explore_only)
        _print_result(r)
        results.append(r)
        repos_seen.setdefault(case.repo, []).append(r)

    # Per-repo summaries if multiple repos
    if len(repos_seen) > 1:
        for repo, repo_results in sorted(repos_seen.items()):
            _print_summary(repo_results, repo)

    _print_summary(results, args.repo or "aggregate")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8"
        )
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
