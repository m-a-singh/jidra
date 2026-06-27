#!/usr/bin/env python3
"""Agent-in-loop eval on JIDRA's OWN Python source — JIDRA vs CodeGraph.

Purpose: catch regressions in JIDRA's *Python* parsing/resolution by giving a
real coding agent one backend's tools on this repo and scoring it. Ground truth
comes from the JIDRA graph.db (same oracle machinery as the Java agent_eval).

Setup (both indexes over THIS repo):
    ./venv/bin/python -m jidra.cli index --codebase . --output /tmp/jidra_py.db --force
    codegraph index .            # writes .codegraph/codegraph.db here

Run:
    ./venv/bin/python scripts/agent_eval_py.py \
        --graph /tmp/jidra_py.db --codebase . [--model claude-haiku-4-5-20251001]
    ./venv/bin/python scripts/agent_eval_py.py --graph /tmp/jidra_py.db --selfcheck
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_eval as ae  # noqa: E402
from agent_eval import Oracle, Task, _lc  # noqa: E402


# ---------------------------------------------------------------------------
# Python-specific tasks. GT verified against /tmp/jidra_py.db (this repo):
#   load_graph <- 13 callers · build_mcp -> {dispatch_tool, visible_tool_names,
#   _maybe_add_stale_hint, _log_session_call} · reindex_all_tenants ABSENT ·
#   query_by_annotation / JidraEngine PRESENT.
# ---------------------------------------------------------------------------
def _callees(o: Oracle, caller: str) -> set[str]:
    rows = o.conn.execute(
        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
           JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           WHERE e.variant='main' AND caller.method_name=?""",
        (caller,),
    ).fetchall()
    return {r[0] for r in rows}


def make_python_tasks() -> list[Task]:
    tasks: list[Task] = []

    # PY1 — caller / impact analysis. load_graph has many real callers.
    def py1(ans: str, o: Oracle) -> tuple[bool, str]:
        callers = {c.split(".")[-1].lower() for c in o.callers_of("load_graph")}
        callers |= {  # also accept the raw caller method names
            r[0].lower()
            for r in o.conn.execute(
                """SELECT DISTINCT cm.method_name FROM resolved_call_edges e
                   JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
                   JOIN methods cm ON cm.id=e.caller_method_id AND cm.variant=e.variant
                   WHERE e.variant='main' AND callee.method_name='load_graph'"""
            ).fetchall()
        }
        if not callers:
            return False, "no GT callers"
        a = _lc(ans)
        hit = {c for c in callers if len(c) > 4 and c in a}
        ok = len(hit) >= 2
        return ok, f"caller_hit {len(hit)} (need>=2)"

    tasks.append(
        Task(
            "PY1",
            "In this Python codebase, which functions call `load_graph`? "
            "List the calling functions — this is impact analysis before changing it.",
            py1,
        )
    )

    # PY2 — flow / direct callees of build_mcp.
    def py2(ans: str, o: Oracle) -> tuple[bool, str]:
        callees = {c.lower() for c in _callees(o, "build_mcp")}
        if not callees:
            return False, "no GT callees"
        a = _lc(ans)
        hit = {c for c in callees if len(c) > 4 and c in a}
        ok = len(hit) >= 1
        return ok, f"callee_hit {len(hit)}/{len(callees)}"

    tasks.append(
        Task(
            "PY2",
            "Trace the `build_mcp` function: what functions does it call directly? "
            "List the downstream functions it invokes.",
            py2,
        )
    )

    # PY3 — negative / hallucination resistance. Function does not exist.
    def py3(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = "reindex_all_tenants" in o.method_names
        a = _lc(ans).replace("*", "").replace("_", "")  # strip md emphasis
        says_absent = any(
            k in a
            for k in (
                "does not exist",
                "doesn't exist",
                "no such",
                "not found",
                "could not find",
                "couldn't find",
                "no function",
                "not present",
                "no method",
                "unable to find",
                "did not find",
            )
        )
        ok = (not exists) and says_absent
        return ok, f"exists={exists} says_absent={says_absent}"

    tasks.append(
        Task(
            "PY3",
            "Explain what the function `reindex_all_tenants()` does in this codebase "
            "and what it calls. If it is not present, say so explicitly.",
            py3,
        )
    )

    # PY4 — definition / resolution. query_by_annotation lives on JidraEngine in engine.py.
    def py4(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = "query_by_annotation" in o.method_names
        a = _lc(ans)
        located = any(k in a for k in ("engine.py", "jidraengine", "engine"))
        purpose = any(
            k in a
            for k in ("annotation", "framework_role", "framework role", "decorator")
        )
        ok = exists and located and purpose
        return ok, f"exists={exists} located={located} purpose={purpose}"

    tasks.append(
        Task(
            "PY4",
            "Where is the `query_by_annotation` method defined (which file/class) and "
            "what does it do? Be specific about the file.",
            py4,
        )
    )

    return tasks


def selfcheck(graph: str) -> bool:
    o = Oracle.load(graph)
    load_callers = o.callers_of("load_graph")
    build_callees = _callees(o, "build_mcp")
    checks = [
        ("PY1 load_graph callers >=2", len(load_callers) >= 2, f"{len(load_callers)}"),
        ("PY2 build_mcp callees >0", len(build_callees) > 0, f"{len(build_callees)}"),
        (
            "PY3 reindex_all_tenants ABSENT",
            "reindex_all_tenants" not in o.method_names,
            "absent" if "reindex_all_tenants" not in o.method_names else "PRESENT!",
        ),
        (
            "PY4 query_by_annotation PRESENT",
            "query_by_annotation" in o.method_names,
            "found" if "query_by_annotation" in o.method_names else "missing",
        ),
    ]
    print("=== deterministic self-check (no LLM) — Python ===")
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'ok ' if ok else 'BAD'}] {name:36} {detail}")
    print("=== ALL GT RESOLVES ===" if all_ok else "=== FIX TASKS ===")
    return all_ok


