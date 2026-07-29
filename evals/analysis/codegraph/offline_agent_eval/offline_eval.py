#!/usr/bin/env python3
"""
JIDRA Offline Ranking Eval
--------------------------
Config-driven eval that measures MRR, recall, and pass rate across repos
without an API key. Tests search, explore, find_callers, and get_flow.

Usage:
  python evals/offline_eval.py                          # all repos, default config
  python evals/offline_eval.py --repo mtkruto           # single repo
  python evals/offline_eval.py --cases evals/configs/cases.yaml
  python evals/offline_eval.py --ranking cfg.yaml       # custom RankingConfig
  python evals/offline_eval.py --baseline results.json  # compare vs saved baseline
  python evals/offline_eval.py --out results.json       # save results

Ranking config YAML example (all fields optional, unset = default):
  exact_name_boost: 2.0
  path_file_boost: 1.5
  nl_use_or: true
  nl_strip_stopwords: true
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jidra.engine.ranking as _ranking_mod
from jidra.engine.engine import JidraEngine
from jidra.engine.ranking import DEFAULT_CONFIG, RankingConfig

PASS_THRESHOLD = 0.5
SEARCH_LIMIT = 20
EXPLORE_TOP_N = 10


# ── data classes ─────────────────────────────────────────────────────────────


@dataclass
class Case:
    id: str
    repo: str
    tool: str  # search | explore | find_callers | get_flow | negative
    query: str
    expected: list[str]
    pass_threshold: float = PASS_THRESHOLD
    rank_threshold: int = SEARCH_LIMIT


@dataclass
class CaseResult:
    case_id: str
    repo: str
    tool: str
    passed: bool
    recall: float
    mrr: float  # 0.0 for non-search tools
    latency_ms: float
    found: list[str]
    missed: list[str]
    rank: int  # rank of first hit (search only)


@dataclass
class RepoSummary:
    repo: str
    total: int
    passed: int
    mean_recall: float
    mean_mrr: float  # search cases only
    explore_recall: float  # explore cases only
    pass_rate: float


# ── helpers ──────────────────────────────────────────────────────────────────


def _extract_names(results: list[dict]) -> list[str]:
    names = []
    for r in results:
        for val in [
            r.get("method_name"),
            (r.get("class_full_name") or "").split(".")[-1],
            (r.get("signature") or "").split("#")[0].split(".")[-1],
        ]:
            if val:
                names.append(val.lower())
    return names


def _score(case: Case, result_names: list[str]) -> tuple[float, float, list[str], list[str], int]:
    """Returns (recall, mrr, found, missed, first_rank)."""
    found, missed = [], []
    first_rank = 0
    for sym in case.expected:
        s = sym.lower()
        try:
            idx = result_names.index(s)
            found.append(sym)
            if first_rank == 0:
                first_rank = idx + 1
        except ValueError:
            partial = next((j + 1 for j, n in enumerate(result_names) if s in n), None)
            if partial:
                found.append(sym)
                if first_rank == 0:
                    first_rank = partial
            else:
                missed.append(sym)
    recall = len(found) / len(case.expected) if case.expected else 0.0
    mrr = 1.0 / first_rank if first_rank else 0.0
    return recall, mrr, found, missed, first_rank


# ── engine wrapper ────────────────────────────────────────────────────────────


def run_case(case: Case, engine: JidraEngine) -> CaseResult:
    t0 = time.perf_counter()

    if case.tool == "search":
        raw = engine.search(case.query, limit=SEARCH_LIMIT)
        rows = raw.get("results", [])
        names = _extract_names(rows)

    elif case.tool == "explore":
        raw = engine.explore(case.query, top_n=EXPLORE_TOP_N)
        rows = raw.get("results", [])
        names = _extract_names(rows)

    elif case.tool == "find_callers":
        raw = engine.find_callers(case.query)
        callers = raw.get("callers", [])
        names = [c.get("method_name", "").lower() for c in callers]

    elif case.tool == "get_flow":
        raw = engine.get_agent_flow(case.query)
        callees = raw.get("callees", [])
        names = [c.get("method_name", "").lower() for c in callees]

    elif case.tool == "negative":
        raw = engine.search(case.query, limit=SEARCH_LIMIT)
        rows = raw.get("results", [])
        names = _extract_names(rows)
        # negative: expect NO match — recall inverted
        found_any = any(e.lower() in " ".join(names) for e in case.expected)
        latency_ms = (time.perf_counter() - t0) * 1000
        return CaseResult(
            case_id=case.id,
            repo=case.repo,
            tool=case.tool,
            passed=not found_any,
            recall=0.0 if found_any else 1.0,
            mrr=0.0,
            latency_ms=latency_ms,
            found=[],
            missed=case.expected if not found_any else [],
            rank=0,
        )
    else:
        raise ValueError(f"Unknown tool: {case.tool}")

    latency_ms = (time.perf_counter() - t0) * 1000
    recall, mrr, found, missed, rank = _score(case, names)
    passed = recall >= case.pass_threshold

    return CaseResult(
        case_id=case.id,
        repo=case.repo,
        tool=case.tool,
        passed=passed,
        recall=recall,
        mrr=mrr,
        latency_ms=latency_ms,
        found=found,
        missed=missed,
        rank=rank,
    )


# ── summary ───────────────────────────────────────────────────────────────────


def summarise(results: list[CaseResult]) -> list[RepoSummary]:
    repos: dict[str, list[CaseResult]] = {}
    for r in results:
        repos.setdefault(r.repo, []).append(r)

    summaries = []
    for repo, cases in repos.items():
        search_cases = [c for c in cases if c.tool == "search"]
        explore_cases = [c for c in cases if c.tool == "explore"]
        mean_mrr = sum(c.mrr for c in search_cases) / len(search_cases) if search_cases else 0.0
        explore_recall = (
            sum(c.recall for c in explore_cases) / len(explore_cases) if explore_cases else 0.0
        )
        summaries.append(
            RepoSummary(
                repo=repo,
                total=len(cases),
                passed=sum(1 for c in cases if c.passed),
                mean_recall=sum(c.recall for c in cases) / len(cases),
                mean_mrr=mean_mrr,
                explore_recall=explore_recall,
                pass_rate=sum(1 for c in cases if c.passed) / len(cases),
            )
        )
    return summaries


# ── diff vs baseline ─────────────────────────────────────────────────────────


def diff_baseline(
    current: list[CaseResult],
    baseline_path: str,
) -> None:
    with open(baseline_path) as f:
        baseline_raw = json.load(f)
    baseline = {r["case_id"]: r for r in baseline_raw.get("cases", [])}

    regressions, improvements = [], []
    for r in current:
        b = baseline.get(r.case_id)
        if not b:
            continue
        if r.passed and not b["passed"]:
            improvements.append(f"  + {r.case_id}  recall {b['recall']:.2f}→{r.recall:.2f}")
        elif not r.passed and b["passed"]:
            regressions.append(f"  - {r.case_id}  recall {b['recall']:.2f}→{r.recall:.2f}")
        elif abs(r.mrr - b["mrr"]) >= 0.1:
            direction = "↑" if r.mrr > b["mrr"] else "↓"
            improvements.append(f"  {direction} {r.case_id}  MRR {b['mrr']:.2f}→{r.mrr:.2f}")

    if improvements:
        print("\nImprovements:")
        print("\n".join(improvements))
    if regressions:
        print("\nRegressions:")
        print("\n".join(regressions))
    if not improvements and not regressions:
        print("\nNo change vs baseline.")


# ── printing ──────────────────────────────────────────────────────────────────


def print_results(results: list[CaseResult], summaries: list[RepoSummary]) -> None:
    current_repo = None
    for r in results:
        if r.repo != current_repo:
            current_repo = r.repo
            print(f"\n{'─' * 70}")
            print(f"  {r.repo}")
            print(f"{'─' * 70}")
        status = "PASS" if r.passed else "FAIL"
        missed_str = f"  missed={r.missed}" if r.missed else ""
        if r.tool == "search":
            print(
                f"  {r.case_id:<48} {status}  recall={r.recall:.2f}  mrr={r.mrr:.2f}  rank={r.rank}  {r.latency_ms:.0f}ms{missed_str}"
            )
        else:
            print(
                f"  {r.case_id:<48} {status}  recall={r.recall:.2f}  {r.latency_ms:.0f}ms{missed_str}"
            )

    print(f"\n{'═' * 70}")
    print(f"  {'REPO':<15} {'PASS':>6}  {'RECALL':>7}  {'MRR':>6}  {'EXPLORE':>8}")
    print(f"{'─' * 70}")
    total_pass = total_cases = 0
    for s in summaries:
        total_pass += s.passed
        total_cases += s.total
        print(
            f"  {s.repo:<15} {s.passed}/{s.total:>2}    {s.mean_recall:.3f}    {s.mean_mrr:.3f}    {s.explore_recall:.3f}"
        )
    print(f"{'─' * 70}")
    # all_results = [r for s in summaries for r in []]  # just for totals
    agg_recall = sum(s.mean_recall * s.total for s in summaries) / total_cases
    agg_mrr = sum(s.mean_mrr for s in summaries) / len(summaries)
    agg_explore = sum(s.explore_recall for s in summaries) / len(summaries)
    print(
        f"  {'TOTAL':<15} {total_pass}/{total_cases:>2}    {agg_recall:.3f}    {agg_mrr:.3f}    {agg_explore:.3f}"
    )
    print(f"{'═' * 70}\n")


# ── main ──────────────────────────────────────────────────────────────────────


def load_cases(path: str, repo_filter: str | None) -> list[Case]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    cases = []
    for c in raw["cases"]:
        if repo_filter and c["repo"] != repo_filter:
            continue
        cases.append(
            Case(
                id=c["id"],
                repo=c["repo"],
                tool=c["tool"],
                query=c["query"],
                expected=c["expected"],
                pass_threshold=c.get("pass_threshold", PASS_THRESHOLD),
                rank_threshold=c.get("rank_threshold", SEARCH_LIMIT),
            )
        )
    return cases


def load_repos(path: str) -> dict[str, str]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return {name: cfg["db"] for name, cfg in raw["repos"].items()}


def load_ranking_config(path: str | None) -> RankingConfig:
    if not path:
        return DEFAULT_CONFIG
    with open(path) as f:
        overrides = yaml.safe_load(f) or {}
    cfg = RankingConfig(**{k: v for k, v in overrides.items() if hasattr(RankingConfig, k) or True})
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="JIDRA offline ranking eval")
    parser.add_argument("--repo", help="Run only this repo")
    parser.add_argument("--cases", default="evals/configs/cases.yaml")
    parser.add_argument("--repos", default="evals/configs/repos.yaml")
    parser.add_argument("--ranking", help="Path to RankingConfig YAML override")
    parser.add_argument("--baseline", help="Path to baseline results JSON for diff")
    parser.add_argument("--out", help="Save results JSON to this path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # Load ranking config override if provided
    if args.ranking:
        with open(args.ranking) as f:
            overrides = yaml.safe_load(f) or {}
        for k, v in overrides.items():
            if hasattr(_ranking_mod.DEFAULT_CONFIG, k):
                setattr(_ranking_mod.DEFAULT_CONFIG, k, v)
        print(f"Ranking config: {_ranking_mod.DEFAULT_CONFIG.describe()}\n")

    cases = load_cases(args.cases, args.repo)
    repos = load_repos(args.repos)

    # Group cases by repo, build engines lazily
    engines: dict[str, JidraEngine] = {}
    results: list[CaseResult] = []

    repos_needed = sorted({c.repo for c in cases})
    print(f"JIDRA Offline Eval — {len(cases)} cases across {len(repos_needed)} repos\n")

    for case in cases:
        if case.repo not in engines:
            db = repos.get(case.repo)
            if not db:
                print(f"  SKIP {case.id} — no DB configured for repo '{case.repo}'")
                continue
            engines[case.repo] = JidraEngine(db)

        engine = engines.get(case.repo)
        if not engine:
            continue

        try:
            result = run_case(case, engine)
        except Exception as e:
            print(f"  ERROR {case.id}: {e}")
            continue

        results.append(result)
        if args.verbose:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"  {result.case_id:<50} {status}  recall={result.recall:.2f}  mrr={result.mrr:.2f}"
            )

    summaries = summarise(results)
    print_results(results, summaries)

    if args.baseline:
        diff_baseline(results, args.baseline)

    if args.out:
        out = {
            "cases": [asdict(r) for r in results],
            "summaries": [asdict(s) for s in summaries],
            "ranking_config": _ranking_mod.DEFAULT_CONFIG.describe(),
        }
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Results saved to {args.out}")


if __name__ == "__main__":
    main()
