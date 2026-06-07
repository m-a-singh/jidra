from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import to_dict

SCHEMA_VERSION = "1.0"


def export_jsonl(path: Path, records: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(records)
    with path.open("w", encoding="utf-8") as fh:
        for record in rows:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")


def graph_records(graph) -> list[dict]:
    out: list[dict] = []
    class_by_id = {c.id: c for c in graph.classes}
    method_by_id = {m.id: m for m in graph.methods}
    called_by: dict[str, list[str]] = {}
    for edge in graph.resolved_call_edges:
        called_by.setdefault(edge.callee_method_id, []).append(edge.caller_method_id)

    for cls in graph.classes:
        payload = to_dict(cls)
        source_set = "test" if "/src/test/" in cls.file_path.replace("\\", "/") else "main"
        out.append(
            {
                "schema_version": SCHEMA_VERSION,
                "node_type": "class",
                "id": cls.id,
                "name": cls.name,
                "qualified_name": cls.full_name,
                "file_path": cls.file_path,
                "source_set": source_set,
                "package": cls.package_name,
                "imports": cls.imports,
                "annotations": cls.annotations,
                "class_kind": "class",
                "stereotypes": cls.stereotypes,
                "methods": [m.id for m in graph.methods if m.class_id == cls.id],
                "fields": [f.id for f in graph.fields if f.class_id == cls.id],
                "type": "class",
                "payload": payload,
            }
        )
    for field in graph.fields:
        payload = to_dict(field)
        owner = class_by_id.get(field.class_id)
        qname = f"{owner.full_name}.{field.name}" if owner else field.name
        source_set = "test" if "/src/test/" in field.file_path.replace("\\", "/") else "main"
        out.append(
            {
                "schema_version": SCHEMA_VERSION,
                "node_type": "field",
                "id": field.id,
                "name": field.name,
                "qualified_name": qname,
                "file_path": field.file_path,
                "source_set": source_set,
                "type_name": field.type_name,
                "type": "field",
                "payload": payload,
            }
        )
    for method in graph.methods:
        payload = to_dict(method)
        source_set = "test" if "/src/test/" in method.file_path.replace("\\", "/") else "main"
        class_entry = class_by_id.get(method.class_id)
        params = [
            {"name": n, "type": t} for n, t in zip(method.parameter_names, method.parameter_types)
        ]
        call_items = [c for c in graph.callsites if c.caller_method_id == method.id]
        calls = []
        for c in call_items:
            target_id = c.resolved_candidates[0] if c.resolved_candidates else None
            target_qn = (
                method_by_id[target_id].signature
                if target_id and target_id in method_by_id
                else None
            )
            calls.append(
                {
                    "name": c.callee_name,
                    "receiver": c.receiver,
                    "target_id": target_id,
                    "target_qualified_name": target_qn,
                    "resolution": c.receiver_resolution_source or "unresolved",
                    "confidence": 1.0
                    if (c.resolution_status or "").startswith("resolved")
                    else 0.5,
                }
            )
        out.append(
            {
                "schema_version": SCHEMA_VERSION,
                "node_type": "method",
                "id": method.id,
                "name": method.method_name,
                "qualified_name": method.signature,
                "file_path": method.file_path,
                "source_set": source_set,
                "class_name": class_entry.name
                if class_entry
                else method.class_full_name.split(".")[-1],
                "qualified_class_name": method.class_full_name,
                "method_name": method.method_name,
                "signature": method.signature,
                "return_type": method.return_type,
                "parameters": params,
                "annotations": method.annotations,
                "visibility": "public" if "public" in payload.get("modifiers", []) else "package",
                "is_static": "static" in payload.get("modifiers", []),
                "is_constructor": method.method_name
                == (class_entry.name if class_entry else method.method_name),
                "start_line": method.start_line,
                "end_line": method.end_line,
                "source_preview": method.source[:400],
                "is_endpoint": method.is_endpoint,
                "http_method": method.http_method,
                "route": method.route,
                "controller_route": method.controller_route,
                "full_route": method.full_route,
                "calls": calls,
                "called_by": sorted(set(called_by.get(method.id, []))),
                "type": "method",
                "payload": payload,
            }
        )
    for call in graph.callsites:
        payload = to_dict(call)
        source_set = "test" if "/src/test/" in call.file_path.replace("\\", "/") else "main"
        out.append(
            {
                "schema_version": SCHEMA_VERSION,
                "node_type": "callsite",
                "id": call.id,
                "name": call.callee_name,
                "qualified_name": call.text,
                "file_path": call.file_path,
                "source_set": source_set,
                "type": "callsite",
                "payload": payload,
            }
        )
    for edge in graph.inheritance_edges:
        payload = to_dict(edge)
        out.append(
            {
                "schema_version": SCHEMA_VERSION,
                "node_type": "edge",
                "id": edge.id,
                "name": edge.relation,
                "qualified_name": f"{edge.source_class}->{edge.target_class}",
                "file_path": "",
                "source_set": "main",
                "edge_type": edge.relation,
                "from_id": edge.source_class_id,
                "to_id": edge.target_class,
                "confidence": 1.0,
                "source": "parser",
                "type": "inheritance_edge",
                "payload": payload,
            }
        )
    for edge in graph.resolved_call_edges:
        payload = to_dict(edge)
        out.append(
            {
                "schema_version": SCHEMA_VERSION,
                "node_type": "edge",
                "id": edge.id,
                "name": "resolved_call",
                "qualified_name": f"{edge.caller_method_id}->{edge.callee_method_id}",
                "file_path": "",
                "source_set": "main",
                "edge_type": "resolved_call",
                "from_id": edge.caller_method_id,
                "to_id": edge.callee_method_id,
                "confidence": 1.0,
                "source": "parser",
                "type": "resolved_call_edge",
                "payload": payload,
            }
        )

    return out


def _normalized_record_path(record: dict) -> str:
    payload = record.get("payload") or record
    raw = str(payload.get("file_path") or payload.get("path") or "")
    return raw.replace("\\", "/")


def split_graph_records_by_source(records: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    prod: list[dict] = []
    test: list[dict] = []

    buffered = list(records)
    class_in_prod: set[str] = set()
    class_in_test: set[str] = set()
    method_in_prod: set[str] = set()
    method_in_test: set[str] = set()
    callsite_in_prod: set[str] = set()
    callsite_in_test: set[str] = set()

    saw_main = False
    saw_test = False

    for record in buffered:
        typ = record.get("type")
        payload = record.get("payload", {})
        path = _normalized_record_path(record)
        if "/src/main/" in path:
            saw_main = True
            if typ == "class":
                class_in_prod.add(payload.get("id"))
            elif typ == "method":
                method_in_prod.add(payload.get("id"))
            elif typ == "callsite":
                callsite_in_prod.add(payload.get("id"))
        elif "/src/test/" in path:
            saw_test = True
            if typ == "class":
                class_in_test.add(payload.get("id"))
            elif typ == "method":
                method_in_test.add(payload.get("id"))
            elif typ == "callsite":
                callsite_in_test.add(payload.get("id"))

    if not saw_main and not saw_test:
        return buffered, []

    for record in buffered:
        typ = record.get("type")
        payload = record.get("payload", {})
        path = _normalized_record_path(record)

        in_prod = "/src/main/" in path
        in_test = "/src/test/" in path

        if not in_prod and not in_test:
            if typ == "field":
                class_id = payload.get("class_id")
                in_prod = class_id in class_in_prod
                in_test = class_id in class_in_test
            elif typ == "callsite":
                caller = payload.get("caller_method_id")
                in_prod = caller in method_in_prod
                in_test = caller in method_in_test
            elif typ == "inheritance_edge":
                source = payload.get("source_class_id")
                in_prod = source in class_in_prod
                in_test = source in class_in_test
            elif typ == "resolved_call_edge":
                caller = payload.get("caller_method_id")
                callsite_id = payload.get("callsite_id")
                in_prod = caller in method_in_prod or callsite_id in callsite_in_prod
                in_test = caller in method_in_test or callsite_id in callsite_in_test

        if in_prod and not in_test:
            prod.append(record)
        elif in_test and not in_prod:
            test.append(record)

    return prod, test
