"""
TypeScript extractor for JIDRA.

Runs the ts_sidecar/index.js script inside an ephemeral node:20-slim container,
reads the JSONL output, and returns a Graph identical in shape to the Java extractor.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from ..models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    ResolvedCallEdge,
)

DOCKER_IMAGE = "jidra-ts-sidecar:latest"
SIDECAR_DIR = Path(__file__).resolve().parents[3] / "sidecar" / "typescript"
SIDECAR_PATH = SIDECAR_DIR / "index.js"


class TsExtractorError(Exception):
    pass


def _ensure_image() -> None:
    """Build the sidecar Docker image if it doesn't exist yet."""
    check = subprocess.run(
        ["docker", "image", "inspect", DOCKER_IMAGE],
        capture_output=True,
    )
    if check.returncode == 0:
        return  # already built

    print("  [jidra] Building ts-sidecar image (first run only)...", flush=True)
    result = subprocess.run(
        ["docker", "build", "-t", DOCKER_IMAGE, str(SIDECAR_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TsExtractorError(
            f"Failed to build ts-sidecar image:\n{result.stderr[-2000:]}"
        )


def _run_sidecar(
    codebase_root: Path,
    files: set[Path] | None = None,
    timeout: int = 300,
) -> list[dict]:
    if not SIDECAR_PATH.exists():
        raise TsExtractorError(f"Sidecar script not found: {SIDECAR_PATH}")

    try:
        _ensure_image()
    except FileNotFoundError as e:
        raise TsExtractorError("Docker is not available on PATH") from e

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{codebase_root}:/repo:ro",
        DOCKER_IMAGE,
        "/repo",
    ]

    if files:
        rel_paths = ",".join(str(f.relative_to(codebase_root)) for f in files)
        cmd.append(rel_paths)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise TsExtractorError(
            f"Sidecar timed out after {timeout}s — repo may be too large or Docker is slow"
        ) from e
    except FileNotFoundError as e:
        raise TsExtractorError("Docker is not available on PATH") from e

    if result.returncode != 0:
        raise TsExtractorError(
            f"Sidecar exited with code {result.returncode}:\n{result.stderr[-2000:]}"
        )

    records = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # non-JSON stderr bleed; ignore

    return records


def _build_graph_from_records(records: list[dict]) -> Graph:
    classes: list[ClassEntry] = []
    methods: list[MethodEntry] = []
    fields: list[FieldEntry] = []
    callsites: list[CallSite] = []
    inheritance_edges: list[InheritanceEdge] = []
    resolved_call_edges: list[ResolvedCallEdge] = []

    for r in records:
        t = r.get("_type")

        if t == "class":
            classes.append(ClassEntry(
                id=r["id"],
                package_name=r["package_name"],
                name=r["name"],
                full_name=r["full_name"],
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                modifiers=r.get("modifiers", []),
                annotations=r.get("annotations", []),
                extends=r.get("extends"),
                implements=r.get("implements", []),
                imports=r.get("imports", []),
                stereotypes=r.get("stereotypes", []),
            ))

        elif t == "method":
            methods.append(
                MethodEntry(
                    id=r["id"],
                    class_id=r["class_id"],
                    class_full_name=r["class_full_name"],
                    method_name=r["method_name"],
                    return_type=r["return_type"],
                    parameter_types=r.get("parameter_types", []),
                    parameter_names=r.get("parameter_names", []),
                    signature=r["signature"],
                    file_path=r["file_path"],
                    start_line=r["start_line"],
                    end_line=r["end_line"],
                    source=r.get("source", ""),
                    class_context=r.get("class_context", {}),
                    annotations=r.get("annotations", []),
                    local_variable_types=r.get("local_variable_types", {}),
                    field_reads=r.get("field_reads", []),
                    field_writes=r.get("field_writes", []),
                    is_endpoint=r.get("is_endpoint", False),
                    http_method=r.get("http_method"),
                    route=r.get("route"),
                    controller_route=r.get("controller_route"),
                    full_route=r.get("full_route"),
                    language=r.get("language", "typescript"),
                    framework_role=r.get("framework_role"),
                )
            )

        elif t == "field":
            fields.append(FieldEntry(
                id=r["id"],
                class_id=r["class_id"],
                name=r["name"],
                type_name=r["type_name"],
                modifiers=r.get("modifiers", []),
                file_path=r["file_path"],
                line=r["line"],
            ))

        elif t == "callsite":
            callsites.append(CallSite(
                id=r["id"],
                caller_method_id=r["caller_method_id"],
                callee_name=r["callee_name"],
                receiver=r.get("receiver"),
                argument_count=r.get("argument_count", 0),
                file_path=r["file_path"],
                line=r["line"],
                column=r["column"],
                text=r.get("text", ""),
                receiver_type_raw=r.get("receiver_type_raw"),
                receiver_type_normalized=r.get("receiver_type_normalized"),
                receiver_resolution_source=r.get("receiver_resolution_source"),
                receiver_type=r.get("receiver_type"),
                resolved_candidates=r.get("resolved_candidates", []),
                resolution_status=r.get("resolution_status", "unresolved"),
                resolution_reason=r.get("resolution_reason", ""),
                candidate_count=r.get("candidate_count", 0),
            ))

        elif t == "inheritance_edge":
            inheritance_edges.append(InheritanceEdge(
                id=r["id"],
                source_class_id=r["source_class_id"],
                source_class=r["source_class"],
                target_class=r["target_class"],
                relation=r["relation"],
            ))

        elif t == "resolved_call_edge":
            resolved_call_edges.append(ResolvedCallEdge(
                id=r["id"],
                callsite_id=r["callsite_id"],
                caller_method_id=r["caller_method_id"],
                callee_method_id=r["callee_method_id"],
            ))

    for cls in classes:
        cls.language = "typescript"
    for m in methods:
        m.language = "typescript"

    return Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=callsites,
        inheritance_edges=inheritance_edges,
        resolved_call_edges=resolved_call_edges,
    )


def build_ts_graph(
    codebase_root: Path,
    on_progress: Callable[[int], None] | None = None,
    timeout: int = 300,
    backend: str = "auto",
) -> Graph:
    """
    Build a JIDRA Graph from a TypeScript/React codebase.

    backend:
      - "auto"/"treesitter" (default): in-process tree-sitter — no Docker needed.
        Syntax-only, so call resolution is lower quality (~65% vs ts-morph's ~80%).
      - "tsmorph": the Docker ts-morph sidecar (full TypeScript compiler types).
    "auto" tries tree-sitter and falls back to the sidecar if the optional
    `tree-sitter-typescript` dependency isn't installed — zero regression for
    existing users.
    """
    if backend in ("auto", "treesitter"):
        try:
            from .ts_treesitter import build_ts_graph_treesitter

            return build_ts_graph_treesitter(codebase_root, on_progress)
        except ImportError:
            if backend == "treesitter":
                raise
            import sys

            print(
                "jidra: tree-sitter-typescript not installed; falling back to the "
                "Docker ts-morph sidecar. `pip install tree-sitter-typescript` to "
                "avoid Docker.",
                file=sys.stderr,
            )

    if on_progress:
        on_progress(0)

    records = _run_sidecar(codebase_root, files=files, timeout=timeout)

    if on_progress:
        on_progress(len([r for r in records if r.get("_type") == "class"]))

    return _build_graph_from_records(records)


def build_ts_graph_for_files(files: set[Path], codebase_root: Path) -> Graph:
    """Build Graph for specific TS/TSX files. Used by incremental reindex.

    Uses the Docker sidecar which already accepts a file subset.
    """
    records = _run_sidecar(codebase_root, files=files)
    return _build_graph_from_records(records)
