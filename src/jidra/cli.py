from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from .graph import graph_store
from .utils import ui
from .server.actuator_client import (
    ActuatorError,
    fetch_beans_from_url,
    run_docker_and_fetch_beans,
)
from .utils.context_builder import build_method_context
from .engine.engine import JidraEngine
from .extractors.extractor import build_graph
from .flow.flow_doc_agent import FlowDocAgent
from .flow.flow_stitcher import stitch_flow
from .graph.graph_validator import parse_actuator_beans, validate_graph
from .graph.graph_visualizer import build_graph_data, render_interactive_html
from .utils.selector import (
    _method_ambiguous_error,
    _method_not_found_error,
    _resolve_method_selector,
)
from .llm.trace_engine import trace_method, trace_route

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output" / "database"


def _git_branch(repo: Path) -> str | None:
    """Current branch name, or None if `repo` isn't a git repo (or is detached)."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch if branch and branch != "HEAD" else None


def _repo_output_dir(repo: Path, suffix: str | None = None) -> Path:
    """Per-repo output dir under jidra's own OUTPUT_DIR, so `jidra up` never
    writes graph.db/reports/visualizations into the target repo.

    Named `<repo-slug>-<branch>` when the target is a git repo on a named
    branch; falls back to `<repo-slug>-<path-hash>` otherwise. `suffix` (e.g.
    a random short hash) can be appended to force a new, non-colliding dir
    when the caller wants to start fresh instead of reusing an existing one.
    """
    import hashlib

    resolved = repo.resolve()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", resolved.name).strip("-") or "repo"

    branch = _git_branch(resolved)
    if branch:
        key = re.sub(r"[^A-Za-z0-9_-]+", "-", branch).strip("-") or "branch"
    else:
        key = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]

    dir_name = f"{slug}-{key}"
    if suffix:
        dir_name = f"{dir_name}-{suffix}"
    return OUTPUT_DIR / dir_name


NON_BUSINESS_SIGNATURE_PARTS = (
    ".metrics.",
    ".datadog.",
    ".config.datadog.",
    ".prometheus.",
    ".logging.",
    ".log.",
    ".utils.",
    ".constants.",
    "searchservicemetrics#",
    "dogstatsdclient#",
    "custommetriccounter",
    "tagbuilder#",
    "datadogconstants#",
)
NON_BUSINESS_CALL_NAMES = {
    "getMarker",
    "increment",
    "decrement",
    "decrementControllerCounter",
    "incrementControllerCounter",
    "recordExecutionTime",
    "createExperimentTag",
    "createLogHeadersMap",
    "build",
}
STACK_RE = re.compile(
    r"^\s*at\s+([A-Za-z0-9_.$]+)\.([A-Za-z0-9_$<>]+)\(([^:()]+):(\d+)\)\s*$"
)


def _resolve_graph_db_path(graph_arg: str | None) -> Path:
    if graph_arg:
        return graph_store.resolve_graph_db_path(Path(graph_arg).resolve())
    return graph_store.resolve_graph_db_path(OUTPUT_DIR)


def _safe_filename_part(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", value.lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def _method_filename_part(method) -> str:
    class_name = ""
    if getattr(method, "class_full_name", None):
        class_name = method.class_full_name.split(".")[-1]
    method_name = getattr(method, "method_name", "")
    combined = f"{class_name}_{method_name}".strip("_")
    if combined:
        return _safe_filename_part(combined)
    return _safe_filename_part(getattr(method, "id", "method"))


def _normalize_stack_trace_text(text: str) -> str:
    text = text or ""
    # Insert newline before Java stack frame markers when pasted inline.
    text = re.sub(r"\s+(at\s+[\w.$/]+\.[\w$<>]+\([^)]*\))", r"\n\1", text)
    return text.strip()


def _parse_stack_trace(text: str) -> list[dict]:
    text = _normalize_stack_trace_text(text)
    frames: list[dict] = []
    for idx, raw in enumerate(text.splitlines()):
        m = STACK_RE.match(raw)
        if not m:
            continue
        class_full, method_name, file_name, line = m.groups()
        frames.append(
            {
                "frame_index": len(frames),
                "raw_index": idx,
                "class_full_name": class_full,
                "method_name": method_name,
                "file_name": file_name,
                "line": int(line),
            }
        )
    return frames


def _match_stack_frames_to_methods(
    graph, frames: list[dict]
) -> tuple[list[dict], dict | None]:
    methods = list(graph.methods)
    matched_rows: list[dict] = []
    anchor = None

    # Avoid hardcoding internal org package prefixes. Allow override via env.
    # Example: export JIDRA_PROJECT_PREFIXES="com.myco.,org.example."
    raw_prefixes = (os.getenv("JIDRA_PROJECT_PREFIXES") or "").strip()
    if raw_prefixes:
        project_prefixes = tuple(
            p.strip() for p in raw_prefixes.split(",") if p.strip()
        )
    else:
        # Default: treat any Java package as "project" for anchoring purposes.
        project_prefixes = ""
    for frame in frames:
        candidates = []
        for m in methods:
            if getattr(m, "class_full_name", "") != frame["class_full_name"]:
                continue
            if getattr(m, "method_name", "") != frame["method_name"]:
                continue
            file_base = Path(getattr(m, "file_path", "")).name
            if file_base != frame["file_name"]:
                continue
            start = int(getattr(m, "start_line", 0) or 0)
            end = int(getattr(m, "end_line", 0) or 0)
            if start and end and not (start <= frame["line"] <= end):
                continue
            candidates.append(m)
        status = "unmatched"
        method_id = ""
        ambiguity = []
        if len(candidates) == 1:
            status = "matched"
            method_id = candidates[0].id
        elif len(candidates) > 1:
            status = "ambiguous"
            ambiguity = [c.id for c in candidates]
        row = {
            **frame,
            "match_status": status,
            "matched_method_id": method_id,
            "ambiguous_method_ids": ambiguity,
        }
        matched_rows.append(row)
        if (
            anchor is None
            and status in {"matched", "ambiguous"}
            and frame["class_full_name"].startswith(project_prefixes)
        ):
            anchor = row
    return matched_rows, anchor


def _is_meaningful_signature(sig: str) -> bool:
    s = (sig or "").lower()
    noisy = (".metrics.", ".utils.", ".constants.", ".datadog.", ".logging.", ".log.")
    return not any(x in s for x in noisy)


def _is_error_doc_noise_call(call: dict) -> bool:
    receiver = str(call.get("receiver") or "")
    receiver_type = str(call.get("receiver_type") or "")
    name = str(call.get("call") or "")
    combined = f"{receiver} {receiver_type} {name}"
    noisy_receiver_prefixes = (
        "builder",
        "log",
        "Thread",
        "dogStatsdClient",
    )
    if any(
        receiver == p or receiver.startswith(f"{p}.") or receiver.startswith(f"{p}(")
        for p in noisy_receiver_prefixes
    ):
        return True
    if receiver == "e" and name in {
        "getMessage",
        "getCause",
        "getStackTrace",
    }:
        return True
    if "Health.Builder" in combined:
        return True
    if name in {
        "warn",
        "error",
        "info",
        "debug",
        "trace",
        "increment",
        "sleep",
        "currentThread",
        "interrupt",
    }:
        return True
    return False


def _extract_focused_map_sections(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("## Root Flow"):
            break
        out.append(line)
    return "\n".join(out).strip()


def _no_stack_frame_error_payload(raw_text: str) -> dict:
    preview = (raw_text or "")[:300]
    return {
        "error": "no_stack_frames_parsed",
        "message": "No Java stack frames were found. Paste the full stack trace including lines like: at package.Class.method(File.java:123)",
        "received_preview": preview,
        "expected_frame_format": "at package.Class.method(File.java:123)",
    }


def _write_or_print_json(
    result: dict, output: str | None, default_filename: str
) -> None:
    if not output:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return

    out_path = Path(output).resolve()
    if out_path.exists() and out_path.is_dir():
        target = out_path / default_filename
    elif out_path.suffix:
        target = out_path
    else:
        out_path.mkdir(parents=True, exist_ok=True)
        target = out_path / default_filename

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_or_print_text(text: str, output: str | None, default_filename: str) -> None:
    if not output:
        print(text)
        return
    out_path = Path(output).resolve()
    if out_path.exists() and out_path.is_dir():
        target = out_path / default_filename
    elif out_path.suffix:
        target = out_path
    else:
        out_path.mkdir(parents=True, exist_ok=True)
        target = out_path / default_filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _terminal_supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _style(text: str, code: str) -> str:
    if not _terminal_supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _style(text, "1")


def dim(text: str) -> str:
    return _style(text, "2")


def green(text: str) -> str:
    return _style(text, "32")


def yellow(text: str) -> str:
    return _style(text, "33")


def cyan(text: str) -> str:
    return _style(text, "36")


def _print_diagnose_report(result: dict) -> None:
    llm = result.get("llm", {})
    usage = llm.get("usage", {})
    print(bold("JIDRA Diagnose"))
    print(f"{dim('Method:')} {result.get('method', '')}")
    print(
        f"{dim('Model:')} {cyan(str(llm.get('model', '')))}  {dim('Profile:')} {cyan(str(llm.get('profile', '')))}"
    )
    print(f"{dim('Latency:')} {green(str(llm.get('latency_seconds', 0.0)) + 's')}")
    print(
        f"{dim('Tokens:')} in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)} "
        f"total={usage.get('total_tokens', 0)}"
    )
    if usage.get("reasoning_tokens", 0):
        print(f"{dim('Reasoning tokens:')} {usage.get('reasoning_tokens', 0)}")
    if usage.get("estimated"):
        print(yellow("Token usage estimated (provider usage unavailable)."))
    print()
    print(result.get("analysis", ""))


def is_business_entry(entry: dict) -> bool:
    call_name = str(entry.get("call") or "").lower()
    if call_name in NON_BUSINESS_CALL_NAMES:
        return False

    signature = str(
        entry.get("target_signature") or entry.get("signature") or ""
    ).lower()
    if any(part in signature for part in NON_BUSINESS_SIGNATURE_PARTS):
        return False
    return True


def _apply_business_only_context(result: dict) -> int:
    resolved = result.get("resolved_callees", [])
    filtered = [entry for entry in resolved if is_business_entry(entry)]
    removed = len(resolved) - len(filtered)
    result["resolved_callees"] = filtered
    result["business_flow"] = filtered
    return removed


def _apply_business_only_trace(result: dict) -> int:
    flow = result.get("flow", [])
    filtered = []
    removed = 0
    for entry in flow:
        if entry.get("depth") == 0:
            filtered.append(entry)
            continue
        if is_business_entry(entry):
            filtered.append(entry)
        else:
            removed += 1
    result["flow"] = filtered
    return removed


def _build_prompt(context: dict, target: str) -> str:
    method_signature = context.get("method_signature", "")
    method_source = context.get("method_source", "")
    business_flow = context.get("business_flow") or context.get("resolved_callees", [])
    unresolved = context.get("unresolved_calls", [])

    if target == "claude":
        target_instruction = "Reason carefully. Be explicit about uncertainty. Do not invent missing call edges."
    elif target == "codex":
        target_instruction = "Focus on code navigation, likely next files/methods to inspect, and implementation-relevant details."
    else:
        target_instruction = ""

    def bullet_flow(items):
        lines = []
        for item in items:
            call = item.get("call", "unknown")
            sig = item.get("target_signature", item.get("signature", "unknown"))
            line_info = item.get("lines", [])
            lines.append(f"- {call} -> {sig} at lines {line_info}")
        return "\n".join(lines) if lines else "- None"

    return f"""# JIDRA Java Diagnostic Context
## Task
Analyze the selected Java method using only the graph-grounded context below.
{target_instruction}

## Entry Method
{method_signature}

## Business Flow
{bullet_flow(business_flow)}

## Method Source
```java
{method_source}
```

## Unresolved / Uncertain Calls
{json.dumps(unresolved, ensure_ascii=True, indent=2)}

## Context Notes
- This context is derived from static analysis of a Java codebase.
- Some calls may represent infrastructure concerns such as logging, metrics, markers, observability, telemetry, or tracing.
- These are often not part of the core business logic, but may still influence behavior in some cases.
- Focus primarily on business-relevant method calls and data flow.
- Treat unresolved calls as uncertain; they may come from:
    - external libraries
    - framework abstractions
    - generated code
    - lambda/local variable chains
- Do not assume unresolved calls are incorrect, but prioritize resolved business calls for reasoning.

