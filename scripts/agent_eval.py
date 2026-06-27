#!/usr/bin/env python3
"""
agent_eval.py — Agent-in-loop comparison: JIDRA vs CodeGraph.

The synthetic recall/token eval (eval_chat.py) measures retrieval, not how an
agent actually performs mid-task. This harness gives a real LLM a coding-nav
task and EXACTLY ONE backend's MCP tools, lets it run a tool-use loop, then
scores what matters for an agent:

    - correct     : did it reach the right answer (deterministic per-task check)
    - tool_calls  : how many tool round-trips it needed
    - tokens      : total in+out tokens burned
    - hallucinated: did the final answer cite a project FQN / .java path that
                    does NOT exist in the codebase (the anti-hallucination moat)
    - wall_ms     : latency

Two arms per task: agent+JIDRA, agent+CodeGraph. Ground truth is computed from
the JIDRA graph.db at runtime (not hardcoded), so checks stay honest if the
index changes.

Usage:
    ./venv/bin/python scripts/agent_eval.py \
        --graph  <path/to/graph.db> \
        --codebase [REDACTED] \
        [--model claude-sonnet-4-6] [--tasks T1,T2] [--out agent_eval_results.json]

Auth: uses ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL from env (proxy), falling
back to ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
VENV_PY = str(REPO_ROOT / "venv" / "bin" / "python")

PROJECT_PKGS = ("[REDACTED]",)
MAX_ITERS = 14
AGENT_MAX_TOKENS = 1500

VERBOSE = True  # set False via --quiet; streams live progress during a run
_T0 = time.perf_counter()


def log(label: str, msg: str) -> None:
    """Timestamped live progress line (elapsed since process start)."""
    if VERBOSE:
        print(f"  [{time.perf_counter() - _T0:6.1f}s] {label:18} {msg}", flush=True)


def _compact(obj: Any, n: int = 140) -> str:
    """One-line snippet of tool args/results for logging."""
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    s = " ".join(s.split())  # collapse whitespace/newlines
    return s[:n] + ("…" if len(s) > n else "")


# ---------------------------------------------------------------------------
# Ground-truth oracle — built from the JIDRA graph.db (source of truth for what
# symbols/paths actually exist).
# ---------------------------------------------------------------------------
@dataclass
class Oracle:
    class_full_names: set[str]
    method_names: set[str]
    signatures: set[str]
    file_paths: set[str]
    file_basenames: set[str]
    conn: sqlite3.Connection

    @classmethod
    def load(cls, db: str, variant: str = "main") -> "Oracle":
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cfn, mn, sig, fp = set(), set(), set(), set()
        for r in conn.execute(
            "SELECT method_name, signature, class_full_name, file_path FROM methods WHERE variant=?",
            (variant,),
        ):
            mn.add(r["method_name"])
            sig.add(r["signature"])
            cfn.add(r["class_full_name"])
            if r["file_path"]:
                fp.add(r["file_path"])
        for r in conn.execute(
            "SELECT full_name FROM classes WHERE variant=?", (variant,)
        ):
            cfn.add(r["full_name"])
        base = {Path(p).name for p in fp}
        return cls(cfn, mn, sig, fp, base, conn)

    # --- queries used by task ground-truth -----------------------------------
    def implementers(self, iface_short: str, variant: str = "main") -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT source_class FROM inheritance_edges "
            "WHERE variant=? AND relation IN ('implements','extends') "
            "AND (target_class=? OR target_class LIKE ?)",
            (variant, iface_short, f"%.{iface_short}"),
        ).fetchall()
        return sorted(r[0] for r in rows)

    def callers_of(self, method_name: str, variant: str = "main") -> set[str]:
        # caller class_full_names of any method with this name
        rows = self.conn.execute(
            """
            SELECT DISTINCT cm.class_full_name
            FROM resolved_call_edges e
            JOIN methods callee ON callee.id = e.callee_method_id AND callee.variant=e.variant
            JOIN methods cm     ON cm.id     = e.caller_method_id AND cm.variant=e.variant
            WHERE e.variant=? AND callee.method_name=?
            """,
            (variant, method_name),
        ).fetchall()
        return {r[0] for r in rows}

    def method_exists(
        self, class_short: str, method: str, variant: str = "main"
    ) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM methods WHERE variant=? AND method_name=? "
            "AND (class_full_name=? OR class_full_name LIKE ?) LIMIT 1",
            (variant, method, class_short, f"%.{class_short}"),
        ).fetchone()
        return r is not None

    # --- hallucination detection ---------------------------------------------
    def hallucinated_refs(self, text: str) -> list[str]:
        """Project FQNs, short class names, or .java paths mentioned in `text` that don't exist."""
        bad: list[str] = []
        # project-package FQNs e.g. [REDACTED_PKG].search.Foo or ...Foo#bar(...)
        for m in re.findall(r"\b(?:[REDACTED])[w.$]*", text):
            head = m.split("#")[0].rstrip(".")
            # accept if it's a known class, OR a known class prefix (package), OR
            # a class.method dotted form whose class is known
            if head in self.class_full_names:
                continue
            parent = head.rsplit(".", 1)[0]
            if parent in self.class_full_names:
                continue  # Class.method or Class.FIELD reference
            if any(
                c == head or c.startswith(head + ".") for c in self.class_full_names
            ):
                continue  # a package prefix
            bad.append(m)
        # *.java basenames
        for m in re.findall(r"\b[A-Z]\w+\.java\b", text):
            if m not in self.file_basenames:
                bad.append(m)
        # Interface/impl short names e.g. "[REDACTED_INTERFACE]", "SearchServiceImpl"
        # Extract PascalCase identifiers that sound like classes (not common words)
        for m in re.findall(
            r"\b[A-Z][a-zA-Z0-9]*(?:Service|Controller|Repository|Manager|Factory|Handler|Listener|Helper|Util|Impl|Interface|Abstract)(?:Impl)?\b",
            text,
        ):
            short = m.split("#")[0].rstrip(".")
            # Check if any known class ends with this short name
            if not any(c.endswith("." + short) for c in self.class_full_names):
                bad.append(short)
        return sorted(set(bad))


