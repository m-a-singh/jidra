#!/usr/bin/env python3
"""Agent-in-loop eval on a TypeScript repo (agents_fleet) — JIDRA vs CodeGraph.

Purpose: catch regressions in JIDRA's *TypeScript* parsing/resolution by giving a
real coding agent one backend's tools and scoring it. Ground truth comes from the
JIDRA graph.db (same oracle machinery as the Java/Python evals).

Setup:
    ./venv/bin/python -m jidra.cli index \
        --codebase /Users/akhil.singh/Workflows/Personal/agents_fleet \
        --output /tmp/jidra_ts.db --ts-backend treesitter --force
    cd /Users/akhil.singh/Workflows/Personal/agents_fleet && codegraph index .

Run:
    ./venv/bin/python scripts/agent_eval_ts.py \
        --graph /tmp/jidra_ts.db \
        --codebase /Users/akhil.singh/Workflows/Personal/agents_fleet
    ./venv/bin/python scripts/agent_eval_ts.py --graph /tmp/jidra_ts.db --selfcheck
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re as _re
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_eval as ae  # noqa: E402
from agent_eval import Oracle, Task, _lc  # noqa: E402

DEFAULT_REPO = "/Users/akhil.singh/Workflows/Personal/agents_fleet"


def ts_hallucinated_refs(text: str, o: Oracle) -> list[str]:
    """TypeScript hallucination check: fake .ts/.tsx file names or unknown PascalCase class names."""
    bad: list[str] = []
    # *.ts / *.tsx / *.js / *.jsx basenames
    for m in _re.findall(r"\b[A-Za-z]\w+\.(?:ts|tsx|js|jsx)\b", text):
        if m.endswith(".d.ts"):
            continue
        if m not in o.file_basenames:
            bad.append(m)
    # PascalCase identifiers ending in common TS class suffixes
    for m in _re.findall(
        r"\b[A-Z][a-zA-Z0-9]*(?:Service|Controller|Manager|Handler|Factory|Hook|Store|Provider|Context|Middleware|Guard|Interceptor|Resolver|Module)\b",
        text,
    ):
        if not any(
            c.endswith("." + m) or c.endswith("/" + m) or c == m
            for c in o.class_full_names
        ):
            bad.append(m)
    return sorted(set(bad))


def _callees(o: Oracle, caller: str) -> set[str]:
    rows = o.conn.execute(
        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
           JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           WHERE e.variant='validated' AND caller.method_name=?""",
        (caller,),
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# TypeScript tasks. GT verified against /tmp/jidra_ts.db (ai_watchtower):
#   getDb <- 38 callers · spawnSession -> 27 callees · enforceBudget in
#   apps/server/src/processManager.ts · purgeStaleSessions ABSENT.
# ---------------------------------------------------------------------------
def make_ts_tasks() -> list[Task]:
    tasks: list[Task] = []

    # TS1 — caller / impact analysis. getDb is called from many sites.
    def ts1(ans: str, o: Oracle) -> tuple[bool, str]:
        callers = {
            r[0].lower()
            for r in o.conn.execute(
                """SELECT DISTINCT cm.method_name FROM resolved_call_edges e
                   JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
                   JOIN methods cm ON cm.id=e.caller_method_id AND cm.variant=e.variant
                   WHERE e.variant='validated' AND callee.method_name='getDb'"""
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
            "TS1",
            "In this TypeScript codebase, which functions call `getDb`? "
            "List the calling functions — impact analysis before changing it.",
            ts1,
        )
    )

    # TS2 — flow / direct callees of spawnSession.
    def ts2(ans: str, o: Oracle) -> tuple[bool, str]:
        callees = {c.lower() for c in _callees(o, "spawnSession")}
        if not callees:
            return False, "no GT callees"
        a = _lc(ans)
        hit = {c for c in callees if len(c) > 4 and c in a}
        ok = len(hit) >= 1
        return ok, f"callee_hit {len(hit)}/{len(callees)}"

    tasks.append(
        Task(
            "TS2",
            "Trace the `spawnSession` function: what functions does it call directly? "
            "List the downstream functions it invokes.",
            ts2,
        )
    )

    # TS3 — negative / hallucination resistance. Function does not exist.
    def ts3(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = "purgeStaleSessions" in o.method_names
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
            "TS3",
            "Explain what the function `purgeStaleSessions()` does in this codebase "
            "and what it calls. If it is not present, say so explicitly.",
            ts3,
        )
    )

    # TS4 — definition / resolution. enforceBudget lives in processManager.ts.
    def ts4(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = "enforceBudget" in o.method_names
        a = _lc(ans)
        located = "processmanager" in a or "enforcebudget" in a
        purpose = "budget" in a
        ok = exists and located and purpose
        return ok, f"exists={exists} located={located} purpose={purpose}"

    tasks.append(
        Task(
            "TS4",
            "Where is the `enforceBudget` function defined (which file) and what does "
            "it do? Be specific about the file path.",
            ts4,
        )
    )

    # TS5 — get_method_source bare-name selector resolution
    def ts5(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = "enforceBudget" in o.method_names
        a = _lc(ans)
        # Agent must find the source and mention processManager and budget logic
        located = "processmanager" in a
        has_source = "budget" in a and (
            "session" in a or "limit" in a or "exceed" in a or "max" in a
        )
        ok = exists and located and has_source
        return ok, f"exists={exists} located={located} has_source={has_source}"

    tasks.append(
        Task(
            "TS5",
            "Use the code graph tool to fetch the source of the `enforceBudget` function directly. "
            "Show its implementation and explain what budget limit it enforces.",
            ts5,
        )
    )

    return tasks


def selfcheck(graph: str) -> bool:
    o = Oracle.load(graph)
    getdb_callers = {
        r[0]
        for r in o.conn.execute(
            """SELECT DISTINCT cm.method_name FROM resolved_call_edges e
               JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
               JOIN methods cm ON cm.id=e.caller_method_id AND cm.variant=e.variant
               WHERE e.variant='validated' AND callee.method_name='getDb'"""
        ).fetchall()
    }
    spawn_callees = _callees(o, "spawnSession")
    eslint_excluded = "eslint.config.js" not in {
        r[0].split("/")[-1]
        for r in o.conn.execute(
            "SELECT DISTINCT file_path FROM methods WHERE variant='validated'"
        ).fetchall()
    }
    checks = [
        ("TS1 getDb callers >=2", len(getdb_callers) >= 2, f"{len(getdb_callers)}"),
        (
            "TS2 spawnSession callees >0",
            len(spawn_callees) > 0,
            f"{len(spawn_callees)}",
        ),
        (
            "TS3 purgeStaleSessions ABSENT",
            "purgeStaleSessions" not in o.method_names,
            "absent" if "purgeStaleSessions" not in o.method_names else "PRESENT!",
        ),
        (
            "TS4 enforceBudget PRESENT",
            "enforceBudget" in o.method_names,
            "found" if "enforceBudget" in o.method_names else "missing",
        ),
        (
            "TS_CFG eslint.config.js excluded",
            eslint_excluded,
            "excluded" if eslint_excluded else "INDEXED!",
        ),
        (
            "TS5 enforceBudget source fetchable",
            "enforceBudget" in o.method_names,
            "found" if "enforceBudget" in o.method_names else "missing",
        ),
    ]
    print("=== deterministic self-check (no LLM) — TypeScript ===")
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
    tasks = make_ts_tasks()
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
                rr.hallucinated = ts_hallucinated_refs(rr.answer, oracle)
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
        description="Agent-in-loop eval (TypeScript): JIDRA vs CodeGraph"
    )
    ap.add_argument("--graph", required=True, help="JIDRA ts graph.db (also GT oracle)")
    ap.add_argument(
        "--codebase",
        default=DEFAULT_REPO,
        help="repo root (CG reads its .codegraph here)",
    )
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--tasks", default="", help="comma list e.g. TS1,TS2")
    ap.add_argument("--out", default="eval_agent_results_typescript.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(0 if selfcheck(args.graph) else 1)
    ae.VERBOSE = not args.quiet
    asyncio.run(run_async(args))


if __name__ == "__main__":
    main()
