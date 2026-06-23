"""
Go code extractor using tree-sitter.

Pipeline:
1. tree-sitter parse per file
2. Structural extraction (structs/interfaces/funcs/methods/fields/embeds)
3. Global call resolution pass using a best-effort local symbol table
   (receiver/param/short-var-decl type tracking), similar in spirit and
   scope to the Python extractor's SymbolTable approach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .go_filters import iter_go_files
from .models import (
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
from .parser import make_go_parser

_TYPE_NODE_TYPES = {
    "type_identifier",
    "pointer_type",
    "qualified_type",
    "array_type",
    "slice_type",
    "map_type",
    "interface_type",
    "struct_type",
    "function_type",
    "channel_type",
    "generic_type",
}


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _child_by_type(node, type_name: str):
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _children_by_type(node, type_name: str) -> list:
    return [c for c in node.children if c.type == type_name]


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def _collect(node, type_name: str) -> list:
    return [n for n in _walk(node) if n.type == type_name]


def _strip_pointer(type_text: str) -> str:
    return type_text.lstrip("*")


def _extract_package(root, source: bytes) -> str:
    pkg = _child_by_type(root, "package_clause")
    if pkg is None:
        return ""
    ident = _child_by_type(pkg, "package_identifier")
    return _text(ident, source) if ident else ""


def _find_type_node(node):
    type_node = None
    for c in node.children:
        if c.type in _TYPE_NODE_TYPES:
            type_node = c
    return type_node


def _find_return_type(node, source: bytes) -> str:
    """Return type is the last child before `block`, if it's type-shaped."""
    children = node.children
    if not children:
        return "void"
    if children[-1].type != "block":
        last = children[-1]
        return _text(last, source) if last.type in _TYPE_NODE_TYPES else "void"
    if len(children) < 2:
        return "void"
    cand = children[-2]
    if cand.type in _TYPE_NODE_TYPES:
        return _text(cand, source)
    return "void"


def _param_types_and_names(parameter_list_node, source: bytes) -> tuple[list[str], list[str]]:
    types: list[str] = []
    names: list[str] = []
    if parameter_list_node is None:
        return types, names
    for p in _children_by_type(parameter_list_node, "parameter_declaration"):
        idents = _children_by_type(p, "identifier")
        type_node = _find_type_node(p)
        type_text = _text(type_node, source) if type_node else "unknown"
        if idents:
            for ident in idents:
                names.append(_text(ident, source))
                types.append(type_text)
        else:
            names.append("")
            types.append(type_text)
    return types, names


def _arg_count(args_node) -> int:
    if args_node is None:
        return 0
    return len([c for c in args_node.children if c.type not in ("(", ")", ",")])


def _extract_struct_fields(field_list_node, source: bytes, cls: "ClassEntry") -> list[FieldEntry]:
    fields: list[FieldEntry] = []
    for fd in _children_by_type(field_list_node, "field_declaration"):
        idents = _children_by_type(fd, "field_identifier")
        type_node = _find_type_node(fd)
        type_text = _text(type_node, source) if type_node else "unknown"
        line = fd.start_point[0] + 1
        if idents:
            for ident in idents:
                fname = _text(ident, source)
                fields.append(
                    FieldEntry(
                        id=field_id(cls.full_name, fname, cls.file_path, line),
                        class_id=cls.id,
                        name=fname,
                        type_name=type_text,
                        modifiers=[],
                        file_path=cls.file_path,
                        line=line,
                    )
                )
        else:
            embedded_name = _strip_pointer(type_text).split(".")[-1]
            if not embedded_name:
                continue
            fields.append(
                FieldEntry(
                    id=field_id(cls.full_name, embedded_name, cls.file_path, line),
                    class_id=cls.id,
                    name=embedded_name,
                    type_name=_strip_pointer(type_text),
                    modifiers=["embedded"],
                    file_path=cls.file_path,
                    line=line,
                )
            )
    return fields