## Instructions
* Explain what this method does at a business level.
* Identify the most important downstream business calls.
* Distinguish between business logic and infrastructure/utility code.
* Highlight ambiguous or unresolved areas and explain possible causes.
* Suggest specific methods or classes to inspect next.
"""


def _select_top_flow_nodes(flow: dict, top_n: int) -> list[dict]:
    agent_view = flow.get("agent_view", {}) if isinstance(flow, dict) else {}
    top_nodes = agent_view.get("top_nodes")
    if isinstance(top_nodes, list) and top_nodes:
        normalized = []
        for n in top_nodes[:top_n]:
            normalized.append(
                {
                    "method_id": n.get("method_id") or n.get("id"),
                    "signature": n.get("signature"),
                    "file_path": n.get("file_path"),
                    "depth": n.get("depth"),
                    "tier": n.get("tier"),
                    "rank_score": n.get("rank_score", 0.0),
                    "rank_reason": n.get("rank_reason", []),
                    "line_start": n.get("line_start"),
                    "line_end": n.get("line_end"),
                    "start_line": n.get("start_line"),
                    "end_line": n.get("end_line"),
                }
            )
        return normalized

    nodes = list(flow.get("nodes", []))
    nodes.sort(key=lambda n: float(n.get("rank_score", 0.0)), reverse=True)
    return [
        {
            "method_id": n.get("id"),
            "signature": n.get("signature"),
            "file_path": n.get("file_path"),
            "depth": n.get("depth"),
            "tier": n.get("tier"),
            "rank_score": n.get("rank_score", 0.0),
            "rank_reason": n.get("rank_reason", []),
            "line_start": n.get("line_start"),
            "line_end": n.get("line_end"),
        }
        for n in nodes[:top_n]
    ]


def _build_flow_prompt(
    method,
    flow: dict,
    target: str,
    *,
    top_n: int,
    include_source: bool,
    verbose_flow: bool,
    max_chars: int,
    graph,
) -> str:
    if target == "claude":
        target_instruction = "Reason carefully. Be explicit about uncertainty. Do not invent missing call edges."
    elif target == "codex":
        target_instruction = "Focus on code navigation, likely next files/methods to inspect, and implementation-relevant details."
    else:
        target_instruction = ""

    top_nodes = _select_top_flow_nodes(flow, top_n=max(1, top_n))
    uncertain = (flow.get("agent_view", {}) or {}).get("uncertain_edges") or flow.get(
        "uncertain_edges", []
    )

    refs = []
    for i, n in enumerate(top_nodes, start=1):
        line_start = n.get("line_start") or n.get("start_line")
        line_end = n.get("line_end") or n.get("end_line")
        if line_start and line_end:
            lines = f"{line_start}-{line_end}"
        elif line_start:
            lines = str(line_start)
        else:
            lines = "unknown"
        refs.append(
            f"{i}. {n.get('signature', '')}\n"
            f"   method_id: {n.get('method_id', '')}\n"
            f"   file: {n.get('file_path', '')}\n"
            f"   lines: {lines}\n"
            f"   rank_score: {n.get('rank_score', 0)}\n"
            f"   tier: {n.get('tier', '')}"
        )
        if verbose_flow:
            refs[-1] += f"\n   reasons: {n.get('rank_reason', [])}"
    refs_text = "\n".join(refs) if refs else "- None"

    if verbose_flow:
        uncertain_section = json.dumps(uncertain, ensure_ascii=True, indent=2)
    else:
        call_counts: dict[str, int] = {}
        total_uncertain = 0
        for edge in uncertain:
            count = int(edge.get("count", 1) or 1)
            total_uncertain += count
            call = str(edge.get("call") or "unknown")
            call_counts[call] = call_counts.get(call, 0) + count
        top_calls = sorted(call_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        top_lines = (
            "\n".join(
                [f"  - call: {call}, count: {count}" for call, count in top_calls]
            )
            or "  - None"
        )
        uncertain_section = (
            "## Uncertain Edge Summary\n"
            f"- total_uncertain_edges: {total_uncertain}\n"
            "- top_unresolved_calls:\n"
            f"{top_lines}\n"
            "- note: Additional unresolved edges omitted. Use flow JSON for full details."
        )

    source_section = ""
    if include_source:
        method_by_id = {m.id: m for m in graph.methods}
        budget = max_chars
        chunks = []
        wanted_ids = [method.id] + [
            n.get("method_id") for n in top_nodes if n.get("method_id")
        ]
        seen = set()
        for mid in wanted_ids:
            if mid in seen:
                continue
            seen.add(mid)
            m = method_by_id.get(mid)
            if not m:
                continue
            snippet = (m.source or "")[: max(0, budget)]
            if not snippet:
                continue
            block = (
                f"### {m.signature}\n"
                f"method_id: {m.id}\n"
                f"file: {m.file_path}:{m.start_line}-{m.end_line}\n"
                f"```java\n{snippet}\n```"
            )
            if len(block) > budget:
                break
            chunks.append(block)
            budget -= len(block)
            if budget <= 0:
                break
        source_section = "\n## Optional Source\n" + (
            "\n\n".join(chunks) if chunks else "- Source unavailable within budget."
        )

    return f"""# JIDRA Java Diagnostic Context
## Task
Analyze the selected Java method using graph-grounded flow references.
Do not invent missing call edges.
Use method_id/file_path/line ranges to decide what code to inspect next.
{target_instruction}

## Entry Method
{method.signature}

## Ranked Flow References
{refs_text}

{uncertain_section}
{source_section}

## Context Notes
- This context is derived from static analysis of a Java codebase.
- Some calls may represent infrastructure concerns such as logging, metrics, markers, observability, telemetry, or tracing.
- These are often not part of the core business logic, but may still influence behavior in some cases.
- Focus primarily on business-relevant method calls and data flow.
- Treat unresolved calls as uncertain; they may come from:
    - external libraries
    - framework abstractions
    - generated code
    - lambda/local variable chains
- Do not assume unresolved calls are incorrect, but prioritize resolved business calls for reasoning.

