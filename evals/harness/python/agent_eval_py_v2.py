#!/usr/bin/env python3
"""Agent-in-loop eval on a Python repo — JIDRA vs CodeGraph (v2 harness).

Config-driven: tasks are defined in a JSON config file, not hardcoded.
Pass --config to point at any repo's eval config.
Hallucinations cause hard failure (correct=False) when count > halluc_max (default 0).

Usage (run from repo root):
    # Run against JIDRA's own Python codebase
    python evals/harness/python/agent_eval_py_v2.py \
        --graph    .jidra/graph.db \
        --codebase . \
        --config   evals/harness/python/jidra_python.json \
        --model    claude-sonnet-4-6 \
        --out      evals/harness/python/results/results_py_v2.json

    # Run specific tasks only
    python evals/harness/python/agent_eval_py_v2.py \
        --graph    .jidra/graph.db \
        --codebase . \
        --config   evals/harness/python/jidra_python.json \
        --tasks    PY1,PY3 \
        --out      evals/harness/python/results/results_py_v2.json

    # Validate ground-truth data in graph before running
    python evals/harness/python/agent_eval_py_v2.py \
        --graph  .jidra/graph.db \
        --config evals/harness/python/jidra_python.json \
        --selfcheck

Flags:
    --graph      Path to JIDRA graph.db  (required)
    --codebase   Repo root dir           (required unless --selfcheck)
    --config     JSON task config file   (required)
    --model      Anthropic model ID      (default: claude-haiku-4-5-20251001)
    --tasks      Comma list e.g. PY1,PY2 (default: all tasks in config)
    --out        JSON output path        (default: results/eval_agent_results_py_v2.json)
    --skill      Path to .md skill file  (appended to system prompt)
    --selfcheck  Validate graph data without running the agent
    --quiet      Suppress per-call logs
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

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "java")
)

import agent_eval as ae
from agent_eval import Oracle, Task, _lc

# ---------------------------------------------------------------------------
# Hallucination checker — Python
# Flags: .py filenames not in oracle + long snake_case names not in oracle
# ---------------------------------------------------------------------------

_py_allowlist_cache: dict[int, frozenset[str]] = {}


def _py_source_allowlist(oracle: Oracle) -> frozenset[str]:
    key = id(oracle)
    if key not in _py_allowlist_cache:
        from pathlib import Path as _Path

        ids: set[str] = set(oracle.method_names)
        # file stems (e.g. graph_store from graph_store.py)
        ids |= {f.rsplit(".", 1)[0] for f in oracle.file_basenames}
        # class short names
        ids |= {c.split(".")[-1] for c in oracle.class_full_names}
        # all snake_case identifiers from actual source files
        for fp in oracle.file_paths:
            try:
                content = _Path(fp).read_text(encoding="utf-8", errors="ignore")
                for m in _re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", content):
                    if len(m) >= 10:
                        ids.add(m)
            except OSError:
                pass
        _py_allowlist_cache[key] = frozenset(ids)
    return _py_allowlist_cache[key]


def py_hallucinated_refs(text: str, oracle: Oracle, exempt: set[str] | None = None) -> list[str]:
    bad: list[str] = []
    for m in _re.findall(r"\b[A-Za-z_]\w+\.py\b", text):
        if m not in oracle.file_basenames:
            bad.append(m)
    allowlist = _py_source_allowlist(oracle)
    if exempt:
        allowlist = allowlist | exempt
    # only flag snake_case identifiers (require underscore) not found anywhere in source
    for m in _re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}\b", text):
        if len(m) >= 10 and m not in allowlist:
            bad.append(m)
    return sorted(set(bad))


# ---------------------------------------------------------------------------
# SQL helpers (same as agent_eval_ts_v2 — duplicated to keep files self-contained)
# ---------------------------------------------------------------------------


def _gt_callers(oracle: Oracle, method: str) -> set[str]:
    rows = oracle.conn.execute(
        """SELECT DISTINCT cm.method_name FROM resolved_call_edges e
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           JOIN methods cm     ON cm.id=e.caller_method_id     AND cm.variant=e.variant
           WHERE e.variant='validated' AND callee.method_name=?""",
        (method,),
    ).fetchall()
    return {r[0] for r in rows}


def _gt_callees(oracle: Oracle, method: str) -> set[str]:
    rows = oracle.conn.execute(
        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
           JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           WHERE e.variant='validated' AND caller.method_name=?""",
        (method,),
    ).fetchall()
    return {r[0] for r in rows}


