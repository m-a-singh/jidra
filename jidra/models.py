from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha1
from typing import Any


def _stable_id(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:16]


@dataclass
class FieldEntry:
    id: str
    class_id: str
    name: str
    type_name: str
    modifiers: list[str]
    file_path: str
    line: int


@dataclass
class ClassEntry:
    id: str
    package_name: str
    name: str
    full_name: str
    file_path: str
    start_line: int
    end_line: int
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    extends: str | None = None
    implements: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    stereotypes: list[str] = field(default_factory=list)
    language: str = "unknown"


@dataclass
class MethodEntry:
    id: str
    class_id: str
    class_full_name: str
    method_name: str
    return_type: str
    parameter_types: list[str]
    parameter_names: list[str]
    signature: str
    file_path: str
    start_line: int
    end_line: int
    source: str
    class_context: dict[str, Any]
    annotations: list[str] = field(default_factory=list)
    local_variable_types: dict[str, str] = field(default_factory=dict)
    field_reads: list[str] = field(default_factory=list)
    field_writes: list[str] = field(default_factory=list)
    is_endpoint: bool = False
    http_method: str | None = None
    route: str | None = None
    controller_route: str | None = None
    full_route: str | None = None
    language: str = "unknown"


@dataclass
class CallSite:
    id: str
    caller_method_id: str
    callee_name: str
    receiver: str | None
    argument_count: int
    file_path: str
    line: int
    column: int
    text: str
    receiver_type_raw: str | None = None
    receiver_type_normalized: str | None = None
    receiver_resolution_source: str | None = None
    receiver_type: str | None = None
    resolved_candidates: list[str] = field(default_factory=list)
    resolution_status: str = "unresolved"
    resolution_reason: str = ""
    candidate_count: int = 0


@dataclass
class InheritanceEdge:
    id: str
    source_class_id: str
    source_class: str
    target_class: str
    relation: str


@dataclass
class ResolvedCallEdge:
    id: str
    callsite_id: str
    caller_method_id: str
    callee_method_id: str


@dataclass
class Graph:
    classes: list[ClassEntry]
    methods: list[MethodEntry]
    fields: list[FieldEntry]
    callsites: list[CallSite]
    inheritance_edges: list[InheritanceEdge]
    resolved_call_edges: list[ResolvedCallEdge]


def class_id(full_name: str, file_path: str) -> str:
    return _stable_id(f"class::{full_name}::{file_path}")


def method_signature(
    class_full_name: str, method_name: str, parameter_types: list[str]
) -> str:
    return f"{class_full_name}#{method_name}({', '.join(parameter_types)})"


def method_id(signature: str, file_path: str, start_line: int) -> str:
    return _stable_id(f"method::{signature}::{file_path}::{start_line}")


def field_id(class_full_name: str, field_name: str, file_path: str, line: int) -> str:
    return _stable_id(f"field::{class_full_name}#{field_name}::{file_path}::{line}")


def callsite_id(caller_method_id: str, line: int, column: int, callee_name: str) -> str:
    return _stable_id(f"call::{caller_method_id}::{line}:{column}::{callee_name}")


def inheritance_edge_id(source_class: str, target_class: str, relation: str) -> str:
    return _stable_id(f"inheritance::{source_class}::{relation}::{target_class}")


def resolved_call_edge_id(callsite_id_value: str, callee_method_id: str) -> str:
    return _stable_id(f"resolved_call::{callsite_id_value}::{callee_method_id}")


def to_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