## Instructions
- Explain the likely execution flow.
- Identify the most important methods to inspect next.
- Distinguish verified graph facts from uncertainty.
- Do not assume code behavior that is not present in the flow references.
"""


def _call_llm(
    prompt: str,
    model: str | None,
    llm_profile: str | None,
    config_path: str | None,
    max_tokens: int | None,
) -> dict:
    from .llm.llm_client import JidraLLMClient

    client = JidraLLMClient.from_config(profile=llm_profile, config_path=config_path)
    if max_tokens is not None:
        client.max_tokens = max_tokens
    return client.generate_diagnosis(prompt, model=model)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jidra", description="JIDRA Java trace/context CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index", help="Build graph.db from a Java or TypeScript codebase"
    )
    index_parser.add_argument(
        "--codebase", required=True, help="Path to repository root"
    )
    index_parser.add_argument(
        "--output", required=True, help="Output graph.db file or output directory"
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Force full rebuild, bypassing the fingerprint cache",
    )
    index_parser.add_argument(
        "--ts-backend",
        choices=("auto", "treesitter", "tsmorph"),
        default="auto",
        help=(
            "TypeScript extraction backend. auto/treesitter: in-process "
            "tree-sitter (no Docker, ~65%% resolution). tsmorph: Docker ts-morph "
            "sidecar (higher resolution)."
        ),
    )

    trace_parser = subparsers.add_parser("trace", help="Trace a method call flow")
    trace_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    trace_parser.add_argument("--graph-type", choices=("main", "test"), default="main")
    trace_parser.add_argument("--method", required=True, help="Method selector")
    trace_parser.add_argument(
        "--max-depth", type=int, default=5, help="Traversal depth"
    )
    trace_parser.add_argument(
        "--business-only",
        action="store_true",
        help="Hide support/metrics/logging calls",
    )
    trace_parser.add_argument("--output", help="Output JSON file path or directory")

    context_parser = subparsers.add_parser("context", help="Build method context")
    context_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    context_parser.add_argument(
        "--graph-type", choices=("main", "test"), default="main"
    )
    context_parser.add_argument("--method", required=True, help="Method selector")
    context_parser.add_argument(
        "--max-chars", type=int, default=12000, help="Max context size"
    )
    context_parser.add_argument("--max-tokens", type=int)
    context_parser.add_argument(
        "--business-only",
        action="store_true",
        help="Hide support/metrics/logging calls",
    )
    context_parser.add_argument("--output", help="Output JSON file path or directory")

    route_parser = subparsers.add_parser(
        "trace-route", help="Trace flow from endpoint route"
    )
    route_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    route_parser.add_argument("--graph-type", choices=("main", "test"), default="main")
    route_parser.add_argument(
        "--route", required=True, help="Route path, e.g. /api/v1/users"
    )
    route_parser.add_argument(
        "--max-depth", type=int, default=5, help="Traversal depth"
    )
    route_parser.add_argument("--output", help="Output JSON file path or directory")

    flow_parser = subparsers.add_parser(
        "flow", help="Stitch recursive business flow from entry method"
    )
    flow_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    flow_parser.add_argument("--graph-type", choices=("main", "test"), default="main")
    flow_parser.add_argument("--method", required=True, help="Method selector")
    flow_parser.add_argument("--depth", type=int, default=4)
    flow_parser.add_argument(
        "--business-only", dest="business_only", action="store_true", default=True
    )
    flow_parser.add_argument(
        "--no-business-only", dest="business_only", action="store_false"
    )
    flow_parser.add_argument("--output", help="Output JSON file path or directory")

    prompt_parser = subparsers.add_parser(
        "prompt", help="Build prompt-ready method context text"
    )
    prompt_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    prompt_parser.add_argument("--graph-type", choices=("main", "test"), default="main")
    prompt_parser.add_argument("--method", required=True, help="Method selector")
    prompt_parser.add_argument(
        "--max-chars", type=int, default=12000, help="Max context size"
    )
    prompt_parser.add_argument("--max-tokens", type=int)
    prompt_parser.add_argument(
        "--business-only", dest="business_only", action="store_true", default=True
    )
    prompt_parser.add_argument(
        "--no-business-only", dest="business_only", action="store_false"
    )
    prompt_parser.add_argument(
        "--target", choices=("claude", "codex", "generic"), default="generic"
    )
    prompt_parser.add_argument("--use-flow", action="store_true")
    prompt_parser.add_argument("--top-n", type=int, default=6)
    prompt_parser.add_argument("--include-source", action="store_true")
    prompt_parser.add_argument("--verbose-flow", action="store_true")
    prompt_parser.add_argument("--debug-flow", action="store_true")
    prompt_parser.add_argument("--output", help="Output text file path or directory")

    diagnose_parser = subparsers.add_parser(
        "diagnose", help="Generate prompt and run LLM diagnosis"
    )
    diagnose_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    diagnose_parser.add_argument(
        "--graph-type", choices=("main", "test"), default="main"
    )
    diagnose_parser.add_argument("--method", required=True)
    diagnose_parser.add_argument(
        "--target", choices=("claude", "codex", "generic"), default="generic"
    )
    diagnose_parser.add_argument("--model")
    diagnose_parser.add_argument(
        "--max-chars", type=int, default=12000, help="Max context size"
    )
    diagnose_parser.add_argument("--max-tokens", type=int)
    diagnose_parser.add_argument(
        "--business-only", dest="business_only", action="store_true", default=True
    )
    diagnose_parser.add_argument(
        "--no-business-only", dest="business_only", action="store_false"
    )
    diagnose_parser.add_argument(
        "--use-flow", dest="use_flow", action="store_true", default=True
    )
    diagnose_parser.add_argument("--no-use-flow", dest="use_flow", action="store_false")
    diagnose_parser.add_argument("--top-n", type=int, default=6)
    diagnose_parser.add_argument("--include-source", action="store_true")
    diagnose_parser.add_argument("--verbose-flow", action="store_true")
    diagnose_parser.add_argument("--debug-flow", action="store_true")
    diagnose_parser.add_argument("--llm-profile", choices=("local", "enterprise"))
    diagnose_parser.add_argument("--config", help="Optional path to JIDRA config.yaml")
    diagnose_parser.add_argument("--quiet", action="store_true")
    diagnose_parser.add_argument("--show-prompt", action="store_true")
    diagnose_parser.add_argument("--output", help="Output JSON file path or directory")

    mcp_parser = subparsers.add_parser("mcp", help="Run JIDRA MCP server over stdio")
    mcp_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    mcp_parser.add_argument("--graph-type", choices=("main", "test"), default="main")
    mcp_parser.add_argument(
        "--codebase", help="Path to Java codebase (for reindex tool)"
    )

    reindex_parser = subparsers.add_parser(
        "reindex", help="Incrementally update graph.db after file changes"
    )
    reindex_parser.add_argument("--graph", help="Path to graph.db")
    reindex_parser.add_argument(
        "--codebase", help="Path to codebase root (defaults to graph's parent)"
    )
    reindex_parser.add_argument(
        "--changed-files",
        nargs="*",
        default=None,
        help="Hint: only these files changed (used by git hooks).",
    )

    hooks_parser = subparsers.add_parser(
        "hooks", help="Install/uninstall git hooks that auto-reindex the graph"
    )
    hooks_parser.add_argument(
        "action", choices=("install", "uninstall"), help="install or uninstall"
    )
    hooks_parser.add_argument(
        "--repo", default=None, help="Repository root (defaults to CWD)"
    )
    hooks_parser.add_argument("--graph", help="Path to graph.db the hooks reindex")

    flow_doc_parser = subparsers.add_parser(
        "flow-doc", help="Generate recursive deterministic flow markdown"
    )
    flow_doc_parser.add_argument(
        "--method", required=True, help="Method selector or method id"
    )
    flow_doc_parser.add_argument("--output", required=True, help="Output markdown path")
    flow_doc_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    flow_doc_parser.add_argument(
        "--graph-type", choices=("main", "test"), default="main"
    )
    flow_doc_parser.add_argument("--depth", type=int, default=4)
    flow_doc_parser.add_argument("--top-n", type=int, default=8)
    flow_doc_parser.add_argument("--max-subflows", type=int, default=8)
    flow_doc_parser.add_argument("--max-context-chars", type=int, default=12000)
    flow_doc_parser.add_argument("--include-utility", action="store_true")
    flow_doc_parser.add_argument("--show-agents", action="store_true")
    flow_doc_parser.add_argument(
        "--mind-map",
        action="store_true",
        help="Use recursive mind-map mode (depth + max-nodes)",
    )
    flow_doc_parser.add_argument(
        "--include-details",
        action="store_true",
        help="Append detailed expanded method sections",
    )
    flow_doc_parser.add_argument(
        "--max-nodes", type=int, default=200, help="Mind-map traversal node cap"
    )
    error_doc_parser = subparsers.add_parser(
        "error-doc",
        help="Generate deterministic stack-trace error investigation markdown",
    )
    error_doc_parser.add_argument(
        "--stack-trace", required=True, help="Path to stack trace text file"
    )
    error_doc_parser.add_argument(
        "--output", required=True, help="Output markdown path"
    )
    error_doc_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    error_doc_parser.add_argument(
        "--graph-type", choices=("main", "test"), default="main"
    )
    error_doc_parser.add_argument("--depth", type=int, default=6)
    error_doc_parser.add_argument("--max-nodes", type=int, default=200)
    error_doc_parser.add_argument("--include-utility", action="store_true")
    error_doc_parser.add_argument(
        "--mind-map",
        action="store_true",
        help="Compatibility flag; error-doc already renders focused mind-map",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate graph against running Spring Boot app actuator beans",
    )
    validate_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    validate_parser.add_argument(
        "--graph-type", choices=("main", "test"), default="main"
    )
    validate_parser.add_argument(
        "--codebase", help="Path to Java codebase root (for Docker build)"
    )
    validate_parser.add_argument(
        "--actuator-url",
        help="Spring Boot actuator base URL (e.g. http://localhost:8080). Skips Docker if provided.",
    )
    validate_parser.add_argument(
        "--port", type=int, default=8080, help="Host port for Docker container"
    )
    validate_parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Seconds to wait for actuator health check",
    )
    validate_parser.add_argument(
        "--output",
        help="Output directory/db for the validated variant (default: same db as --graph)",
    )
    validate_parser.add_argument(
        "--report",
        help="Write validation report JSON to this path (default: stdout)",
    )
    validate_parser.add_argument(
        "--no-filter",
        action="store_true",
        help="Annotate only, do not remove edges (debug mode)",
    )
    validate_parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip auto-building Java app (assume already built)",
    )
    validate_parser.add_argument(
        "--build-dir",
        help="Build directory for multi-module projects (relative to codebase root, e.g., search-api)",
    )

    graph_view_parser = subparsers.add_parser(
        "graph-view",
        help="Visualize call graph with interactive HTML",
    )
    graph_view_parser.add_argument(
        "--graph", help="Path to graph.db (overrides --graph-type default path)"
    )
    graph_view_parser.add_argument(
        "--graph-type", choices=("main", "test"), default="main"
    )
    graph_view_parser.add_argument(
        "--output", help="Output HTML path (default: graph.html)"
    )
    graph_view_parser.add_argument(
        "--method", help="Focus on method subgraph (optional)"
    )
    graph_view_parser.add_argument(
        "--depth", type=int, default=4, help="Traversal depth for focused view"
    )
    graph_view_parser.add_argument(
        "--package", help="Filter to package prefix (e.g., com.example.service)"
    )

    process_parser = subparsers.add_parser(
        "process",
        help="Complete end-to-end: index codebase → validate → generate visualization",
    )
    process_parser.add_argument(
        "--codebase", required=True, help="Path to codebase root"
    )
    process_parser.add_argument(
        "--actuator-url",
        help="Spring Boot actuator URL (e.g. http://localhost:8080). If omitted, uses Docker. Java only.",
    )
    process_parser.add_argument(
        "--port", type=int, default=8080, help="Docker container port (Java only)"
    )
    process_parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Actuator health check timeout (Java only)",
    )
    process_parser.add_argument(
        "--output", help="Output directory for all generated files"
    )
    process_parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip Java build (assume already built). Java only.",
    )
    process_parser.add_argument(
        "--build-dir", help="Build directory for multi-module projects (Java only)"
    )

    cost_roi_parser = subparsers.add_parser(
        "cost-roi", help="Measure token savings and LLM cost reduction from your graph"
    )
    cost_roi_parser.add_argument(
        "--graph",
        help="Path to graph.db, validated variant (defaults to jidra/output/graph.db)",
    )
    cost_roi_parser.add_argument(
        "--method",
        help="Class.method selector for a specific proof (e.g. SearchServiceController.search). "
        "If omitted, shows graph-wide averages.",
    )
    cost_roi_parser.add_argument(
        "--codebase",
        help="Path to Java repo root. Required for --offline false; "
        "used to read source files for the naive baseline.",
    )
    cost_roi_parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="LLM model to calculate costs for (default: claude-sonnet-4-6)",
    )
    cost_roi_parser.add_argument(
        "--queries",
        type=int,
        default=500,
        help="Estimated number of times Claude calls a JIDRA tool per year (default: 500, ~10/week)",
    )
    cost_roi_parser.add_argument(
        "--offline",
        default="true",
        choices=("true", "false"),
        help="true (default): measure tokens from graph, no API calls. "
        "false: make real Claude API calls for exact numbers (requires ANTHROPIC_API_KEY).",
    )
    cost_roi_parser.add_argument(
        "--output", help="Write JSON result to this file instead of printing"
    )

    graph_docs_parser = subparsers.add_parser(
        "graph-docs",
        help="Generate doc-to-code linkage graph HTML from indexed documents",
    )
    graph_docs_parser.add_argument("--graph", help="Path to graph.db")
    graph_docs_parser.add_argument(
        "--output", help="Output HTML path (default: <graph_dir>/doc_graph.html)"
    )

    index_docs_parser = subparsers.add_parser(
        "index-docs",
        help="Index documents (MD, PDF, DOCX, URL) into the doc store for LLM context",
    )
    index_docs_parser.add_argument(
        "--path",
        required=True,
        help="File path, directory, or URL to index",
    )
    index_docs_parser.add_argument(
        "--graph",
        help="Path to graph.db (defaults to jidra output dir)",
    )
    index_docs_parser.add_argument(
        "--extensions",
        nargs="*",
        default=[".md", ".mdx", ".txt", ".pdf"],
        help="File extensions when --path is a directory",
    )

    history_parser = subparsers.add_parser(
        "history",
        help="Show telemetry history (index + reindex events)",
    )
    history_parser.add_argument("--repo", help="Filter to a specific repo path")
    history_parser.add_argument(
        "--html",
        nargs="?",
        const="",
        help="Write HTML report (optional path, defaults to output/telemetry.html)",
    )
    history_parser.add_argument(
        "--limit", type=int, default=50, help="Max events to show"
    )

    subparsers.add_parser(
        "up",
        help="One-command setup: build graph, write MCP config, optionally watch for changes",
    )

    ui_parser = subparsers.add_parser("ui", help="Launch the JIDRA web UI")
    ui_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    ui_parser.add_argument(
        "--port", type=int, default=7474, help="Bind port (default: 7474)"
    )
    ui_parser.add_argument(
        "--reload", action="store_true", help="Enable uvicorn auto-reload"
    )

    return parser.parse_args()


def _resolve_single_method(graph, selector: str):
    candidates = _resolve_method_selector(graph, selector)
    if not candidates:
        raise SystemExit(_method_not_found_error(selector))
    if len(candidates) > 1:
        raise SystemExit(_method_ambiguous_error(selector, candidates))
    return candidates[0]


def _load_graph_by_type(graph_arg: str | None, graph_type: str):
    """Resolve the graph.db path and load the requested variant."""
    db_path = _resolve_graph_db_path(graph_arg)
    conn = graph_store.connect(db_path)
    graph = graph_store.load_graph(conn, variant=graph_type)
    return graph, db_path


def _load_cli_config(config_path: str | None = None) -> dict:
    path = (
        Path(config_path).resolve()
        if config_path
        else Path(__file__).resolve().parent / "config.yaml"
    )
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


_SOURCE_FILE_EXTENSIONS = (".java", ".py", ".ts", ".tsx", ".scala", ".go")


def _gather_source_files(codebase_path: Path) -> list[Path]:
    files: list[Path] = []
    for ext in _SOURCE_FILE_EXTENSIONS:
        files.extend(codebase_path.rglob(f"*{ext}"))
    return sorted(files)


def compute_graph_health(graph) -> dict:
    """Resolved/unresolved/external breakdown of callsites, by status and reason."""
    callsites = graph.callsites
    total = len(callsites)

    resolved = 0
    external = 0
    unresolved = 0
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}

    for c in callsites:
        status = c.resolution_status or "unresolved"
        by_status[status] = by_status.get(status, 0) + 1
        if c.resolution_reason:
            by_reason[c.resolution_reason] = by_reason.get(c.resolution_reason, 0) + 1

        if status == "external_library":
            external += 1
        elif status.startswith("resolved"):
            resolved += 1
        else:
            unresolved += 1

    def pct(n: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    return {
        "total_callsites": total,
        "resolved": resolved,
        "resolved_pct": pct(resolved),
        "unresolved": unresolved,
        "unresolved_pct": pct(unresolved),
        "external": external,
        "external_pct": pct(external),
        "by_status": by_status,
        "by_reason": by_reason,
    }


def _index(
    codebase: str,
    output: str,
    on_progress=None,
    _quiet: bool = False,
    force: bool = False,
    ts_backend: str = "auto",
    skip_folders: set[str] | None = None,
) -> None:
    from .utils.cache import (
        compute_file_manifest,
        compute_fingerprint,
        load_cache,
        save_cache,
    )
    from .models import Graph as _Graph

    codebase_path = Path(codebase).resolve()
    output_path = Path(output).resolve()
    db_path = graph_store.resolve_graph_db_path(output_path)
    conn = graph_store.connect(db_path)

    source_files = _gather_source_files(codebase_path)
    fp = compute_fingerprint(source_files)
    manifest = compute_file_manifest(source_files)

    cached = None if force else load_cache(output_path)

    main_count = conn.execute(
        "SELECT COUNT(*) FROM methods WHERE variant = 'main' AND module_id IS NULL"
    ).fetchone()[0]

    if cached and cached.get("fingerprint") == fp and main_count > 0:
        if not _quiet:
            print("Graph up to date, skipping rebuild.")
        return

    old_manifest = (cached or {}).get("manifest", {})
    changed_files: set[Path] | None = None
    previous_graph: _Graph | None = None

    if old_manifest and main_count > 0:
        changed_paths = {
            Path(p) for p, h in manifest.items() if old_manifest.get(p) != h
        }
        deleted_paths = {p for p in old_manifest if p not in manifest}

        if changed_paths or deleted_paths:
            main_graph = graph_store.load_graph(conn, variant="main", module_id=None)
            test_graph = graph_store.load_graph(conn, variant="test", module_id=None)
            previous_graph = _Graph(
                classes=main_graph.classes + test_graph.classes,
                methods=main_graph.methods + test_graph.methods,
                fields=main_graph.fields + test_graph.fields,
                callsites=main_graph.callsites + test_graph.callsites,
                inheritance_edges=main_graph.inheritance_edges
                + test_graph.inheritance_edges,
                resolved_call_edges=[],
            )
            if deleted_paths:
                previous_graph.classes = [
                    c
                    for c in previous_graph.classes
                    if c.file_path not in deleted_paths
                ]
                previous_graph.methods = [
                    m
                    for m in previous_graph.methods
                    if m.file_path not in deleted_paths
                ]
                previous_graph.fields = [
                    f for f in previous_graph.fields if f.file_path not in deleted_paths
                ]
                previous_graph.callsites = [
                    c
                    for c in previous_graph.callsites
                    if c.file_path not in deleted_paths
                ]
            changed_files = changed_paths

    # Detect smithy4j generated sources. On full index, run gradle to (re)generate
    # them. On incremental reindex, reuse whatever was already built — smithy
    # contracts rarely change so we don't rebuild.
    extra_java_roots: list[Path] = []
    from .smithy.smithy4j_builder import (
        _GENERATED_SUBPATH,
        build_smithy4j_sources,
        find_smithy4j_modules,
    )

    if changed_files is None:
        extra_java_roots = build_smithy4j_sources(codebase_path)
    else:
        # Incremental: include already-generated dirs if they exist on disk.
        for module_dir in find_smithy4j_modules(codebase_path):
            generated = module_dir / _GENERATED_SUBPATH
            if generated.exists():
                extra_java_roots.append(generated)

    graph = build_graph(
        codebase_path,
        on_progress=on_progress,
        changed_files=changed_files,
        previous_graph=previous_graph,
        ts_backend=ts_backend,
        extra_java_roots=extra_java_roots or None,
        skip_folders=skip_folders,
    )

    if changed_files is not None and previous_graph is not None and not _quiet:
        print(f"Re-parsed {len(changed_files)}/{len(source_files)} files")

    graph_store.save_full_graph(conn, graph)

    from .smithy.smithy_bridge import link_operations
    from .extractors.smithy_extractor import build_smithy_graph

    smithy_shapes, smithy_operations = build_smithy_graph(codebase_path)
    smithy_links = (
        link_operations(graph.classes, smithy_operations) if smithy_operations else []
    )
    graph_store.save_smithy_graph(conn, smithy_shapes, smithy_operations, smithy_links)
    if smithy_operations and not _quiet:
        print(
            f"Smithy: {len(smithy_operations)} operations, "
            f"{len(smithy_shapes)} shapes, {len(smithy_links)} handler links"
        )

    save_cache(output_path, {"fingerprint": fp, "manifest": manifest})

    health = compute_graph_health(graph)

    if not _quiet:
        main_records = sum(
            1
            for m in graph.methods
            if graph_store.infer_variant_split(m.file_path) == "main"
        )
        test_records = len(graph.methods) - main_records
        print(
            json.dumps(
                {
                    "graph_db": str(db_path),
                    "main_records": main_records,
                    "test_records": test_records,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        print(
            f"Graph health: {health['resolved_pct']}% resolved, "
            f"{health['unresolved_pct']}% unresolved, "
            f"{health['external_pct']}% external "
            f"({health['total_callsites']} callsites)"
        )


def _validate(
    graph_arg: str | None,
    graph_type: str,
    codebase: str | None,
    actuator_url: str | None,
    port: int,
    timeout: int,
    output: str | None,
    report: str | None,
    no_filter: bool,
    skip_build: bool,
    build_dir: str | None,
) -> None:
    db_path = _resolve_graph_db_path(graph_arg)
    conn = graph_store.connect(db_path)
    graph = graph_store.load_graph(conn, variant=graph_type)

    try:
        if actuator_url:
            beans_response = fetch_beans_from_url(actuator_url, timeout=timeout)
        elif codebase:
            with run_docker_and_fetch_beans(
                codebase,
                port=port,
                timeout=timeout,
                skip_build=skip_build,
                build_dir=build_dir,
            ) as beans_response:
                pass
        else:
            raise SystemExit("Either --actuator-url or --codebase is required")
    except ActuatorError as e:
        raise SystemExit(f"Actuator error: {e}") from e

    confirmed_beans = parse_actuator_beans(beans_response)
    filtered_graph, validation_report = validate_graph(
        graph, confirmed_beans, no_filter=no_filter
    )

    # Cache actuator response for future incremental reindex
    from .graph.graph_validator import save_actuator_cache

    graph_dir = Path(output).resolve() if output else db_path.parent
    save_actuator_cache(graph_dir, beans_response)

    # Determine destination db (defaults to the same db, "validated" variant)
    output_db_path = (
        graph_store.resolve_graph_db_path(Path(output).resolve()) if output else db_path
    )
    output_conn = (
        graph_store.connect(output_db_path) if output_db_path != db_path else conn
    )
    graph_store.save_full_graph(output_conn, filtered_graph, variant="validated")

    # Prepare report
    report_dict = {
        "total_classes": validation_report.total_classes,
        "confirmed_beans": validation_report.confirmed_beans,
        "unconfirmed_classes_sample": validation_report.unconfirmed_classes[:20],
        "edges_before": validation_report.edges_before,
        "edges_after": validation_report.edges_after,
        "edges_removed": validation_report.edges_removed,
        "callsites_upgraded": validation_report.callsites_upgraded,
        "removed_edges_sample": [
            {"caller": c, "callee": m} for c, m in validation_report.removed_edges[:20]
        ],
    }

    if report:
        report_path = Path(report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report_dict, indent=2, ensure_ascii=True))
        print(
            json.dumps(
                {"graph": str(output_db_path), "report": str(report_path)}, indent=2
            )
        )
    else:
        print(json.dumps(report_dict, indent=2))


def _process(
    codebase: str,
    actuator_url: str | None,
    port: int,
    timeout: int,
    output: str | None,
    skip_build: bool,
    build_dir: str | None,
    repo_root: str | None = None,
    use_docker: bool = False,
    skip_folders: set[str] | None = None,
) -> None:
    ui.banner("JIDRA Processing Pipeline")
    _pipeline_start = time.time()

    codebase_path = Path(codebase).resolve()
    output_dir = Path(output).resolve() if output else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    from .filters.ts_filters import detect_languages

    langs = detect_languages(codebase_path)
    if not langs:
        langs = ["java"]
    has_java = "java" in langs
    lang_label = " + ".join(lang.upper() for lang in langs)
    total_steps = 3 if has_java else 2

    # ===== STEP 1: INDEX (Build static call graph) =====
    ui.section(1, total_steps, f"Indexing codebase ({lang_label})")
    ui.info(f"Scanning {codebase_path}")

    db_path = graph_store.resolve_graph_db_path(output_dir)
    try:
        with ui.spinner("Parsing source...") as handle:

            def on_class_parsed(class_count):
                handle.update(f"Parsing source... {class_count} classes")

            _index(
                str(codebase_path),
                str(output_dir),
                on_progress=on_class_parsed,
                _quiet=True,
                skip_folders=skip_folders,
            )
        conn = graph_store.connect(db_path)
        graph = graph_store.load_graph(conn, variant="main")
        ui.success(
            f"Indexed {len(graph.classes)} classes, {len(graph.methods)} methods, "
            f"{len(graph.resolved_call_edges)} edges"
        )
    except Exception as e:
        raise SystemExit(f"Indexing failed: {e}")

    # Generate raw visualization immediately after static indexing (before actuator filtering).
    try:
        raw_graph_data = build_graph_data(graph, verbose=False)
        raw_html = render_interactive_html(raw_graph_data)
        raw_html_path = output_dir / "graph_visualization_raw.html"
        raw_html_path.write_text(raw_html, encoding="utf-8")
        ui.success(f"Raw visualization: {raw_html_path.name}")
    except Exception as _viz_err:
        ui.warn(f"Raw visualization skipped: {_viz_err}")

    # ===== STEP 2: VALIDATE (Java only — filter phantom edges with Spring Actuator) =====
    if not has_java:
        # No actuator for non-Java repos — the static graph is the final graph
        graph_store.save_full_graph(conn, graph, variant="validated")
        report_dict = {
            "total_classes": len(graph.classes),
            "edges_before": len(graph.resolved_call_edges),
            "edges_after": len(graph.resolved_call_edges),
            "edges_removed": 0,
            "edges_removed_pct": 0.0,
            "note": f"{lang_label} — actuator validation not applicable",
        }
        report_path = output_dir / "validation_report.json"
        report_path.write_text(json.dumps(report_dict, indent=2, ensure_ascii=True))
        filtered_graph = graph
    else:
        _actuator_label = (
            actuator_url
            if actuator_url
            else (
                "Docker (will auto-build)"
                if use_docker
                else "static analysis (best estimate)"
            )
        )
        ui.section(2, total_steps, "Validating with Spring Actuator")
        ui.info(f"Connecting to {_actuator_label}")

        try:
            with ui.spinner("Fetching actuator beans..."):
                if actuator_url:
                    beans_response = fetch_beans_from_url(actuator_url, timeout=timeout)
                    confirmed_beans = parse_actuator_beans(beans_response)
                elif use_docker:
                    docker_context = (
                        Path(repo_root).resolve() if repo_root else codebase_path
                    )
                    with run_docker_and_fetch_beans(
                        str(docker_context),
                        port=port,
                        timeout=timeout,
                        skip_build=skip_build,
                        build_dir=build_dir,
                    ) as beans_response:
                        confirmed_beans = parse_actuator_beans(beans_response)
                        from .graph.graph_validator import save_actuator_cache

                        save_actuator_cache(output_dir, beans_response)
                else:
                    from .graph.graph_validator import detect_beans_from_graph

                    confirmed_beans = detect_beans_from_graph(graph)
        except ActuatorError as e:
            raise SystemExit(f"Actuator error: {e}") from e

        with ui.spinner("Filtering phantom edges..."):
            filtered_graph, validation_report = validate_graph(
                graph, confirmed_beans, verbose=True
            )

            graph_store.save_full_graph(conn, filtered_graph, variant="validated")

        report_path = output_dir / "validation_report.json"
        report_dict = {
            "total_classes": validation_report.total_classes,
            "confirmed_beans": validation_report.confirmed_beans,
            "edges_before": validation_report.edges_before,
            "edges_after": validation_report.edges_after,
            "edges_removed": validation_report.edges_removed,
            "edges_removed_pct": round(
                100
                * validation_report.edges_removed
                / max(1, validation_report.edges_before),
                1,
            ),
            "callsites_upgraded": validation_report.callsites_upgraded,
        }
        report_path.write_text(json.dumps(report_dict, indent=2, ensure_ascii=True))
        ui.success(
            f"Removed {validation_report.edges_removed} phantom edges "
            f"({report_dict['edges_removed_pct']:.1f}%)"
        )

    # ===== FINAL STEP: VISUALIZE (Generate interactive HTML) =====
    viz_step = total_steps
    ui.section(viz_step, total_steps, "Generating interactive visualization")
    ui.info(f"Output: {output_dir}")

    with ui.spinner("Building graph visualization..."):
        graph_data = build_graph_data(filtered_graph, verbose=True)
        html = render_interactive_html(graph_data)

        html_path = output_dir / "graph_visualization.html"
        html_path.write_text(html, encoding="utf-8")
    ui.success(f"Generated visualization: {html_path.name}")

    # Doc graph (generated only if docs have been indexed)
    doc_graph_path = _write_doc_graph(output_dir, db_path)
    if doc_graph_path:
        ui.success(f"Doc graph: {doc_graph_path.name}")

    # ===== TELEMETRY =====
    try:
        from .llm.telemetry import record_index_event

        _pipeline_elapsed = int((time.time() - _pipeline_start) * 1000)
        record_index_event(str(codebase_path), langs, filtered_graph, _pipeline_elapsed)
    except Exception:
        pass

    # ===== SUMMARY =====
    edges_pct = 100 - report_dict.get("edges_removed_pct", 0.0)
    ui.kv_panel(
        "Pipeline complete",
        [
            (
                db_path.name,
                f"{len(graph.classes)} classes, {len(graph.methods)} methods",
            ),
            (
                "",
                f"{len(filtered_graph.resolved_call_edges)} edges ({edges_pct:.1f}% of original)",
            ),
            (report_path.name, "Validation metrics"),
            (html_path.name, "Interactive graph (Interactive | Graphviz | JSON)"),
            ("View graph", f"file://{html_path}"),
        ],
    )


def _prompt(
    prompt_text: str,
    default: str = "",
    allowed_values: list[str] | None = None,
    optional: bool = False,
) -> str:
    return ui.prompt(
        prompt_text, default=default, choices=allowed_values, optional=optional
    )


def _prompt_int(prompt_text: str, default: int) -> int:
    while True:
        response = input(f"{prompt_text} [{default}]: ").strip()
        if not response:
            return default
        try:
            return int(response)
        except ValueError:
            print("Please enter a valid integer.")
            continue


def _prompt_yn(prompt_text: str, default: bool = False) -> bool:
    return ui.prompt_yn(prompt_text, default=default)


_JIDRA_CLAUDE_MD_MARKER = "<!-- jidra-managed -->"


def _write_claude_md(repo: Path, langs: list[str]) -> None:
    """
    Inject JIDRA instructions into the repo's CLAUDE.md.
    - If no CLAUDE.md exists: create one.
    - If CLAUDE.md exists without our marker: append our section.
    - If CLAUDE.md already has our marker: replace just our section (idempotent).
    """
    lang_note = ""
    if len(langs) > 1:
        lang_note = (
            f"\nThis is a multi-language repo ({', '.join(langs)}). "
            "Each node in the graph has a `language` field. "
            "When a method name appears in multiple languages, check `language` to pick the right one."
        )

    jidra_section = f"""{_JIDRA_CLAUDE_MD_MARKER}