def _gt_caller_files(oracle: Oracle, method: str) -> set[str]:
    rows = oracle.conn.execute(
        """SELECT DISTINCT cm.file_path FROM resolved_call_edges e
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           JOIN methods cm     ON cm.id=e.caller_method_id     AND cm.variant=e.variant
           WHERE e.variant='validated' AND callee.method_name=?""",
        (method,),
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Checker factory — identical structure to agent_eval_ts_v2
# ---------------------------------------------------------------------------

_ABSENT_PHRASES = (
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


def _build_checker(cfg: dict, oracle: Oracle) -> ae.Checker:
    kind = cfg["checker"]
    method = cfg.get("method", "")

    if kind == "caller_hit":
        min_hits = cfg.get("min_hits", 2)

        def check(ans: str, _o: Oracle) -> tuple[bool, str]:
            callers = {c.lower() for c in _gt_callers(oracle, method)}
            callers |= {c.split(".")[-1].lower() for c in oracle.callers_of(method)}
            if not callers:
                return False, "no GT callers"
            a = _lc(ans)
            hit = {c for c in callers if len(c) > 4 and c in a}
            return len(hit) >= min_hits, f"caller_hit {len(hit)} (need>={min_hits})"

        return check

    if kind == "callee_hit":
        min_hits = cfg.get("min_hits", 1)

        def check(ans: str, _o: Oracle) -> tuple[bool, str]:
            callees = {c.lower() for c in _gt_callees(oracle, method)}
            if not callees:
                return False, "no GT callees"
            a = _lc(ans)
            hit = {c for c in callees if len(c) > 4 and c in a}
            return len(hit) >= min_hits, f"callee_hit {len(hit)}/{len(callees)}"

        return check

    if kind == "negative":

        def check(ans: str, _o: Oracle) -> tuple[bool, str]:
            exists = method in oracle.method_names
            a = _lc(ans).replace("*", "").replace("_", "")
            says_absent = any(k in a for k in _ABSENT_PHRASES)
            return (not exists) and says_absent, f"exists={exists} says_absent={says_absent}"

        return check

    if kind == "locate_method":
        file_hint = cfg.get("file_hint", "").lower()
        purpose_kws = [k.lower() for k in cfg.get("purpose_keywords", [])]

        def check(ans: str, _o: Oracle) -> tuple[bool, str]:
            exists = method in oracle.method_names
            a = _lc(ans)
            located = file_hint in a if file_hint else True
            purpose = any(k in a for k in purpose_kws) if purpose_kws else True
            ok = exists and located and purpose
            return ok, f"exists={exists} located={located} purpose={purpose}"

        return check

    if kind == "get_source":
        file_hint = cfg.get("file_hint", "").lower()
        source_kws = [k.lower() for k in cfg.get("source_keywords", [])]

        def check(ans: str, _o: Oracle) -> tuple[bool, str]:
            exists = method in oracle.method_names
            a = _lc(ans)
            located = file_hint in a if file_hint else True
            has_source = any(k in a for k in source_kws) if source_kws else True
            ok = exists and located and has_source
            return ok, f"exists={exists} located={located} has_source={has_source}"

        return check

    if kind == "change_impact":
        min_files = cfg.get("min_files", 2)

        def check(ans: str, _o: Oracle) -> tuple[bool, str]:
            caller_files = _gt_caller_files(oracle, method)
            if not caller_files:
                return False, "no GT caller files"
            a = _lc(ans)
            stems = {_re.sub(r"\.[^.]+$", "", f.split("/")[-1]).lower() for f in caller_files}
            hit_path = {f for f in caller_files if f.lower() in a}
            hit_stem = {s for s in stems if len(s) >= 5 and s in a}
            hit = len(hit_path | hit_stem)
            return (
                hit >= min_files,
                f"file_hit {hit}/{len(caller_files)} files (need>={min_files})",
            )

        return check

    raise ValueError(f"Unknown checker type: {kind!r}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run_async(args) -> None:
    config = json.loads(Path(args.config).read_text())
    halluc_max = config.get("halluc_max", 0)
    oracle = Oracle.load(args.graph)
    client = ae.make_client()
    backends = [
        ae.jidra_backend(args.graph, args.codebase),
        ae.codegraph_backend(args.codebase),
    ]

    tasks = []
    task_methods: dict[str, str] = {}
    for tc in config["tasks"]:
        checker = _build_checker(tc, oracle)
        tasks.append(Task(tc["id"], tc["prompt"], checker))
        task_methods[tc["id"]] = tc.get("method", "")

    if args.tasks:
        want = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in want]

    results: list[dict] = []
    for task in tasks:
        for be in backends:
            print(f"\n── {task.id} / {be.name} ─────────────────────────────", flush=True)
            _skill_system = ae.SYSTEM if be.name == "jidra" else ae._SYSTEM_BASE
            rr = await ae.run_agent(
                client,
                args.model,
                be,
                task.prompt,
                label=f"{task.id}/{be.name}",
                system=_skill_system,
            )
            rr.task = task.id
            note = "run_error"
            if not rr.error:
                try:
                    rr.correct, note = task.check(rr.answer, oracle)
                except Exception as e:
                    note = f"check_error: {e!r}"
                _exempt = {task_methods[task.id]} if task_methods.get(task.id) else None
                rr.hallucinated = py_hallucinated_refs(rr.answer, oracle, exempt=_exempt)
                if len(rr.hallucinated) > halluc_max:
                    rr.correct = False
                    note += f" [HALLUC_FAIL: {rr.hallucinated}]"

            d = asdict(rr)
            d["check_note"] = note
            d["cost_usd"] = ae._cost(d, args.model)
            results.append(d)
            tag = "ERR" if rr.error else ("OK " if rr.correct else "XX ")
            print(
                f"    {tag} {be.name:9} calls={rr.tool_calls:2} tok={rr.total_tokens:5} "
                f"cost=${d['cost_usd']:.4f} halluc={len(rr.hallucinated)} {note}",
                flush=True,
            )

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    ae._summary(results)
    print(f"\nwrote {args.out}")


def selfcheck(graph: str, config_path: str) -> bool:
    config = json.loads(Path(config_path).read_text())
    oracle = Oracle.load(graph)
    checks = []
    for tc in config["tasks"]:
        method = tc.get("method", "")
        kind = tc["checker"]
        if kind == "negative":
            absent = method not in oracle.method_names
            checks.append(
                (
                    f"{tc['id']} {method} ABSENT",
                    absent,
                    "absent" if absent else "PRESENT!",
                )
            )
        else:
            present = method in oracle.method_names
            if kind == "caller_hit":
                n = len(_gt_callers(oracle, method))
                detail = f"{n} callers"
            elif kind == "callee_hit":
                n = len(_gt_callees(oracle, method))
                detail = f"{n} callees"
            elif kind == "change_impact":
                n = len(_gt_caller_files(oracle, method))
                detail = f"{n} caller files"
            else:
                detail = "present" if present else "MISSING"
            checks.append((f"{tc['id']} {method} PRESENT", present, detail))

    print("=== deterministic self-check (no LLM) ===")
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'ok ' if ok else 'BAD'}] {name:40} {detail}")
    print("=== ALL GT RESOLVES ===" if all_ok else "=== FIX TASKS ===")
    return all_ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent-in-loop eval (Python v2): JIDRA vs CodeGraph")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--codebase", default="")
    ap.add_argument("--config", required=True, help="path to JSON task config")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--tasks", default="", help="comma list e.g. PY1,PY2")
    ap.add_argument("--out", default="results/eval_agent_results_py_v2.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument(
        "--skill",
        default="",
        help="path to a skill/agent .md file — body appended to SYSTEM prompt (YAML frontmatter stripped)",
    )
    args = ap.parse_args()
    if args.skill:
        ae.SYSTEM = ae._SYSTEM_BASE + "\n\n" + ae._load_skill(args.skill)
    if args.selfcheck:
        raise SystemExit(0 if selfcheck(args.graph, args.config) else 1)
    if not args.codebase:
        ap.error("--codebase required (except with --selfcheck)")
    ae.VERBOSE = not args.quiet
    asyncio.run(run_async(args))


if __name__ == "__main__":
    main()