async def run_async(args) -> None:
    oracle = Oracle.load(args.graph)
    client = ae.make_client()
    backends = [
        ae.jidra_backend(args.graph, args.codebase),
        ae.codegraph_backend(args.codebase),
    ]
    tasks = make_python_tasks()
    if args.tasks:
        want = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in want]

    results: list[dict] = []
    for task in tasks:
        for be in backends:
            print(
                f"\n── {task.id} / {be.name} ─────────────────────────────", flush=True
            )
            rr = await ae.run_agent(
                client, args.model, be, task.prompt, label=f"{task.id}/{be.name}"
            )
            rr.task = task.id
            if not rr.error:
                try:
                    ok, note = task.check(rr.answer, oracle)
                    rr.correct = ok
                except Exception as e:  # noqa: BLE001
                    note = f"check_error: {e!r}"
                rr.hallucinated = oracle.hallucinated_refs(rr.answer)
            else:
                note = "run_error"
            d = asdict(rr)
            d["check_note"] = note
            results.append(d)
            tag = "ERR" if rr.error else ("OK " if rr.correct else "XX ")
            print(
                f"    {tag} {be.name:9} calls={rr.tool_calls:2} tok={rr.total_tokens:5} "
                f"halluc={len(rr.hallucinated)} {note}",
                flush=True,
            )

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    ae._summary(results)
    print(f"\nwrote {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Agent-in-loop eval (Python): JIDRA vs CodeGraph"
    )
    ap.add_argument(
        "--graph", required=True, help="JIDRA python graph.db (also GT oracle)"
    )
    ap.add_argument("--codebase", help="repo root (CG reads its .codegraph here)")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--tasks", default="", help="comma list e.g. PY1,PY2")
    ap.add_argument("--out", default="eval_agent_results_python.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(0 if selfcheck(args.graph) else 1)
    if not args.codebase:
        ap.error("--codebase required (except with --selfcheck)")
    ae.VERBOSE = not args.quiet
    asyncio.run(run_async(args))


if __name__ == "__main__":
    main()