## JIDRA — Code Graph Tools (MANDATORY)

ALWAYS call a JIDRA tool first before reading any file, running grep, or using
glob — for any question about code structure, call flows, or method implementations.{lang_note}

- If a JIDRA tool returns suggestions, pick the best match and retry immediately.
- Only fall back to file reads if JIDRA explicitly returns no data.
<!-- /jidra-managed -->"""

    claude_md = repo / "CLAUDE.md"

    if not claude_md.exists():
        claude_md.write_text(jidra_section + "\n", encoding="utf-8")
        ui.success(f"Created CLAUDE.md in {repo}")
        return

    existing = claude_md.read_text(encoding="utf-8")

    if _JIDRA_CLAUDE_MD_MARKER in existing:
        # Replace our existing section
        import re

        updated = re.sub(
            r"<!-- jidra-managed -->.*?<!-- /jidra-managed -->",
            jidra_section,
            existing,
            flags=re.DOTALL,
        )
        claude_md.write_text(updated, encoding="utf-8")
        ui.success("Updated JIDRA section in existing CLAUDE.md")
    else:
        # Append our section without touching existing content
        claude_md.write_text(
            existing.rstrip() + "\n\n" + jidra_section + "\n",
            encoding="utf-8",
        )
        ui.success("Appended JIDRA section to existing CLAUDE.md")


def _up() -> None:
    ui.banner("JIDRA", "One-command setup — code graph + MCP server")

    repo_path = _prompt("Repository path")
    repo = Path(repo_path).resolve()
    if not repo.exists():
        raise SystemExit(f"Repository path does not exist: {repo}")

    build_sub_dir = _prompt("Build directory (relative to repo, or . for root)", ".")
    codebase_path = repo / build_sub_dir if build_sub_dir != "." else repo
    build_dir = build_sub_dir if build_sub_dir != "." else None

    skip_input = _prompt(
        "Folders to skip, comma-separated (optional, relative to repo)",
        "",
        optional=True,
    )
    skip_folders = {s.strip() for s in skip_input.split(",") if s.strip()} or None

    from .filters.ts_filters import detect_languages

    langs = detect_languages(repo)
    if not langs:
        langs = ["java"]
    has_java = "java" in langs
    has_typescript = "typescript" in langs
    has_python = "python" in langs
    has_go = "go" in langs

    display_langs = list(langs)
    if has_java:
        from .smithy.smithy4j_builder import find_smithy4j_modules

        if find_smithy4j_modules(repo):
            display_langs.append("smithy4j")

    ui.success(f"Detected languages: {', '.join(display_langs)}")

    if has_java:
        actuator_url = _prompt(
            "Spring Boot actuator URL (leave blank to choose validation method)",
            "",
            optional=True,
        )
        if not actuator_url:
            use_docker = _prompt_yn(
                "Run Docker to fetch live actuator beans? (N = best estimate via static analysis)",
                False,
            )
        else:
            use_docker = False
        skip_build = _prompt_yn("Skip Java build step (assume already built)?", False)
    else:
        actuator_url = None
        use_docker = False
        skip_build = False

    write_config = _prompt_yn("Write MCP config to <repo>/.mcp.json?", True)
    watch = _prompt_yn("Watch for file changes? (keeps jidra up running)", False)

    jidra_dir = _repo_output_dir(repo)
    if jidra_dir.exists():
        ui.info(f"Found existing JIDRA output for this repo at: {jidra_dir}")
        reuse = _prompt_yn(
            "Reuse existing database? (N = fresh rebuild in same dir)", True
        )
        if not reuse:
            db_path_existing = graph_store.resolve_graph_db_path(jidra_dir)
            if db_path_existing.exists():
                db_path_existing.unlink()
            ui.info("Existing database deleted — will do a full rebuild.")

    ui.section(1, 2, "Building graph")
    ui.info(f"Repository: {repo}")
    ui.info(f"Codebase path: {codebase_path}")

    try:
        _process(
            codebase=str(codebase_path),
            actuator_url=actuator_url or None,
            port=8080,
            timeout=180,
            output=str(jidra_dir),
            skip_build=skip_build,
            build_dir=build_dir,
            repo_root=str(repo),
            use_docker=use_docker,
            skip_folders=skip_folders,
        )
    except SystemExit as e:
        raise e
    except Exception as e:
        raise SystemExit(f"Graph build failed: {e}") from e

    # `_process()` always populates the "validated" variant in graph.db, regardless
    # of language (Java gets Spring Actuator filtering; other languages get an
    # unfiltered copy of the static graph).
    graph_validated_path = graph_store.resolve_graph_db_path(jidra_dir)
    if not graph_validated_path.exists():
        raise SystemExit(f"Graph build failed: {graph_validated_path} not created")

    # ── Doc indexing ──────────────────────────────────────────────────────────
    from .indexing import doc_store as _doc_store
    from .indexing.doc_indexer import extract_graph_names

    _doc_extensions = (".md", ".mdx", ".txt", ".pdf", ".docx")
    _doc_files = [
        f
        for f in repo.rglob("*")
        if f.is_file()
        and f.suffix.lower() in _doc_extensions
        and not any(
            p in f.parts
            for p in ("node_modules", ".git", "venv", "__pycache__", "dist", "build")
        )
    ]

    if _doc_files:
        ui.info(
            f"Found {len(_doc_files)} document(s) in repo ({', '.join(sorted({f.suffix.lower() for f in _doc_files}))})"
        )
        index_docs = _prompt_yn(
            "Index documentation files for spec/design context?", True
        )
        if index_docs:
            from rich import print as rprint
            from rich.live import Live
            from rich.table import Table

            _conn = graph_store.connect(graph_validated_path)
            _doc_store.migrate(_conn)
            _graph = graph_store.load_graph(_conn, variant="main")
            _class_names, _method_names = extract_graph_names(_graph)

            table = Table(
                show_header=True, header_style="bold #4d6173", box=None, padding=(0, 2)
            )
            table.add_column("File", style="#67e8f9", min_width=28, no_wrap=True)
            table.add_column("Type", style="#94a3b8", width=9)
            table.add_column("Chunks", style="#a78bfa", width=7, justify="right")
            table.add_column(
                "Linked Classes", style="#38bdf8", width=15, justify="right"
            )
            table.add_column("Elapsed", style="#f59e0b", width=9, justify="right")
            table.add_column("Status", width=7)

            from .indexing.doc_indexer import index_document
            from .llm.telemetry import record_doc_index_event

            with Live(table, refresh_per_second=8, vertical_overflow="visible"):
                for f in _doc_files:
                    size = f.stat().st_size
                    t0 = time.time()
                    err = None
                    n_chunks = 0
                    n_linked = 0
                    try:
                        n_chunks = index_document(
                            _conn, str(f), _class_names, _method_names
                        )
                        linked_set: set[str] = set()
                        for row in _conn.execute(
                            "SELECT linked_classes FROM doc_chunks WHERE source_path=?",
                            (str(f),),
                        ).fetchall():
                            linked_set.update(x for x in row[0].split(",") if x)
                        n_linked = len(linked_set)
                        status_str = "[green]✓[/green]"
                    except Exception as e:
                        err = str(e)
                        status_str = "[red]✗[/red]"
                    elapsed_ms = int((time.time() - t0) * 1000)
                    src_type = f.suffix.lstrip(".") or "file"
                    table.add_row(
                        f.name,
                        src_type,
                        str(n_chunks),
                        str(n_linked),
                        f"{elapsed_ms / 1000:.1f}s"
                        if elapsed_ms >= 1000
                        else f"{elapsed_ms}ms",
                        status_str,
                    )
                    record_doc_index_event(
                        str(f),
                        src_type,
                        n_chunks,
                        n_linked,
                        size,
                        elapsed_ms,
                        status="ok" if not err else "error",
                        error=err,
                    )

            sources = _doc_store.list_sources(_conn)
            total_chunks = sum(s["chunk_count"] for s in sources)
            rprint(
                f"[bold #38bdf8]Docs indexed:[/bold #38bdf8] {len(_doc_files)} files · {total_chunks} chunks"
            )
            doc_graph_out = _write_doc_graph(jidra_dir, graph_validated_path)
            if doc_graph_out:
                rprint(f"[dim]Doc graph:[/dim] file://{doc_graph_out}")

    ui.section(2, 2, "MCP configuration")

    settings_path = repo / ".mcp.json"
    _pkg_dir = Path(__file__).resolve().parent.parent
    _venv_python = _pkg_dir / "venv" / "bin" / "python"
    _python = str(_venv_python) if _venv_python.exists() else sys.executable

    mcp_entry = {
        "type": "stdio",
        "command": _python,
        "args": [
            "-m",
            "jidra.mcp_server",
            "--mode",
            "proxy",
            "--graph",
            str(graph_validated_path),
            "--codebase",
            str(codebase_path),
        ],
        "alwaysAllow": [
            "jidra_get_method_context",
            "jidra_get_method_source",
            "jidra_find_callers",
            "jidra_get_flow",
            "jidra_get_agent_flow",
            "jidra_get_call_chain",
            "jidra_search",
            "jidra_explore",
            "jidra_get_file_dependents",
            "jidra_get_file_dependencies",
            "jidra_get_endpoints",
            "jidra_get_components",
            "jidra_get_framework_summary",
            "jidra_analyze_stack_trace",
            "jidra_check_staleness",
            "jidra_reindex",
            "jidra_get_docs",
            "jidra_index_docs",
        ],
    }

    manual_mcp_lines: list[str] = []
    if write_config:
        settings = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                settings = {}
        if not isinstance(settings, dict):
            settings = {}
        settings.setdefault("mcpServers", {})["jidra"] = mcp_entry
        settings_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=True), encoding="utf-8"
        )
        ui.success(f"MCP config written to: {settings_path}")
    else:
        system_prompt = (
            "Use JIDRA for code context when available. "
            "Fall back to built-in tools only if it fails."
        )
        _srv = (
            f"{_python} -m jidra.mcp_server --mode proxy \\\n    "
            f"--graph {graph_validated_path} \\\n    "
            f"--codebase {codebase_path}"
        )
        claude_cmd = f"claude mcp add --scope local jidra -- \\\n    {_srv}"
        codex_cmd = f"codex mcp add --scope local jidra -- \\\n    {_srv}"
        claude_rm_cmd = "claude mcp remove --scope local jidra"
        codex_rm_cmd = "codex mcp remove --scope local jidra"
        manual_mcp_lines = [
            "No file written to the repo. Run one of these to register the MCP server:",
            "",
            "  Claude Code:",
            f"    {claude_cmd}",
            "",
            f'  claude --system-prompt "{system_prompt}"',
            "",
            "  Codex:",
            f"    {codex_cmd}",
            "",
            f'  codex --system "{system_prompt}"',
            "",
            "  To remove later:",
            f"    {claude_rm_cmd}",
            f"    {codex_rm_cmd}",
            "",
        ]

    ready_rows = [
        ("Graph", str(graph_validated_path)),
        ("Config", str(settings_path)),
        ("Repo", f"Open Claude Code in {repo}"),
    ]

    if watch:
        ext_map = []
        if has_java:
            ext_map += [".java"]
        if has_python:
            ext_map += [".py"]
        if has_typescript:
            ext_map += [".ts", ".tsx", ".js", ".jsx"]
        if has_go:
            ext_map += [".go"]
        watch_ext = tuple(ext_map)
        watch_ext_str = " / ".join(f"*{e}" for e in watch_ext)
        ready_rows.append(
            (
                "Watching",
                f"{codebase_path}/**/{watch_ext_str} (full re-index on each change)",
            )
        )
        ui.kv_panel("JIDRA is ready", ready_rows)
        _print_manual_mcp(manual_mcp_lines)
        ui.info("Press Ctrl+C to stop.")

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            rebuild_in_progress = threading.Event()
            _viz_timer: threading.Timer | None = None
            _viz_timer_lock = threading.Lock()
            VIZ_IDLE_SECS = 300

            db_path = graph_store.resolve_graph_db_path(jidra_dir)

            def _regenerate_viz():
                try:
                    conn = graph_store.connect(db_path)
                    graph = graph_store.load_graph(conn, variant="reindex")
                    graph_data = build_graph_data(graph, verbose=True)
                    html = render_interactive_html(graph_data)
                    html_path = jidra_dir / "graph_visualization.html"
                    html_path.write_text(html, encoding="utf-8")
                    ui.success(f"Visualization updated: {html_path.name}")
                    doc_graph_path = _write_doc_graph(jidra_dir, db_path)
                    if doc_graph_path:
                        ui.success(f"Doc graph updated: {doc_graph_path.name}")
                except Exception as e:
                    ui.error(f"Visualization failed: {e}")

            def _schedule_viz():
                nonlocal _viz_timer
                with _viz_timer_lock:
                    if _viz_timer is not None:
                        _viz_timer.cancel()
                    _viz_timer = threading.Timer(VIZ_IDLE_SECS, _regenerate_viz)
                    _viz_timer.daemon = True
                    _viz_timer.start()

            class SourceFileHandler(FileSystemEventHandler):
                def on_modified(self, event):
                    if rebuild_in_progress.is_set() or event.is_directory:
                        return
                    if not any(event.src_path.endswith(ext) for ext in watch_ext):
                        return

                    rebuild_in_progress.set()
                    src_path = (
                        event.src_path.decode()
                        if isinstance(event.src_path, bytes)
                        else event.src_path
                    )
                    file_name = Path(src_path).name
                    ui.rule(f"Detected change: {file_name}")
                    try:
                        from .engine.reindexer import incremental_reindex
                        from .llm.telemetry import record_reindex_event

                        _t0 = time.time()
                        try:
                            summary = incremental_reindex(
                                codebase_path,
                                db_path,
                                hint_changed_files=[src_path],
                            )
                            _elapsed = int((time.time() - _t0) * 1000)
                            change_type = summary.get("change_type", "?")
                            added = summary.get("added_methods", 0)
                            removed = summary.get("removed_methods", 0)
                            ui.success(
                                f"Graph updated ({change_type}): +{added}/-{removed} methods"
                                + (
                                    f" [viz in {VIZ_IDLE_SECS}s]"
                                    if change_type != "no_change"
                                    else ""
                                )
                            )
                            record_reindex_event(
                                str(codebase_path),
                                src_path,
                                change_type,
                                summary,
                                _elapsed,
                            )
                            if change_type != "no_change":
                                _schedule_viz()
                        except Exception as reindex_err:
                            ui.warn(
                                f"Incremental reindex failed ({reindex_err}), falling back to full rebuild..."
                            )
                            _t0 = time.time()
                            _process(
                                codebase=str(codebase_path),
                                actuator_url=actuator_url or None,
                                port=8080,
                                timeout=180,
                                output=str(jidra_dir),
                                skip_build=skip_build,
                                build_dir=build_dir,
                                repo_root=str(repo),
                                use_docker=use_docker,
                            )
                            _elapsed = int((time.time() - _t0) * 1000)
                            record_reindex_event(
                                str(codebase_path),
                                src_path,
                                "full_rebuild",
                                {},
                                _elapsed,
                            )
                            ui.success("Full rebuild complete.")
                    except Exception as e:
                        ui.error(f"Rebuild failed: {e}")
                    finally:
                        rebuild_in_progress.clear()

            observer = Observer()
            observer.schedule(SourceFileHandler(), str(codebase_path), recursive=True)
            observer.start()

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                observer.stop()
                observer.join()
                ui.success("Done.")
        except ImportError:
            raise SystemExit(
                "watchdog is required for --watch mode but is not installed"
            )
        except Exception as e:
            raise SystemExit(f"Watch mode failed: {e}") from e
    else:
        ui.kv_panel("JIDRA is ready", ready_rows)
        _print_manual_mcp(manual_mcp_lines)

    if write_config:
        _write_claude_md(repo, langs)


def _write_doc_graph(output_dir: Path, db_path: Path) -> Path | None:
    """Generate doc_graph.html alongside the code graph. Returns path or None if no docs."""
    try:
        from .indexing import doc_store
        from .indexing.doc_graph_visualizer import (
            build_doc_graph_data,
            render_doc_graph_html,
        )

        conn = graph_store.connect(db_path)
        doc_store.migrate(conn)
        if not doc_store.list_sources(conn):
            return None
        graph = graph_store.load_graph(conn, variant="main")
        data = build_doc_graph_data(conn, graph)
        html = render_doc_graph_html(data)
        out = output_dir / "doc_graph.html"
        out.write_text(html, encoding="utf-8")
        return out
    except Exception:
        return None


def _render_history_html(
    index_rows: list[dict], reindex_rows: list[dict], doc_rows: list[dict] | None = None
) -> str:
    import datetime

    def _fmt_ts(ts_ms: int) -> str:
        return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def _fmt_elapsed(ms: int) -> str:
        if ms >= 60000:
            return f"{ms / 60000:.1f}m"
        if ms >= 1000:
            return f"{ms / 1000:.1f}s"
        return f"{ms}ms"

    doc_rows = doc_rows or []

    # Summary stats
    total_classes = index_rows[0]["classes"] if index_rows else 0
    total_methods = index_rows[0]["methods"] if index_rows else 0
    total_lines = index_rows[0]["lines"] if index_rows else 0
    avg_index_ms = (
        int(sum(r["elapsed_ms"] for r in index_rows) / len(index_rows))
        if index_rows
        else 0
    )
    total_reindex = len(reindex_rows)
    total_docs = sum(1 for r in doc_rows if r["status"] == "ok")
    total_doc_chunks = sum(r["chunks"] for r in doc_rows if r["status"] == "ok")
    change_type_counts: dict[str, int] = {}
    for r in reindex_rows:
        ct = r["change_type"]
        change_type_counts[ct] = change_type_counts.get(ct, 0) + 1

    def _stat_cards() -> str:
        cards = [
            ("Classes", f"{total_classes:,}", "#38bdf8", "⬡"),
            ("Methods", f"{total_methods:,}", "#a78bfa", "ƒ"),
            ("Lines of Code", f"{total_lines:,}", "#34d399", "≡"),
            ("Avg Index Time", _fmt_elapsed(avg_index_ms), "#f59e0b", "⏱"),
            ("Reindex Events", f"{total_reindex:,}", "#fb7185", "↻"),
            ("Docs Indexed", f"{total_docs:,}", "#67e8f9", "📄"),
            ("Doc Chunks", f"{total_doc_chunks:,}", "#a78bfa", "⊞"),
        ]
        parts = []
        for label, value, color, icon in cards:
            parts.append(f"""
            <div class="stat-card">
              <div class="stat-icon" style="color:{color}">{icon}</div>
              <div class="stat-value" style="color:{color}">{value}</div>
              <div class="stat-label">{label}</div>
            </div>""")
        return "".join(parts)

    def _change_type_badge(ct: str) -> str:
        colors = {
            "no_change": ("#1e293b", "#64748b"),
            "metadata_only": ("#1e3a5f", "#38bdf8"),
            "callsite_change": ("#3d2e00", "#f59e0b"),
            "structural": ("#3d0e15", "#fb7185"),
            "full_rebuild": ("#1a1a2e", "#a78bfa"),
        }
        bg, fg = colors.get(ct, ("#1e293b", "#94a3b8"))
        return f"<span class='badge' style='background:{bg};color:{fg}'>{ct}</span>"

    def _lang_badge(lang: str) -> str:
        colors = {
            "java": "#f59e0b",
            "python": "#34d399",
            "typescript": "#38bdf8",
            "scala": "#fb7185",
            "go": "#67e8f9",
        }
        color = colors.get(lang, "#94a3b8")
        return f"<span class='badge' style='background:#1e293b;color:{color};border:1px solid {color}33'>{lang}</span>"

    def _index_table_rows() -> str:
        if not index_rows:
            return "<tr><td colspan='7' class='empty-row'>No index events recorded yet</td></tr>"
        rows = []
        for i, r in enumerate(index_rows):
            repo_short = Path(r["repo"]).name
            langs_html = " ".join(
                _lang_badge(lang) for lang in r["languages"].split(",") if lang
            )
            cls = "row-alt" if i % 2 else ""
            rows.append(
                f"<tr class='{cls}'>"
                f"<td class='ts'>{_fmt_ts(r['ts'])}</td>"
                f"<td><span class='repo-name' title='{r['repo']}'>{repo_short}</span></td>"
                f"<td>{langs_html}</td>"
                f"<td class='num'>{r['classes']:,}</td>"
                f"<td class='num'>{r['methods']:,}</td>"
                f"<td class='num'>{r['lines']:,}</td>"
                f"<td class='num elapsed'>{_fmt_elapsed(r['elapsed_ms'])}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    def _reindex_table_rows() -> str:
        if not reindex_rows:
            return "<tr><td colspan='8' class='empty-row'>No reindex events recorded yet</td></tr>"
        rows = []
        for i, r in enumerate(reindex_rows):
            repo_short = Path(r["repo"]).name
            file_short = Path(r["changed_file"]).name
            m_added = r["methods_added"]
            m_del = r["methods_deleted"]
            l_added = r["lines_added"]
            l_del = r["lines_deleted"]
            methods_html = f"<span class='delta-pos'>+{m_added}</span> / <span class='delta-neg'>-{m_del}</span>"
            lines_html = f"<span class='delta-pos'>+{l_added}</span> / <span class='delta-neg'>-{l_del}</span>"
            cls = "row-alt" if i % 2 else ""
            rows.append(
                f"<tr class='{cls}'>"
                f"<td class='ts'>{_fmt_ts(r['ts'])}</td>"
                f"<td><span class='repo-name' title='{r['repo']}'>{repo_short}</span></td>"
                f"<td><span class='file-name' title='{r['changed_file']}'>{file_short}</span></td>"
                f"<td>{_lang_badge(r['language'] or '')}</td>"
                f"<td>{_change_type_badge(r['change_type'])}</td>"
                f"<td class='num'>{methods_html}</td>"
                f"<td class='num'>{lines_html}</td>"
                f"<td class='num elapsed'>{_fmt_elapsed(r['elapsed_ms'])}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    def _doc_table_rows() -> str:
        if not doc_rows:
            return "<tr><td colspan='7' class='empty-row'>No documents indexed yet — run `jidra index-docs --path ./specs/`</td></tr>"
        rows = []
        for i, r in enumerate(doc_rows):
            file_short = Path(r["source_path"]).name
            size_kb = r["file_size_bytes"] / 1024
            size_str = (
                f"{size_kb:.1f} KB" if size_kb >= 1 else f"{r['file_size_bytes']} B"
            )
            status_html = (
                "<span class='badge' style='background:#14291a;color:#34d399'>ok</span>"
                if r["status"] == "ok"
                else "<span class='badge' style='background:#3d0e15;color:#fb7185' title='"
                + (r.get("error") or "")
                + "'>"
                + r["status"]
                + "</span>"
            )
            cls = "row-alt" if i % 2 else ""
            rows.append(
                f"<tr class='{cls}'>"
                f"<td class='ts'>{_fmt_ts(r['ts'])}</td>"
                f"<td><span class='file-name' title='{r['source_path']}'>{file_short}</span></td>"
                f"<td>{_lang_badge(r['source_type'])}</td>"
                f"<td class='num'>{r['chunks']}</td>"
                f"<td class='num'>{r['linked_classes']}</td>"
                f"<td class='num'>{size_str}</td>"
                f"<td class='num elapsed'>{_fmt_elapsed(r['elapsed_ms'])}</td>"
                f"<td>{status_html}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    # Chart data
    idx_rev = list(reversed(index_rows))
    chart_labels = json.dumps([_fmt_ts(r["ts"]) for r in idx_rev])
    chart_classes = json.dumps([r["classes"] for r in idx_rev])
    chart_methods = json.dumps([r["methods"] for r in idx_rev])
    chart_elapsed = json.dumps([r["elapsed_ms"] for r in idx_rev])

    reindex_rev = list(reversed(reindex_rows[-50:]))
    ri_labels = json.dumps(
        [Path(r["changed_file"]).name + " " + _fmt_ts(r["ts"]) for r in reindex_rev]
    )
    ri_elapsed = json.dumps([r["elapsed_ms"] for r in reindex_rev])
    ct_labels = json.dumps(list(change_type_counts.keys()))
    ct_values = json.dumps(list(change_type_counts.values()))

    doc_rev = list(reversed(doc_rows[-30:]))
    doc_chart_labels = json.dumps([Path(r["source_path"]).name for r in doc_rev])
    doc_chart_chunks = json.dumps([r["chunks"] for r in doc_rev])
    doc_chart_linked = json.dumps([r["linked_classes"] for r in doc_rev])
    doc_chart_elapsed = json.dumps([r["elapsed_ms"] for r in doc_rev])
    ct_colors = json.dumps(
        ["#64748b", "#38bdf8", "#f59e0b", "#fb7185", "#a78bfa"][
            : len(change_type_counts)
        ]
    )

    import datetime as _dt

    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>JIDRA Telemetry</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg:        #080d14;
    --surface:   #0f1923;
    --surface2:  #162032;
    --border:    #1e2d3d;
    --text:      #cdd9e5;
    --muted:     #4d6173;
    --accent:    #38bdf8;
    --purple:    #a78bfa;
    --green:     #34d399;
    --amber:     #f59e0b;
    --red:       #fb7185;
    --cyan:      #67e8f9;
  }}
  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 0;
  }}

  /* ── Header ── */
  .header {{
    background: linear-gradient(135deg, #0d1f35 0%, #080d14 60%);
    border-bottom: 1px solid var(--border);
    padding: 28px 40px 24px;
    display: flex;
    align-items: flex-end;
    gap: 24px;
  }}
  .header-title {{ flex: 1; }}
  .header h1 {{
    font-size: 1.75rem;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .header h1 .logo {{ font-size: 1.4rem; }}
  .header .sub {{
    color: var(--muted);
    font-size: 0.82rem;
    margin-top: 4px;
    letter-spacing: 0.02em;
  }}
  .header .generated {{
    color: var(--muted);
    font-size: 0.75rem;
    text-align: right;
    line-height: 1.6;
  }}

  /* ── Layout ── */
  .main {{ padding: 32px 40px; max-width: 1600px; margin: 0 auto; }}

  /* ── Stat cards ── */
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 16px;
    margin-bottom: 32px;
  }}
  .stat-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
    position: relative;
    overflow: hidden;
    transition: border-color .2s;
  }}
  .stat-card:hover {{ border-color: #2d4a63; }}
  .stat-card::before {{
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, currentColor 0%, transparent 60%);
    opacity: .03;
  }}
  .stat-icon {{
    font-size: 1.1rem;
    margin-bottom: 10px;
    opacity: .8;
  }}
  .stat-value {{
    font-size: 1.65rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1;
    margin-bottom: 6px;
  }}
  .stat-label {{
    font-size: 0.72rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: .07em;
    font-weight: 500;
  }}

  /* ── Charts ── */
  .charts-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 20px;
    margin-bottom: 32px;
  }}
  .charts-row2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 32px;
  }}
  .chart-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
  }}
  .chart-card h3 {{
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--muted);
    font-weight: 600;
    margin-bottom: 16px;
  }}
  .chart-card canvas {{ max-height: 200px; }}

  /* ── Section headers ── */
  .section-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    margin-top: 8px;
  }}
  .section-header h2 {{
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--muted);
    font-weight: 600;
  }}
  .section-header .count {{
    background: var(--surface2);
    color: var(--muted);
    font-size: 0.7rem;
    padding: 2px 8px;
    border-radius: 20px;
    border: 1px solid var(--border);
  }}

  /* ── Tables ── */
  .table-wrap {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 28px;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  thead th {{
    background: var(--surface2);
    color: var(--muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: .07em;
    font-weight: 600;
    padding: 11px 16px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}
  td {{
    padding: 10px 16px;
    border-bottom: 1px solid #0f1923;
    vertical-align: middle;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr.row-alt td {{ background: #0b1520; }}
  tr:hover td {{ background: #152030 !important; }}
  .ts {{ color: var(--muted); font-size: 0.78rem; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  .num {{ font-variant-numeric: tabular-nums; text-align: right; }}
  .elapsed {{ color: var(--amber); font-weight: 500; }}
  .repo-name {{
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--text);
    white-space: nowrap;
  }}
  .file-name {{ color: var(--cyan); font-size: 0.8rem; font-family: 'SF Mono', 'Fira Code', monospace; }}
  .badge {{
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 20px;
    white-space: nowrap;
    letter-spacing: .02em;
  }}
  .delta-pos {{ color: var(--green); font-weight: 600; }}
  .delta-neg {{ color: var(--red); font-weight: 600; }}
  .empty-row {{ text-align: center; color: var(--muted); padding: 32px; font-size: 0.85rem; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-title">
    <h1><span class="logo">◈</span> JIDRA Telemetry</h1>
    <p class="sub">Index &amp; reindex history across all repositories</p>
  </div>
  <div class="generated">Generated<br>{generated_at}</div>
</div>

<div class="main">

  <!-- Stat Cards -->
  <div class="stats-grid">
    {_stat_cards()}
  </div>

  <!-- Charts row 1: growth + change type donut -->
  <div class="charts-grid">
    <div class="chart-card">
      <h3>Classes &amp; Methods — Index History</h3>
      <canvas id="chartGrowth"></canvas>
    </div>
    <div class="chart-card">
      <h3>Reindex Change Types</h3>
      <canvas id="chartDoughnut"></canvas>
    </div>
  </div>

  <!-- Charts row 2: elapsed times -->
  <div class="charts-row2">
    <div class="chart-card">
      <h3>Full Index Elapsed Time</h3>
      <canvas id="chartElapsed"></canvas>
    </div>
    <div class="chart-card">
      <h3>Reindex Elapsed Time (last 50)</h3>
      <canvas id="chartReindexElapsed"></canvas>
    </div>
  </div>

  <!-- Index Events Table -->
  <div class="section-header">
    <h2>Full Index Events</h2>
    <span class="count">{len(index_rows)}</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>Time</th><th>Repo</th><th>Languages</th><th>Classes</th><th>Methods</th><th>Lines</th><th>Elapsed</th></tr>
      </thead>
      <tbody>{_index_table_rows()}</tbody>
    </table>
  </div>

  <!-- Reindex Events Table -->
  <div class="section-header">
    <h2>Incremental Reindex Events</h2>
    <span class="count">{len(reindex_rows)}</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>Time</th><th>Repo</th><th>File</th><th>Lang</th><th>Change Type</th><th>Methods +/−</th><th>Lines +/−</th><th>Elapsed</th></tr>
      </thead>
      <tbody>{_reindex_table_rows()}</tbody>
    </table>
  </div>

  <!-- Doc Index Charts -->
  <div class="charts-row2" style="margin-top:8px">
    <div class="chart-card">
      <h3>Doc Chunks &amp; Linked Classes per File</h3>
      <canvas id="chartDocChunks"></canvas>
    </div>
    <div class="chart-card">
      <h3>Doc Indexing Elapsed Time (ms)</h3>
      <canvas id="chartDocElapsed"></canvas>
    </div>
  </div>

  <!-- Doc Index Events Table -->
  <div class="section-header">
    <h2>Doc Index Events</h2>
    <span class="count">{len(doc_rows)}</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>Time</th><th>File</th><th>Type</th><th>Chunks</th><th>Linked Classes</th><th>Size</th><th>Elapsed</th><th>Status</th></tr>
      </thead>
      <tbody>{_doc_table_rows()}</tbody>
    </table>
  </div>

</div><!-- /main -->

<script>
const GRID = '#1a2a3a';
const TICK = '#4d6173';
const baseOpts = {{
  responsive: true,
  plugins: {{ legend: {{ labels: {{ color: TICK, boxWidth: 12, font: {{ size: 11 }} }} }} }},
  scales: {{
    x: {{ ticks: {{ color: TICK, maxTicksLimit: 6, font: {{ size: 10 }} }}, grid: {{ color: GRID }} }},
    y: {{ ticks: {{ color: TICK, font: {{ size: 10 }} }}, grid: {{ color: GRID }} }},
  }},
}};
const noScales = {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: TICK, font: {{ size: 11 }} }} }} }} }};

// Growth chart
new Chart('chartGrowth', {{ type: 'line', data: {{
  labels: {chart_labels},
  datasets: [
    {{ label: 'Classes', data: {chart_classes}, borderColor: '#38bdf8', backgroundColor: '#38bdf81a', fill: true, tension: 0.4, pointRadius: 4, pointHoverRadius: 6 }},
    {{ label: 'Methods', data: {chart_methods}, borderColor: '#a78bfa', backgroundColor: '#a78bfa1a', fill: true, tension: 0.4, pointRadius: 4, pointHoverRadius: 6 }},
  ]
}}, options: baseOpts }});

// Elapsed index
new Chart('chartElapsed', {{ type: 'bar', data: {{
  labels: {chart_labels},
  datasets: [{{ label: 'Elapsed (ms)', data: {chart_elapsed}, backgroundColor: '#f59e0b33', borderColor: '#f59e0b', borderWidth: 1, borderRadius: 4 }}]
}}, options: baseOpts }});

// Reindex elapsed
new Chart('chartReindexElapsed', {{ type: 'bar', data: {{
  labels: {ri_labels},
  datasets: [{{ label: 'Elapsed (ms)', data: {ri_elapsed}, backgroundColor: '#34d39933', borderColor: '#34d399', borderWidth: 1, borderRadius: 4 }}]
}}, options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, x: {{ ...baseOpts.scales.x, ticks: {{ ...baseOpts.scales.x.ticks, maxRotation: 45 }} }} }} }} }});

// Change type doughnut
new Chart('chartDoughnut', {{ type: 'doughnut', data: {{
  labels: {ct_labels},
  datasets: [{{ data: {ct_values}, backgroundColor: {ct_colors}, borderWidth: 0, hoverOffset: 6 }}]
}}, options: {{ ...noScales, cutout: '65%' }} }});

// Doc chunks + linked classes
new Chart('chartDocChunks', {{ type: 'bar', data: {{
  labels: {doc_chart_labels},
  datasets: [
    {{ label: 'Chunks', data: {doc_chart_chunks}, backgroundColor: '#67e8f933', borderColor: '#67e8f9', borderWidth: 1, borderRadius: 4 }},
    {{ label: 'Linked Classes', data: {doc_chart_linked}, backgroundColor: '#a78bfa33', borderColor: '#a78bfa', borderWidth: 1, borderRadius: 4 }},
  ]
}}, options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, x: {{ ...baseOpts.scales.x, ticks: {{ ...baseOpts.scales.x.ticks, maxRotation: 45 }} }} }} }} }});

// Doc elapsed
new Chart('chartDocElapsed', {{ type: 'bar', data: {{
  labels: {doc_chart_labels},
  datasets: [{{ label: 'Elapsed (ms)', data: {doc_chart_elapsed}, backgroundColor: '#fb718533', borderColor: '#fb7185', borderWidth: 1, borderRadius: 4 }}]
}}, options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, x: {{ ...baseOpts.scales.x, ticks: {{ ...baseOpts.scales.x.ticks, maxRotation: 45 }} }} }} }} }});
</script>
</body>
</html>"""


