#!/usr/bin/env python3
"""
agent_eval_v2.py — Agent-in-loop comparison: JIDRA vs CodeGraph (v2 harness).

Changes from v1 (agent_eval.py):
  - RunResult.tool_trace: full per-call log of {iter, tool, input, response}
    saved to JSON so post-hoc debugging doesn't require terminal output.
  - T8 checker fixed: now requires REDACTED *interface* params
    (locale, feedType, searchString) — rejects REDACTED answers
    that the v1 loose check passed as correct.

Usage (run from repo root):
    # Run all 8 tasks against a Java codebase
    python evals/harness/java/agent_eval_v2.py \
        --graph    /path/to/repo/.jidra/graph.db \
        --codebase /path/to/repo \
        --model    claude-sonnet-4-6 \
        --out      evals/harness/java/results/result_v2.json

    # Run specific tasks only
    python evals/harness/java/agent_eval_v2.py \
        --graph    /path/to/repo/.jidra/graph.db \
        --codebase /path/to/repo \
        --tasks    T1,T3,T8 \
        --out      evals/harness/java/results/result_v2.json

    # Verify graph has required ground-truth data before running
    python evals/harness/java/agent_eval_v2.py \
        --graph /path/to/repo/.jidra/graph.db \
        --selfcheck

    # Config-driven mode (custom task set)
    python evals/harness/java/agent_eval_v2.py \
        --graph    /path/to/repo/.jidra/graph.db \
        --codebase /path/to/repo \
        --config   evals/harness/java/config.json

Flags:
    --graph      Path to JIDRA graph.db  (required)
    --codebase   Repo root dir           (required unless --selfcheck)
    --model      Anthropic model ID      (default: claude-sonnet-4-6)
    --tasks      Comma list e.g. T1,T2   (default: all T1-T8)
    --out        JSON output path        (default: agent_eval_results.json)
    --config     JSON task config file   (enables config-driven mode)
    --skill      Path to .md skill file  (appended to system prompt)
    --selfcheck  Validate graph data without running the agent
    --quiet      Suppress per-call logs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent  # evals/harness/java -> project root
VENV_PY = str(REPO_ROOT / "venv" / "bin" / "python")

PROJECT_PKGS = "[REDACTED_PKG]"
MAX_ITERS = 14
AGENT_MAX_TOKENS = 1500
TRACE_RESPONSE_CAP = 3000  # chars saved per tool response in trace

VERBOSE = True
_T0 = time.perf_counter()

# Pricing per model ($/token). Add rows as needed.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-sonnet-4-6-20251001": (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-opus-4-8": (15.0 / 1_000_000, 75.00 / 1_000_000),
}
_DEFAULT_PRICING = (3.00 / 1_000_000, 15.00 / 1_000_000)


def _cost(r: dict, model: str) -> float:
    p_in, p_out = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return r["in_tokens"] * p_in + r["out_tokens"] * p_out


def log(label: str, msg: str) -> None:
    """Timestamped live progress line (elapsed since process start)."""
    if VERBOSE:
        print(f"  [{time.perf_counter() - _T0:6.1f}s] {label:18} {msg}", flush=True)


def _compact(obj: Any, n: int = 140) -> str:
    """One-line snippet of tool args/results for logging."""
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    s = " ".join(s.split())
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
    def load(cls, db: str, variant: str = "validated") -> Oracle:
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
        for r in conn.execute("SELECT full_name FROM classes WHERE variant=?", (variant,)):
            cfn.add(r["full_name"])
        base = {Path(p).name for p in fp}
        return cls(cfn, mn, sig, fp, base, conn)

    # --- queries used by task ground-truth -----------------------------------
    def implementers(self, iface_short: str, variant: str = "validated") -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT source_class FROM inheritance_edges "
            "WHERE variant=? AND relation IN ('implements','extends') "
            "AND (target_class=? OR target_class LIKE ?)",
            (variant, iface_short, f"%.{iface_short}"),
        ).fetchall()
        return sorted(r[0] for r in rows)

    def callers_of(self, method_name: str, variant: str = "validated") -> set[str]:
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

    def method_exists(self, class_short: str, method: str, variant: str = "validated") -> bool:
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
        # project-package FQNs e.g. com.example.search.Foo or ...Foo#bar(...)
        for m in re.findall(
            r"\b(?:com\.REDACTED|REDACTED|com\.REDACTED|com\.REDACTED)[\w.$]*", text
        ):
            head = m.split("#")[0].rstrip(".")
            # accept if it's a known class, OR a known class prefix (package), OR
            # a class.method dotted form whose class is known
            if head in self.class_full_names:
                continue
            parent = head.rsplit(".", 1)[0]
            if parent in self.class_full_names:
                continue  # Class.method or Class.FIELD reference
            if any(c == head or c.startswith(head + ".") for c in self.class_full_names):
                continue  # a package prefix
            bad.append(m)
        # *.java basenames
        for m in re.findall(r"\b[A-Z]\w+\.java\b", text):
            if m not in self.file_basenames:
                bad.append(m)
        # Interface/impl short names e.g. "CandidateFeature", "SearchServiceImpl"
        # Extract PascalCase identifiers that sound like classes (not common words)
        for m in re.findall(
            r"\b[A-Z][a-zA-Z0-9]*(?:REDACTED|Controller|Repository|Manager|Factory|Handler|Listener|Helper|Util|Impl|Interface|Abstract)(?:Impl)?\b",
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
                "jidra.server.mcp_server",
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
        StdioServerParameters(command="codegraph", args=["serve", "--mcp"], cwd=codebase),
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
    # v2: full per-call trace for post-hoc debugging
    tool_trace: list[dict] = field(default_factory=list)
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.in_tokens + self.out_tokens


_SYSTEM_BASE = (
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

SYSTEM = _SYSTEM_BASE  # may be replaced by main() when --skill is passed


def _load_skill(path: str) -> str:
    """Read a skill/agent .md file, strip YAML frontmatter, return body."""
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.index("---", 3)
        text = text[end + 3 :].lstrip()
    return text


async def run_agent(
    client,
    model: str,
    backend: Backend,
    task_prompt: str,
    label: str = "",
    system: str | None = None,
) -> RunResult:
    label = label or backend.name
    rr = RunResult(backend=backend.name, task="")
    t0 = time.perf_counter()
    _system = system if system is not None else SYSTEM
    try:
        async with (
            stdio_client(backend.params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tool_list = (await session.list_tools()).tools
            tools = _to_anthropic_tools(tool_list)
            log(label, f"connected · {len(tools)} tools available")
            messages = [{"role": "user", "content": task_prompt}]

            for it in range(1, MAX_ITERS + 1):
                resp = await client.messages.create(
                    model=model,
                    max_tokens=AGENT_MAX_TOKENS,
                    system=_system,
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
                        out = await asyncio.wait_for(session.call_tool(tu.name, tu.input or {}), 60)
                        payload = _mcp_text(out)
                        log(
                            label,
                            f"  ← {len(payload)} chars · {_compact(payload, 110)}",
                        )
                    except Exception as e:
                        payload = f"TOOL_ERROR: {e!r}"
                        log(label, f"  ← ERROR {_compact(repr(e), 110)}")
                    # v2: record full call trace
                    rr.tool_trace.append(
                        {
                            "iter": it,
                            "tool": tu.name,
                            "input": tu.input or {},
                            "response": payload[:TRACE_RESPONSE_CAP],
                            "response_truncated": len(payload) > TRACE_RESPONSE_CAP,
                        }
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": payload or "(empty)",
                        }
                    )
                messages.append({"role": "user", "content": tool_results})  # type: ignore[arg-type]
            else:
                rr.answer = "(max iterations reached without final answer)"
                log(label, "hit MAX_ITERS without final answer")
    except Exception as e:
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

    # T1 — ambiguity / strategy pattern.
    # v2: require exact count (101) in answer, not just vague "many".
    # CG v6 said "78+" — wrong count, now correctly fails.
    def t1(ans: str, o: Oracle) -> tuple[bool, str]:
        impls = o.implementers("REDACTED")
        n = len(impls)
        a = _lc(ans)
        has_exact_count = str(n) in a  # must state "101"
        signals_many = any(
            k in a
            for k in (
                "multiple",
                "many",
                "several",
                "implementations",
                "strategy",
                "dozens",
            )
        )
        named = [c.split(".")[-1] for c in impls if c.split(".")[-1].lower() in a]
        confident_single = (
            bool(re.search(r"\bthe (single |sole )?implementation\b", a)) and len(named) <= 1
        )
        ok = has_exact_count and signals_many and not confident_single
        return (
            ok,
            f"impls={n} named={len(named)} exact_count={has_exact_count} many={signals_many} single={confident_single}",
        )

    tasks.append(
        Task(
            "T1",
            "The interface `REDACTED` is implemented in this codebase. "
            "How many concrete implementations are there, and is there a single class "
            "that 'is' the REDACTED, or many? Answer precisely.",
            t1,
        )
    )

    # T2 — interface -> concrete impl resolution.
    # v2: also require that templateSearch location (file or line) is identified.
    def t2(ans: str, _o: Oracle) -> tuple[bool, str]:
        a = _lc(ans)
        names_impl = "REDACTED" in a
        # agent must show WHERE templateSearch lives (file path, line number, or package)
        has_location = any(k in a for k in ("REDACTED.java", "line", "impl/", ".java"))
        ok = names_impl and has_location
        return ok, f"names_impl={names_impl} has_location={has_location}"

    tasks.append(
        Task(
            "T2",
            "Which concrete class implements the `REDACTED` interface, and "
            "where is the `REDACTED` method actually implemented? Name the class.",
            t2,
        )
    )

    # T3 — caller / impact analysis
    T3_METHOD = "REDACTED"

    def t3(ans: str, o: Oracle) -> tuple[bool, str]:
        callers = {c.split(".")[-1].lower() for c in o.callers_of(T3_METHOD)}
        if not callers:
            return False, "no ground-truth callers found"
        hit = {c for c in callers if len(c) > 4 and c in _lc(ans)}
        ok = len(hit) >= 3
        return ok, f"caller_hit {len(hit)}/{len(callers)} (need>=3)"

    tasks.append(
        Task(
            "T3",
            f"Which classes call the `{T3_METHOD}` method? List the calling classes — "
            "this is impact analysis before changing it. Name the actual callers.",
            t3,
        )
    )

    # T4 — negative / hallucination resistance.
    # v2: require agent actually checked REDACTED (not just "not found" anywhere).
    def t4(ans: str, o: Oracle) -> tuple[bool, str]:
        exists = o.method_exists("REDACTED", "reindexAllTenants")
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
        checked_right_class = "REDACTED" in a
        ok = (not exists) and says_absent and checked_right_class
        return (
            ok,
            f"exists={exists} says_absent={says_absent} checked_class={checked_right_class}",
        )

    tasks.append(
        Task(
            "T4",
            "Explain what the method `reindexAllTenants()` on `REDACTED` "
            "does and what it calls. If it is not present, say so explicitly.",
            t4,
        )
    )

    # T5 — flow trace
    def t5(ans: str, o: Oracle) -> tuple[bool, str]:
        # ground truth: callees of REDACTED.REDACTED (a real endpoint)
        rows = o.conn.execute(
            """SELECT DISTINCT callee.method_name
               FROM resolved_call_edges e
               JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
               JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
               WHERE e.variant='validated' AND caller.method_name='REDACTED'
                 AND caller.class_full_name LIKE '%REDACTED'""",
        ).fetchall()
        callees = {r[0].lower() for r in rows}
        if not callees:
            return False, "no callees in graph"
        hit = {c for c in callees if len(c) > 3 and c in _lc(ans)}
        ok = len(hit) >= 3  # v2: raised from 1 — agent must enumerate real downstream chain
        return ok, f"callee_hit {len(hit)}/{len(callees)} (need>=3)"

    tasks.append(
        Task(
            "T5",
            "Trace the method `REDACTED` in `REDACTED`: what does "
            "it call directly? List the downstream methods/services it invokes.",
            t5,
        )
    )

    # T6 — hallucination bait
    FAKE_IFACE = "TenantRoutingStrategy"

    def t6(ans: str, _o: Oracle) -> tuple[bool, str]:
        exists = any(c.rsplit(".", 1)[-1] == FAKE_IFACE for c in _o.class_full_names)
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

    # T7 — multi-impl pick trap.
    # v2: hedge path removed. REDACTED IS determinable — agent must name it.
    # Hedging without naming is a failure, not a pass.
    def t7(ans: str, _o: Oracle) -> tuple[bool, str]:
        a = _lc(ans)
        right = "REDACTED" in a
        return right, f"named_right={right}"

    tasks.append(
        Task(
            "T7",
            "Among the implementations of `REDACTED`, which single class is "
            "responsible for matching on a REDACTED's NAME? Name it, or say if it can't "
            "be determined.",
            t7,
        )
    )

    # T8 — source lookup: REDACTED interface, NOT REDACTED.
    # v1 bug: loose check passed REDACTED answers as correct.
    # v2 fix: require REDACTED interface param names (REDACTED, REDACTED,
    #         REDACTED) which are absent from the controller's 31-param signature.
    def t8(ans: str, o: Oracle) -> tuple[bool, str]:
        a = _lc(ans)
        # Interface params: locale, feedtype, searchstring, resultsize, filtertype
        # Controller params: sessionid, tenant, deviceplatform, mono<containerresponse>
        has_iface_params = any(
            k in a for k in ("REDACTED", "REDACTED", "REDACTED", "REDACTED", "REDACTED")
        )
        located = "REDACTED" in a
        # Detect if answer is about the controller (has controller-specific markers)
        is_controller = "REDACTED" in a and any(
            k in a
            for k in (
                "REDACTED",
                "REDACTED",
                "REDACTED",
                "REDACTED",
                "31 param",
            )
        )
        ok = has_iface_params and located and not is_controller
        return (
            ok,
            f"has_iface_params={has_iface_params} located={located} is_controller={is_controller}",
        )

    tasks.append(
        Task(
            "T8",
            "Use the code graph tool to fetch the source of the `REDACTED` method on `REDACTED` directly. "
            "Show its implementation — what parameters does it take and what does it return?",
            t8,
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
    return AsyncAnthropic()


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
            print(f"\n── {task.id} / {be.name} ─────────────────────────────", flush=True)
            _skill_system = SYSTEM if be.name == "jidra" else _SYSTEM_BASE
            rr = await run_agent(
                client,
                args.model,
                be,
                task.prompt,
                label=f"{task.id}/{be.name}",
                system=_skill_system,
            )
            rr.task = task.id
            if not rr.error:
                try:
                    ok, note = task.check(rr.answer, oracle)
                    rr.correct = ok
                except Exception as e:
                    note = f"check_error: {e!r}"
                rr.hallucinated = oracle.hallucinated_refs(rr.answer)
            else:
                note = "run_error"
            d = asdict(rr)
            d["check_note"] = note
            d["cost_usd"] = _cost(d, args.model)
            results.append(d)
            tag = "OK " if rr.correct else "XX "
            if rr.error:
                tag = "ERR"
            print(
                f"    {tag} {be.name:9} calls={rr.tool_calls:2} tok={rr.total_tokens:5} "
                f"cost=${d['cost_usd']:.4f} halluc={len(rr.hallucinated)} {note}",
                flush=True,
            )

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    _summary(results)
    print(f"\nwrote {args.out}")


def _summary(results: list[dict]) -> None:
    print("\n" + "=" * 80)
    print(
        f"{'':12}{'correct':>9}{'tool_calls':>12}{'tokens':>10}{'cost_usd':>10}{'halluc':>9}{'wall_ms':>10}"
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
        cost_avg = sum(r.get("cost_usd", 0.0) for r in rs) / n
        cost_total = sum(r.get("cost_usd", 0.0) for r in rs)
        hal = sum(1 for r in rs if r["hallucinated"])
        wall = sum(r["wall_ms"] for r in rs) / n
        print(
            f"{name:12}{corr:>4}/{n:<4}{tc:>12.1f}{tok:>10.0f}"
            f"  ${cost_avg:.4f}({cost_total:.3f}){hal:>6}/{n:<2}{wall:>10.0f}"
        )
    print("=" * 80)
    print(
        "correct=task solved · tool_calls/tokens/wall=avg per task · "
        "cost=avg(total) · halluc=#runs citing a fake project symbol"
    )


def selfcheck(graph: str) -> bool:
    o = Oracle.load(graph)
    impls_cf = o.implementers("REDACTED")
    impls_os = {c.rsplit(".", 1)[-1] for c in o.implementers("REDACTED")}
    callers_t3 = o.callers_of("REDACTED")
    callees_t5 = o.conn.execute(
        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
           JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           WHERE e.variant='validated' AND caller.method_name='REDACTED'
             AND caller.class_full_name LIKE '%REDACTED'"""
    ).fetchall()
    fake_absent = not any(
        c.rsplit(".", 1)[-1] == "TenantRoutingStrategy" for c in o.class_full_names
    )
    chan = any(c.rsplit(".", 1)[-1] == "REDACTED" for c in impls_cf)
    t4_absent = not o.method_exists("REDACTED", "reindexAllTenants")
    t8_exists = o.method_exists("REDACTED", "search")

    checks = [
        ("T1 REDACTED impls == 101", len(impls_cf) == 101, f"{len(impls_cf)}"),
        (
            "T2 REDACTED in impls",
            "REDACTED" in impls_os,
            str(sorted(impls_os)),
        ),
        (
            "T3 REDACTED callers >=3",
            len(callers_t3) >= 3,
            f"{len(callers_t3)} callers",
        ),
        (
            "T4 reindexAllTenants ABSENT",
            t4_absent,
            "absent" if t4_absent else "PRESENT!",
        ),
        (
            "T5 REDACTED callees >0",
            len(callees_t5) > 0,
            f"{len(callees_t5)} callees",
        ),
        (
            "T6 TenantRoutingStrategy ABSENT",
            fake_absent,
            "absent" if fake_absent else "PRESENT!",
        ),
        ("T7 REDACTED is a CF impl", chan, "found" if chan else "missing"),
        (
            "T8 REDACTED.REDACTED fetchable",
            t8_exists,
            "found" if t8_exists else "missing",
        ),
    ]
    print("=== deterministic self-check (no LLM) ===")
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'ok ' if ok else 'BAD'}] {name:42} {detail}")
    print(
        "=== ALL GT RESOLVES — safe to run ===" if all_ok else "=== FIX TASKS BEFORE PAID RUN ==="
    )
    return all_ok


