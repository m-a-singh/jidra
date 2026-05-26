from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .context_builder import build_method_context
from .engine import JidraEngine
from .exporter import export_jsonl, graph_records, split_graph_records_by_source
from .extractor import build_graph
from .flow_doc_agent import FlowDocAgent
from .flow_stitcher import stitch_flow
from .graph_io import load_graph_jsonl, resolve_graph_paths
from .selector import (
    _method_ambiguous_error,
    _method_not_found_error,
    _resolve_method_selector,
)
from .trace_engine import trace_method, trace_route

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _parse_csv_env(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


# Keep these defaults generic (no employer-specific identifiers).
# Extend/override via env vars or config.yaml.
DEFAULT_NON_BUSINESS_SIGNATURE_PARTS = (
    ".metrics.",
    ".datadog.",
    ".prometheus.",
    ".logging.",
    ".log.",
    ".utils.",
    ".constants.",
)
DEFAULT_NON_BUSINESS_CALL_NAMES = {
    "getMarker",
    "increment",
    "decrement",
    "recordExecutionTime",
    "build",
}


def _load_non_business_filters(
    config_path: str | None = None,
) -> tuple[tuple[str, ...], set[str]]:
    """Load non-business filters from env/config, with safe generic defaults.

    Env vars (comma-separated):
      - JIDRA_NON_BUSINESS_SIGNATURE_PARTS
      - JIDRA_NON_BUSINESS_CALL_NAMES

    config.yaml (optional):
      non_business:
        # mode: "extend" (default) or "replace"
        mode: extend
        signature_parts: [".metrics.", "foo#"]
        call_names: ["increment", "decrement"]

    Behavior:
      - values are normalized to lowercase for case-insensitive matching
      - mode=extend: config/env add to defaults
      - mode=replace: config replaces defaults (env still extends after)
    """

    cfg = _load_cli_config(config_path)
    nb = cfg.get("non_business", {}) if isinstance(cfg, dict) else {}

    cfg_mode = None
    cfg_sig = None
    cfg_calls = None
    if isinstance(nb, dict):
        cfg_mode = nb.get("mode")
        cfg_sig = nb.get("signature_parts")
        cfg_calls = nb.get("call_names")

    mode = str(cfg_mode or "extend").strip().lower()

    if mode == "replace":
        sig_parts: list[str] = []
        call_names: set[str] = set()
    else:
        sig_parts = [s.lower() for s in DEFAULT_NON_BUSINESS_SIGNATURE_PARTS]
        call_names = {c.lower() for c in DEFAULT_NON_BUSINESS_CALL_NAMES}

    if isinstance(cfg_sig, list):
        sig_parts.extend([str(s).lower() for s in cfg_sig if str(s).strip()])
    if isinstance(cfg_calls, list):
        call_names.update([str(s).lower() for s in cfg_calls if str(s).strip()])

    # Env always extends (useful for quick one-off experiments without editing config).
    sig_parts.extend(
        [s.lower() for s in _parse_csv_env("JIDRA_NON_BUSINESS_SIGNATURE_PARTS")]
    )
    call_names.update(
        [s.lower() for s in _parse_csv_env("JIDRA_NON_BUSINESS_CALL_NAMES")]
    )

    # de-dupe while preserving order for signature parts
    deduped_sig_parts: list[str] = []
    seen = set()
    for s in sig_parts:
        if s in seen:
            continue
        seen.add(s)
        deduped_sig_parts.append(s)

    return tuple(deduped_sig_parts), call_names
STACK_RE = re.compile(
    r"^\s*at\s+([A-Za-z0-9_.$]+)\.([A-Za-z0-9_$<>]+)\(([^:()]+):(\d+)\)\s*$"
)


def _default_graph_for_type(graph_type: str) -> Path:
    if graph_type == "test":
        return OUTPUT_DIR / "graph_test.jsonl"
    return OUTPUT_DIR / "graph.jsonl"


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


def _load_error_doc_noise_filters(config_path: str | None = None) -> dict:
    """Load filters for error-doc noise suppression.

    config.yaml:
      error_doc:
        noise:
          mode: extend|replace
          receiver_prefixes: ["log", "thread"]
          call_names: ["warn", "info"]
          exception_receiver: "e"
          exception_call_names: ["getMessage", "getCause"]

    Env (always extends; comma-separated):
      - JIDRA_ERROR_DOC_NOISE_RECEIVER_PREFIXES
      - JIDRA_ERROR_DOC_NOISE_CALL_NAMES
    """

    cfg = _load_cli_config(config_path)
    ed = cfg.get("error_doc", {}) if isinstance(cfg, dict) else {}
    noise = ed.get("noise", {}) if isinstance(ed, dict) else {}

    mode = str((noise.get("mode") if isinstance(noise, dict) else None) or "extend").strip().lower()

    default_receiver_prefixes = ["builder", "log", "thread"]
    default_call_names = [
        "warn",
        "error",
        "info",
        "debug",
        "trace",
        "increment",
        "sleep",
        "currentThread",
        "interrupt",
    ]
    default_exception_receiver = "e"
    default_exception_call_names = ["getMessage", "getCause", "getStackTrace"]

    if mode == "replace":
        receiver_prefixes: list[str] = []
        call_names: list[str] = []
        exception_receiver = None
        exception_call_names: list[str] = []
    else:
        receiver_prefixes = default_receiver_prefixes[:]
        call_names = default_call_names[:]
        exception_receiver = default_exception_receiver
        exception_call_names = default_exception_call_names[:]

    if isinstance(noise, dict):
        rp = noise.get("receiver_prefixes")
        cn = noise.get("call_names")
        er = noise.get("exception_receiver")
        ecn = noise.get("exception_call_names")

        if isinstance(rp, list):
            receiver_prefixes.extend([str(x) for x in rp if str(x).strip()])
        if isinstance(cn, list):
            call_names.extend([str(x) for x in cn if str(x).strip()])
        if isinstance(er, str) and er.strip():
            exception_receiver = er.strip()
        if isinstance(ecn, list):
            exception_call_names.extend([str(x) for x in ecn if str(x).strip()])

    receiver_prefixes.extend(_parse_csv_env("JIDRA_ERROR_DOC_NOISE_RECEIVER_PREFIXES"))
    call_names.extend(_parse_csv_env("JIDRA_ERROR_DOC_NOISE_CALL_NAMES"))

    # normalize
    receiver_prefixes = [str(x).lower() for x in receiver_prefixes if str(x).strip()]
    call_names = [str(x).lower() for x in call_names if str(x).strip()]
    exception_call_names = [str(x).lower() for x in exception_call_names if str(x).strip()]

    # de-dupe preserve order
    def _dedupe(xs: list[str]) -> list[str]:
        out: list[str] = []
        seen = set()
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    return {
        "receiver_prefixes": tuple(_dedupe(receiver_prefixes)),
        "call_names": set(_dedupe(call_names)),
        "exception_receiver": (str(exception_receiver).lower() if exception_receiver else None),
        "exception_call_names": set(_dedupe(exception_call_names)),
    }


def _is_error_doc_noise_call(call: dict, *, config_path: str | None = None) -> bool:
    f = _load_error_doc_noise_filters(config_path)

    receiver = str(call.get("receiver") or "").lower()
    name = str(call.get("call") or "").lower()

    noisy_receiver_prefixes = f.get("receiver_prefixes") or ()
    if any(
        receiver == p or receiver.startswith(f"{p}.") or receiver.startswith(f"{p}(")
        for p in noisy_receiver_prefixes
    ):
        return True

    ex_receiver = f.get("exception_receiver")
    ex_calls = f.get("exception_call_names") or set()
    if ex_receiver and receiver == ex_receiver and name in ex_calls:
        return True

    noisy_names = f.get("call_names") or set()
    if name in noisy_names:
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


def is_business_entry(
    entry: dict,
    *,
    non_business_signature_parts: tuple[str, ...] = DEFAULT_NON_BUSINESS_SIGNATURE_PARTS,
    non_business_call_names: set[str] = DEFAULT_NON_BUSINESS_CALL_NAMES,
) -> bool:
    call_name = str(entry.get("call") or "").lower()
    if call_name in non_business_call_names:
        return False

    signature = str(
        entry.get("target_signature") or entry.get("signature") or ""
    ).lower()
    if any(part in signature for part in non_business_signature_parts):
        return False
    return True


def _apply_business_only_context(result: dict, *, config_path: str | None = None) -> int:
    sig_parts, call_names = _load_non_business_filters(config_path)

    resolved = result.get("resolved_callees", [])
    filtered = [
        entry
        for entry in resolved
        if is_business_entry(
            entry,
            non_business_signature_parts=sig_parts,
            non_business_call_names=call_names,
        )
    ]
    removed = len(resolved) - len(filtered)
    result["resolved_callees"] = filtered
    result["business_flow"] = filtered
    return removed


def _apply_business_only_trace(result: dict, *, config_path: str | None = None) -> int:
    sig_parts, call_names = _load_non_business_filters(config_path)

    flow = result.get("flow", [])
    filtered = []
    removed = 0
    for entry in flow:
        if entry.get("depth") == 0:
            filtered.append(entry)
            continue
        if is_business_entry(
            entry,
            non_business_signature_parts=sig_parts,
            non_business_call_names=call_names,
        ):
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
    from .llm_client import JidraLLMClient

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
        "index", help="Build graph JSONL from a Java codebase"
    )
    index_parser.add_argument(
        "--codebase", required=True, help="Path to Java repository root"
    )
    index_parser.add_argument(
        "--output", required=True, help="Output graph file or output directory"
    )

    trace_parser = subparsers.add_parser("trace", help="Trace a method call flow")
    trace_parser.add_argument(
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
    )
    mcp_parser.add_argument("--graph-type", choices=("main", "test"), default="main")

    flow_doc_parser = subparsers.add_parser(
        "flow-doc", help="Generate recursive deterministic flow markdown"
    )
    flow_doc_parser.add_argument(
        "--method", required=True, help="Method selector or method id"
    )
    flow_doc_parser.add_argument("--output", required=True, help="Output markdown path")
    flow_doc_parser.add_argument(
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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
        "--graph", help="Path to graph JSONL (overrides --graph-type default path)"
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

    return parser.parse_args()


def _resolve_single_method(graph, selector: str):
    candidates = _resolve_method_selector(graph, selector)
    if not candidates:
        raise SystemExit(_method_not_found_error(selector))
    if len(candidates) > 1:
        raise SystemExit(_method_ambiguous_error(selector, candidates))
    return candidates[0]


def _resolve_graph_path(graph_arg: str | None, graph_type: str) -> Path:
    if graph_arg:
        raw = Path(graph_arg).resolve()
        if raw.is_dir() or raw.suffix.lower() != ".jsonl":
            main, test, _ = resolve_graph_paths(raw)
            return main if graph_type == "main" else test
        return raw
    return _default_graph_for_type(graph_type)


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


def _index(codebase: str, output: str) -> None:
    codebase_path = Path(codebase).resolve()
    output_path = Path(output).resolve()
    main_path, test_path, _ = resolve_graph_paths(output_path)
    main_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.parent.mkdir(parents=True, exist_ok=True)

    graph = build_graph(codebase_path)
    records = graph_records(graph)
    main_records, test_records = split_graph_records_by_source(records)

    export_jsonl(main_path, main_records)
    export_jsonl(test_path, test_records)

    print(
        json.dumps(
            {
                "main_graph": str(main_path),
                "main_records": len(main_records),
                "test_graph": str(test_path),
                "test_records": len(test_records),
            },
            ensure_ascii=True,
            indent=2,
        )
    )


def main() -> None:
    args = _parse_args()

    if args.command == "index":
        _index(args.codebase, args.output)
        return

    if args.command == "mcp":
        graph_path = _resolve_graph_path(args.graph, args.graph_type)
        try:
            from .mcp_server import run_mcp_server

            run_mcp_server(str(graph_path))
            return
        except RuntimeError as exc:
            raise SystemExit(str(exc))

    if args.command == "flow-doc":
        graph_path = _resolve_graph_path(args.graph, args.graph_type)
        engine = JidraEngine(str(graph_path))
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

        ui = None
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
                ui = AgentProgressUI("FlowDoc Agent Progress", slots=slots)
                ui.start()
                for name in slots[1:]:
                    ui.update(name, "queued")
                agent.progress_ui = ui  # type: ignore[assignment]
            except Exception:
                ui = None

        result = agent.build(args.method)
        if result.get("error"):
            if ui:
                ui.update("root", "failed", error=str(result["error"]))
                ui.stop({"ok": False})
            raise SystemExit(result["error"])

        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(agent.render_markdown(result), encoding="utf-8")
        if ui:
            if "root" in ui.slots:
                ui.update("root", "enriched", phase="done")
            ui.stop({"ok": True})
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
        graph_path = _resolve_graph_path(args.graph, args.graph_type)
        graph = load_graph_jsonl(graph_path)
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

        engine = JidraEngine(str(graph_path))
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
            c
            for c in unresolved_near_all
            if not _is_error_doc_noise_call(c, config_path=getattr(args, "config", None))
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

    graph_path = _resolve_graph_path(args.graph, args.graph_type)
    graph = load_graph_jsonl(graph_path)

    if args.command == "trace":
        method = _resolve_single_method(graph, args.method)
        result = trace_method(
            graph, method.id, max_depth=args.max_depth, config_path=getattr(args, "config", None)
        )
        if result.get("error"):
            raise SystemExit(result["error"])
        if args.business_only:
            removed = _apply_business_only_trace(result, config_path=getattr(args, "config", None))
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
            removed = _apply_business_only_context(result, config_path=getattr(args, "config", None))
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
        result = trace_route(
            graph, args.route, max_depth=args.max_depth, config_path=getattr(args, "config", None)
        )
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
                removed = _apply_business_only_context(
                    context, config_path=getattr(args, "config", None)
                )
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
                removed = _apply_business_only_context(
                    context, config_path=getattr(args, "config", None)
                )
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
