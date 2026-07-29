"""
C# code extractor using tree-sitter.

Phase A:
- Classes, interfaces, structs, records (with namespace resolution)
- Methods, constructors, properties (as graph nodes)
- Fields
- Basic call resolution via local symbol table (params + fields + local vars)

Phase B (deferred):
- Partial class merging
- Extension methods
- Generic type parameter inference
- using static

Known limitations:
- Cross-assembly (NuGet) types remain unresolved
- Partial classes treated as independent entities
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..engine.parallel import parallel_map
from ..filters.cs_filters import iter_cs_files
from ..models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    ResolvedCallEdge,
    callsite_id,
    class_id,
    field_id,
    inheritance_edge_id,
    method_id,
    method_signature,
    resolved_call_edge_id,
)
from ..utils.parser import make_cs_parser

# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _field(node, name: str):
    return node.child_by_field_name(name)


def _children_by_type(node, type_name: str) -> list:
    return [c for c in node.children if c.type == type_name]


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def _collect(node, type_name: str) -> list:
    return [n for n in _walk(node) if n.type == type_name]


# ---------------------------------------------------------------------------
# C# semantic helpers
# ---------------------------------------------------------------------------

_TYPE_DECL_NODE_TYPES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "record_declaration",
        "record_struct_declaration",
    }
)


def _extract_modifiers(node, source: bytes) -> list[str]:
    return [_text(c, source) for c in node.children if c.type == "modifier"]


def _get_type_text(type_node, source: bytes) -> str:
    if type_node is None:
        return "unknown"
    return _text(type_node, source).strip()


def _arg_count(args_node) -> int:
    if args_node is None:
        return 0
    return len([c for c in args_node.children if c.type == "argument"])


def _extract_param_types(params_node, source: bytes) -> tuple[list[str], list[str]]:
    if params_node is None:
        return [], []
    types: list[str] = []
    names: list[str] = []
    for param in _collect(params_node, "parameter"):
        type_node = _field(param, "type")
        name_node = _field(param, "name")
        types.append(_get_type_text(type_node, source) if type_node else "unknown")
        names.append(_text(name_node, source) if name_node else "")
    return types, names


def _parse_base_list(bases_node, source: bytes) -> list[str]:
    """Extract base type names from a base_list node."""
    if bases_node is None:
        return []
    result: list[str] = []
    for child in bases_node.children:
        if child.type in ("identifier", "generic_name", "qualified_name"):
            result.append(_text(child, source))
        elif child.type == "base_list":
            result.extend(_parse_base_list(child, source))
    return result


def _file_scoped_namespace(root, source: bytes) -> str:
    """Return the namespace name from a file-scoped namespace declaration, if present."""
    for child in root.children:
        if child.type == "file_scoped_namespace_declaration":
            name_node = _field(child, "name")
            if name_node:
                return _text(name_node, source)
    return ""


# ---------------------------------------------------------------------------
# Pass 1: extract types (classes, fields, inheritance edges)
# ---------------------------------------------------------------------------


def _collect_type_decls(
    node,
    source: bytes,
    file_path: str,
    namespace: str,
    parent_full_name: str | None,
) -> tuple[list[ClassEntry], list[FieldEntry], list[InheritanceEdge], dict[str, ClassEntry]]:
    """Recursively collect type declarations from a syntax subtree."""
    classes: list[ClassEntry] = []
    fields: list[FieldEntry] = []
    edges: list[InheritanceEdge] = []
    class_by_name: dict[str, ClassEntry] = {}

    for child in node.children:
        if child.type in _TYPE_DECL_NODE_TYPES:
            name_node = _field(child, "name")
            if name_node is None:
                continue
            name = _text(name_node, source)

            if parent_full_name:
                full_name = f"{parent_full_name}.{name}"
            elif namespace:
                full_name = f"{namespace}.{name}"
            else:
                full_name = name

            modifiers = _extract_modifiers(child, source)
            stereotypes: list[str] = []
            if child.type == "interface_declaration":
                stereotypes.append("interface")
            elif child.type in ("struct_declaration", "record_struct_declaration"):
                stereotypes.append("struct")
            elif child.type == "record_declaration":
                stereotypes.append("record")
            else:
                stereotypes.append("class")
            if "abstract" in modifiers:
                stereotypes.append("abstract")
            if "static" in modifiers:
                stereotypes.append("static")
            if "sealed" in modifiers:
                stereotypes.append("sealed")

            bases_node = _field(child, "bases")
            base_types = _parse_base_list(bases_node, source)

            cls = ClassEntry(
                id=class_id(full_name, file_path),
                package_name=namespace,
                name=name,
                full_name=full_name,
                file_path=file_path,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                modifiers=modifiers,
                annotations=[],
                extends=None,
                implements=base_types,
                imports=[],
                stereotypes=stereotypes,
                language="csharp",
            )
            classes.append(cls)
            class_by_name[name] = cls

            for base in base_types:
                target = base.split("<")[0]
                edges.append(
                    InheritanceEdge(
                        id=inheritance_edge_id(full_name, target, "implements"),
                        source_class_id=cls.id,
                        source_class=full_name,
                        target_class=base,
                        relation="implements",
                    )
                )

            body = _field(child, "body")
            if body is not None:
                for fnode in _children_by_type(body, "field_declaration"):
                    fmods = _extract_modifiers(fnode, source)
                    var_decl = next(
                        (c for c in fnode.children if c.type == "variable_declaration"),
                        None,
                    )
                    if var_decl is None:
                        continue
                    type_node = _field(var_decl, "type")
                    type_text = _get_type_text(type_node, source)
                    for decl in _collect(var_decl, "variable_declarator"):
                        fname_node = _field(decl, "name")
                        if fname_node is None:
                            continue
                        fname = _text(fname_node, source)
                        fline = decl.start_point[0] + 1
                        fields.append(
                            FieldEntry(
                                id=field_id(full_name, fname, file_path, fline),
                                class_id=cls.id,
                                name=fname,
                                type_name=type_text,
                                modifiers=fmods,
                                file_path=file_path,
                                line=fline,
                            )
                        )

                nc, nf, ne, nm = _collect_type_decls(body, source, file_path, namespace, full_name)
                classes.extend(nc)
                fields.extend(nf)
                edges.extend(ne)
                class_by_name.update(nm)

        elif child.type == "namespace_declaration":
            name_node = _field(child, "name")
            ns = _text(name_node, source) if name_node else namespace
            body = _field(child, "body")
            if body is not None:
                nc, nf, ne, nm = _collect_type_decls(body, source, file_path, ns, None)
                classes.extend(nc)
                fields.extend(nf)
                edges.extend(ne)
                class_by_name.update(nm)

        # file_scoped_namespace_declaration has no body — handled via _file_scoped_namespace

    return classes, fields, edges, class_by_name


def _parse_and_extract_types(
    file_path: Path,
) -> tuple[Path, list[ClassEntry], list[FieldEntry], list[InheritanceEdge], dict[str, ClassEntry]]:
    """Pass-1 worker: parse one file and extract types. Returns picklable data only."""
    parser = make_cs_parser()
    source = file_path.read_bytes()
    root = parser.parse(source).root_node
    namespace = _file_scoped_namespace(root, source)
    classes, fields, edges, local_map = _collect_type_decls(
        root, source, str(file_path), namespace, None
    )
    return file_path, classes, fields, edges, local_map


# ---------------------------------------------------------------------------
# Pass 2: extract methods using global class map
# ---------------------------------------------------------------------------


class _PendingBody:
    __slots__ = (
        "block",
        "class_full_name",
        "field_types",
        "file_path",
        "method",
        "param_types",
        "source",
    )

    def __init__(
        self,
        method: MethodEntry,
        block,
        field_types: dict[str, str],
        param_types: dict[str, str],
        source: bytes,
        file_path: str,
        class_full_name: str,
    ) -> None:
        self.method = method
        self.block = block
        self.field_types = field_types
        self.param_types = param_types
        self.source = source
        self.file_path = file_path
        self.class_full_name = class_full_name


class _FileMeta:
    __slots__ = ("file_path", "root", "source")

    def __init__(self, file_path: Path, root, source: bytes) -> None:
        self.file_path = file_path
        self.root = root
        self.source = source


def _parse_file(file_path: Path, parser) -> _FileMeta:
    source = file_path.read_bytes()
    root = parser.parse(source).root_node
    return _FileMeta(file_path, root, source)


def _extract_methods_for_class(
    body_node,
    source: bytes,
    file_path: str,
    cls: ClassEntry,
    field_types: dict[str, str],
) -> tuple[list[MethodEntry], list[_PendingBody]]:
    methods: list[MethodEntry] = []
    pending: list[_PendingBody] = []

    if body_node is None:
        return methods, pending

    for child in body_node.children:
        if child.type == "method_declaration":
            type_node = _field(child, "type")
            name_node = _field(child, "name")
            params_node = _field(child, "parameters")
            body = _field(child, "body")
            if name_node is None:
                continue

            method_name = _text(name_node, source)
            return_type = _get_type_text(type_node, source) if type_node else "void"
            param_types, param_names = _extract_param_types(params_node, source)
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            sig = method_signature(cls.full_name, method_name, param_types)
            mid = method_id(sig, file_path, start_line)

            m = MethodEntry(
                id=mid,
                class_id=cls.id,
                class_full_name=cls.full_name,
                method_name=method_name,
                return_type=return_type,
                parameter_types=param_types,
                parameter_names=param_names,
                signature=sig,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                source=_text(child, source),
                class_context={},
                language="csharp",
            )
            methods.append(m)

            if body is not None:
                pending.append(
                    _PendingBody(
                        m,
                        body,
                        field_types,
                        dict(zip(param_names, param_types)),
                        source,
                        file_path,
                        cls.full_name,
                    )
                )

        elif child.type == "constructor_declaration":
            name_node = _field(child, "name")
            params_node = _field(child, "parameters")
            body = _field(child, "body")
            if name_node is None:
                continue

            method_name = _text(name_node, source)
            param_types, param_names = _extract_param_types(params_node, source)
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            sig = method_signature(cls.full_name, method_name, param_types)
            mid = method_id(sig, file_path, start_line)

            m = MethodEntry(
                id=mid,
                class_id=cls.id,
                class_full_name=cls.full_name,
                method_name=method_name,
                return_type=cls.name,
                parameter_types=param_types,
                parameter_names=param_names,
                signature=sig,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                source=_text(child, source),
                class_context={},
                language="csharp",
            )
            methods.append(m)

            if body is not None:
                pending.append(
                    _PendingBody(
                        m,
                        body,
                        field_types,
                        dict(zip(param_names, param_types)),
                        source,
                        file_path,
                        cls.full_name,
                    )
                )

        elif child.type == "property_declaration":
            type_node = _field(child, "type")
            name_node = _field(child, "name")
            if name_node is None:
                continue

            prop_name = _text(name_node, source)
            prop_type = _get_type_text(type_node, source) if type_node else "unknown"
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            sig = method_signature(cls.full_name, f"get_{prop_name}", [])
            mid = method_id(sig, file_path, start_line)

            methods.append(
                MethodEntry(
                    id=mid,
                    class_id=cls.id,
                    class_full_name=cls.full_name,
                    method_name=f"get_{prop_name}",
                    return_type=prop_type,
                    parameter_types=[],
                    parameter_names=[],
                    signature=sig,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    source=_text(child, source),
                    class_context={},
                    language="csharp",
                )
            )

    return methods, pending


def _extract_methods_for_file(
    meta: _FileMeta,
    class_map: dict[tuple[str, str], ClassEntry],
    fields_by_class_id: dict[str, list[FieldEntry]],
) -> tuple[list[MethodEntry], list[_PendingBody]]:
    source = meta.source
    file_path = str(meta.file_path)
    all_methods: list[MethodEntry] = []
    all_pending: list[_PendingBody] = []

    for type_node in [n for n in _walk(meta.root) if n.type in _TYPE_DECL_NODE_TYPES]:
        name_node = _field(type_node, "name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        cls = class_map.get((file_path, name))
        if cls is None:
            continue
        body = _field(type_node, "body")
        if body is None:
            continue
        field_types = {f.name: f.type_name for f in fields_by_class_id.get(cls.id, [])}
        methods, pending = _extract_methods_for_class(body, source, file_path, cls, field_types)
        all_methods.extend(methods)
        all_pending.extend(pending)

    return all_methods, all_pending


# ---------------------------------------------------------------------------
# Pass 3: call resolution
# ---------------------------------------------------------------------------


def _infer_local_types(block_node, source: bytes, param_types: dict[str, str]) -> dict[str, str]:
    local_types = dict(param_types)

    for stmt in _collect(block_node, "local_declaration_statement"):
        var_decl = next((c for c in stmt.children if c.type == "variable_declaration"), None)
        if var_decl is None:
            continue
        type_node = _field(var_decl, "type")
        if type_node is None:
            continue
        type_text = _get_type_text(type_node, source)
        if type_text == "var":
            continue
        for decl in _collect(var_decl, "variable_declarator"):
            name_node = _field(decl, "name")
            if name_node:
                local_types[_text(name_node, source)] = type_text

    for foreach in _collect(block_node, "for_each_statement"):
        type_node = _field(foreach, "type")
        name_node = _field(foreach, "left")
        if type_node and name_node:
            type_text = _get_type_text(type_node, source)
            if type_text != "var":
                local_types[_text(name_node, source)] = type_text

    return local_types


def _resolve_calls(graph: Graph, pending: list[_PendingBody]) -> None:
    methods_by_class_and_name: dict[tuple[str, str], list[MethodEntry]] = {}
    for m in graph.methods:
        methods_by_class_and_name.setdefault((m.class_full_name, m.method_name), []).append(m)

    classes_by_short_name: dict[str, list[ClassEntry]] = {}
    for c in graph.classes:
        classes_by_short_name.setdefault(c.name, []).append(c)

    callsites: list[CallSite] = []
    resolved_edges: list[ResolvedCallEdge] = []

    for p in pending:
        local_types = _infer_local_types(p.block, p.source, p.param_types)
        all_var_types: dict[str, str] = {**p.field_types, **local_types}

        for call_node in _collect(p.block, "invocation_expression"):
            func_node = _field(call_node, "function")
            args_node = _field(call_node, "arguments")
            if func_node is None:
                continue

            arg_count = _arg_count(args_node)
            line = call_node.start_point[0] + 1
            col = call_node.start_point[1] + 1

            receiver_text: str | None = None
            receiver_type_raw: str | None = None
            callee_name = ""
            candidates: list[MethodEntry] = []

            if func_node.type == "member_access_expression":
                expr_node = _field(func_node, "expression")
                name_node = _field(func_node, "name")
                if name_node is None:
                    continue
                callee_name = _text(name_node, p.source)

                if expr_node is not None:
                    receiver_text = _text(expr_node, p.source)
                    if expr_node.type == "this_expression":
                        receiver_type_raw = p.class_full_name.split(".")[-1]
                    elif expr_node.type == "identifier":
                        var_name = _text(expr_node, p.source)
                        if var_name == "this":
                            receiver_type_raw = p.class_full_name.split(".")[-1]
                        else:
                            receiver_type_raw = all_var_types.get(var_name)

                if receiver_type_raw:
                    short = receiver_type_raw.split(".")[-1].split("<")[0]
                    for owner in classes_by_short_name.get(short, []):
                        candidates.extend(
                            methods_by_class_and_name.get((owner.full_name, callee_name), [])
                        )

            elif func_node.type == "identifier":
                callee_name = _text(func_node, p.source)
                candidates.extend(
                    methods_by_class_and_name.get((p.class_full_name, callee_name), [])
                )
                receiver_type_raw = p.class_full_name.split(".")[-1]
                receiver_text = "this"

            else:
                continue

            if not callee_name:
                continue

            cid = callsite_id(p.method.id, line, col, callee_name)
            resolved_ids = [c.id for c in candidates]

            if len(candidates) == 1:
                status = "resolved_exact"
                reason = "receiver type resolved via local symbol table"
            elif len(candidates) > 1:
                status = "resolved_ambiguous"
                reason = f"{len(candidates)} candidates matched by name"
            else:
                status = "unresolved"
                reason = "no matching method found in indexed types"

            callsites.append(
                CallSite(
                    id=cid,
                    caller_method_id=p.method.id,
                    callee_name=callee_name,
                    receiver=receiver_text,
                    argument_count=arg_count,
                    file_path=p.file_path,
                    line=line,
                    column=col,
                    text=_text(call_node, p.source),
                    receiver_type_raw=receiver_type_raw,
                    receiver_type_normalized=receiver_type_raw,
                    receiver_resolution_source=(
                        "local_symbol_table" if receiver_type_raw else None
                    ),
                    receiver_type=receiver_type_raw,
                    resolved_candidates=resolved_ids,
                    resolution_status=status,
                    resolution_reason=reason,
                    candidate_count=len(candidates),
                )
            )
            for cm in candidates:
                resolved_edges.append(
                    ResolvedCallEdge(
                        id=resolved_call_edge_id(cid, cm.id),
                        callsite_id=cid,
                        caller_method_id=p.method.id,
                        callee_method_id=cm.id,
                    )
                )

    graph.callsites = callsites
    graph.resolved_call_edges = resolved_edges


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_cs_graph(
    codebase_root: Path,
    on_progress: Callable[[int, int], None] | None = None,
    skip_folders: set[str] | None = None,
) -> Graph:
    file_paths = list(iter_cs_files(codebase_root, skip_folders=skip_folders))

    all_classes: list[ClassEntry] = []
    all_fields: list[FieldEntry] = []
    all_edges: list[InheritanceEdge] = []
    class_map: dict[tuple[str, str], ClassEntry] = {}
    fields_by_class_id: dict[str, list[FieldEntry]] = {}

    for fp, classes, fields, edges, local_map in parallel_map(_parse_and_extract_types, file_paths):
        all_classes.extend(classes)
        all_fields.extend(fields)
        all_edges.extend(edges)
        fp_str = str(fp)
        for short_name, cls in local_map.items():
            class_map[(fp_str, short_name)] = cls
        for f in fields:
            fields_by_class_id.setdefault(f.class_id, []).append(f)

    if on_progress:
        on_progress(1, 1)

    parser = make_cs_parser()
    all_methods: list[MethodEntry] = []
    all_pending: list[_PendingBody] = []

    for fp in file_paths:
        try:
            meta = _parse_file(fp, parser)
            methods, pending = _extract_methods_for_file(meta, class_map, fields_by_class_id)
            all_methods.extend(methods)
            all_pending.extend(pending)
        except Exception:
            pass

    graph = Graph(
        classes=all_classes,
        methods=all_methods,
        fields=all_fields,
        callsites=[],
        inheritance_edges=all_edges,
        resolved_call_edges=[],
    )
    _resolve_calls(graph, all_pending)
    return graph


def build_cs_graph_for_files(
    files: set[Path],
    codebase_root: Path,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> Graph:
    """Build an unresolved C# graph for a specific file set (incremental reindex)."""
    parser = make_cs_parser()
    existing = [fp for fp in files if fp.exists()]

    all_classes: list[ClassEntry] = []
    all_fields: list[FieldEntry] = []
    all_edges: list[InheritanceEdge] = []
    class_map: dict[tuple[str, str], ClassEntry] = {}
    fields_by_class_id: dict[str, list[FieldEntry]] = {}

    for fp in existing:
        try:
            source = fp.read_bytes()
            root = parser.parse(source).root_node
            namespace = _file_scoped_namespace(root, source)
            classes, fields, edges, local_map = _collect_type_decls(
                root, source, str(fp), namespace, None
            )
            all_classes.extend(classes)
            all_fields.extend(fields)
            all_edges.extend(edges)
            fp_str = str(fp)
            for short_name, cls in local_map.items():
                class_map[(fp_str, short_name)] = cls
            for f in fields:
                fields_by_class_id.setdefault(f.class_id, []).append(f)
        except Exception as exc:
            if on_error:
                on_error(fp, exc)

    all_methods: list[MethodEntry] = []
    for fp in existing:
        try:
            meta = _parse_file(fp, parser)
            methods, _ = _extract_methods_for_file(meta, class_map, fields_by_class_id)
            all_methods.extend(methods)
        except Exception as exc:
            if on_error:
                on_error(fp, exc)

    return Graph(
        classes=all_classes,
        methods=all_methods,
        fields=all_fields,
        callsites=[],
        inheritance_edges=all_edges,
        resolved_call_edges=[],
    )