def _print_manual_mcp(lines: list[str]) -> None:
    if not lines:
        return
    if not ui.RICH:
        for line in lines:
            print(f"   {line}" if line else "")
        return
    for line in lines:
        ui.console.print(f"  [dim]{line}[/dim]" if line else "")


def _cost_roi(
    graph_arg: str | None,
    method: str | None,
    codebase: str | None,
    model: str,
    queries: int,
    offline: bool,
    output: str | None,
) -> None:
    from .llm.cost_calculator import (
        CostCalculator,
        analyze_graph,
        analyze_method_offline,
        analyze_method_online,
        format_method_proof,
        format_metrics,
        format_stats,
    )

    graph_path = _resolve_graph_db_path(graph_arg)
    if not graph_path.exists():
        raise SystemExit(
            f"Graph not found: {graph_path}\n"
            "Run `jidra process` first to build graph.db"
        )

    codebase_path = Path(codebase).resolve() if codebase else None

    # --- Method-specific proof ---
    if method:
        if not offline and not codebase_path:
            raise SystemExit(
                "--codebase is required for --offline false\n"
                "Provide the path to the Java repo root so JIDRA can read source files."
            )
        try:
            if offline:
                proof = analyze_method_offline(
                    graph_path, method, model, queries, codebase_path
                )
            else:
                proof = analyze_method_online(
                    graph_path, method, model, queries, codebase_path
                )
        except (ValueError, RuntimeError) as e:
            raise SystemExit(str(e))

        if output:
            import dataclasses

            _write_or_print_json(
                dataclasses.asdict(proof), output, "cost_roi_method.json"
            )
        else:
            print(format_method_proof(proof))
        return

    # --- Graph-wide averages (no method specified) ---
    stats = analyze_graph(graph_path)
    calc = CostCalculator()
    try:
        roi = calc.calculate_roi(model=model, stats=stats, num_queries_per_year=queries)
    except ValueError as e:
        raise SystemExit(str(e))

    if output:
        import dataclasses

        result = {
            "graph": str(graph_path),
            "model": model,
            "num_queries": queries,
            "graph_stats": dataclasses.asdict(stats),
            "cost_without_jidra": roi.cost_without_jidra,
            "cost_with_jidra": roi.cost_with_jidra,
            "annual_savings": roi.annual_savings,
        }
        _write_or_print_json(result, output, "cost_roi.json")
    else:
        print(format_stats(stats))
        print(format_metrics(roi))