def _extract_classes_and_fields(
    root, source: bytes, file_path: str, package_name: str
) -> tuple[list[ClassEntry], list[FieldEntry], list[InheritanceEdge], dict[str, ClassEntry]]:
    classes: list[ClassEntry] = []
    fields: list[FieldEntry] = []
    inheritance_edges: list[InheritanceEdge] = []
    class_by_name: dict[str, ClassEntry] = {}

    for spec in _collect(root, "type_spec"):
        ident = _child_by_type(spec, "type_identifier")
        if ident is None:
            continue
        name = _text(ident, source)
        full_name = f"{package_name}.{name}" if package_name else name

        struct_t = _child_by_type(spec, "struct_type")
        iface_t = _child_by_type(spec, "interface_type")
        if struct_t is None and iface_t is None:
            continue  # type alias to a non-struct/interface type — out of scope for v1

        stereotypes = ["struct"] if struct_t is not None else ["interface"]
        cls = ClassEntry(
            id=class_id(full_name, file_path),
            package_name=package_name,
            name=name,
            full_name=full_name,
            file_path=file_path,
            start_line=spec.start_point[0] + 1,
            end_line=spec.end_point[0] + 1,
            modifiers=[],
            annotations=[],
            extends=None,
            implements=[],
            imports=[],
            stereotypes=stereotypes,
            language="go",
        )
        classes.append(cls)
        class_by_name[name] = cls

        if struct_t is not None:
            field_list = _child_by_type(struct_t, "field_declaration_list")
            if field_list is not None:
                struct_fields = _extract_struct_fields(field_list, source, cls)
                fields.extend(struct_fields)
                for f in struct_fields:
                    if "embedded" in f.modifiers:
                        inheritance_edges.append(
                            InheritanceEdge(
                                id=inheritance_edge_id(full_name, f.type_name, "embeds"),
                                source_class_id=cls.id,
                                source_class=full_name,
                                target_class=f.type_name,
                                relation="embeds",
                            )
                        )

    return classes, fields, inheritance_edges, class_by_name


class _PendingBody:
    """A function/method body queued up for the global call-resolution pass."""

    __slots__ = ("method", "block", "local_types", "source", "file_path", "package_name")

    def __init__(self, method, block, local_types, source, file_path, package_name):
        self.method = method
        self.block = block
        self.local_types = local_types
        self.source = source
        self.file_path = file_path
        self.package_name = package_name


def _extract_methods_and_functions(
    root,
    source: bytes,
    file_path: str,
    package_name: str,
    package_scope: str,
    class_by_name: dict[str, ClassEntry],
) -> tuple[list[MethodEntry], list[ClassEntry], list[_PendingBody]]:
    """package_scope identifies the actual Go package (by directory) so that
    same-named packages declared in unrelated directories (common in vendored
    code and test fixtures) don't get treated as one package for call resolution.
    """
    methods: list[MethodEntry] = []
    pending: list[_PendingBody] = []
    module_class: ClassEntry | None = None

    def get_module_class() -> ClassEntry:
        nonlocal module_class
        if module_class is None:
            mfull = f"{package_name}._functions" if package_name else "_functions"
            module_class = ClassEntry(
                id=class_id(mfull, file_path),
                package_name=package_scope,
                name="_functions",
                full_name=mfull,
                file_path=file_path,
                start_line=1,
                end_line=1,
                modifiers=[],
                annotations=[],
                stereotypes=["module"],
                language="go",
            )
        return module_class

    for node in _collect(root, "method_declaration"):
        param_lists = _children_by_type(node, "parameter_list")
        name_node = _child_by_type(node, "field_identifier")
        if len(param_lists) < 1 or name_node is None:
            continue
        recv_list = param_lists[0]
        recv_decl = _child_by_type(recv_list, "parameter_declaration")
        if recv_decl is None:
            continue
        recv_idents = _children_by_type(recv_decl, "identifier")
        recv_var = _text(recv_idents[0], source) if recv_idents else ""
        recv_type_node = _find_type_node(recv_decl)
        if recv_type_node is None:
            continue
        recv_type = _strip_pointer(_text(recv_type_node, source))

        owner_cls = class_by_name.get(recv_type)
        if owner_cls is None:
            continue

        method_name = _text(name_node, source)
        params_node = param_lists[1] if len(param_lists) > 1 else None
        param_types, param_names = _param_types_and_names(params_node, source)
        return_type = _find_return_type(node, source)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        sig = method_signature(owner_cls.full_name, method_name, param_types)
        mid = method_id(sig, file_path, start_line)

        m_entry = MethodEntry(
            id=mid,
            class_id=owner_cls.id,
            class_full_name=owner_cls.full_name,
            method_name=method_name,
            return_type=return_type,
            parameter_types=param_types,
            parameter_names=param_names,
            signature=sig,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            source=_text(node, source),
            class_context={},
            language="go",
        )
        methods.append(m_entry)

        block = _child_by_type(node, "block")
        if block is not None:
            local_types = {}
            if recv_var:
                local_types[recv_var] = recv_type
            for pname, ptype in zip(param_names, param_types):
                if pname:
                    local_types[pname] = _strip_pointer(ptype)
            pending.append(_PendingBody(m_entry, block, local_types, source, file_path, package_scope))

    for node in _collect(root, "function_declaration"):
        name_node = _child_by_type(node, "identifier")
        if name_node is None:
            continue
        method_name = _text(name_node, source)
        param_lists = _children_by_type(node, "parameter_list")
        params_node = param_lists[0] if param_lists else None
        param_types, param_names = _param_types_and_names(params_node, source)
        return_type = _find_return_type(node, source)
        mclass = get_module_class()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        sig = method_signature(mclass.full_name, method_name, param_types)
        mid = method_id(sig, file_path, start_line)

        m_entry = MethodEntry(
            id=mid,
            class_id=mclass.id,
            class_full_name=mclass.full_name,
            method_name=method_name,
            return_type=return_type,
            parameter_types=param_types,
            parameter_names=param_names,
            signature=sig,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            source=_text(node, source),
            class_context={},
            language="go",
        )
        methods.append(m_entry)

        block = _child_by_type(node, "block")
        if block is not None:
            local_types = {}
            for pname, ptype in zip(param_names, param_types):
                if pname:
                    local_types[pname] = _strip_pointer(ptype)
            pending.append(_PendingBody(m_entry, block, local_types, source, file_path, package_scope))

    extra_classes = [module_class] if module_class is not None else []
    return methods, extra_classes, pending


