#!/usr/bin/env python3
"""
compare_chat.py — Compare jidra vs codegraph search side-by-side.

Usage:
    python scripts/compare_chat.py --graph /path/to/graph.db --codebase /path/to/repo
    python scripts/compare_chat.py --graph ... --codebase ... --mode search

Requires: pip install rich mcp  (already in jidra venv)
Codegraph index must exist: npx @colbymchenry/codegraph init <codebase>
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from rich.console import Console
from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VENV_PYTHON = str(Path(__file__).parent.parent / "venv" / "bin" / "python")


def approx_tokens(text: str) -> int:
    """GPT-style approximation: ~4 chars per token."""
    return max(1, len(text) // 4)


def truncate(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n… [{len(text) - max_chars} chars truncated]"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class BackendResult:
    name: str
    elapsed_ms: float = 0.0
    tokens: int = 0
    output: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


# ---------------------------------------------------------------------------
# Backend: Jidra (MCP stdio)
# ---------------------------------------------------------------------------


async def run_jidra(query: str, graph: str, codebase: str, mode: str) -> BackendResult:
    result = BackendResult(name="JIDRA")
    tool = "jidra_explore" if mode == "explore" else "jidra_search"
    params = StdioServerParameters(
        command=VENV_PYTHON,
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
        cwd=str(Path(__file__).parent.parent),
    )
    t0 = time.perf_counter()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.call_tool(tool, {"query": query})
                raw = _extract_mcp_text(resp)
                result.elapsed_ms = (time.perf_counter() - t0) * 1000
                result.output = raw
                result.tokens = approx_tokens(raw)
    except Exception as exc:
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.error = str(exc)
    return result


def _extract_mcp_text(resp: Any) -> str:
    if hasattr(resp, "content"):
        parts = []
        for item in resp.content:
            if hasattr(item, "text"):
                parts.append(item.text)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(resp)


# ---------------------------------------------------------------------------
# Backend: Codegraph (CLI subprocess)
# ---------------------------------------------------------------------------


async def run_codegraph(query: str, codebase: str, mode: str) -> BackendResult:
    result = BackendResult(name="CODEGRAPH")
    npx = shutil.which("npx")
    if not npx:
        result.error = "npx not found — install Node.js"
        return result

    sub_cmd = "explore" if mode == "explore" else "query"
    cmd = [npx, "--yes", "@colbymchenry/codegraph", sub_cmd, query, "--path", codebase]

    t0 = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            if (
                "No CodeGraph index" in err_text
                or "not initialized" in err_text.lower()
            ):
                result.error = (
                    "No index — run: npx @colbymchenry/codegraph init " + codebase
                )
            else:
                result.error = err_text or f"exit {proc.returncode}"
        else:
            raw = stdout.decode("utf-8", errors="replace").strip()
            result.output = raw
            result.tokens = approx_tokens(raw)
    except asyncio.TimeoutError:
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.error = "Timed out after 60s"
    except Exception as exc:
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Backend: Graph RAG (FTS seeds + BFS graph walk, no LLM)
# ---------------------------------------------------------------------------


def run_graph_rag(query: str, graph: str) -> BackendResult:
    result = BackendResult(name="GRAPH-RAG")
    t0 = time.perf_counter()
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from jidra.graph.graph_rag import graph_rag_query

        data = graph_rag_query(query, graph, hops=2, seed_limit=10, max_nodes=150)
        lines = [
            f"seeds={data['seed_count']}  nodes={data['node_count']}",
            "",
        ]
        for r in data["results"][:30]:
            fname = Path(r["file_path"]).name
            lines.append(
                f"[hop {r['hop']}] {r['method_name']}  ({fname}:{r.get('start_line', '')})"
            )
        raw = "\n".join(lines)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.output = raw
        result.tokens = approx_tokens(raw)
    except Exception as exc:
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


async def compare(
    query: str, graph: str, codebase: str, mode: str
) -> list[BackendResult]:
    loop = asyncio.get_event_loop()
    rag_future = loop.run_in_executor(None, run_graph_rag, query, graph)
    jidra_task = asyncio.ensure_future(run_jidra(query, graph, codebase, mode))
    cg_task = asyncio.ensure_future(run_codegraph(query, codebase, mode))
    jidra_res, cg_res, rag_res = await asyncio.gather(jidra_task, cg_task, rag_future)
    return [jidra_res, cg_res, rag_res]


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

COLORS = {"JIDRA": "cyan", "CODEGRAPH": "magenta", "GRAPH-RAG": "green"}


def render_results(results: list[BackendResult], console: Console) -> None:
    panels = []
    for r in results:
        color = COLORS.get(r.name, "white")
        header = Text()
        header.append(f"{r.name}\n", style=f"bold {color}")
        header.append(f"{r.elapsed_ms:.0f}ms  ", style="dim")
        header.append(f"~{r.tokens} tok", style="dim")

        if r.ok:
            body = Text(truncate(r.output), overflow="fold")
        else:
            body = Text(f"[ERROR] {r.error}", style="red")

        content = Text.assemble(header, "\n", body)
        panels.append(Panel(content, border_style=color, expand=True))

    console.print(Columns(panels, equal=True, expand=True))


def render_summary_row(results: list[BackendResult], console: Console) -> None:
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold white")
    t.add_column("Backend")
    t.add_column("Time (ms)", justify="right")
    t.add_column("~Tokens", justify="right")
    t.add_column("Status")
    for r in results:
        color = COLORS.get(r.name, "white")
        status = "[green]ok[/]" if r.ok else "[red]error[/]"
        t.add_row(
            f"[{color}]{r.name}[/]",
            f"{r.elapsed_ms:.0f}",
            str(r.tokens) if r.ok else "—",
            status,
        )
    console.print(t)


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


def repl(graph: str, codebase: str, mode: str) -> None:
    console = Console()
    console.print(
        Panel(
            f"[bold]jidra vs codegraph[/]  |  mode=[cyan]{mode}[/]  |  "
            f"graph=[dim]{graph}[/]  |  codebase=[dim]{codebase}[/]\n"
            "Type a query and press Enter.  [dim]Ctrl-C or 'exit' to quit.[/]",
            border_style="blue",
        )
    )

    while True:
        try:
            query = console.input("[bold blue]>[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]bye[/]")
            break

        if not query:
            continue
        if query.lower() in {"exit", "quit", "q"}:
            console.print("[dim]bye[/]")
            break

        console.print("[dim]Querying all backends in parallel…[/]")
        results = asyncio.run(compare(query, graph, codebase, mode))
        render_results(results, console)
        render_summary_row(results, console)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare jidra / codegraph search side-by-side"
    )
    parser.add_argument(
        "--graph", required=True, help="Path to jidra graph.db (or .jsonl)"
    )
    parser.add_argument("--codebase", required=True, help="Path to the codebase root")
    parser.add_argument(
        "--mode",
        choices=["explore", "search"],
        default="explore",
        help="Tool mode: explore (default) or search",
    )
    args = parser.parse_args()

    graph = str(Path(args.graph).expanduser().resolve())
    codebase = str(Path(args.codebase).expanduser().resolve())

    if not Path(graph).exists():
        print(f"ERROR: graph not found: {graph}", file=sys.stderr)
        sys.exit(1)
    if not Path(codebase).is_dir():
        print(f"ERROR: codebase not a directory: {codebase}", file=sys.stderr)
        sys.exit(1)

    repl(graph, codebase, args.mode)


if __name__ == "__main__":
    main()