def main() -> None:
    args = _parse_args()

    if args.command == "ui":
        try:
            import uvicorn
            from .ui.app import app as _ui_app
        except ImportError:
            raise SystemExit("UI dependencies missing. Run: pip install 'jidra[ui]'")

        uvicorn.run(
            _ui_app,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return

    if args.command == "up":
        _up()
        return

    if args.command == "cost-roi":
        _cost_roi(
            args.graph,
            args.method,
            args.codebase,
            args.model,
            args.queries,
            args.offline == "true",
            args.output,
        )
        return

    if args.command == "index":
        _index(
            args.codebase,
            args.output,
            force=args.force,
            ts_backend=getattr(args, "ts_backend", "auto"),
        )
        return

    if args.command == "validate":
        _validate(
            args.graph,
            args.graph_type,
            args.codebase,
            args.actuator_url,
            args.port,
            args.timeout,
            args.output,
            args.report,
            args.no_filter,
            args.skip_build,
            args.build_dir,
        )
        return

    if args.command == "process":
        _process(
            args.codebase,
            args.actuator_url,
            args.port,
            args.timeout,
            args.output,
            args.skip_build,
            args.build_dir,
        )
        return

    if args.command == "graph-view":
        graph, graph_path = _load_graph_by_type(args.graph, args.graph_type)

        # Build graph data
        graph_data = build_graph_data(
            graph,
            method_selector=args.method,
            depth=args.depth,
            package_filter=args.package,
        )

        # Generate HTML
        html = render_interactive_html(graph_data)

        # Determine output path
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = graph_path.parent / "graph.html"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "nodes": len(graph_data["nodes"]),
                    "edges": len(graph_data["edges"]),
                },
                indent=2,
            )
        )
        return

    if args.command == "mcp":
        graph_path = _resolve_graph_db_path(args.graph)
        try:
            from .server.mcp_server import run_mcp_server

            run_mcp_server(str(graph_path), codebase_path=args.codebase)
            return
        except RuntimeError as exc:
            raise SystemExit(str(exc))

    if args.command == "graph-docs":
        from .indexing import doc_store
        from .indexing.doc_graph_visualizer import (
            build_doc_graph_data,
            render_doc_graph_html,
        )

        db_path = _resolve_graph_db_path(args.graph)
        conn = graph_store.connect(db_path)
        doc_store.migrate(conn)
        sources = doc_store.list_sources(conn)
        if not sources:
            raise SystemExit(
                "No documents indexed yet. Run `jidra index-docs --path ./specs/` first."
            )
        graph = graph_store.load_graph(conn, variant="main")
        with ui.spinner("Building doc graph..."):
            data = build_doc_graph_data(conn, graph)
            html = render_doc_graph_html(data)
        out = (
            Path(args.output).resolve()
            if args.output
            else db_path.parent / "doc_graph.html"
        )
        out.write_text(html, encoding="utf-8")
        s = data["stats"]
        ui.success(
            f"Doc graph: {s['docs']} docs · {s['chunks']} chunks · {s['classes']} classes · {s['links']} links"
        )
        print(f"file://{out}")
        import subprocess
        import sys as _sys

        try:
            if _sys.platform == "darwin":
                subprocess.Popen(["open", str(out)])
        except Exception:
            pass
        return

    if args.command == "index-docs":
        from .indexing import doc_store
        from .indexing.doc_indexer import extract_graph_names, index_document
        from .llm.telemetry import record_doc_index_event

        if args.path.startswith(("http://", "https://")):
            raise SystemExit(
                "URL indexing is disabled — download the document locally first."
            )

        db_path = _resolve_graph_db_path(args.graph)
        conn = graph_store.connect(db_path)
        doc_store.migrate(conn)
        graph = graph_store.load_graph(conn, variant="main")
        class_names, method_names = extract_graph_names(graph)

        p = Path(args.path)
        exts = tuple(args.extensions)
        files = sorted(p.rglob("*") if p.is_dir() else [p])
        files = [
            f
            for f in files
            if f.is_file()
            and (p.is_dir() and f.suffix.lower() in exts or not p.is_dir())
        ]

        if not files:
            raise SystemExit(f"No matching files found in {p}")

        ui.banner("JIDRA Doc Indexer")
        ui.info(f"Source: {p}  |  Files: {len(files)}  |  Graph: {db_path.name}")

        if ui.RICH:
            import datetime

            from rich import print as rprint
            from rich.live import Live
            from rich.table import Table

            table = Table(
                show_header=True, header_style="bold #4d6173", box=None, padding=(0, 2)
            )
            table.add_column("File", style="#67e8f9", min_width=30, no_wrap=True)
            table.add_column("Type", style="#94a3b8", width=10)
            table.add_column("Size", style="#64748b", width=9, justify="right")
            table.add_column("Chunks", style="#a78bfa", width=7, justify="right")
            table.add_column(
                "Linked Classes", style="#38bdf8", width=15, justify="right"
            )
            table.add_column("Elapsed", style="#f59e0b", width=9, justify="right")
            table.add_column("Status", width=8)

            def _fmt_size(b: int) -> str:
                return f"{b / 1024:.1f}KB" if b >= 1024 else f"{b}B"

            with Live(table, refresh_per_second=8, vertical_overflow="visible"):
                for f in files:
                    size = f.stat().st_size
                    t0 = time.time()
                    err = None
                    n_chunks = 0
                    n_linked = 0
                    try:
                        n_chunks = index_document(
                            conn, str(f), class_names, method_names
                        )
                        linked_set: set[str] = set()
                        for row in conn.execute(
                            "SELECT linked_classes FROM doc_chunks WHERE source_path=?",
                            (str(f),),
                        ).fetchall():
                            linked_set.update(x for x in row[0].split(",") if x)
                        n_linked = len(linked_set)
                        status_str = "[green]✓[/green]"
                    except Exception as e:
                        err = str(e)
                        status_str = "[red]✗[/red]"

                    elapsed_ms = int((time.time() - t0) * 1000)
                    source_type = (
                        "pdf"
                        if f.suffix.lower() == ".pdf"
                        else "docx"
                        if f.suffix.lower() in (".docx", ".doc")
                        else "markdown"
                        if f.suffix.lower() in (".md", ".mdx", ".txt")
                        else "file"
                    )

                    table.add_row(
                        f.name,
                        source_type,
                        _fmt_size(size),
                        str(n_chunks),
                        str(n_linked),
                        f"{elapsed_ms / 1000:.2f}s"
                        if elapsed_ms >= 1000
                        else f"{elapsed_ms}ms",
                        status_str,
                    )
                    record_doc_index_event(
                        str(f),
                        source_type,
                        n_chunks,
                        n_linked,
                        size,
                        elapsed_ms,
                        status="ok" if not err else "error",
                        error=err,
                    )

            sources = doc_store.list_sources(conn)
            total_chunks = sum(s["chunk_count"] for s in sources)
            rprint(
                f"\n[bold #38bdf8]Done.[/bold #38bdf8] {len(files)} files · {total_chunks} total chunks in doc store"
            )
            doc_graph_out = _write_doc_graph(db_path.parent, db_path)
            if doc_graph_out:
                rprint(f"[dim]Doc graph:[/dim] file://{doc_graph_out}")
        else:
            # Fallback plain output
            for f in files:
                size = f.stat().st_size
                t0 = time.time()
                try:
                    n_chunks = index_document(conn, str(f), class_names, method_names)
                    elapsed_ms = int((time.time() - t0) * 1000)
                    linked_set = set()
                    for row in conn.execute(
                        "SELECT linked_classes FROM doc_chunks WHERE source_path=?",
                        (str(f),),
                    ).fetchall():
                        linked_set.update(x for x in row[0].split(",") if x)
                    source_type = Path(f).suffix.lstrip(".") or "file"
                    record_doc_index_event(
                        str(f), source_type, n_chunks, len(linked_set), size, elapsed_ms
                    )
                    ui.success(
                        f"{f.name}: {n_chunks} chunks, {len(linked_set)} classes linked ({elapsed_ms}ms)"
                    )
                except Exception as e:
                    record_doc_index_event(
                        str(f), "unknown", 0, 0, size, 0, status="error", error=str(e)
                    )
                    ui.error(f"{f.name}: {e}")
            doc_graph_out = _write_doc_graph(db_path.parent, db_path)
            if doc_graph_out:
                ui.success(f"Doc graph: file://{doc_graph_out}")
        return

    if args.command == "history":
        from .llm.telemetry import (
            fetch_doc_index_history,
            fetch_index_history,
            fetch_reindex_history,
        )

        repo_filter = args.repo
        index_rows = fetch_index_history(repo=repo_filter, limit=args.limit)
        reindex_rows = fetch_reindex_history(repo=repo_filter, limit=args.limit)
        doc_rows = fetch_doc_index_history(limit=args.limit * 4)

        if args.html is not None:
            html = _render_history_html(index_rows, reindex_rows, doc_rows)
            out = (
                Path(args.html).resolve()
                if args.html
                else (OUTPUT_DIR / "telemetry.html")
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            print(f"History report: file://{out}")
            import subprocess
            import sys as _sys

            try:
                if _sys.platform == "darwin":
                    subprocess.Popen(["open", str(out)])
                elif _sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(out)])
            except Exception:
                pass
            return

        if not index_rows and not reindex_rows:
            print("No telemetry recorded yet. Run `jidra up` or `jidra process` first.")
            return

        if ui.RICH:
            import datetime

            from rich import print as rprint
            from rich.table import Table

            def _fmt_ts(ts_ms: int) -> str:
                return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime(
                    "%Y-%m-%d %H:%M"
                )

            if index_rows:
                t = Table(title="Index Events", show_lines=False)
                for col in (
                    "Time",
                    "Repo",
                    "Languages",
                    "Classes",
                    "Methods",
                    "Lines",
                    "Elapsed",
                ):
                    t.add_column(col, no_wrap=True)
                for r in index_rows:
                    repo_short = Path(r["repo"]).name
                    t.add_row(
                        _fmt_ts(r["ts"]),
                        repo_short,
                        r["languages"],
                        str(r["classes"]),
                        str(r["methods"]),
                        str(r["lines"]),
                        f"{r['elapsed_ms']}ms",
                    )
                rprint(t)

            if reindex_rows:
                t = Table(title="Reindex Events", show_lines=False)
                for col in (
                    "Time",
                    "Repo",
                    "File",
                    "Lang",
                    "Type",
                    "Methods +/-",
                    "Elapsed",
                ):
                    t.add_column(col, no_wrap=True)
                for r in reindex_rows:
                    repo_short = Path(r["repo"]).name
                    t.add_row(
                        _fmt_ts(r["ts"]),
                        repo_short,
                        Path(r["changed_file"]).name,
                        r["language"] or "",
                        r["change_type"],
                        f"+{r['methods_added']}/-{r['methods_deleted']}",
                        f"{r['elapsed_ms']}ms",
                    )
                rprint(t)
        else:
            print(
                json.dumps(
                    {"index_events": index_rows, "reindex_events": reindex_rows},
                    indent=2,
                )
            )
        return

    if args.command == "reindex":
        from .engine.reindexer import incremental_reindex

        graph_path = _resolve_graph_db_path(args.graph)
        codebase = (
            Path(args.codebase).resolve() if args.codebase else graph_path.parent.parent
        )
        summary = incremental_reindex(
            codebase, graph_path, hint_changed_files=args.changed_files
        )
        print(json.dumps(summary, indent=2, default=str))
        return

    if args.command == "hooks":
        from .utils.git_hooks import install_hooks, uninstall_hooks

        repo = Path(args.repo).resolve() if args.repo else Path.cwd()
        graph_path = _resolve_graph_db_path(args.graph)
        if args.action == "install":
            written = install_hooks(repo, graph_path)
            print(f"✓ Installed JIDRA git hooks: {', '.join(written) or '(none)'}")
        else:
            removed = uninstall_hooks(repo)
            print(f"✓ Removed JIDRA blocks from: {', '.join(removed) or '(none)'}")
        return

    if args.command == "flow-doc":
        graph_path = _resolve_graph_db_path(args.graph)
        engine = JidraEngine(str(graph_path), variant=args.graph_type)
        agent = FlowDocAgent(
            engine,
            max_subflows=args.max_subflows,
            flow_depth=args.depth,
            top_n=args.top_n,
            max_context_chars=args.max_context_chars,
            include_utility=args.include_utility,
            mind_map_mode=args.mind_map,
            include_details=args.include_details,
            max_nodes=args.max_nodes,
        )

        _progress_ui = None
        prepared = None
        if args.show_agents:
            try:
                from .experiments.enrichment_ui import AgentProgressUI

                prepared = agent.prepare(args.method)
                slots = ["root"]
                if prepared and not prepared.get("error"):
                    for c in prepared.get("queue", []):
                        label = (
                            c.signature
                            if getattr(c, "signature", None)
                            else c.method_id
                        )
                        if "#" in label:
                            label = label.split("#", 1)[1]
                        slots.append(str(label))
                _progress_ui = AgentProgressUI("FlowDoc Agent Progress", slots=slots)
                _progress_ui.start()
                for name in slots[1:]:
                    _progress_ui.update(name, "queued")
                agent.progress_ui = _progress_ui  # type: ignore[assignment]
            except Exception:
                _progress_ui = None

        result = agent.build(args.method)
        if result.get("error"):
            if _progress_ui:
                _progress_ui.update("root", "failed", error=str(result["error"]))
                _progress_ui.stop({"ok": False})
            raise SystemExit(result["error"])

        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(agent.render_markdown(result), encoding="utf-8")
        if _progress_ui:
            if "root" in _progress_ui.slots:
                _progress_ui.update("root", "enriched", phase="done")
            _progress_ui.stop({"ok": True})
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "expanded_methods": len(result.get("expanded_methods", [])),
                    "important_unresolved_calls": len(
                        result.get("important_unresolved_calls", [])
                    ),
                    "suggested_next_methods": len(
                        result.get("suggested_next_methods", [])
                    ),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return
    if args.command == "error-doc":
        graph, graph_path = _load_graph_by_type(args.graph, args.graph_type)
        stack_text = Path(args.stack_trace).resolve().read_text(encoding="utf-8")
        frames = _parse_stack_trace(stack_text)
        matched_rows, anchor = _match_stack_frames_to_methods(graph, frames)
        if not frames:
            raise SystemExit("No Java stack frames parsed from stack trace input.")
        if anchor is None:
            raise SystemExit(
                "No project frame matched/ambiguous for primary failure anchor."
            )
        if anchor["match_status"] == "ambiguous":
            method_selector = anchor["ambiguous_method_ids"][0]
        else:
            method_selector = anchor["matched_method_id"]

        engine = JidraEngine(str(graph_path), variant=args.graph_type)
        agent = FlowDocAgent(
            engine,
            flow_depth=args.depth,
            include_utility=args.include_utility,
            mind_map_mode=True,
            include_details=False,
            max_nodes=args.max_nodes,
        )
        flow_result = agent.build(method_selector)
        if flow_result.get("error"):
            raise SystemExit(flow_result["error"])
        mind_map_md = _extract_focused_map_sections(agent.render_markdown(flow_result))
        failing_row = anchor
        caller_row = (
            matched_rows[anchor["frame_index"] - 1]
            if anchor["frame_index"] > 0
            else None
        )
        method_by_id = {m.id: m for m in graph.methods}
        neighbors = []
        if failing_row.get("matched_method_id"):
            mid = failing_row["matched_method_id"]
            for e in graph.resolved_call_edges:
                if e.caller_method_id == mid:
                    tgt = method_by_id.get(e.callee_method_id)
                    if tgt:
                        neighbors.append(tgt.signature)
                if e.callee_method_id == mid:
                    src = method_by_id.get(e.caller_method_id)
                    if src:
                        neighbors.append(src.signature)
        neighbors = sorted(set(neighbors))[:10]
        unresolved_near_all = (flow_result.get("mind_map", {}) or {}).get(
            "unresolved_calls", []
        )
        unresolved_near = [
            c for c in unresolved_near_all if not _is_error_doc_noise_call(c)
        ][:10]
        anchor_id = failing_row.get("matched_method_id")
        meaningful_downstream = []
        for src, dst in (flow_result.get("mind_map", {}) or {}).get("edges", []):
            if src != anchor_id:
                continue
            dm = method_by_id.get(dst)
            if dm and _is_meaningful_signature(dm.signature):
                meaningful_downstream.append(dm.signature)
        upstream_mode = len(meaningful_downstream) == 0

        matched_frame0 = (
            matched_rows[0]
            if len(matched_rows) > 0
            and matched_rows[0]["match_status"] in {"matched", "ambiguous"}
            else None
        )
        matched_frame1 = (
            matched_rows[1]
            if len(matched_rows) > 1
            and matched_rows[1]["match_status"] in {"matched", "ambiguous"}
            else None
        )
        nearest_controller = None
        for r in matched_rows:
            if r["match_status"] not in {"matched", "ambiguous"}:
                continue
            if ".controller." in r["class_full_name"].lower():
                nearest_controller = r
                break
        caller_signatures = []
        if anchor_id:
            for e in graph.resolved_call_edges:
                if e.callee_method_id == anchor_id:
                    sm = method_by_id.get(e.caller_method_id)
                    if sm:
                        caller_signatures.append(sm.signature)
        caller_signatures = sorted(set(caller_signatures))[:10]
        lines = [
            "# Error Investigation",
            "",
            f"- stack_trace: `{Path(args.stack_trace).resolve()}`",
            f"- anchor_frame_index: {failing_row['frame_index']}",
            f"- anchor_match_status: `{failing_row['match_status']}`",
            "",
            "## Stack Frames",
            "| frame index | class | method | file | line | matched_method_id | match_status |",
            "|---:|---|---|---|---:|---|---|",
        ]
        for r in matched_rows:
            lines.append(
                f"| {r['frame_index']} | `{r['class_full_name']}` | `{r['method_name']}` | `{r['file_name']}` | {r['line']} | `{r.get('matched_method_id', '')}` | `{r['match_status']}` |"
            )
            if r["match_status"] == "ambiguous":
                lines.append(
                    f"| {r['frame_index']} | `ambiguous_candidates` |  |  |  | `{', '.join(r.get('ambiguous_method_ids', []))}` | `ambiguous` |"
                )
        lines.append("")
        lines.append("## Focused Flow Map")
        lines.append(mind_map_md)
        lines.append("## Suggested Debug Locations")
        lines.append("| priority | location | reason |")
        lines.append("|---:|---|---|")
        failing_location = failing_row.get("matched_method_id") or ",".join(
            failing_row.get("ambiguous_method_ids", [])
        )
        if failing_row.get("matched_method_id"):
            m = method_by_id.get(failing_row["matched_method_id"])
            if m:
                failing_location = m.signature
        lines.append(f"| 1 | `{failing_location}` | failing project frame |")
        if caller_row:
            caller_loc = f"{caller_row['class_full_name']}#{caller_row['method_name']}:{caller_row['line']}"
            lines.append(f"| 2 | `{caller_loc}` | caller frame above failure |")
        unresolved_priority = 3
        for c in unresolved_near:
            receiver = str(c.get("receiver") or "").strip()
            call_name = str(c.get("call") or "").strip()
            if receiver and call_name:
                location = f"{receiver}.{call_name}"
            elif call_name:
                location = call_name
            else:
                continue
            lines.append(
                f"| {unresolved_priority} | `{location}` | unresolved external call near failure |"
            )
        caller_priority = 4
        for sig in caller_signatures:
            lines.append(
                f"| {caller_priority} | `{sig}` | graph caller of failing method |"
            )
        if upstream_mode:
            if matched_frame0:
                loc0 = f"{matched_frame0['class_full_name']}#{matched_frame0['method_name']}:{matched_frame0['line']}"
                lines.append(f"| 2 | `{loc0}` | matched frame 0 |")
            if matched_frame1:
                loc1 = f"{matched_frame1['class_full_name']}#{matched_frame1['method_name']}:{matched_frame1['line']}"
                lines.append(f"| 2 | `{loc1}` | matched caller frame 1 |")
            if nearest_controller:
                locc = f"{nearest_controller['class_full_name']}#{nearest_controller['method_name']}:{nearest_controller['line']}"
                lines.append(f"| 2 | `{locc}` | nearest matched controller frame |")
        elif neighbors:
            for sig in neighbors[:10]:
                lines.append(
                    f"| 4 | `{sig}` | callee graph neighbor of failing method |"
                )
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "frames": len(matched_rows),
                    "anchor_method": method_selector,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return

    graph, graph_path = _load_graph_by_type(args.graph, args.graph_type)

    if args.command == "trace":
        method = _resolve_single_method(graph, args.method)
        result = trace_method(graph, method.id, max_depth=args.max_depth)
        if result.get("error"):
            raise SystemExit(result["error"])
        if args.business_only:
            removed = _apply_business_only_trace(result)
            result["filters"] = {"business_only": True, "removed_count": removed}
            filename = (
                f"trace_business_{args.graph_type}_{_method_filename_part(method)}.json"
            )
        else:
            filename = f"trace_{args.graph_type}_{_method_filename_part(method)}.json"
        _write_or_print_json(result, args.output, filename)
        return

    if args.command == "context":
        method = _resolve_single_method(graph, args.method)
        result = build_method_context(graph, method.id, max_chars=args.max_chars)
        if result.get("error"):
            raise SystemExit(result["error"])
        if args.business_only:
            removed = _apply_business_only_context(result)
            result["filters"] = {"business_only": True, "removed_count": removed}
            filename = f"context_business_{args.graph_type}_{_method_filename_part(method)}.json"
        else:
            filename = f"context_{args.graph_type}_{_method_filename_part(method)}.json"
        _write_or_print_json(result, args.output, filename)
        return

    if args.command == "flow":
        method = _resolve_single_method(graph, args.method)
        config = _load_cli_config()
        flow_config = config.get("flow", {}) if isinstance(config, dict) else {}
        result = stitch_flow(
            graph,
            method,
            max_depth=args.depth,
            business_only=args.business_only,
            is_business_entry=is_business_entry,
            flow_config=flow_config,
        )
        if args.business_only:
            filename = (
                f"flow_business_{args.graph_type}_{_method_filename_part(method)}.json"
            )
        else:
            filename = f"flow_{args.graph_type}_{_method_filename_part(method)}.json"
        _write_or_print_json(result, args.output, filename)
        return

    if args.command == "trace-route":
        result = trace_route(graph, args.route, max_depth=args.max_depth)
        if result.get("error"):
            raise SystemExit(result["error"])

        route_part = _safe_filename_part(args.route)
        root_sig = (result.get("root") or {}).get("signature") or ""
        if "#" in root_sig:
            class_name, method_part = root_sig.split("#", 1)
            method_name = method_part.split("(", 1)[0]
            route_part = _safe_filename_part(
                f"{class_name.split('.')[-1]}_{method_name}"
            )

        filename = f"trace_route_{args.graph_type}_{route_part}.json"
        _write_or_print_json(result, args.output, filename)
        return

    if args.command == "prompt":
        method = _resolve_single_method(graph, args.method)
        top_nodes = None
        if args.use_flow:
            cfg = _load_cli_config()
            flow_config = cfg.get("flow", {}) if isinstance(cfg, dict) else {}
            flow = stitch_flow(
                graph,
                method,
                max_depth=4,
                business_only=True,
                is_business_entry=is_business_entry,
                flow_config=flow_config,
            )
            top_nodes = _select_top_flow_nodes(flow, max(1, args.top_n))
            prompt_text = _build_flow_prompt(
                method,
                flow,
                args.target,
                top_n=args.top_n,
                include_source=args.include_source,
                verbose_flow=args.verbose_flow,
                max_chars=args.max_chars,
                graph=graph,
            )
            filename = f"prompt_flow_{args.target}_{args.graph_type}_{_method_filename_part(method)}.txt"
        else:
            context = build_method_context(graph, method.id, max_chars=args.max_chars)
            if context.get("error"):
                raise SystemExit(context["error"])
            if args.business_only:
                removed = _apply_business_only_context(context)
                context["filters"] = {
                    "business_only": True,
                    "removed_count": removed,
                }
            prompt_text = _build_prompt(context, args.target)
            filename = f"prompt_{args.target}_{args.graph_type}_{_method_filename_part(method)}.txt"
        _write_or_print_text(prompt_text, args.output, filename)
        if args.debug_flow and args.use_flow and top_nodes is not None and args.output:
            debug_payload = {
                "debug": {
                    "top_nodes": [
                        {
                            "method_id": n.get("method_id"),
                            "signature": n.get("signature"),
                            "rank_score": n.get("rank_score"),
                            "tier": n.get("tier"),
                            "depth": n.get("depth"),
                        }
                        for n in top_nodes
                    ]
                }
            }
            base_out = Path(args.output).resolve()
            if base_out.exists() and base_out.is_dir():
                debug_target = base_out / filename.replace(".txt", ".debug.json")
            elif base_out.suffix:
                debug_target = base_out.with_suffix(".debug.json")
            else:
                base_out.mkdir(parents=True, exist_ok=True)
                debug_target = base_out / filename.replace(".txt", ".debug.json")
            debug_target.parent.mkdir(parents=True, exist_ok=True)
            debug_target.write_text(
                json.dumps(debug_payload, ensure_ascii=True, indent=2), encoding="utf-8"
            )
        return

    if args.command == "diagnose":
        method = _resolve_single_method(graph, args.method)
        flow = None
        top_nodes = None
        if args.use_flow:
            cfg = _load_cli_config(args.config)
            flow_config = cfg.get("flow", {}) if isinstance(cfg, dict) else {}
            flow = stitch_flow(
                graph,
                method,
                max_depth=4,
                business_only=True,
                is_business_entry=is_business_entry,
                flow_config=flow_config,
            )
            top_nodes = _select_top_flow_nodes(flow, max(1, args.top_n))
            prompt_text = _build_flow_prompt(
                method,
                flow,
                args.target,
                top_n=args.top_n,
                include_source=args.include_source,
                verbose_flow=args.verbose_flow,
                max_chars=args.max_chars,
                graph=graph,
            )
            context = build_method_context(graph, method.id, max_chars=args.max_chars)
        else:
            context = build_method_context(graph, method.id, max_chars=args.max_chars)
            if context.get("error"):
                raise SystemExit(context["error"])
            if args.business_only:
                removed = _apply_business_only_context(context)
                context["filters"] = {
                    "business_only": True,
                    "removed_count": removed,
                }
            prompt_text = _build_prompt(context, args.target)

        if context.get("error"):
            raise SystemExit(context["error"])
        startTime = time.time()
        llm_result = _call_llm(
            prompt_text,
            args.model,
            args.llm_profile,
            args.config,
            args.max_tokens,
        )
        print(f"Time Taken by LLM: {(time.time() - startTime)}")
        business_flow = context.get("business_flow") or context.get(
            "resolved_callees", []
        )

        result = {
            "method": method.signature,
            "analysis": llm_result.get("text", ""),
            "llm": {
                "provider": llm_result.get("provider", "litellm"),
                "profile": llm_result.get("profile", args.llm_profile or "local"),
                "model": llm_result.get("model", args.model or ""),
                "usage": llm_result.get("usage", {}),
                "latency_seconds": llm_result.get("latency_seconds", 0.0),
                "limits": {
                    "max_chars": args.max_chars,
                    "max_tokens": args.max_tokens,
                },
            },
            "context_summary": {
                "business_flow_count": len(business_flow),
                "unresolved_count": len(context.get("unresolved_calls", [])),
            },
        }
        if args.use_flow and flow is not None:
            top_nodes = _select_top_flow_nodes(flow, max(1, args.top_n))
            result["flow_summary"] = {
                "used_flow": True,
                "top_n": args.top_n,
                "node_count": (flow.get("summary", {}) or {}).get(
                    "node_count", len(flow.get("nodes", []))
                ),
                "top_node_count": len(top_nodes),
                "uncertain_edge_count": (flow.get("summary", {}) or {}).get(
                    "uncertain_edge_count", len(flow.get("uncertain_edges", []))
                ),
            }
            if args.debug_flow:
                result["debug"] = {
                    "top_nodes": [
                        {
                            "method_id": n.get("method_id"),
                            "signature": n.get("signature"),
                            "rank_score": n.get("rank_score"),
                            "tier": n.get("tier"),
                            "depth": n.get("depth"),
                        }
                        for n in (top_nodes or [])
                    ]
                }
        if args.show_prompt:
            result["prompt"] = prompt_text

        if args.use_flow:
            filename = f"diagnose_flow_{args.target}_{args.graph_type}_{_method_filename_part(method)}.json"
        else:
            filename = f"diagnose_{args.target}_{args.graph_type}_{_method_filename_part(method)}.json"
        if args.output:
            _write_or_print_json(result, args.output, filename)
            if not args.quiet:
                out_path = Path(args.output).resolve()
                if out_path.exists() and out_path.is_dir():
                    target = out_path / filename
                elif out_path.suffix:
                    target = out_path
                else:
                    target = out_path / filename
                print(f"Wrote diagnosis to {target}")
            return

        if args.quiet or not _terminal_supports_color():
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            _print_diagnose_report(result)
        return

    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