# ---------------------------------------------------------------------------
# MCP backend — connects to one stdio server, exposes its tools to the agent.
# ---------------------------------------------------------------------------
@dataclass
class Backend:
    name: str
    params: StdioServerParameters


def jidra_backend(graph: str, codebase: str) -> Backend:
    return Backend(
        "jidra",
        StdioServerParameters(
            command=VENV_PY,
            args=[
                "-m",
                "jidra.mcp_server",
                "--mode",
                "direct",
                "--graph",
                graph,
                "--codebase",
                codebase,
            ],
            cwd=str(REPO_ROOT),
        ),
    )


def codegraph_backend(codebase: str) -> Backend:
    return Backend(
        "codegraph",
        StdioServerParameters(
            command="codegraph", args=["serve", "--mcp"], cwd=codebase
        ),
    )


def _mcp_text(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", []) or []:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts)


def _to_anthropic_tools(mcp_tools: list) -> list[dict]:
    out = []
    for t in mcp_tools:
        out.append(
            {
                "name": t.name,
                "description": (t.description or "")[:1024],
                "input_schema": t.inputSchema or {"type": "object", "properties": {}},
            }
        )
    return out


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    backend: str
    task: str
    answer: str = ""
    tool_calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    wall_ms: float = 0.0
    correct: bool | None = None
    hallucinated: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.in_tokens + self.out_tokens


SYSTEM = (
    "You are a code-navigation agent answering a question about a "
    "codebase. You have ONLY the provided tools to inspect the code — you cannot "
    "read files directly. Rules:\n"
    "1. Ground every claim in tool output. Do NOT guess class names, method "
    "names, or file paths. If a tool doesn't surface something, say so.\n"
    "2. If something is ambiguous (e.g. an interface with many implementations), "
    "say it is ambiguous and report what you found — do not invent a single answer.\n"
    "3. If the thing asked about does not exist, say it does not exist.\n"
    "4. Be concise. Stop calling tools once you can answer. Give the final answer "
    "as plain text (no tool call) when done."
)


async def run_agent(
    client, model: str, backend: Backend, task_prompt: str, label: str = ""
) -> RunResult:
    label = label or backend.name
    rr = RunResult(backend=backend.name, task="")
    t0 = time.perf_counter()
    try:
        async with stdio_client(backend.params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool_list = (await session.list_tools()).tools
                tools = _to_anthropic_tools(tool_list)
                log(label, f"connected · {len(tools)} tools available")
                messages = [{"role": "user", "content": task_prompt}]

                for it in range(1, MAX_ITERS + 1):
                    resp = await client.messages.create(
                        model=model,
                        max_tokens=AGENT_MAX_TOKENS,
                        system=SYSTEM,
                        tools=tools,
                        messages=messages,
                    )
                    rr.in_tokens += resp.usage.input_tokens
                    rr.out_tokens += resp.usage.output_tokens

                    tool_uses = [b for b in resp.content if b.type == "tool_use"]
                    text = "".join(b.text for b in resp.content if b.type == "text")
                    log(
                        label,
                        f"iter {it}: +{resp.usage.output_tokens}out tok "
                        f"(cum {rr.total_tokens}) · {len(tool_uses)} tool-call(s)"
                        + (f" · thinks: {_compact(text, 80)}" if text.strip() else ""),
                    )

                    if not tool_uses:
                        rr.answer = text.strip()
                        log(
                            label,
                            f"FINAL ({rr.tool_calls} calls): {_compact(rr.answer, 160)}",
                        )
                        break

                    messages.append({"role": "assistant", "content": resp.content})
                    tool_results = []
                    for tu in tool_uses:
                        rr.tool_calls += 1
                        rr.tools_used.append(tu.name)
                        log(label, f"  → {tu.name}({_compact(tu.input or {}, 90)})")
                        try:
                            out = await asyncio.wait_for(
                                session.call_tool(tu.name, tu.input or {}), 60
                            )
                            payload = _mcp_text(out)[:8000]
                            log(
                                label,
                                f"  ← {len(payload)} chars · {_compact(payload, 110)}",
                            )
                        except Exception as e:  # noqa: BLE001
                            payload = f"TOOL_ERROR: {e!r}"
                            log(label, f"  ← ERROR {_compact(repr(e), 110)}")
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": payload or "(empty)",
                            }
                        )
                    messages.append({"role": "user", "content": tool_results})
                else:
                    rr.answer = "(max iterations reached without final answer)"
                    log(label, "hit MAX_ITERS without final answer")
    except Exception as e:  # noqa: BLE001
        rr.error = repr(e)[:300]
        log(label, f"RUN ERROR {_compact(repr(e), 160)}")
    rr.wall_ms = (time.perf_counter() - t0) * 1000
    return rr


