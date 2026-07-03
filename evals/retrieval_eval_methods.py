#!/usr/bin/env python3
"""
retrieval_eval_all.py — JIDRA method-level retrieval eval across 4 repos.

Mirrors codegraph's __tests__/evaluation/runner.ts with method-level
symbols only (fair comparison for JIDRA's method-centric architecture).

12 cases per repo: 6 search + 6 explore.
Explore runs both `explore` AND `get_agent_flow`, reports separately + combined.

Usage:
    # Run one repo
    PYTHONPATH=src python evals/retrieval_eval_all.py \
      --repo mtkruto \
      --db /path/to/MTKruto/graph.db \
      --out evals/results_mtkruto.json

    # Compare against codegraph JSON
    PYTHONPATH=src python evals/retrieval_eval_all.py \
      --repo mtkruto \
      --db /path/to/graph.db \
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

    # ── MTKruto ──────────────────────────────────────────────────────
    cases += [
        TestCase("mtkruto-search-sendMessage",    "sendMessage",    "search", ["sendMessage"],    "mtkruto", ["method"]),
        TestCase("mtkruto-search-invoke",         "invoke",         "search", ["invoke"],         "mtkruto", ["method"]),
        TestCase("mtkruto-search-serialize",      "serializeObject","search", ["serializeObject"],"mtkruto", ["method"]),
        TestCase("mtkruto-search-getMe",          "getMe",          "search", ["getMe"],          "mtkruto", ["method"]),
        TestCase("mtkruto-search-signIn",         "signIn",         "search", ["signIn"],         "mtkruto", ["method"]),
        TestCase("mtkruto-search-forwardMessages","forwardMessages","search", ["forwardMessages"],"mtkruto", ["method"]),
        TestCase("mtkruto-explore-send-flow",
            "How does calling sendMessage get serialized and sent over the network transport?",
            "explore", ["sendMessage", "serializeObject", "send"], "mtkruto"),
        TestCase("mtkruto-explore-session",
            "How does the encrypted session handle encryption and transport?",
            "explore", ["send", "receive"], "mtkruto"),
        TestCase("mtkruto-explore-invoke",
            "How does the client invoke a TL function?",
            "explore", ["invoke", "sendMessage"], "mtkruto"),
        TestCase("mtkruto-explore-connection",
            "How does the client establish and manage a connection?",
            "explore", ["connect", "send"], "mtkruto"),
        TestCase("mtkruto-explore-auth",
            "How does user authentication and sign in work?",
            "explore", ["signIn", "getMe"], "mtkruto"),
        TestCase("mtkruto-explore-messaging",
            "How does sending and forwarding messages work?",
            "explore", ["sendMessage", "forwardMessages"], "mtkruto"),
    ]

    # ── Trezor Suite ─────────────────────────────────────────────────
    cases += [
        TestCase("trezor-search-signTransaction","signTransaction","search", ["signTransaction"],"trezor", ["method"]),
        TestCase("trezor-search-getAddress",     "getAddress",     "search", ["getAddress"],    "trezor", ["method"]),
        TestCase("trezor-search-getFeatures",    "getFeatures",    "search", ["getFeatures"],   "trezor", ["method"]),
        TestCase("trezor-search-useSendForm",    "useSendForm",    "search", ["useSendForm"],   "trezor", ["method"]),
        TestCase("trezor-search-applySettings",  "applySettings",  "search", ["applySettings"], "trezor", ["method"]),
        TestCase("trezor-search-estimateFee",    "estimateFee",    "search", ["estimateFee"],   "trezor", ["method"]),
        TestCase("trezor-explore-tx-flow",
            "How does a transaction get signed and broadcast to the network?",
            "explore", ["signTransaction", "pushTransaction"], "trezor"),
        TestCase("trezor-explore-device",
            "How does the app connect to and get info from a Trezor device?",
            "explore", ["getFeatures", "getAddress"], "trezor"),
        TestCase("trezor-explore-send-form",
            "How does the send form compose and submit a transaction?",
            "explore", ["useSendForm", "signTransaction"], "trezor"),
        TestCase("trezor-explore-fees",
            "How are transaction fees estimated and applied?",
            "explore", ["estimateFee", "composeTransaction"], "trezor"),
        TestCase("trezor-explore-settings",
            "How does the user change device settings and PIN?",
            "explore", ["applySettings", "changePin"], "trezor"),
        TestCase("trezor-explore-account",
            "How does the app fetch account info and balance?",
            "explore", ["getAccountInfo", "getAddress"], "trezor"),
    ]

    # ── PostyBirb ─────────────────────────────────────────────────────
    cases += [
        TestCase("postybirb-search-validateSubmission","validateSubmission","search",["validateSubmission"],"postybirb",["method"]),
        TestCase("postybirb-search-postSubmission",    "postSubmission",    "search",["postSubmission"],    "postybirb",["method"]),
        TestCase("postybirb-search-uploadFile",        "uploadFile",        "search",["uploadFile"],        "postybirb",["method"]),
        TestCase("postybirb-search-login",             "login",             "search",["login"],             "postybirb",["method"]),
        TestCase("postybirb-search-updateSubmission",  "updateSubmission",  "search",["updateSubmission"],  "postybirb",["method"]),
        TestCase("postybirb-search-post",              "post",              "search",["post"],              "postybirb",["method"]),
        TestCase("postybirb-explore-submit",
            "How does a submission get validated and posted to a website?",
            "explore", ["validateSubmission", "postSubmission"], "postybirb"),
        TestCase("postybirb-explore-file",
            "How does file uploading work when creating a post?",
            "explore", ["uploadFile", "post"], "postybirb"),
        TestCase("postybirb-explore-auth",
            "How does the app authenticate and log in to a website account?",
            "explore", ["login", "logout"], "postybirb"),
        TestCase("postybirb-explore-website",
            "How does the app know which websites are available and supported?",
            "explore", ["validateSubmission", "login"], "postybirb"),
        TestCase("postybirb-explore-update",
            "How does updating or editing a submission work?",
            "explore", ["updateSubmission", "validateSubmission"], "postybirb"),
        TestCase("postybirb-explore-posting",
            "How does the posting pipeline work end to end?",
            "explore", ["post", "postSubmission"], "postybirb"),
    ]



    # ── Shapeshift Web ────────────────────────────────────────────────
    # Symbols verified against shapeshift/web repo
    cases += [
        TestCase("shapeshift-search-getTradeQuote",       "getTradeQuote",       "search",["getTradeQuote"],       "shapeshift",["method"]),
        TestCase("shapeshift-search-signTransaction",      "signTransaction",     "search",["signTransaction"],     "shapeshift",["method"]),
        TestCase("shapeshift-search-broadcastTransaction", "broadcastTransaction","search",["broadcastTransaction"],"shapeshift",["method"]),
        TestCase("shapeshift-search-estimateFees",         "estimateFees",        "search",["estimateFees"],        "shapeshift",["method"]),
        TestCase("shapeshift-search-getRates",             "getRates",            "search",["getRates"],            "shapeshift",["method"]),
        TestCase("shapeshift-search-getAssets",            "getAssets",           "search",["getAssets"],           "shapeshift",["method"]),
        TestCase("shapeshift-explore-quote",
            "How does getting a trade quote work end to end?",
            "explore", ["getTradeQuote", "getQuote"], "shapeshift"),
        TestCase("shapeshift-explore-sign-broadcast",
            "How does a transaction get signed and broadcast to the network?",
            "explore", ["signTransaction", "broadcastTransaction"], "shapeshift"),
        TestCase("shapeshift-explore-assets",
            "How does the app fetch and display available assets?",
            "explore", ["getAssets", "getAsset"], "shapeshift"),
        TestCase("shapeshift-explore-fees",
            "How are network fees estimated for a swap?",
            "explore", ["estimateFees", "getNetworkFee"], "shapeshift"),
        TestCase("shapeshift-explore-rates",
            "How does the app get current exchange rates for a trade?",
            "explore", ["getRates", "getTradeRate"], "shapeshift"),
        TestCase("shapeshift-explore-trade",
            "How does the full swap trade flow work from quote to execution?",
            "explore", ["getTradeQuote", "broadcastTransaction"], "shapeshift"),
    ]

    return cases


def _extract_names(results: list[dict]) -> set[str]:
    names = set()
    for r in results:
        for val in [
            r.get("method_name"),
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
    ex_r,   ex_f,   ex_m   = _recall(case.expected_symbols, _extract_names(explore_res))
    fl_r,   fl_f,   fl_m   = _recall(case.expected_symbols, _extract_names(flow_res))
    comb_r, comb_f, comb_m = _recall(case.expected_symbols,
                                      _extract_names(explore_res) | _extract_names(flow_res))
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
    repo_cases = [c for c in cases if c.repo == repo_name]
    if not repo_cases:
        return []
    if not db_path.exists():
        print(f"  ⚠  no graph.db at {db_path} — skipping")
        return []

    engine  = JidraEngine(str(db_path), variant="main")
    results = []
    for case in repo_cases:
        r = run_case(case, engine)
        status = "PASS" if r.passed else "FAIL"
        if case.api == "search":
            detail = f"found={r.found}" if r.found else ""
            missed = f"missed={r.missed}" if r.missed else ""
            print(f"  {case.id:<50} {status}  recall={r.recall:.2f}  mrr={r.mrr:.2f}  {r.latency_ms:.0f}ms  {detail}  {missed}")
        else:
            print(f"  {case.id:<50} {status}  "
                  f"explore={r.explore_recall:.2f}  flow={r.flow_recall:.2f}  "
                  f"combined={r.combined_recall:.2f}  {r.latency_ms:.0f}ms")
            if r.missed:
                print(f"  {'':50}       missed={r.missed}")
        results.append(r)
    return results


def print_summary(results: list[EvalResult], repo: str) -> dict:
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
    print(f"  {repo} — {passed}/{total} passed ({passed/total*100:.0f}%)")
    print(f"  Mean Recall:    {mean_recall:.3f}")
    print(f"  Mean MRR:       {mean_mrr:.3f}  (search only)")
    if explore_res:
        ex   = sum(r.explore_recall  for r in explore_res) / len(explore_res)
        fl   = sum(r.flow_recall     for r in explore_res) / len(explore_res)
        comb = sum(r.combined_recall for r in explore_res) / len(explore_res)
        print(f"  Explore only:   {ex:.3f}  recall")
        print(f"  Flow only:      {fl:.3f}  recall")
        print(f"  Combined:       {comb:.3f}  recall")
    print("─" * 70)

    return {
        "repo": repo, "total": total, "passed": passed,
        "mean_recall": round(mean_recall, 4),
        "mean_mrr": round(mean_mrr, 4),
        "explore_only_recall":     round(sum(r.explore_recall  for r in explore_res) / len(explore_res), 4) if explore_res else 0,
        "flow_only_recall":        round(sum(r.flow_recall     for r in explore_res) / len(explore_res), 4) if explore_res else 0,
        "combined_explore_recall": round(sum(r.combined_recall for r in explore_res) / len(explore_res), 4) if explore_res else 0,
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

    print(f"\n── Comparison vs CodeGraph ──────────────────────────────────────────")
    print(f"  {'metric':<30} {'JIDRA':>10} {'CodeGraph':>12}")
    print(f"  {'─'*30} {'─'*10} {'─'*12}")
    print(f"  {'passed':<30} {jidra_passed}/{len(results):>6} {f'{cg_passed}/{cg_total}':>12}")
    print(f"  {'mean recall':<30} {jidra_recall:>10.3f} {cg_recall if isinstance(cg_recall, str) else f'{cg_recall:.3f}':>12}")
    print(f"  {'mean MRR (search)':<30} {jidra_mrr:>10.3f} {cg_mrr if isinstance(cg_mrr, str) else f'{cg_mrr:.3f}':>12}")
    print(f"  {'explore+flow combined':<30} {comb_recall:>10.3f} {'(density)':>12}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo",    required=True, choices=["mtkruto","trezor","postybirb","shapeshift"])
    ap.add_argument("--db",      required=True)
    ap.add_argument("--out",     help="Write JSON report")
    ap.add_argument("--compare", help="Codegraph JSON report for comparison")
    args = ap.parse_args()

    cases   = make_test_cases()
    db_path = Path(args.db)

    print(f"\nJIDRA Method-Level Retrieval Eval — {args.repo}")
    print(f"DB:    {db_path}")
    print(f"Cases: {len([c for c in cases if c.repo == args.repo])}")
    print(f"Pass threshold: recall >= {PASS_THRESHOLD}")
    print()

    results = run_repo(args.repo, db_path, cases)
    summary = print_summary(results, args.repo)

    if args.compare:
        compare_with_codegraph(results, Path(args.compare))

    if args.out and results:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "repo": args.repo, "eval_type": "method-level",
            "summary": summary,
            "results": [asdict(r) for r in results],
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.out}")


if __name__ == "__main__":
    main()
