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
        "docker",
        "run",
        "--rm",
        "-v",
        f"{codebase_root}:/repo:ro",
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
            classes.append(
                ClassEntry(
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
                )
            )

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
            fields.append(
                FieldEntry(
                    id=r["id"],
                    class_id=r["class_id"],
                    name=r["name"],
                    type_name=r["type_name"],
                    modifiers=r.get("modifiers", []),
                    file_path=r["file_path"],
                    line=r["line"],
                )
            )

        elif t == "callsite":
            callsites.append(
                CallSite(
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
                )
            )

        elif t == "inheritance_edge":
            inheritance_edges.append(
                InheritanceEdge(
                    id=r["id"],
                    source_class_id=r["source_class_id"],
                    source_class=r["source_class"],
                    target_class=r["target_class"],
                    relation=r["relation"],
                )
            )

        elif t == "resolved_call_edge":
            resolved_call_edges.append(
                ResolvedCallEdge(
                    id=r["id"],
                    callsite_id=r["callsite_id"],
                    caller_method_id=r["caller_method_id"],
                    callee_method_id=r["callee_method_id"],
                )
            )

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
    skip_folders: set[str] | None = None,
) -> Graph:
    """
    Build a JIDRA Graph from a TypeScript/React codebase.

    backend:
      - "auto" (default) / "tsmorph": runs the Docker ts-morph sidecar
        (full TypeScript compiler types, ~80% call resolution) over every
        TS/JS project it can find (it now discovers every `tsconfig.json`
        in the repo, not just the root), then gap-fills with in-process
        tree-sitter (~65%) for any source files the sidecar didn't cover
        (e.g. plain JS with no tsconfig at all, or a sidecar run that
        failed outright). The two extractors never process the same file
        twice, so merging their output is safe. If Docker isn't available
        at all, falls back to tree-sitter for the whole repo.
      - "treesitter": in-process tree-sitter only, no Docker, no gap-fill.
    """
    if backend == "treesitter":
        from .ts_treesitter import build_ts_graph_treesitter

        return build_ts_graph_treesitter(
            codebase_root, on_progress, skip_folders=skip_folders
        )

    # "auto" and "tsmorph" both run the sidecar and gap-fill with
    # tree-sitter. They differ only in failure mode: "tsmorph" propagates a
    # hard Docker/sidecar failure (the caller explicitly asked for it),
    # "auto" falls back to tree-sitter-only on that same failure.
    try:
        if on_progress:
            on_progress(0)

        records = _run_sidecar(codebase_root, timeout=timeout)
    except TsExtractorError:
        if backend == "tsmorph":
            raise

        import sys

        print(
            "jidra: Docker ts-morph sidecar unavailable; falling back to "
            "in-process tree-sitter (lower-quality call resolution, ~65% vs "
            "~80%). Install/start Docker to use the higher-accuracy sidecar.",
            file=sys.stderr,
        )

        from .ts_treesitter import build_ts_graph_treesitter

        return build_ts_graph_treesitter(
            codebase_root, on_progress, skip_folders=skip_folders
        )

    if on_progress:
        on_progress(len([r for r in records if r.get("_type") == "class"]))

    sidecar_graph = _build_graph_from_records(records)
    if skip_folders:
        sidecar_graph = _filter_graph_by_skip_folders(
            sidecar_graph, codebase_root, skip_folders
        )

    gap_files = _compute_gap_files(codebase_root, records, skip_folders)

    if not gap_files:
        return sidecar_graph

    from .ts_treesitter import build_ts_graph_treesitter

    gap_graph = build_ts_graph_treesitter(
        codebase_root, on_progress, only_files=gap_files
    )
    return _merge_ts_graphs(sidecar_graph, gap_graph)


def _compute_gap_files(
    codebase_root: Path, records: list[dict], skip_folders: set[str] | None = None
) -> set[Path]:
    """Files the sidecar didn't cover, for tree-sitter gap-fill.

    The sidecar emits one `covered_files` record listing every source file
    it actually processed (repo-relative, posix-style). Anything in the
    repo's full TS/JS file set that isn't in that list — plain JS with no
    tsconfig, a file outside every discovered tsconfig's `include`, etc. —
    gets handed to tree-sitter instead. `skip_folders` is applied here too,
    so a user-excluded folder doesn't get gap-filled back in.
    """
    from .ts_treesitter import _iter_ts_files

    covered_rel: set[str] = set()
    for r in records:
        if r.get("_type") == "covered_files":
            covered_rel.update(r.get("files", []))

    covered_abs = {(codebase_root / rel).resolve() for rel in covered_rel}
    all_files = {
        p.resolve() for p in _iter_ts_files(codebase_root, skip_folders=skip_folders)
    }
    return all_files - covered_abs


def _filter_graph_by_skip_folders(
    graph: Graph, codebase_root: Path, skip_folders: set[str]
) -> Graph:
    """Drop sidecar-extracted entities under a user-excluded folder.

    The Docker sidecar discovers and processes its own files via tsconfig
    resolution inside the container — it has no knowledge of `skip_folders`.
    Rather than degrade its file discovery by constraining it to a
    host-computed allowlist, filter its output by `file_path` after the
    fact, the same way `excluded_by_skip_folders` would for any other
    language's file list.
    """
    from ..filters.file_filters import excluded_by_skip_folders

    all_file_paths = {
        e.file_path for e in graph.classes + graph.fields + graph.callsites
    } | {m.file_path for m in graph.methods}
    abs_paths = [(codebase_root / fp).resolve() for fp in all_file_paths]
    excluded_abs = excluded_by_skip_folders(abs_paths, codebase_root, skip_folders)
    root_resolved = codebase_root.resolve()
    excluded = {p.relative_to(root_resolved).as_posix() for p in excluded_abs}
    if not excluded:
        return graph

    kept_classes = [c for c in graph.classes if c.file_path not in excluded]
    kept_methods = [m for m in graph.methods if m.file_path not in excluded]
    kept_class_full_names = {c.full_name for c in kept_classes}
    kept_method_ids = {m.id for m in kept_methods}

    return Graph(
        classes=kept_classes,
        methods=kept_methods,
        fields=[f for f in graph.fields if f.file_path not in excluded],
        callsites=[c for c in graph.callsites if c.file_path not in excluded],
        inheritance_edges=[
            e
            for e in graph.inheritance_edges
            if e.source_class in kept_class_full_names
        ],
        resolved_call_edges=[
            e
            for e in graph.resolved_call_edges
            if e.caller_method_id in kept_method_ids
            and e.callee_method_id in kept_method_ids
        ],
    )


def _merge_ts_graphs(a: Graph, b: Graph) -> Graph:
    """Concatenate two TS graphs that cover disjoint file sets.

    Safe only because the sidecar and tree-sitter gap-fill never process
    the same file — there's no ID collision or duplicate-entity risk to
    dedupe here, unlike the general multi-language `_merge_graphs`.
    """
    return Graph(
        classes=a.classes + b.classes,
        methods=a.methods + b.methods,
        fields=a.fields + b.fields,
        callsites=a.callsites + b.callsites,
        inheritance_edges=a.inheritance_edges + b.inheritance_edges,
        resolved_call_edges=a.resolved_call_edges + b.resolved_call_edges,
    )


def build_ts_graph_for_files(files: set[Path], codebase_root: Path) -> Graph:
    """Build Graph for specific TS/TSX files. Used by incremental reindex.

    Uses the Docker sidecar which already accepts a file subset.
    """
    records = _run_sidecar(codebase_root, files=files)
    return _build_graph_from_records(records)
