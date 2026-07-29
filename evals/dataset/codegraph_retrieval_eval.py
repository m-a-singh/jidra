#!/usr/bin/env python3
"""
codegraph_retrieval_eval.py — run CodeGraph against SWE-bench yaml cases.

Queries CodeGraph's SQLite db (read-only) directly.
Produces JSON in the same format as swebench_retrieval_eval.py so the two
outputs can be fed into compare_results.py for JIDRA vs CodeGraph.

Requires:
  - CodeGraph index built: npx @colbymchenry/codegraph init <codebase>
  - Resulting db at <codebase>/.codegraph/codegraph.db

Usage:
    python evals/dataset/codegraph_retrieval_eval.py \
        --cases evals/dataset/ts_python_test_cases.yaml \
        --db /path/to/django/.codegraph/codegraph.db \
        --repo django/django \
        --out evals/results_django_cg.json

    python evals/dataset/codegraph_retrieval_eval.py \
        --cases evals/dataset/ts_go_test_cases.yaml \
        --db /path/to/gin/.codegraph/codegraph.db \
        --repo gin-gonic/gin \
        --limit 10 \
        --out evals/results_gin_cg.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

PASS_THRESHOLD = 0.5
SEARCH_LIMIT = 20
EXPLORE_TOP_N = 20


# ── Data ───────────────────────────────────────────────────────────────────────


@dataclass
class Case:
    id: str
    repo: str
    commit: str
    query: str
    expected_files: list[str]


@dataclass
class Result:
    case_id: str
    repo: str
    passed: bool
    file_recall: float
    mrr: float
    search_recall: float
    explore_recall: float
    combined_recall: float
    found_files: list[str]
    missed_files: list[str]
    latency_ms: float
    n_results: int


# ── Yaml loading ───────────────────────────────────────────────────────────────


def load_cases(yaml_paths: list[Path], repo_filter: str | None, limit: int | None) -> list[Case]:
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


# ── CodeGraph db queries (read-only) ──────────────────────────────────────────


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _search(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    """FTS search over nodes (name + qualified_name + docstring + signature)."""
    # Escape FTS special chars
    safe = query.replace('"', '""')
    try:
        rows = conn.execute(
            """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.kind, n.docstring
            FROM nodes_fts f
            JOIN nodes n ON n.id = f.id
            WHERE nodes_fts MATCH ? AND n.kind IN ('function','method','class')
            ORDER BY rank
            LIMIT ?
            """,
            (safe, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # fallback: LIKE search on name
        terms = [t for t in query.split() if len(t) > 2][:5]
        if not terms:
            return []
        where = " OR ".join("n.name LIKE ?" for _ in terms)
        params = [f"%{t}%" for t in terms] + [limit]
        rows = conn.execute(
            f"SELECT id, name, qualified_name, file_path, kind, docstring "
            f"FROM nodes n WHERE kind IN ('function','method','class') AND ({where}) LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def _explore(conn: sqlite3.Connection, query: str, top_n: int) -> list[dict]:
    """Broader explore: FTS + traverse 1-hop call edges from top seeds."""
    seeds = _search(conn, query, min(top_n, 5))
    if not seeds:
        return []

    seed_ids = [r["id"] for r in seeds]
    placeholders = ",".join("?" * len(seed_ids))

    # 1-hop neighbours via edges
    neighbours = conn.execute(
        f"""
        SELECT DISTINCT n.id, n.name, n.qualified_name, n.file_path, n.kind, n.docstring
        FROM edges e
        JOIN nodes n ON n.id = e.target OR n.id = e.source
        WHERE (e.source IN ({placeholders}) OR e.target IN ({placeholders}))
          AND n.kind IN ('function','method','class')
          AND n.id NOT IN ({placeholders})
        LIMIT ?
        """,
        seed_ids + seed_ids + seed_ids + [top_n],
    ).fetchall()

    seen = {r["id"] for r in seeds}
    result = list(seeds)
    for r in neighbours:
        if r["id"] not in seen:
            result.append(dict(r))
            seen.add(r["id"])
    return result[:top_n]


# ── Scoring ───────────────────────────────────────────────────────────────────


def _normalise(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").lower()


def _file_hit(result_files: list[str], expected: str) -> bool:
    norm_exp = _normalise(expected)
    exp_base = norm_exp.split("/")[-1]
    for rf in result_files:
        nrf = _normalise(rf)
        if nrf.endswith(norm_exp) or nrf == norm_exp:
            return True
        if nrf.split("/")[-1] == exp_base:
            return True
    return False


def _score(
    expected_files: list[str], results: list[dict]
) -> tuple[float, list[str], list[str], float]:
    result_files = [r.get("file_path", "") for r in results if r.get("file_path")]
    found, missed = [], []
    first_rank = 0
    for i, exp in enumerate(expected_files):
        if _file_hit(result_files, exp):
            found.append(exp)
            if first_rank == 0:
                first_rank = next(
                    (
                        j + 1
                        for j, r in enumerate(results)
                        if _file_hit([r.get("file_path", "")], exp)
                    ),
                    i + 1,
                )
        else:
            missed.append(exp)
    recall = len(found) / len(expected_files) if expected_files else 0.0
    mrr = 1.0 / first_rank if first_rank else 0.0
    return recall, found, missed, mrr


# ── Per-case runner ───────────────────────────────────────────────────────────


def run_case(
    case: Case,
    conn: sqlite3.Connection,
    search_only: bool = False,
    explore_only: bool = False,
) -> Result:
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
    if search_only:
        search_hits = _search(conn, case.query, SEARCH_LIMIT)
        explore_hits = []
    elif explore_only:
        search_hits = []
        explore_hits = _explore(conn, case.query, EXPLORE_TOP_N)
    else:
        search_hits = _search(conn, case.query, SEARCH_LIMIT)
        explore_hits = _explore(conn, case.query, EXPLORE_TOP_N)
    latency_ms = (time.perf_counter() - t0) * 1000

    seen: set[str] = set()
    combined: list[dict] = []
    for r in search_hits + explore_hits:
        if r["id"] not in seen:
            combined.append(r)
            seen.add(r["id"])

    s_recall, s_found, s_missed, s_mrr = _score(case.expected_files, search_hits)
    e_recall, e_found, _, e_mrr = _score(case.expected_files, explore_hits)
    c_recall, c_found, c_missed, _ = _score(case.expected_files, combined)

    if search_only:
        return Result(
            case_id=case.id,
            repo=case.repo,
            passed=s_recall >= PASS_THRESHOLD,
            file_recall=s_recall,
            mrr=s_mrr,
            search_recall=s_recall,
            explore_recall=0.0,
            combined_recall=s_recall,
            found_files=s_found,
            missed_files=s_missed,
            latency_ms=latency_ms,
            n_results=len(search_hits),
        )
    if explore_only:
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


# ── Output ────────────────────────────────────────────────────────────────────


def _print_result(r: Result) -> None:
    status = "PASS" if r.passed else "FAIL"
    cid = r.case_id[:52]
    print(
        f"  {cid:<52} {status}  "
        f"recall={r.file_recall:.2f}  mrr={r.mrr:.2f}  "
        f"search={r.search_recall:.2f}  explore={r.explore_recall:.2f}  "
        f"{r.latency_ms:.0f}ms"
    )
    if r.missed_files:
        print(f"  {'':52}       missed={r.missed_files}")


def _print_summary(results: list[Result], label: str) -> None:
    if not results:
        return
    n = len(results)
    passed = sum(1 for r in results if r.passed)
    latencies = sorted(r.latency_ms for r in results)
    mean_lat = sum(latencies) / n
    p50 = latencies[n // 2]
    p95 = latencies[int(n * 0.95)]
    sep = "─" * 70
    print(f"\n{sep}")
    print(f"  {label} — {passed}/{n} passed ({100 * passed // n}%)")
    print(f"  File recall:     {sum(r.file_recall for r in results) / n:.3f}")
    print(f"  Search MRR:      {sum(r.mrr for r in results) / n:.3f}")
    print(f"  Search recall:   {sum(r.search_recall for r in results) / n:.3f}")
    print(f"  Explore recall:  {sum(r.explore_recall for r in results) / n:.3f}")
    print(f"  Latency (mean):  {mean_lat:.0f}ms  p50={p50:.0f}ms  p95={p95:.0f}ms")
    print(sep)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run CodeGraph db against SWE-bench yaml cases (read-only)"
    )
    parser.add_argument(
        "--cases", nargs="+", required=True, metavar="YAML", help="Yaml dataset file(s)"
    )
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="Path to CodeGraph db (e.g. <repo>/.codegraph/codegraph.db)",
    )
    parser.add_argument("--repo", default=None, metavar="ORG/REPO", help="Filter to one repo slug")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Max cases to run")
    parser.add_argument("--out", default=None, metavar="JSON", help="Write results JSON")
    parser.add_argument(
        "--search-only", action="store_true", help="Run FTS search only, skip explore"
    )
    parser.add_argument(
        "--explore-only", action="store_true", help="Run explore only, skip FTS search"
    )
    args = parser.parse_args()

    if args.search_only and args.explore_only:
        sys.exit("--search-only and --explore-only are mutually exclusive")

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

    mode = (
        "search-only"
        if args.search_only
        else "explore-only"
        if args.explore_only
        else "search+explore"
    )
    print(f"\nCodeGraph Retrieval Eval  [{mode}]")
    print(f"DB:     {db_path}")
    print(f"Cases:  {len(cases)}")
    if args.repo:
        print(f"Repo:   {args.repo}")
    print()

    conn = _connect(db_path)
    results: list[Result] = []
    repos_seen: dict[str, list[Result]] = {}

    for case in cases:
        r = run_case(case, conn, search_only=args.search_only, explore_only=args.explore_only)
        _print_result(r)
        results.append(r)
        repos_seen.setdefault(case.repo, []).append(r)

    conn.close()

    if len(repos_seen) > 1:
        for repo, rr in sorted(repos_seen.items()):
            _print_summary(rr, repo)

    _print_summary(results, args.repo or "aggregate")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