# ---------------------------------------------------------------------------
# Tasks — prompt + a checker(answer, oracle) -> (correct: bool, note: str)
# Ground truth derived from the oracle at runtime.
# ---------------------------------------------------------------------------
Checker = Callable[[str, Oracle], tuple[bool, str]]


@dataclass
class Task:
    id: str
    prompt: str
    check: Checker


def _lc(s: str) -> str:
    return s.lower()


def make_tasks() -> list[Task]:
    tasks: list[Task] = []

    # T1 — ambiguity / strategy pattern. Must NOT fabricate a single impl.
    def t1(ans: str, o: Oracle) -> tuple[bool, str]:
        impls = o.implementers("[REDACTED_INTERFACE]")
        n = len(impls)
        a = _lc(ans)
        signals_many = any(
            k in a
            for k in (
                "multiple",
                "many",
                "several",
                "implementations",
                "strategy",
                "dozens",
                str(n),
            )
        )
        # fail if it confidently names ONE as "the" implementation
        named = [c.split(".")[-1] for c in impls if c.split(".")[-1].lower() in a]
        confident_single = (
            bool(re.search(r"\bthe (single |sole )?implementation\b", a))
            and len(named) <= 1
        )
        ok = signals_many and not confident_single
        return (
            ok,
            f"impls={n} named={len(named)} many={signals_many} single={confident_single}",
        )

    tasks.append(
        Task(
            "T1",
            "The interface `[REDACTED_INTERFACE]` is implemented in this codebase. "
            "How many concrete implementations are there, and is there a single class "
            "that 'is' the [REDACTED_INTERFACE], or many? Answer precisely.",
            t1,
        )
    )

    # T2 — interface -> concrete impl resolution.
    def t2(ans: str, o: Oracle) -> tuple[bool, str]:
        ok = "opensearchclientimpl" in _lc(ans)
        return ok, "names [REDACTED_IMPL]" if ok else "missed [REDACTED_IMPL]"

    tasks.append(
        Task(
            "T2",
            "Which concrete class implements the `[REDACTED_CLIENT]` interface, and "
            "where is the `[REDACTED_METHOD]` method actually implemented? Name the class.",
            t2,
        )
    )

    # T3 — caller / impact analysis. `[REDACTED_METHOD2]` has many real
    # internal callers (unlike an HTTP endpoint, which has none). Pass = surfaces
    # >=3 genuine caller classes; fabricated callers are caught by hallucinated_refs.
    T3_METHOD = "[REDACTED_METHOD2]"

    def t3(ans: str, o: Oracle) -> tuple[bool, str]:
        callers = {c.split(".")[-1].lower() for c in o.callers_of(T3_METHOD)}
        if not callers:
            return False, "no ground-truth callers found"
        hit = {c for c in callers if len(c) > 4 and c in _lc(ans)}
        ok = len(hit) >= 3  # impact analysis: found a real, non-trivial caller set
        return ok, f"caller_hit {len(hit)}/{len(callers)} (need>=3)"

    tasks.append(
        Task(
            "T3",
            f"Which classes call the `{T3_METHOD}` method? List the calling classes — "
            "this is impact analysis before changing it. Name the actual callers.",
            t3,
        )
    )

    # T4 — negative / hallucination resistance. Method does not exist.
    def t4(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = o.method_exists("[REDACTED_CONTROLLER]", "[REDACTED_METHOD3]")
        a = _lc(ans)
        says_absent = any(
            k in a
            for k in (
                "does not exist",
                "doesn't exist",
                "no such",
                "not found",
                "could not find",
                "couldn't find",
                "no method",
                "not present",
                "no `reindex",
            )
        )
        ok = (not exists) and says_absent
        return ok, f"exists={exists} says_absent={says_absent}"

    tasks.append(
        Task(
            "T4",
            "Explain what the method `[REDACTED_METHOD3]()` on `[REDACTED_CONTROLLER]` "
            "does and what it calls. If it is not present, say so explicitly.",
            t4,
        )
    )

    # T5 — flow trace: immediate downstream of the search endpoint.
    def t5(ans: str, o: Oracle) -> tuple[bool, str]:
        # ground truth: callees of [REDACTED_CONTROLLER].[REDACTED_METHOD4] (a real endpoint)
        rows = o.conn.execute(
            """SELECT DISTINCT callee.method_name
               FROM resolved_call_edges e
               JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
               JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
               WHERE e.variant='main' AND caller.method_name='[REDACTED_METHOD4]'
                 AND caller.class_full_name LIKE '%[REDACTED_CONTROLLER]'""",
        ).fetchall()
        callees = {r[0].lower() for r in rows}
        if not callees:
            return False, "no callees in graph"
        hit = {c for c in callees if len(c) > 3 and c in _lc(ans)}
        ok = len(hit) >= 1
        return ok, f"callee_hit {len(hit)}/{len(callees)}"

    tasks.append(
        Task(
            "T5",
            "Trace the method `[REDACTED_METHOD4]` in `[REDACTED_CONTROLLER]`: what does "
            "it call directly? List the downstream methods/services it invokes.",
            t5,
        )
    )

    # T6 — hallucination bait: an interface that DOES NOT EXIST. A grounded tool
    # forces "not found"; a name-matching backend may fabricate implementations.
    FAKE_IFACE = "[REDACTED_IFACE2]"

    def t6(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = any(c.rsplit(".", 1)[-1] == FAKE_IFACE for c in o.class_full_names)
        a = _lc(ans)
        says_absent = any(
            k in a
            for k in (
                "does not exist",
                "doesn't exist",
                "no such",
                "not found",
                "could not find",
                "couldn't find",
                "no interface",
                "not present",
                "no implementations",
                "no class",
                "did not find",
                "unable to find",
            )
        )
        ok = (not exists) and says_absent
        return ok, f"exists={exists} says_absent={says_absent}"

    tasks.append(
        Task(
            "T6",
            f"Describe the `{FAKE_IFACE}` interface and list its implementations. "
            "If it does not exist in this codebase, say so explicitly.",
            t6,
        )
    )

    # T7 — multi-impl pick trap. [REDACTED_INTERFACE] has 101 impls; one ([REDACTED_CLASS])
    # matches the described purpose. Pass = narrows to the right impl OR honestly
    # flags ambiguity; fail = confidently names a wrong/fabricated single class.
    def t7(ans: str, o: Oracle) -> tuple[bool, str]:
        a = _lc(ans)
        right = "channelnamematch" in a
        hedges = any(
            k in a
            for k in (
                "ambiguous",
                "multiple",
                "several",
                "many",
                "cannot determine",
                "can't determine",
                "depends",
            )
        )
        ok = right or hedges
        return ok, f"named_right={right} hedged={hedges}"

    tasks.append(
        Task(
            "T7",
            "Among the implementations of `[REDACTED_INTERFACE]`, which single class is "
            "responsible for matching on a channel's NAME? Name it, or say if it can't "
            "be determined.",
            t7,
        )
    )

    return tasks


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def make_client():
    from anthropic import AsyncAnthropic

    tok = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if tok:
        return AsyncAnthropic(auth_token=tok, base_url=base)
    return AsyncAnthropic()  # falls back to ANTHROPIC_API_KEY


async def main_async(args) -> None:
    oracle = Oracle.load(args.graph)
    client = make_client()
    backends = [
        jidra_backend(args.graph, args.codebase),
        codegraph_backend(args.codebase),
    ]
    tasks = make_tasks()
    if args.tasks:
        want = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in want]

    results: list[dict] = []
    for task in tasks:
        for be in backends:
            print(
                f"\n── {task.id} / {be.name} ─────────────────────────────", flush=True
            )
            rr = await run_agent(
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
            tag = "OK " if rr.correct else "XX "
            if rr.error:
                tag = "ERR"
            print(
                f"    {tag} {be.name:9} calls={rr.tool_calls:2} tok={rr.total_tokens:5} "
                f"halluc={len(rr.hallucinated)} {note}",
                flush=True,
            )

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    _summary(results)
    print(f"\nwrote {args.out}")


def _summary(results: list[dict]) -> None:
    print("\n" + "=" * 72)
    print(
        f"{'':12}{'correct':>9}{'tool_calls':>12}{'tokens':>10}{'halluc':>9}{'wall_ms':>10}"
    )
    for name in ("jidra", "codegraph"):
        rs = [r for r in results if r["backend"] == name and not r["error"]]
        if not rs:
            print(f"{name:12} (no successful runs)")
            continue
        n = len(rs)
        corr = sum(1 for r in rs if r["correct"])
        tc = sum(r["tool_calls"] for r in rs) / n
        tok = sum(r["in_tokens"] + r["out_tokens"] for r in rs) / n
        hal = sum(1 for r in rs if r["hallucinated"])
        wall = sum(r["wall_ms"] for r in rs) / n
        print(
            f"{name:12}{corr:>4}/{n:<4}{tc:>12.1f}{tok:>10.0f}{hal:>6}/{n:<2}{wall:>10.0f}"
        )
    print("=" * 72)
    print(
        "correct=task solved · tool_calls/tokens/wall=avg per task · halluc=#runs citing a fake project symbol"
    )


def selfcheck(graph: str) -> bool:
    """Deterministic GT validation — NO LLM, NO money. Confirms every task's
    ground truth resolves before a paid run. Each row must read 'ok'."""
    o = Oracle.load(graph)
    impls_cf = o.implementers("[REDACTED_INTERFACE]")
    impls_os = {c.rsplit(".", 1)[-1] for c in o.implementers("[REDACTED_CLIENT]")}
    callers_t3 = o.callers_of("[REDACTED_METHOD2]")
    callees_t5 = o.conn.execute(
        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
           JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           WHERE e.variant='main' AND caller.method_name='[REDACTED_METHOD4]'
             AND caller.class_full_name LIKE '%[REDACTED_CONTROLLER]'"""
    ).fetchall()
    fake_absent = not any(
        c.rsplit(".", 1)[-1] == "[REDACTED_IFACE2]" for c in o.class_full_names
    )
    chan = any(c.rsplit(".", 1)[-1] == "[REDACTED_CLASS]" for c in impls_cf)
    t4_absent = not o.method_exists("[REDACTED_CONTROLLER]", "[REDACTED_METHOD3]")

    checks = [
        (
            "T1 [REDACTED_INTERFACE] impls == 101",
            len(impls_cf) == 101,
            f"{len(impls_cf)}",
        ),
        (
            "T2 [REDACTED_IMPL] in impls",
            "[REDACTED_IMPL]" in impls_os,
            str(sorted(impls_os)),
        ),
        (
            "T3 [REDACTED_METHOD2] callers >=3",
            len(callers_t3) >= 3,
            f"{len(callers_t3)} callers",
        ),
        (
            "T4 [REDACTED_METHOD3] ABSENT",
            t4_absent,
            "absent" if t4_absent else "PRESENT!",
        ),
        (
            "T5 [REDACTED_METHOD4] callees >0",
            len(callees_t5) > 0,
            f"{len(callees_t5)} callees",
        ),
        (
            "T6 [REDACTED_IFACE2] ABSENT",
            fake_absent,
            "absent" if fake_absent else "PRESENT!",
        ),
        ("T7 [REDACTED_CLASS] is a CF impl", chan, "found" if chan else "missing"),
    ]
    print("=== deterministic self-check (no LLM) ===")
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'ok ' if ok else 'BAD'}] {name:42} {detail}")
    print(
        "=== ALL GT RESOLVES — safe to run ==="
        if all_ok
        else "=== FIX TASKS BEFORE PAID RUN ==="
    )
    return all_ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent-in-loop eval: JIDRA vs CodeGraph")
    ap.add_argument(
        "--graph", required=True, help="path to JIDRA graph.db (also the GT oracle)"
    )
    ap.add_argument("--codebase", help="repo root (CG reads its .codegraph here)")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--tasks", default="", help="comma list e.g. T1,T2 (default all)")
    ap.add_argument("--out", default="agent_eval_results.json")
    ap.add_argument("--quiet", action="store_true", help="suppress live per-step logs")
    ap.add_argument(
        "--selfcheck",
        action="store_true",
        help="validate all task ground-truth deterministically (no LLM) and exit",
    )
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(0 if selfcheck(args.graph) else 1)
    if not args.codebase:
        ap.error("--codebase required (except with --selfcheck)")
    global VERBOSE
    VERBOSE = not args.quiet
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