def _infer_expr_type(node, source: bytes, func_return_types: dict[str, str]) -> str | None:
    if node.type == "unary_expression":
        inner = node.children[-1] if node.children else None
        return _infer_expr_type(inner, source, func_return_types) if inner is not None else None
    if node.type == "composite_literal":
        type_node = node.children[0] if node.children else None
        return _strip_pointer(_text(type_node, source)) if type_node is not None else None
    if node.type == "call_expression":
        callee = node.children[0] if node.children else None
        if callee is not None and callee.type == "identifier":
            return func_return_types.get(_text(callee, source))
        return None
    return None


def _infer_var_types(
    block_node, source: bytes, base_local_types: dict[str, str], func_return_types: dict[str, str]
) -> dict[str, str]:
    local_types = dict(base_local_types)

    for decl in _collect(block_node, "short_var_declaration"):
        expr_lists = _children_by_type(decl, "expression_list")
        if len(expr_lists) < 2:
            continue
        lhs_idents = _children_by_type(expr_lists[0], "identifier")
        rhs_exprs = [c for c in expr_lists[1].children if c.type != ","]
        if len(lhs_idents) != 1 or len(rhs_exprs) != 1:
            continue
        inferred = _infer_expr_type(rhs_exprs[0], source, func_return_types)
        if inferred:
            local_types[_text(lhs_idents[0], source)] = inferred

    for decl in _collect(block_node, "var_declaration"):
        for spec in _children_by_type(decl, "var_spec"):
            idents = _children_by_type(spec, "identifier")
            type_node = _find_type_node(spec)
            if type_node is None:
                continue
            t = _strip_pointer(_text(type_node, source))
            for ident in idents:
                local_types[_text(ident, source)] = t

    return local_types


