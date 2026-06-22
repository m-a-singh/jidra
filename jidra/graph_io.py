from __future__ import annotations

import json
from pathlib import Path

from .models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    ResolvedCallEdge,
)


def resolve_graph_paths(output: Path) -> tuple[Path, Path, Path]:
    if output.exists() and output.is_dir():
        main = output / "graph.jsonl"
        test = output / "graph_test.jsonl"
    elif output.suffix.lower() == ".jsonl":
        main = output
        test = output.parent / "graph_test.jsonl"
    else:
        main = output / "graph.jsonl"
        test = output / "graph_test.jsonl"
    return main, test, main


def _read_jsonl_text(path: Path) -> str:
    if path.suffix == ".zst":
        import zstandard

        raw = zstandard.ZstdDecompressor().decompress(path.read_bytes())
        return raw.decode("utf-8")
    return path.read_text(encoding="utf-8")


def load_graph_jsonl(path: Path) -> Graph:
    classes: list[ClassEntry] = []
    methods: list[MethodEntry] = []
    fields: list[FieldEntry] = []
    callsites: list[CallSite] = []
    inheritance_edges: list[InheritanceEdge] = []
    resolved_call_edges: list[ResolvedCallEdge] = []

    for line in _read_jsonl_text(path).splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        typ = rec.get("type") or rec.get("node_type")
        payload = rec.get("payload", {})
        if typ == "class":
            classes.append(ClassEntry(**payload))
        elif typ == "method":
            methods.append(MethodEntry(**payload))
        elif typ == "field":
            fields.append(FieldEntry(**payload))
        elif typ == "callsite":
            callsites.append(CallSite(**payload))
        elif typ == "inheritance_edge":
            inheritance_edges.append(InheritanceEdge(**payload))
        elif typ == "resolved_call_edge":
            resolved_call_edges.append(ResolvedCallEdge(**payload))

    return Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=callsites,
        inheritance_edges=inheritance_edges,
        resolved_call_edges=resolved_call_edges,
    )
