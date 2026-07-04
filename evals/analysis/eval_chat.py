#!/usr/bin/env python3
"""
eval_chat.py — Score jidra vs codegraph on queries with known answers.

Usage:
    python scripts/eval_chat.py --graph .jidra/graph.db --codebase /path/to/repo --queries scripts/eval_queries.yaml

Query file format (YAML):
    queries:
      - query: "REDACTED"
        expected: ["REDACTED"]   # substrings to match in results
        type: exact                              # exact | caller | concept
        note: "Should rank controller methods first"

Outputs:
    - Rich table in terminal
    - eval_results.json  (for further analysis)

Scoring per query:
    rank_first   : 1-based position of first hit matching any expected substring (0 = not found)
    recall       : % of expected substrings found anywhere in results
    tokens       : approx token count of raw response (chars/4)
    time_ms      : wall-clock ms
    noise_ratio  : (total_hits - matched_hits) / total_hits
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from rich.console import Console
from rich.table import Table
from rich import box

VENV_PYTHON = str(Path(__file__).parent.parent / "venv" / "bin" / "python")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


EXCLUDE_DIRS = [
    "venv",
    "node_modules",
    ".git",
    "__pycache__",
    ".jidra",
    ".codegraph",
    ".jidra_test",
    "dist",
    "build",
    "jidra.egg-info",
]


def grep_ground_truth(expected: list[str], codebase: str) -> set[str]:
    """Run rg/grep -rl for each expected symbol → union of matching files (absolute paths)."""
    import shutil
    import subprocess

    rg = (
        shutil.which("rg")
        or shutil.which("/opt/homebrew/bin/rg")
        or shutil.which("/usr/local/bin/rg")
    )
    gt: set[str] = set()
    for sym in expected:
        try:
            if rg:
                exclude_args = []
                for d in EXCLUDE_DIRS:
                    exclude_args += ["--glob", f"!{d}/**"]
                cmd = (
                    [rg, "-l", "--max-filesize", "1M"] + exclude_args + [sym, codebase]
                )
            else:
                exclude_args = [f"--exclude-dir={d}" for d in EXCLUDE_DIRS]
                cmd = ["grep", "-rl"] + exclude_args + [sym, codebase]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=15)
            for line in out.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line:
                    gt.add(str(Path(line).resolve()))
        except subprocess.CalledProcessError:
            pass
        except Exception:
            pass
    return gt


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def extract_result_files(raw: str, codebase: str = "") -> list[str]:
    """Extract absolute file paths from tool output (jidra JSON or codegraph plain text)."""
    files: list[str] = []
    try:
        data = json.loads(raw)
        for r in data.get("results", []):
            fp = r.get("file_path", "")
            if fp:
                files.append(str(Path(fp).resolve()))
        return files
    except Exception:
        # codegraph plain text — lines like:
        #   "  REDACTED/src/main/java/.../Foo.java:45"
        #   "  boolean (Optional<REDACTED> filter)"   ← skip
        for line in raw.splitlines():
            clean = _ANSI_RE.sub("", line).strip()
            # strip markdown bold/backtick decorators codegraph adds: **`path`**
            clean = re.sub(r"[*`]+", "", clean).strip()
            # match a path segment ending in a known source extension, optional :lineno
            m = re.match(
                r"([^\s]+\.(?:java|kt|scala|go|py|ts|tsx|js|jsx|rs|cpp|c|h))(?::\d+)?",
                clean,
            )
            if m:
                candidate = m.group(1)
                p = Path(candidate)
                if p.is_absolute():
                    files.append(str(p.resolve()))
                elif codebase:
                    # codegraph outputs repo-relative paths e.g. "REDACTED/src/..."
                    files.append(str((Path(codebase) / candidate).resolve()))
        return files


def extract_result_texts(raw: str) -> list[str]:
    """Pull method_name + signature + file_path strings for legacy substring scoring."""
    try:
        data = json.loads(raw)
        return [
            " ".join(
                [
                    r.get("method_name", ""),
                    r.get("signature", ""),
                    r.get("file_path", ""),
                    r.get("class_full_name", ""),
                ]
            ).lower()
            for r in data.get("results", [])
        ]
    except Exception:
        return [line.lower() for line in raw.splitlines() if line.strip()]


def score(result_texts: list[str], expected: list[str]) -> tuple[int, float, float]:
    """Legacy substring score. Returns (rank_first, recall, noise_ratio)."""
    expected_lower = [e.lower() for e in expected]
    rank_first = 0
    matched_positions: set[int] = set()
    expected_found: set[str] = set()
    for i, text in enumerate(result_texts, start=1):
        for exp in expected_lower:
            if exp in text:
                if rank_first == 0:
                    rank_first = i
                matched_positions.add(i)
                expected_found.add(exp)
    recall = len(expected_found) / len(expected_lower) if expected_lower else 1.0
    noise_ratio = (
        (len(result_texts) - len(matched_positions)) / len(result_texts)
        if result_texts
        else 0.0
    )
    return rank_first, recall, noise_ratio


def score_gt(
    result_files: list[str], gt_files: set[str], codebase: str
) -> tuple[int, float, float]:
    """Ground-truth file-based score. Returns (rank_first, gt_recall, gt_noise).

    rank_first : position of first result whose file is in gt_files (0 = not found)
    gt_recall  : fraction of GT files surfaced anywhere in results
    gt_noise   : fraction of result files NOT in GT
    """
    if not gt_files:
        return 0, 0.0, 1.0

    # normalise codegraph relative paths against codebase
    def resolve(p: str) -> str:
        path = Path(p)
        if path.is_absolute():
            return str(path)
        return str((Path(codebase) / path).resolve())

    resolved = [resolve(f) for f in result_files]

    rank_first = 0
    gt_found: set[str] = set()
    noise_count = 0
    for i, fp in enumerate(resolved, start=1):
        if fp in gt_files:
            if rank_first == 0:
                rank_first = i
            gt_found.add(fp)
        else:
            noise_count += 1

    gt_recall = len(gt_found) / len(gt_files)
    gt_noise = noise_count / len(resolved) if resolved else 0.0
    return rank_first, gt_recall, gt_noise


# ---------------------------------------------------------------------------
# MCP text extractor
# ---------------------------------------------------------------------------


def _extract_mcp_text(resp: Any) -> str:
    if hasattr(resp, "content"):
        return "\n".join(
            item.text if hasattr(item, "text") else str(item) for item in resp.content
        )
    return str(resp)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


async def run_jidra(
    query: str, graph: str, codebase: str, mode: str
) -> tuple[str, float]:
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
                return raw, (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return f"ERROR: {exc}", (time.perf_counter() - t0) * 1000


def run_graph_rag_sync(query: str, graph: str, codebase: str = "") -> tuple[str, float]:  # noqa: ARG001
    t0 = time.perf_counter()
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from jidra.graph.graph_rag import graph_rag_query

        data = graph_rag_query(query, graph, hops=2, seed_limit=10, max_nodes=150)
        # emit one line per result so extract_result_files can parse file_path
        raw = json.dumps(
            {
                "results": [
                    {
                        "method_id": r["method_id"],
                        "method_name": r["method_name"],
                        "signature": r["signature"],
                        "class_full_name": r["class_full_name"],
                        "file_path": r["file_path"],
                        "language": r["language"],
                    }
                    for r in data["results"]
                ]
            }
        )
        return raw, (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return f"ERROR: {exc}", (time.perf_counter() - t0) * 1000


async def run_graph_rag(query: str, graph: str, codebase: str) -> tuple[str, float]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_graph_rag_sync, query, graph, codebase)


async def run_codegraph(query: str, codebase: str, mode: str) -> tuple[str, float]:
    import shutil

    npx = shutil.which("npx")
    if not npx:
        return "ERROR: npx not found", 0.0
    sub_cmd = "explore" if mode == "explore" else "query"
    cmd = [npx, "--yes", "@colbymchenry/codegraph", sub_cmd, query, "--path", codebase]
    t0 = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        elapsed = (time.perf_counter() - t0) * 1000
        if proc.returncode != 0:
            return f"ERROR: {stderr.decode('utf-8', errors='replace').strip()}", elapsed
        return stdout.decode("utf-8", errors="replace").strip(), elapsed
    except asyncio.TimeoutError:
        return "ERROR: timeout", (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return f"ERROR: {exc}", (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    query: str
    query_type: str
    expected: list[str]
    note: str = ""

    # grep ground truth
    gt_files: list[str] = None  # type: ignore[assignment]
    gt_count: int = 0

    jidra_raw: str = ""
    jidra_time_ms: float = 0.0
    jidra_tokens: int = 0
    jidra_rank_first: int = 0
    jidra_recall: float = 0.0  # legacy substring recall
    jidra_noise: float = 0.0
    jidra_gt_recall: float = 0.0  # grep-based recall
    jidra_gt_noise: float = 0.0
    jidra_gt_rank: int = 0

    codegraph_raw: str = ""
    codegraph_time_ms: float = 0.0
    codegraph_tokens: int = 0
    codegraph_rank_first: int = 0
    codegraph_recall: float = 0.0
    codegraph_noise: float = 0.0
    codegraph_gt_recall: float = 0.0
    codegraph_gt_noise: float = 0.0
    codegraph_gt_rank: int = 0

    rag_raw: str = ""
    rag_time_ms: float = 0.0
    rag_tokens: int = 0
    rag_gt_recall: float = 0.0
    rag_gt_noise: float = 0.0
    rag_gt_rank: int = 0

    def __post_init__(self):
        if self.gt_files is None:
            self.gt_files = []


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------


async def eval_query(q: dict, graph: str, codebase: str, mode: str) -> QueryResult:
    query = q["query"]
    expected = q.get("expected", [])
    result = QueryResult(
        query=query,
        query_type=q.get("type", "unknown"),
        expected=expected,
        note=q.get("note", ""),
    )

    # grep ground truth (sync, fast — runs before backends)
    gt_files = grep_ground_truth(expected, codebase)
    result.gt_files = sorted(gt_files)
    result.gt_count = len(gt_files)

    (
        (jidra_raw, jidra_time),
        (cg_raw, cg_time),
        (rag_raw, rag_time),
    ) = await asyncio.gather(
        run_jidra(query, graph, codebase, mode),
        run_codegraph(query, codebase, mode),
        run_graph_rag(query, graph, codebase),
    )

    result.jidra_raw = jidra_raw
    result.jidra_time_ms = jidra_time
    result.jidra_tokens = approx_tokens(jidra_raw)
    jidra_texts = extract_result_texts(jidra_raw)
    result.jidra_rank_first, result.jidra_recall, result.jidra_noise = score(
        jidra_texts, expected
    )
    jidra_files = extract_result_files(jidra_raw, codebase)
    result.jidra_gt_rank, result.jidra_gt_recall, result.jidra_gt_noise = score_gt(
        jidra_files, gt_files, codebase
    )

    result.codegraph_raw = cg_raw
    result.codegraph_time_ms = cg_time
    result.codegraph_tokens = approx_tokens(cg_raw)
    cg_texts = extract_result_texts(cg_raw)
    result.codegraph_rank_first, result.codegraph_recall, result.codegraph_noise = (
        score(cg_texts, expected)
    )
    cg_files = extract_result_files(cg_raw, codebase)
    result.codegraph_gt_rank, result.codegraph_gt_recall, result.codegraph_gt_noise = (
        score_gt(cg_files, gt_files, codebase)
    )

    result.rag_raw = rag_raw
    result.rag_time_ms = rag_time
    result.rag_tokens = approx_tokens(rag_raw)
    rag_files = extract_result_files(rag_raw, codebase)
    result.rag_gt_rank, result.rag_gt_recall, result.rag_gt_noise = score_gt(
        rag_files, gt_files, codebase
    )

    return result


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

RANK_STYLE = {0: "red", 1: "green", 2: "yellow", 3: "yellow"}


def rank_style(r: int) -> str:
    return RANK_STYLE.get(r, "white")


def pct(f: float) -> str:
    return f"{f * 100:.0f}%"


def render_table(results: list[QueryResult], console: Console) -> None:
    t = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        title="Eval Results (GT = grep ground truth)",
    )
    t.add_column("Query", max_width=28)
    t.add_column("Type", width=7)
    t.add_column("GT#", justify="right")  # grep hit count
    # jidra cols
    t.add_column("J.rank", justify="right", style="cyan")
    t.add_column("J.GT↑", justify="right", style="cyan")  # gt recall
    t.add_column("J.noise", justify="right", style="cyan")
    t.add_column("J ms", justify="right", style="cyan")
    t.add_column("J tok", justify="right", style="cyan")
    # codegraph cols
    t.add_column("CG.rank", justify="right", style="magenta")
    t.add_column("CG.GT↑", justify="right", style="magenta")
    t.add_column("CG.noise", justify="right", style="magenta")
    t.add_column("CG ms", justify="right", style="magenta")
    # graph-rag cols
    t.add_column("RAG.rank", justify="right", style="green")
    t.add_column("RAG.GT↑", justify="right", style="green")
    t.add_column("RAG.noise", justify="right", style="green")
    t.add_column("RAG ms", justify="right", style="green")
    t.add_column("Winner(GT)", justify="center")

    for r in results:
        j_err = r.jidra_raw.startswith("ERROR")
        cg_err = r.codegraph_raw.startswith("ERROR")
        rag_err = r.rag_raw.startswith("ERROR") if r.rag_raw else True

        def _s(rank):
            return rank if rank > 0 else 9999

        scores = {
            "JIDRA": (_s(r.jidra_gt_rank), -r.jidra_gt_recall),
            "CG": (_s(r.codegraph_gt_rank), -r.codegraph_gt_recall),
            "RAG": (_s(r.rag_gt_rank), -r.rag_gt_recall),
        }
        if j_err:
            scores.pop("JIDRA")
        if cg_err:
            scores.pop("CG")
        if rag_err:
            scores.pop("RAG")

        if not scores or r.gt_count == 0:
            winner = "[dim]no GT[/]" if r.gt_count == 0 else "[red]all err[/]"
        else:
            best = min(scores, key=lambda k: scores[k])
            color = {"JIDRA": "cyan", "CG": "magenta", "RAG": "green"}[best]
            winner = f"[{color}]{best}[/]"

        j_rank_str = f"[{rank_style(r.jidra_gt_rank)}]{r.jidra_gt_rank or 'NF'}[/]"
        cg_rank_str = (
            f"[{rank_style(r.codegraph_gt_rank)}]{r.codegraph_gt_rank or 'NF'}[/]"
        )
        rag_rank_str = f"[{rank_style(r.rag_gt_rank)}]{r.rag_gt_rank or 'NF'}[/]"

        t.add_row(
            r.query[:28],
            r.query_type,
            str(r.gt_count),
            j_rank_str if not j_err else "[red]ERR[/]",
            pct(r.jidra_gt_recall) if not j_err else "—",
            pct(r.jidra_gt_noise) if not j_err else "—",
            f"{r.jidra_time_ms:.0f}" if not j_err else "—",
            str(r.jidra_tokens) if not j_err else "—",
            cg_rank_str if not cg_err else "[red]ERR[/]",
            pct(r.codegraph_gt_recall) if not cg_err else "—",
            pct(r.codegraph_gt_noise) if not cg_err else "—",
            f"{r.codegraph_time_ms:.0f}" if not cg_err else "—",
            rag_rank_str if not rag_err else "[red]ERR[/]",
            pct(r.rag_gt_recall) if not rag_err else "—",
            pct(r.rag_gt_noise) if not rag_err else "—",
            f"{r.rag_time_ms:.0f}" if not rag_err else "—",
            winner,
        )

    console.print(t)


def render_summary(results: list[QueryResult], console: Console) -> None:
    valid = [
        r
        for r in results
        if not r.jidra_raw.startswith("ERROR")
        and not r.codegraph_raw.startswith("ERROR")
    ]
    if not valid:
        console.print("[red]No valid results to summarize.[/]")
        return

    def avg(vals):
        return sum(vals) / len(vals) if vals else 0

    gt_valid = [r for r in valid if r.gt_count > 0]

    j_wins = sum(
        1 for r in gt_valid if (r.jidra_gt_rank or 9999) < (r.codegraph_gt_rank or 9999)
    )
    cg_wins = sum(
        1 for r in gt_valid if (r.codegraph_gt_rank or 9999) < (r.jidra_gt_rank or 9999)
    )
    ties = len(gt_valid) - j_wins - cg_wins

    s = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold white",
        title="Summary (GT-scored)",
    )
    s.add_column("Metric")
    s.add_column("JIDRA", style="cyan", justify="right")
    s.add_column("CODEGRAPH", style="magenta", justify="right")
    s.add_column("GRAPH-RAG", style="green", justify="right")

    s.add_row(
        "GT rank (lower=better)",
        f"{avg([r.jidra_gt_rank for r in gt_valid if r.jidra_gt_rank > 0]):.1f}",
        f"{avg([r.codegraph_gt_rank for r in gt_valid if r.codegraph_gt_rank > 0]):.1f}",
        f"{avg([r.rag_gt_rank for r in gt_valid if r.rag_gt_rank > 0]):.1f}",
    )
    s.add_row(
        "GT recall (higher=better)",
        pct(avg([r.jidra_gt_recall for r in gt_valid])),
        pct(avg([r.codegraph_gt_recall for r in gt_valid])),
        pct(avg([r.rag_gt_recall for r in gt_valid])),
    )
    s.add_row(
        "GT noise  (lower=better)",
        pct(avg([r.jidra_gt_noise for r in gt_valid])),
        pct(avg([r.codegraph_gt_noise for r in gt_valid])),
        pct(avg([r.rag_gt_noise for r in gt_valid])),
    )
    s.add_row(
        "Avg time ms",
        f"{avg([r.jidra_time_ms for r in valid]):.0f}",
        f"{avg([r.codegraph_time_ms for r in valid]):.0f}",
        f"{avg([r.rag_time_ms for r in valid]):.0f}",
    )
    s.add_row(
        "Avg tokens",
        f"{avg([r.jidra_tokens for r in valid]):.0f}",
        f"{avg([r.codegraph_tokens for r in valid]):.0f}",
        f"{avg([r.rag_tokens for r in valid]):.0f}",
    )
    s.add_row("GT rank wins", str(j_wins), str(cg_wins), "—")
    s.add_row("Ties / no-GT", str(ties), str(len(valid) - len(gt_valid)), "—")

    console.print(s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score jidra vs codegraph on known-answer queries"
    )
    parser.add_argument("--graph", required=True)
    parser.add_argument("--codebase", required=True)
    parser.add_argument("--queries", required=True, help="YAML file with query list")
    parser.add_argument("--mode", choices=["explore", "search"], default="explore")
    parser.add_argument("--out", default="eval_results.json", help="JSON output path")
    args = parser.parse_args()

    graph = str(Path(args.graph).expanduser().resolve())
    codebase = str(Path(args.codebase).expanduser().resolve())
    queries_path = Path(args.queries).expanduser().resolve()

    if not Path(graph).exists():
        print(f"ERROR: graph not found: {graph}", file=sys.stderr)
        sys.exit(1)
    if not Path(codebase).is_dir():
        print(f"ERROR: codebase not a dir: {codebase}", file=sys.stderr)
        sys.exit(1)
    if not queries_path.exists():
        print(f"ERROR: queries file not found: {queries_path}", file=sys.stderr)
        sys.exit(1)

    with open(queries_path) as f:
        data = yaml.safe_load(f)
    queries = data.get("queries", [])

    console = Console()
    console.print(f"[bold]Running {len(queries)} queries | mode={args.mode}[/]")

    async def run_all():
        results = []
        for i, q in enumerate(queries, 1):
            console.print(f"[dim]  [{i}/{len(queries)}] {q['query'][:60]}[/]")
            r = await eval_query(q, graph, codebase, args.mode)
            results.append(r)
        return results

    results = asyncio.run(run_all())

    render_table(results, console)
    render_summary(results, console)

    out_path = Path(args.out)
    with open(out_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    console.print(f"\n[dim]Results saved → {out_path}[/]")


if __name__ == "__main__":
    main()
