#!/usr/bin/env python3
"""
retrieval_eval_methods.py — Method-level retrieval eval for JIDRA.

Uses only method-level symbols — the fair comparison for jidra which
is method-centric by design. Mirrors test-cases-mtkruto-methods.ts
for codegraph.

Usage:
    PYTHONPATH=src python evals/retrieval_eval_methods.py \
      --repo mtkruto \
      --db /path/to/graph.db \
      --out evals/jidra_retrieval_methods.json \
      --compare /path/to/codegraph_results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jidra.engine.engine import JidraEngine

PASS_THRESHOLD = 0.5
SEARCH_LIMIT   = 10
EXPLORE_TOP_N  = 20
FLOW_TOP_N     = 10
FLOW_DEPTH     = 3


@dataclass
class TestCase:
    id: str
    query: str
    api: str
    expected_symbols: list[str]
    repo: str
    kinds: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    repo: str
    api: str
    passed: bool
    recall: float
    mrr: float
    found: list[str]
    missed: list[str]
    node_count: int = 0
    latency_ms: float = 0.0
    explore_recall: float = 0.0
    flow_recall: float = 0.0
    combined_recall: float = 0.0


def make_test_cases() -> list[TestCase]:
    cases: list[TestCase] = []

    # 6 search — method symbols only
    cases += [
        TestCase("search-method-sendMessage",     "sendMessage",     "search", ["sendMessage"],     "mtkruto", ["method"]),
        TestCase("search-method-invoke",           "invoke",          "search", ["invoke"],          "mtkruto", ["method"]),
        TestCase("search-method-serializeObject",  "serializeObject", "search", ["serializeObject"], "mtkruto", ["method"]),
        TestCase("search-method-getMe",            "getMe",           "search", ["getMe"],           "mtkruto", ["method"]),
        TestCase("search-method-signIn",           "signIn",          "search", ["signIn"],          "mtkruto", ["method"]),
        TestCase("search-method-forwardMessages",  "forwardMessages", "search", ["forwardMessages"], "mtkruto", ["method"]),
    ]

    # 6 explore — expected symbols are methods only
    cases += [
        TestCase("explore-send-flow",
            "How does calling sendMessage get serialized and sent over the network transport?",
            "explore", ["sendMessage", "serializeObject", "send"], "mtkruto"),
        TestCase("explore-session",
            "How does the encrypted session handle encryption and transport?",
            "explore", ["send", "receive"], "mtkruto"),
        TestCase("explore-client-invoke",
            "How does the client invoke a TL function?",
            "explore", ["invoke", "sendMessage"], "mtkruto"),
        TestCase("explore-connection",
            "How does the client establish and manage a connection?",
            "explore", ["connect", "send"], "mtkruto"),
        TestCase("explore-auth",
            "How does user authentication and sign in work?",
            "explore", ["signIn", "getMe"], "mtkruto"),
        TestCase("explore-messaging",
            "How does sending and forwarding messages work?",
            "explore", ["sendMessage", "forwardMessages"], "mtkruto"),
    ]
    return cases


def _extract_names(results: list[dict]) -> set[str]:
    names = set()
    for r in results:
        for val in [
            r.get("method_name"),
            r.get("class_name"),
            (r.get("class_full_name") or "").split(".")[-1],
            (r.get("signature") or "").split("#")[0].split(".")[-1],
        ]:
            if val:
                names.add(val.lower())
    return names


def _recall(expected: list[str], names: set[str]) -> tuple[float, list[str], list[str]]:
    found, missed = [], []
    for sym in expected:
        s = sym.lower()
        (found if (s in names or any(s in n for n in names)) else missed).append(sym)
    return len(found) / len(expected) if expected else 0.0, found, missed


def score_search(case: TestCase, results: list[dict], latency_ms: float) -> EvalResult:
    result_names_list = []
    for r in results:
        for val in [
            r.get("method_name"),
            (r.get("class_full_name") or "").split(".")[-1],
            (r.get("signature") or "").split("#")[0].split(".")[-1],
        ]:
            if val:
                result_names_list.append(val.lower())

    found, missed = [], []
    first_rank = 0
    for i, sym in enumerate([s.lower() for s in case.expected_symbols]):
        try:
            idx = result_names_list.index(sym)
            found.append(case.expected_symbols[i])
            if first_rank == 0:
                first_rank = idx + 1
        except ValueError:
            partial = next((j + 1 for j, n in enumerate(result_names_list) if sym in n), None)
            if partial:
                found.append(case.expected_symbols[i])
                if first_rank == 0:
                    first_rank = partial
            else:
                missed.append(case.expected_symbols[i])

    recall = len(found) / len(case.expected_symbols) if case.expected_symbols else 0.0
    return EvalResult(
        case_id=case.id, repo=case.repo, api="search",
        passed=recall >= PASS_THRESHOLD,
        recall=recall, mrr=1.0 / first_rank if first_rank else 0.0,
        found=found, missed=missed, latency_ms=latency_ms,
    )


def score_explore_combined(case: TestCase, explore_res: list[dict],
                            flow_res: list[dict], latency_ms: float) -> EvalResult:
    ex_names   = _extract_names(explore_res)
    fl_names   = _extract_names(flow_res)
    comb_names = ex_names | fl_names

    ex_r,   ex_f,   ex_m   = _recall(case.expected_symbols, ex_names)
    fl_r,   fl_f,   fl_m   = _recall(case.expected_symbols, fl_names)
    comb_r, comb_f, comb_m = _recall(case.expected_symbols, comb_names)

    return EvalResult(
        case_id=case.id, repo=case.repo, api="explore",
        passed=comb_r >= PASS_THRESHOLD,
        recall=comb_r, mrr=0.0,
        found=comb_f, missed=comb_m,
        node_count=len(explore_res) + len(flow_res),
        latency_ms=latency_ms,
        explore_recall=ex_r, flow_recall=fl_r, combined_recall=comb_r,
    )


def run_flow_from_explore(engine: JidraEngine, query: str) -> list[dict]:
    seeds = engine.explore(query, top_n=5).get("results", [])
    nodes = []
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


def run_case(case: TestCase, engine: JidraEngine) -> EvalResult:
    t0 = time.perf_counter()
    if case.api == "search":
        raw  = engine.search(case.query, limit=SEARCH_LIMIT)
        hits = raw.get("results", raw.get("hits", []))
        return score_search(case, hits, (time.perf_counter() - t0) * 1000)
    else:
        explore_res = engine.explore(case.query, top_n=EXPLORE_TOP_N).get("results", [])
        flow_res    = run_flow_from_explore(engine, case.query)
        return score_explore_combined(case, explore_res, flow_res,
                                       (time.perf_counter() - t0) * 1000)


def run_repo(repo_name: str, db_path: Path, cases: list[TestCase]) -> list[EvalResult]:
    if not db_path.exists():
        print(f"  ⚠  {repo_name}: no graph.db at {db_path}")
        return []

    engine  = JidraEngine(str(db_path), variant="main")
    results = []
    for case in [c for c in cases if c.repo == repo_name]:
        r = run_case(case, engine)
        status = "PASS" if r.passed else "FAIL"
        if case.api == "search":
            detail = f"found={r.found}" if r.found else ""
            missed = f"missed={r.missed}" if r.missed else ""
            print(f"  {case.id:<40} {status}  recall={r.recall:.2f}  mrr={r.mrr:.2f}  {r.latency_ms:.0f}ms  {detail}  {missed}")
        else:
            print(f"  {case.id:<40} {status}  "
                  f"explore={r.explore_recall:.2f}  "
                  f"flow={r.flow_recall:.2f}  "
                  f"combined={r.combined_recall:.2f}  {r.latency_ms:.0f}ms")
            if r.missed:
                print(f"  {'':40}       missed={r.missed}")
        results.append(r)
    return results


def print_summary(results: list[EvalResult]) -> dict:
    if not results:
        return {}
    passed      = sum(1 for r in results if r.passed)
    total       = len(results)
    mean_recall = sum(r.recall for r in results) / total
    search_res  = [r for r in results if r.api == "search"]
    explore_res = [r for r in results if r.api == "explore"]
    mean_mrr    = sum(r.mrr for r in search_res) / len(search_res) if search_res else 0.0

    print()
    print("─" * 70)
    print(f"  Total:             {passed}/{total} passed ({passed/total*100:.0f}%)")
    print(f"  Mean Recall:       {mean_recall:.3f}")
    print(f"  Mean MRR:          {mean_mrr:.3f}  (search only)")
    if explore_res:
        ex   = sum(r.explore_recall  for r in explore_res) / len(explore_res)
        fl   = sum(r.flow_recall     for r in explore_res) / len(explore_res)
        comb = sum(r.combined_recall for r in explore_res) / len(explore_res)
        print(f"  Explore only:      {ex:.3f}  recall")
        print(f"  Flow only:         {fl:.3f}  recall")
        print(f"  Combined:          {comb:.3f}  recall")
    print("─" * 70)

    return {
        "total": total, "passed": passed,
        "mean_recall": round(mean_recall, 4),
        "mean_mrr": round(mean_mrr, 4),
        "explore_only_recall":    round(sum(r.explore_recall  for r in explore_res) / len(explore_res), 4) if explore_res else 0,
        "flow_only_recall":       round(sum(r.flow_recall     for r in explore_res) / len(explore_res), 4) if explore_res else 0,
        "combined_explore_recall":round(sum(r.combined_recall for r in explore_res) / len(explore_res), 4) if explore_res else 0,
    }


def compare_with_codegraph(results: list[EvalResult], cg_path: Path) -> None:
    with open(cg_path) as f:
        cg = json.load(f)
    cg_s = cg.get("summary", {})

    jidra_recall = sum(r.recall for r in results) / len(results)
    jidra_passed = sum(1 for r in results if r.passed)
    search_res   = [r for r in results if r.api == "search"]
    explore_res  = [r for r in results if r.api == "explore"]
    jidra_mrr    = sum(r.mrr for r in search_res) / len(search_res) if search_res else 0
    comb_recall  = sum(r.combined_recall for r in explore_res) / len(explore_res) if explore_res else 0

    cg_recall = cg_s.get("meanRecall", "—")
    cg_mrr    = cg_s.get("meanMRR",    "—")
    cg_passed = cg_s.get("passed",     "—")
    cg_total  = cg_s.get("total",      "—")

    print("\n── Comparison: JIDRA vs CodeGraph (method-level) ───────────────────")
    print(f"  {'metric':<30} {'JIDRA':>10} {'CodeGraph':>12}")
    print(f"  {'─'*30} {'─'*10} {'─'*12}")
    print(f"  {'passed':<30} {jidra_passed}/{len(results):>6} {f'{cg_passed}/{cg_total}':>12}")
    print(f"  {'mean recall':<30} {jidra_recall:>10.3f} {cg_recall if isinstance(cg_recall, str) else f'{cg_recall:.3f}':>12}")
    print(f"  {'mean MRR (search)':<30} {jidra_mrr:>10.3f} {cg_mrr if isinstance(cg_mrr, str) else f'{cg_mrr:.3f}':>12}")
    print(f"  {'explore+flow combined':<30} {comb_recall:>10.3f} {'(density)':>12}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo",    default="mtkruto")
    ap.add_argument("--db",      required=True)
    ap.add_argument("--out",     help="Write JSON report")
    ap.add_argument("--compare", help="Codegraph JSON report")
    args = ap.parse_args()

    cases   = make_test_cases()
    db_path = Path(args.db)

    print(f"\nJIDRA Method-Level Retrieval Eval — {args.repo}")
    print(f"DB:    {db_path}")
    print(f"Cases: {len([c for c in cases if c.repo == args.repo])}")
    print(f"Note:  search cases use method symbols only (fair comparison for method-centric graph)")
    print(f"Pass threshold: recall >= {PASS_THRESHOLD}")
    print()

    print(f"── {args.repo} ({db_path})")
    results = run_repo(args.repo, db_path, cases)
    summary = print_summary(results)

    if args.compare:
        compare_with_codegraph(results, Path(args.compare))

    if args.out:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "repo": args.repo, "eval_type": "method-level",
            "summary": summary,
            "results": [asdict(r) for r in results],
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