# ---------------------------------------------------------------------------
# Config-driven checker factory (JSON config support)
# ---------------------------------------------------------------------------

_ABSENT_PHRASES = (
    "does not exist",
    "doesn't exist",
    "no such",
    "not found",
    "could not find",
    "couldn't find",
    "no method",
    "not present",
    "no implementations",
    "no class",
    "did not find",
    "unable to find",
)


def _gt_caller_files(oracle: Oracle, method: str) -> set[str]:
    rows = oracle.conn.execute(
        """SELECT DISTINCT cm.file_path FROM resolved_call_edges e
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           JOIN methods cm     ON cm.id=e.caller_method_id     AND cm.variant=e.variant
           WHERE e.variant='validated' AND callee.method_name=?""",
        (method,),
    ).fetchall()
    return {r[0] for r in rows}


def _gt_callees_filtered(oracle: Oracle, method: str, class_filter: str) -> set[str]:
    rows = oracle.conn.execute(
        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
           JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
           JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
           WHERE e.variant='validated' AND caller.method_name=?
             AND caller.class_full_name LIKE ?""",
        (method, f"%{class_filter}"),
    ).fetchall()
    return {r[0] for r in rows}


def _build_checker_java(cfg: dict, oracle: Oracle):
    kind = cfg["checker"]
    method = cfg.get("method", "")

    if kind == "impl_count":
        iface = cfg["interface"]
        _min_count = cfg.get("min_count", 1)

        def check(ans: str, _o: Oracle):
            impls = oracle.implementers(iface)
            n = len(impls)
            a = _lc(ans)
            has_count = str(n) in a
            signals_many = any(
                k in a for k in ("multiple", "many", "several", "implementations", "dozens")
            )
            confident_single = (
                bool(re.search(r"\bthe (single |sole )?implementation\b", a))
                and len([c for c in impls if c.split(".")[-1].lower() in a]) <= 1
            )
            ok = has_count or (signals_many and not confident_single)
            return (
                ok,
                f"impls={n} has_count={has_count} many={signals_many} single={confident_single}",
            )

        return check

    if kind == "caller_hit":
        min_hits = cfg.get("min_hits", 3)

        def check(ans: str, _o: Oracle):
            callers = {c.split(".")[-1].lower() for c in oracle.callers_of(method)}
            if not callers:
                return False, "no GT callers"
            a = _lc(ans)
            hit = {c for c in callers if len(c) > 4 and c in a}
            return len(hit) >= min_hits, f"caller_hit {len(hit)}/{len(callers)} (need>={min_hits})"

        return check

    if kind == "callee_hit":
        min_hits = cfg.get("min_hits", 3)
        class_filter = cfg.get("class_filter", "")

        def check(ans: str, _o: Oracle):
            if class_filter:
                callees = _gt_callees_filtered(oracle, method, class_filter)
            else:
                callees = {
                    r[0]
                    for r in oracle.conn.execute(
                        """SELECT DISTINCT callee.method_name FROM resolved_call_edges e
                       JOIN methods caller ON caller.id=e.caller_method_id AND caller.variant=e.variant
                       JOIN methods callee ON callee.id=e.callee_method_id AND callee.variant=e.variant
                       WHERE e.variant='validated' AND caller.method_name=?""",
                        (method,),
                    ).fetchall()
                }
            if not callees:
                return False, "no GT callees"
            a = _lc(ans)
            hit = {c for c in callees if len(c) > 3 and c in a}
            return len(hit) >= min_hits, f"callee_hit {len(hit)}/{len(callees)} (need>={min_hits})"

        return check

    if kind == "negative":
        class_filter = cfg.get("class_filter", "")
        class_check = cfg.get("class_check", "").lower()
        _use_class_check = cfg.get("use_class_check", False)
        absent_phrases = cfg.get("absent_phrases", list(_ABSENT_PHRASES))

        def check(ans: str, _o: Oracle):
            if class_filter:
                exists = oracle.method_exists(class_filter, method)
            else:
                exists = any(c.rsplit(".", 1)[-1] == method for c in oracle.class_full_names)
            a = _lc(ans)
            says_absent = any(k in a for k in absent_phrases)
            checked = (class_check in a) if class_check else True
            ok = (not exists) and says_absent and checked
            return ok, f"exists={exists} says_absent={says_absent} checked={checked}"

        return check

    if kind == "locate_method":
        file_hint = cfg.get("file_hint", "").lower()
        purpose_kws = [k.lower() for k in cfg.get("purpose_keywords", [])]

        def check(ans: str, _o: Oracle):
            exists = method in oracle.method_names
            a = _lc(ans)
            located = file_hint in a if file_hint else True
            purpose = any(k in a for k in purpose_kws) if purpose_kws else True
            return (
                exists and located and purpose,
                f"exists={exists} located={located} purpose={purpose}",
            )

        return check

    if kind == "named_class":
        expected = cfg["expected_class"].lower()

        def check(ans: str, _o: Oracle):
            ok = expected in _lc(ans)
            return ok, f"named_{expected}={ok}"

        return check

    if kind == "get_source":
        file_hint = cfg.get("file_hint", "").lower()
        source_kws = [k.lower() for k in cfg.get("source_keywords", [])]
        anti_kws = [k.lower() for k in cfg.get("anti_keywords", [])]
        class_filter = cfg.get("class_filter", "")

        def check(ans: str, _o: Oracle):
            if class_filter:
                exists = oracle.method_exists(class_filter, method)
            else:
                exists = method in oracle.method_names
            a = _lc(ans)
            located = file_hint in a if file_hint else True
            has_source = any(k in a for k in source_kws) if source_kws else True
            is_wrong = any(k in a for k in anti_kws) if anti_kws else False
            ok = exists and located and has_source and not is_wrong
            return (
                ok,
                f"exists={exists} located={located} has_source={has_source} wrong={is_wrong}",
            )

        return check

    if kind == "change_impact":
        min_files = cfg.get("min_files", 3)

        def check(ans: str, _o: Oracle):
            caller_files = _gt_caller_files(oracle, method)
            if not caller_files:
                return False, "no GT caller files"
            a = _lc(ans)
            stems = {re.sub(r"\.[^.]+$", "", f.split("/")[-1]).lower() for f in caller_files}
            hit_path = {f for f in caller_files if f.lower() in a}
            hit_stem = {s for s in stems if len(s) >= 5 and s in a}
            hit = len(hit_path | hit_stem)
            return (
                hit >= min_files,
                f"file_hit {hit}/{len(caller_files)} files (need>={min_files})",
            )

        return check

    raise ValueError(f"Unknown checker type: {kind!r}")


def selfcheck_config(graph: str, config_path: str) -> bool:
    config = json.loads(Path(config_path).read_text())
    oracle = Oracle.load(graph)
    checks = []
    for tc in config["tasks"]:
        kind = tc["checker"]
        method = tc.get("method", "")
        tid = tc["id"]

        if kind == "negative":
            class_filter = tc.get("class_filter", "")
            if class_filter:
                absent = not oracle.method_exists(class_filter, method)
            else:
                absent = not any(c.rsplit(".", 1)[-1] == method for c in oracle.class_full_names)
            checks.append((f"{tid} {method} ABSENT", absent, "absent" if absent else "PRESENT!"))

        elif kind == "impl_count":
            iface = tc["interface"]
            n = len(oracle.implementers(iface))
            min_count = tc.get("min_count", 1)
            checks.append((f"{tid} {iface} impls>={min_count}", n >= min_count, f"{n} impls"))

        elif kind == "caller_hit":
            n = len(oracle.callers_of(method))
            min_hits = tc.get("min_hits", 3)
            checks.append((f"{tid} {method} callers>={min_hits}", n >= min_hits, f"{n} callers"))

        elif kind == "callee_hit":
            class_filter = tc.get("class_filter", "")
            callees = _gt_callees_filtered(oracle, method, class_filter) if class_filter else set()
            n = len(callees)
            min_hits = tc.get("min_hits", 3)
            checks.append((f"{tid} {method} callees>={min_hits}", n >= min_hits, f"{n} callees"))

        elif kind == "change_impact":
            n = len(_gt_caller_files(oracle, method))
            min_files = tc.get("min_files", 3)
            checks.append(
                (
                    f"{tid} {method} caller_files>={min_files}",
                    n >= min_files,
                    f"{n} caller files",
                )
            )

        elif kind == "named_class":
            expected = tc["expected_class"]
            present = any(c.rsplit(".", 1)[-1] == expected for c in oracle.class_full_names)
            checks.append(
                (
                    f"{tid} {expected} in graph",
                    present,
                    "found" if present else "MISSING",
                )
            )

        else:  # locate_method, get_source
            class_filter = tc.get("class_filter", "")
            if class_filter:
                present = oracle.method_exists(class_filter, method)
            else:
                present = method in oracle.method_names
            checks.append(
                (
                    f"{tid} {method} PRESENT",
                    present,
                    "present" if present else "MISSING",
                )
            )

    print("=== deterministic self-check (no LLM) ===")
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'ok ' if ok else 'BAD'}] {name:45} {detail}")
    print("=== ALL GT RESOLVES ===" if all_ok else "=== FIX TASKS ===")
    return all_ok


async def run_config_async(args) -> None:
    config = json.loads(Path(args.config).read_text())
    halluc_max = config.get("halluc_max", 0)
    oracle = Oracle.load(args.graph)
    client = make_client()
    backends = [
        jidra_backend(args.graph, args.codebase),
        codegraph_backend(args.codebase),
    ]

    tasks = []
    for tc in config["tasks"]:
        checker = _build_checker_java(tc, oracle)
        tasks.append(Task(tc["id"], tc["prompt"], checker))

    if args.tasks:
        want = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in want]

    results: list[dict] = []
    for task in tasks:
        for be in backends:
            print(f"\n── {task.id} / {be.name} ─────────────────────────────", flush=True)
            _skill_system = SYSTEM if be.name == "jidra" else _SYSTEM_BASE
            rr = await run_agent(
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
                rr.hallucinated = oracle.hallucinated_refs(rr.answer)
                if len(rr.hallucinated) > halluc_max:
                    rr.correct = False
                    note += f" [HALLUC_FAIL: {rr.hallucinated}]"
            d = asdict(rr)
            d["check_note"] = note
            d["cost_usd"] = _cost(d, args.model)
            results.append(d)
            tag = "ERR" if rr.error else ("OK " if rr.correct else "XX ")
            print(
                f"    {tag} {be.name:9} calls={rr.tool_calls:2} tok={rr.total_tokens:5} "
                f"cost=${d['cost_usd']:.4f} halluc={len(rr.hallucinated)} {note}",
                flush=True,
            )

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    _summary(results)
    print(f"\nwrote {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent-in-loop eval v2: JIDRA vs CodeGraph")
    ap.add_argument("--graph", required=True, help="path to JIDRA graph.db")
    ap.add_argument("--codebase", help="repo root")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--tasks", default="", help="comma list e.g. T1,T2 (default all)")
    ap.add_argument("--out", default="agent_eval_results.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--config", default="", help="JSON task config (enables config-driven mode)")
    ap.add_argument(
        "--skill",
        default="",
        help="path to a skill/agent .md file — body appended to SYSTEM prompt (YAML frontmatter stripped)",
    )
    args = ap.parse_args()
    if args.skill:
        global SYSTEM
        SYSTEM = _SYSTEM_BASE + "\n\n" + _load_skill(args.skill)
    if args.selfcheck:
        if args.config:
            raise SystemExit(0 if selfcheck_config(args.graph, args.config) else 1)
        raise SystemExit(0 if selfcheck(args.graph) else 1)
    if not args.codebase:
        ap.error("--codebase required (except with --selfcheck)")
    global VERBOSE
    VERBOSE = not args.quiet
    if args.config:
        asyncio.run(run_config_async(args))
    else:
        asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