def _resolve_calls(graph: Graph, pending: list[_PendingBody]) -> None:
    methods_by_class_and_name: dict[tuple[str, str], list[MethodEntry]] = {}
    for m in graph.methods:
        methods_by_class_and_name.setdefault((m.class_full_name, m.method_name), []).append(m)

    func_return_types: dict[str, str] = {}
    module_classes_by_package: dict[str, list[str]] = {}
    classes_by_short_name: dict[str, list[ClassEntry]] = {}
    embeds_by_full_name: dict[str, list[str]] = {}
    for c in graph.classes:
        classes_by_short_name.setdefault(c.name, []).append(c)
        if "module" in c.stereotypes:
            module_classes_by_package.setdefault(c.package_name, []).append(c.full_name)
    for edge in graph.inheritance_edges:
        if edge.relation == "embeds":
            embeds_by_full_name.setdefault(edge.source_class, []).append(edge.target_class)
    for m in graph.methods:
        if "_functions" in m.class_full_name:
            func_return_types[m.method_name] = _strip_pointer(m.return_type)

    def find_methods(owner: ClassEntry, callee_name: str, seen: set[str] | None = None) -> list[MethodEntry]:
        """Direct methods, or promoted methods reachable via struct embedding."""
        direct = methods_by_class_and_name.get((owner.full_name, callee_name))
        if direct:
            return direct
        seen = seen or set()
        if owner.full_name in seen:
            return []
        seen.add(owner.full_name)
        for embedded_short in embeds_by_full_name.get(owner.full_name, []):
            for embedded_cls in classes_by_short_name.get(embedded_short, []):
                found = find_methods(embedded_cls, callee_name, seen)
                if found:
                    return found
        return []

    callsites: list[CallSite] = []
    resolved_edges: list[ResolvedCallEdge] = []

    for p in pending:
        local_types = _infer_var_types(p.block, p.source, p.local_types, func_return_types)

        for call_node in _collect(p.block, "call_expression"):
            if not call_node.children:
                continue
            callee_expr = call_node.children[0]
            args_node = _child_by_type(call_node, "argument_list")
            arg_count = _arg_count(args_node)
            line = call_node.start_point[0] + 1
            col = call_node.start_point[1] + 1

            receiver_text: str | None = None
            receiver_type_raw: str | None = None
            candidates: list[MethodEntry] = []

            if callee_expr.type == "identifier":
                callee_name = _text(callee_expr, p.source)
                for pkg_full in module_classes_by_package.get(p.package_name, []):
                    candidates.extend(methods_by_class_and_name.get((pkg_full, callee_name), []))
            elif callee_expr.type == "selector_expression":
                base = callee_expr.children[0]
                field_ident = _child_by_type(callee_expr, "field_identifier")
                if field_ident is None:
                    continue
                callee_name = _text(field_ident, p.source)
                receiver_text = _text(base, p.source)
                if base.type == "identifier":
                    receiver_type_raw = local_types.get(_text(base, p.source))
                if receiver_type_raw:
                    owners = classes_by_short_name.get(receiver_type_raw) or classes_by_short_name.get(
                        receiver_type_raw.split(".")[-1]
                    )
                    if owners:
                        for owner in owners:
                            candidates.extend(find_methods(owner, callee_name))
            else:
                continue

            cid = callsite_id(p.method.id, line, col, callee_name)
            resolved_candidate_ids = [c.id for c in candidates]
            if len(candidates) == 1:
                status = "resolved_exact"
                reason = "receiver type resolved via local symbol table"
            elif len(candidates) > 1:
                status = "resolved_ambiguous"
                reason = f"{len(candidates)} candidates matched by name"
            else:
                status = "unresolved"
                reason = "no matching method/function found in indexed package"

            cs = CallSite(
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
                receiver_resolution_source="local_symbol_table" if receiver_type_raw else None,
                receiver_type=receiver_type_raw,
                resolved_candidates=resolved_candidate_ids,
                resolution_status=status,
                resolution_reason=reason,
                candidate_count=len(candidates),
            )
            callsites.append(cs)
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


def _extract_file(file_path: Path, parser) -> tuple[Graph, list[_PendingBody]]:
    source = file_path.read_bytes()
    root = parser.parse(source).root_node
    package_name = _extract_package(root, source)
    package_scope = str(file_path.parent)
    file_path_str = str(file_path)

    classes, fields, inheritance_edges, class_by_name = _extract_classes_and_fields(
        root, source, file_path_str, package_name
    )
    methods, extra_classes, pending = _extract_methods_and_functions(
        root, source, file_path_str, package_name, package_scope, class_by_name
    )
    classes.extend(extra_classes)

    graph = Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=[],
        inheritance_edges=inheritance_edges,
        resolved_call_edges=[],
    )
    return graph, pending


def _merge_unresolved(graphs: list[Graph]) -> Graph:
    return Graph(
        classes=sum([g.classes for g in graphs], []),
        methods=sum([g.methods for g in graphs], []),
        fields=sum([g.fields for g in graphs], []),
        callsites=[],
        inheritance_edges=sum([g.inheritance_edges for g in graphs], []),
        resolved_call_edges=[],
    )


def build_go_graph(codebase_root: Path, on_progress: Callable[[int], None] | None = None) -> Graph:
    parser = make_go_parser()
    graphs: list[Graph] = []
    all_pending: list[_PendingBody] = []

    for file_path in iter_go_files(codebase_root):
        file_graph, pending = _extract_file(file_path, parser)
        graphs.append(file_graph)
        all_pending.extend(pending)
        if on_progress:
            on_progress(sum(len(g.classes) for g in graphs))

    graph = _merge_unresolved(graphs)
    _resolve_calls(graph, all_pending)
    return graph


def build_go_graph_for_files(files: set[Path], codebase_root: Path) -> Graph:
    """Build an unresolved Go graph for specific files (incremental reindex).

    Mirrors build_py_graph_for_files: resolution happens in the caller's merge step.
    """
    parser = make_go_parser()
    graphs: list[Graph] = []

    for file_path in files:
        if not file_path.exists():
            continue
        file_graph, _pending = _extract_file(file_path, parser)
        graphs.append(file_graph)

    if not graphs:
        return Graph(
            classes=[], methods=[], fields=[], callsites=[], inheritance_edges=[], resolved_call_edges=[]
        )
    return _merge_unresolved(graphs)
