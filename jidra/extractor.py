from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from tree_sitter import Node

from .filters import iter_java_files
from .models import (
    CallSite,
    ClassEntry,
    FieldEntry,
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
from .parser import make_parser


@dataclass
class Graph:
    classes: list[ClassEntry]
    methods: list[MethodEntry]
    fields: list[FieldEntry]
    callsites: list[CallSite]
    inheritance_edges: list[InheritanceEdge]
    resolved_call_edges: list[ResolvedCallEdge]


@dataclass
class SymbolTable:
    params: dict[str, str]
    locals: dict[str, str]
    fields: dict[str, str]
    static_symbols: set[str]
    super_class: str | None = None

    def lookup(self, name: str | None) -> tuple[str | None, str | None]:
        if not name:
            return None, None
        if name == "this":
            return None, "this"
        if name == "super":
            return self.super_class, "super"
        if name.startswith("this."):
            field_name = name.split(".", 1)[1]
            if field_name in self.fields:
                return self.fields[field_name], "field"
        if name in self.locals:
            return self.locals[name], "local"
        if name in self.params:
            return self.params[name], "param"
        if name in self.fields:
            return self.fields[name], "field"
        if name in self.static_symbols:
            return name, "static_symbol"
        if "." not in name and name[0].isupper():
            return name, "static_symbol"
        return None, None


TYPE_NODE_NAMES = {
    "type_identifier",
    "integral_type",
    "floating_point_type",
    "boolean_type",
    "void_type",
    "generic_type",
    "scoped_type_identifier",
    "array_type",
}

JAVA_LANG_TYPES = {
    "String",
    "Integer",
    "Boolean",
    "Long",
    "Double",
    "Float",
    "Object",
}

# All Java declaration types that can contain methods and fields.
CLASS_LIKE_NODES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
}


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_child(node: Node, kind: str) -> Node | None:
    for child in node.children:
        if child.type == kind:
            return child
    return None


def _children_by_type(node: Node, kind: str) -> list[Node]:
    return [c for c in node.children if c.type == kind]


def _first_type_text(node: Node, source: bytes, default: str = "") -> str:
    for child in node.children:
        if child.type in TYPE_NODE_NAMES:
            return _text(child, source)
    return default


def _walk(node: Node):
    """Iterative pre-order walk — avoids Python recursion limits on deep ASTs."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _strip_generic(type_str: str) -> str:
    """Remove type arguments so 'List<MyService>' becomes 'List'."""
    idx = type_str.find("<")
    return type_str[:idx].strip() if idx != -1 else type_str


def _extract_package(root: Node, source: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            for pkg_child in child.children:
                if pkg_child.type == "scoped_identifier":
                    return _text(pkg_child, source)
    return ""


def _extract_imports(root: Node, source: bytes) -> list[str]:
    """AST-driven import extraction — more robust than text-replacement."""
    out: list[str] = []
    for child in root.children:
        if child.type != "import_declaration":
            continue
        is_static = any(c.type == "static" for c in child.children)
        has_asterisk = any(c.type == "asterisk" for c in child.children)
        for imp_child in child.children:
            if imp_child.type in {"scoped_identifier", "identifier"}:
                import_path = _text(imp_child, source)
                if has_asterisk:
                    import_path += ".*"
                if is_static:
                    import_path = f"static {import_path}"
                out.append(import_path)
                break
    return sorted(set(out))


def _extract_modifiers(node: Node, source: bytes) -> list[str]:
    mods = _find_child(node, "modifiers")
    if not mods:
        return []
    return [
        _text(c, source)
        for c in mods.children
        if c.type in {"public", "private", "protected", "static", "final", "abstract"}
    ]


def _extract_annotations(node: Node, source: bytes) -> list[str]:
    mods = _find_child(node, "modifiers")
    if not mods:
        return []
    values: list[str] = []
    for c in mods.children:
        if c.type in {"marker_annotation", "annotation"}:
            values.append(_text(c, source))
    return values


def _annotation_name(annotation: str) -> str:
    raw = annotation.strip().lstrip("@")
    head = raw.split("(", 1)[0]
    return head.split(".")[-1]


def _extract_annotation_value(annotation: str) -> str | None:
    m = re.search(r'\(\s*"([^"]+)"\s*\)', annotation)
    if m:
        return m.group(1)
    m = re.search(r'value\s*=\s*"([^"]+)"', annotation)
    if m:
        return m.group(1)
    return None


def _class_stereotypes(
    class_name: str, annotations: list[str], node_type: str = "class_declaration"
) -> list[str]:
    out: list[str] = []

    # Structural kind takes priority for non-class declarations.
    if node_type == "interface_declaration":
        return ["interface"]
    if node_type == "enum_declaration":
        return ["enum"]
    if node_type == "record_declaration":
        return ["record"]
    if node_type == "annotation_type_declaration":
        return ["annotation"]

    names = {_annotation_name(a) for a in annotations}
    if "RestController" in names or "Controller" in names:
        out.append("controller")
    if "Service" in names:
        out.append("service")
    if "Repository" in names:
        out.append("repository")
    if "Component" in names:
        out.append("component")
    if "Configuration" in names:
        out.append("configuration")
    if "Entity" in names:
        out.append("entity")
    if class_name.endswith("Dto") or class_name.endswith("DTO"):
        out.append("dto")
    if class_name.endswith("Test") or class_name.endswith("Tests"):
        out.append("test")
    if not out:
        out.append("unknown")
    return sorted(set(out))


def _endpoint_meta(
    method_annotations: list[str], class_annotations: list[str]
) -> tuple[bool, str | None, str | None, str | None, str | None]:
    mapping_to_http = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
        "RequestMapping": None,
    }
    controller_route = None
    for ann in class_annotations:
        if _annotation_name(ann) == "RequestMapping":
            controller_route = _extract_annotation_value(ann)
            break

    is_endpoint = False
    http_method = None
    route = None
    for ann in method_annotations:
        name = _annotation_name(ann)
        if name in mapping_to_http:
            is_endpoint = True
            route = _extract_annotation_value(ann) or route
            if mapping_to_http[name]:
                http_method = mapping_to_http[name]
            elif name == "RequestMapping":
                # Use re.findall to handle both single and array forms:
                # method = RequestMethod.GET  or  method = {RequestMethod.GET, RequestMethod.POST}
                found = re.findall(r"RequestMethod\.(\w+)", ann)
                if found:
                    http_method = found[0].upper()
    full_route = None
    if controller_route or route:
        full_route = f"{(controller_route or '').rstrip('/')}/{(route or '').lstrip('/')}".replace(
            "//", "/"
        )
    return is_endpoint, http_method, route, controller_route, full_route


def _extract_extends_implements(class_node: Node, source: bytes) -> tuple[str | None, list[str]]:
    extends_value: str | None = None
    implements_values: list[str] = []
    for child in class_node.children:
        if child.type == "superclass":
            target = _find_child(child, "type_identifier") or _find_child(
                child, "scoped_type_identifier"
            )
            if target:
                extends_value = _text(target, source)
        # super_interfaces: class implements X, Y
        # extends_interfaces: interface extends X, Y  (multiple inheritance)
        if child.type in {"super_interfaces", "extends_interfaces"}:
            for gc in _walk(child):
                if gc.type in {"type_identifier", "scoped_type_identifier"}:
                    implements_values.append(_text(gc, source))
    return extends_value, implements_values


def _get_body_node(class_node: Node) -> Node | None:
    """Return the correct body container for any class-like declaration."""
    ntype = class_node.type
    if ntype == "interface_declaration":
        return _find_child(class_node, "interface_body")
    if ntype == "enum_declaration":
        enum_body = _find_child(class_node, "enum_body")
        if enum_body:
            # Methods and fields live inside enum_body_declarations when present.
            return _find_child(enum_body, "enum_body_declarations") or enum_body
        return None
    if ntype == "annotation_type_declaration":
        return _find_child(class_node, "annotation_type_body")
    # class_declaration and record_declaration both use class_body.
    return _find_child(class_node, "class_body")


def _extract_fields(class_node: Node, source: bytes, cls: ClassEntry) -> list[FieldEntry]:
    out: list[FieldEntry] = []
    body = _get_body_node(class_node)
    if not body:
        return out

    for child in body.children:
        if child.type != "field_declaration":
            continue
        type_name = _strip_generic(_first_type_text(child, source, default="unknown"))
        mods = _extract_modifiers(child, source)
        for declarator in _children_by_type(child, "variable_declarator"):
            ident = _find_child(declarator, "identifier")
            if not ident:
                continue
            name = _text(ident, source)
            line = declarator.start_point[0] + 1
            out.append(
                FieldEntry(
                    id=field_id(cls.full_name, name, cls.file_path, line),
                    class_id=cls.id,
                    name=name,
                    type_name=type_name,
                    modifiers=mods,
                    file_path=cls.file_path,
                    line=line,
                )
            )
    return out


def _extract_parameters(node: Node, source: bytes) -> tuple[list[str], list[str]]:
    params = _find_child(node, "formal_parameters")
    if not params:
        return [], []
    ptypes: list[str] = []
    pnames: list[str] = []
    for p in params.children:
        if p.type not in {"formal_parameter", "spread_parameter"}:
            continue
        ptypes.append(_strip_generic(_first_type_text(p, source, default="unknown")))
        ident = _find_child(p, "identifier")
        pnames.append(_text(ident, source) if ident else "arg")
    return ptypes, pnames


def _extract_local_variable_types(body_node: Node, source: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in _walk(body_node):
        if node.type == "local_variable_declaration":
            type_name = _strip_generic(_first_type_text(node, source, default="unknown"))
            for decl in _children_by_type(node, "variable_declarator"):
                ident = _find_child(decl, "identifier")
                if ident:
                    out[_text(ident, source)] = type_name

        elif node.type == "resource":
            # try-with-resources: try (Type var = expr) { ... }
            type_name = _strip_generic(_first_type_text(node, source, default="unknown"))
            ident = _find_child(node, "identifier")
            if ident:
                out[_text(ident, source)] = type_name

        elif node.type == "catch_formal_parameter":
            # catch (ExceptionType e)
            type_name = _strip_generic(_first_type_text(node, source, default="unknown"))
            ident = _find_child(node, "identifier")
            if ident:
                out[_text(ident, source)] = type_name

        elif node.type == "enhanced_for_statement":
            # for (Type item : collection)
            type_name = _strip_generic(_first_type_text(node, source, default="unknown"))
            ident = _find_child(node, "identifier")
            if ident:
                out[_text(ident, source)] = type_name

        elif node.type == "lambda_expression":
            # Shadow lambda parameters so they don't incorrectly resolve as outer-scope vars.
            # Use setdefault so outer scope wins for re-used names (conservative).
            params_node = _find_child(node, "formal_parameters") or _find_child(
                node, "inferred_parameters"
            )
            if params_node:
                for p in params_node.children:
                    if p.type == "identifier":
                        out.setdefault(_text(p, source), "unknown")
                    elif p.type == "formal_parameter":
                        ident = _find_child(p, "identifier")
                        if ident:
                            out.setdefault(
                                _text(ident, source),
                                _strip_generic(_first_type_text(p, source, "unknown")),
                            )
    return out


def _extract_field_accesses(
    body_node: Node,
    field_names: set[str],
    params: set[str],
    locals_map: dict[str, str],
    source: bytes,
) -> tuple[list[str], list[str]]:
    reads: set[str] = set()
    writes: set[str] = set()

    for node in _walk(body_node):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left:
                if left.type == "identifier":
                    name = _text(left, source)
                    if name not in params and name not in locals_map:
                        writes.add(name)
                elif left.type == "field_access":
                    field = left.child_by_field_name("field")
                    if field:
                        writes.add(_text(field, source))

        if node.type == "identifier":
            name = _text(node, source)
            if name in field_names and name not in params and name not in locals_map:
                # Exclude identifiers that are in method-name or declaration-name position.
                parent = node.parent
                if parent is not None:
                    if parent.type == "method_invocation":
                        name_child = parent.child_by_field_name("name")
                        if (
                            name_child is not None
                            and name_child.start_byte == node.start_byte
                            and name_child.end_byte == node.end_byte
                        ):
                            continue
                    elif parent.type in {
                        "method_declaration",
                        "constructor_declaration",
                        "class_declaration",
                        "interface_declaration",
                        "enum_declaration",
                    }:
                        continue
                reads.add(name)

        if node.type == "field_access":
            field = node.child_by_field_name("field")
            if field:
                fname = _text(field, source)
                if fname in field_names:
                    reads.add(fname)

    return sorted(reads), sorted(writes)


def _iter_call_nodes(node: Node):
    """Yield every method_invocation and method_reference node in a body."""
    for n in _walk(node):
        if n.type in {"method_invocation", "method_reference"}:
            yield n


def _infer_receiver_type_raw(
    receiver_text: str | None, cls: ClassEntry, symbols: SymbolTable
) -> tuple[str | None, str | None]:
    if receiver_text is None:
        return cls.full_name, "same_class"
    return symbols.lookup(receiver_text)


def _extract_callsite(
    invocation: Node,
    source: bytes,
    caller_method_id: str,
    file_path: str,
    receiver_type_raw: str | None,
    receiver_source: str | None,
) -> CallSite:
    callee_name = "unknown"
    receiver = None
    args_count = 0

    if invocation.type == "method_reference":
        # Structure: <expression> "::" (<identifier> | "new")
        non_sep = [c for c in invocation.children if c.type != "::"]
        if non_sep:
            receiver = _text(non_sep[0], source)
            if len(non_sep) >= 2:
                last_text = _text(non_sep[-1], source)
                callee_name = "<init>" if last_text == "new" else last_text
        # -1 signals arity-unknown to the resolver; method references carry no argument list.
        args_count = -1
    else:
        name_node = invocation.child_by_field_name("name")
        if name_node:
            callee_name = _text(name_node, source)

        object_node = invocation.child_by_field_name("object")
        if object_node:
            receiver = _text(object_node, source)

        arguments = invocation.child_by_field_name("arguments")
        if arguments:
            args_count = sum(1 for c in arguments.children if c.type not in {",", "(", ")"})

    line = invocation.start_point[0] + 1
    column = invocation.start_point[1] + 1
    cid = callsite_id(caller_method_id, line, column, callee_name)

    return CallSite(
        id=cid,
        caller_method_id=caller_method_id,
        callee_name=callee_name,
        receiver=receiver,
        argument_count=args_count,
        file_path=file_path,
        line=line,
        column=column,
        text=_text(invocation, source),
        receiver_type_raw=receiver_type_raw,
        receiver_resolution_source=receiver_source,
        receiver_type=receiver_type_raw,
    )


def _extract_methods(
    class_node: Node,
    source: bytes,
    cls: ClassEntry,
    class_fields: list[FieldEntry],
) -> tuple[list[MethodEntry], list[CallSite]]:
    methods: list[MethodEntry] = []
    calls: list[CallSite] = []

    fields_map = {f.name: f.type_name for f in class_fields}
    static_symbols = {cls.name, cls.full_name}
    class_context = {
        "class_name": cls.name,
        "class_full_name": cls.full_name,
        "imports": cls.imports,
        "annotations": cls.annotations,
        "extends": cls.extends,
        "implements": cls.implements,
        "fields": [
            {"name": f.name, "type": f.type_name, "modifiers": f.modifiers} for f in class_fields
        ],
    }

    body = _get_body_node(class_node)
    if not body:
        return methods, calls

    for node in body.children:
        is_initializer = node.type == "static_initializer"
        if (
            node.type not in {"method_declaration", "constructor_declaration"}
            and not is_initializer
        ):
            continue

        if is_initializer:
            method_name = "<clinit>"
            return_type = "void"
            parameter_types: list[str] = []
            parameter_names: list[str] = []
            body_node = _find_child(node, "block") or node
            method_annotations: list[str] = []
        else:
            name_node = _find_child(node, "identifier")
            method_name = _text(name_node, source) if name_node else cls.name
            return_type = _first_type_text(
                node,
                source,
                default=cls.name if node.type == "constructor_declaration" else "void",
            )
            parameter_types, parameter_names = _extract_parameters(node, source)
            body_node = _find_child(node, "block") or _find_child(node, "constructor_body")
            method_annotations = _extract_annotations(node, source)

        signature = method_signature(cls.full_name, method_name, parameter_types)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        mid = method_id(signature, cls.file_path, start_line)

        local_types = _extract_local_variable_types(body_node, source) if body_node else {}
        params_map = dict(zip(parameter_names, parameter_types))
        field_reads, field_writes = (
            _extract_field_accesses(
                body_node, set(fields_map.keys()), set(parameter_names), local_types, source
            )
            if body_node
            else ([], [])
        )
        symbols = SymbolTable(
            params=params_map,
            locals=local_types,
            fields=fields_map,
            static_symbols=static_symbols,
            super_class=cls.extends,
        )

        is_endpoint, http_method, route, controller_route, full_route = _endpoint_meta(
            method_annotations, cls.annotations
        )
        methods.append(
            MethodEntry(
                id=mid,
                class_id=cls.id,
                class_full_name=cls.full_name,
                method_name=method_name,
                return_type=return_type,
                parameter_types=parameter_types,
                parameter_names=parameter_names,
                signature=signature,
                file_path=cls.file_path,
                start_line=start_line,
                end_line=end_line,
                source=_text(node, source),
                class_context=class_context,
                annotations=method_annotations,
                local_variable_types=local_types,
                field_reads=field_reads,
                field_writes=field_writes,
                is_endpoint=is_endpoint,
                http_method=http_method,
                route=route,
                controller_route=controller_route,
                full_route=full_route,
            )
        )

        if body_node:
            for invocation in _iter_call_nodes(body_node):
                if invocation.type == "method_reference":
                    non_sep = [c for c in invocation.children if c.type != "::"]
                    ref_receiver_text = _text(non_sep[0], source) if non_sep else None
                    receiver_type_raw, receiver_source = _infer_receiver_type_raw(
                        ref_receiver_text, cls, symbols
                    )
                else:
                    receiver_node = invocation.child_by_field_name("object")
                    receiver_text = _text(receiver_node, source) if receiver_node else None
                    receiver_type_raw, receiver_source = _infer_receiver_type_raw(
                        receiver_text, cls, symbols
                    )
                calls.append(
                    _extract_callsite(
                        invocation, source, mid, cls.file_path, receiver_type_raw, receiver_source
                    )
                )

    return methods, calls


def _iter_class_nodes(node: Node):
    """Yield every class-like declaration node anywhere in the tree."""
    if node.type in CLASS_LIKE_NODES:
        yield node
    for child in node.children:
        yield from _iter_class_nodes(child)


def _extract_file(file_path: Path, parser=None) -> Graph:
    if parser is None:
        parser = make_parser()
    source = file_path.read_bytes()
    root = parser.parse(source).root_node
    package_name = _extract_package(root, source)
    imports = _extract_imports(root, source)

    classes: list[ClassEntry] = []
    fields: list[FieldEntry] = []
    methods: list[MethodEntry] = []
    calls: list[CallSite] = []
    inheritance_edges: list[InheritanceEdge] = []

    for class_node in _iter_class_nodes(root):
        ident = _find_child(class_node, "identifier")
        if not ident:
            continue

        cls_name = _text(ident, source)
        full_name = f"{package_name}.{cls_name}" if package_name else cls_name
        extends_name, implements_names = _extract_extends_implements(class_node, source)

        class_annotations = _extract_annotations(class_node, source)
        cls = ClassEntry(
            id=class_id(full_name, str(file_path)),
            package_name=package_name,
            name=cls_name,
            full_name=full_name,
            file_path=str(file_path),
            start_line=class_node.start_point[0] + 1,
            end_line=class_node.end_point[0] + 1,
            modifiers=_extract_modifiers(class_node, source),
            annotations=class_annotations,
            extends=extends_name,
            implements=implements_names,
            imports=imports,
            stereotypes=_class_stereotypes(cls_name, class_annotations, class_node.type),
        )
        classes.append(cls)

        if extends_name:
            inheritance_edges.append(
                InheritanceEdge(
                    id=inheritance_edge_id(cls.full_name, extends_name, "extends"),
                    source_class_id=cls.id,
                    source_class=cls.full_name,
                    target_class=extends_name,
                    relation="extends",
                )
            )
        for iface in implements_names:
            inheritance_edges.append(
                InheritanceEdge(
                    id=inheritance_edge_id(cls.full_name, iface, "implements"),
                    source_class_id=cls.id,
                    source_class=cls.full_name,
                    target_class=iface,
                    relation="implements",
                )
            )

        class_fields = _extract_fields(class_node, source, cls)
        fields.extend(class_fields)

        class_methods, class_calls = _extract_methods(class_node, source, cls, class_fields)
        methods.extend(class_methods)
        calls.extend(class_calls)

    return Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=calls,
        inheritance_edges=inheritance_edges,
        resolved_call_edges=[],
    )


def _normalize_type(
    raw_type: str | None,
    caller_class: ClassEntry,
    methods_by_full_class: dict[str, list[MethodEntry]],
    all_class_full_names: set[str],
) -> tuple[str | None, str | None, list[str]]:
    if raw_type is None:
        return None, None, []

    # Strip generics before any lookup so 'List<Foo>' resolves as 'List'.
    raw_type = _strip_generic(raw_type)

    if "." in raw_type:
        return raw_type, "fqcn", [raw_type]

    short = raw_type
    imports = caller_class.imports

    for imp in imports:
        if imp.endswith(".*") or imp.startswith("static "):
            continue
        if imp.split(".")[-1] == short:
            return imp, "import_exact", [imp]

    for imp in imports:
        if not imp.startswith("static "):
            continue
        parts = imp.replace("static ", "").split(".")
        if parts[-1] == short:
            owner = ".".join(parts[:-1])
            return owner, "static_import", [owner]

    same_pkg = f"{caller_class.package_name}.{short}" if caller_class.package_name else short
    if same_pkg in all_class_full_names:
        return same_pkg, "same_package", [same_pkg]

    wildcard_candidates: list[str] = []
    for imp in imports:
        if not imp.endswith(".*"):
            continue
        prefix = imp[:-2]
        candidate = f"{prefix}.{short}"
        if candidate in all_class_full_names:
            wildcard_candidates.append(candidate)

    if wildcard_candidates:
        unique = sorted(set(wildcard_candidates))
        return (unique[0] if len(unique) == 1 else None), "import_wildcard", unique

    if short in JAVA_LANG_TYPES:
        return f"java.lang.{short}", "java_lang", [f"java.lang.{short}"]

    return short, "unqualified", [short]


def _find_method_in_hierarchy(
    normalized_class: str,
    callee_name: str,
    methods_by_full_class_and_name: dict[tuple[str, str], list[MethodEntry]],
    class_by_full_name: dict[str, ClassEntry],
    all_class_full_names: set[str],
) -> tuple[list[MethodEntry], str | None]:
    """Walk the extends chain to find a method not declared on the direct receiver type."""
    visited: set[str] = set()
    current: str | None = normalized_class
    while current and current not in visited:
        visited.add(current)
        matches = methods_by_full_class_and_name.get((current, callee_name), [])
        if matches:
            return matches, current
        cls = class_by_full_name.get(current)
        if not cls or not cls.extends:
            break
        parent = cls.extends
        if "." not in parent and cls.package_name:
            candidate = f"{cls.package_name}.{parent}"
            if candidate in all_class_full_names:
                current = candidate
                continue
        current = parent if parent in all_class_full_names else None
    return [], None


def _resolve_calls(graph: Graph) -> None:
    class_by_id = {c.id: c for c in graph.classes}
    class_by_full_name = {c.full_name: c for c in graph.classes}
    method_by_id = {m.id: m for m in graph.methods}

    methods_by_full_class_and_name: dict[tuple[str, str], list[MethodEntry]] = {}
    methods_by_short_class_and_name: dict[tuple[str, str], list[MethodEntry]] = {}
    methods_by_name_arity: dict[tuple[str, int], list[MethodEntry]] = {}
    methods_by_name: dict[str, list[MethodEntry]] = {}
    methods_by_full_class: dict[str, list[MethodEntry]] = {}

    for m in graph.methods:
        methods_by_full_class_and_name.setdefault((m.class_full_name, m.method_name), []).append(m)
        methods_by_short_class_and_name.setdefault(
            (m.class_full_name.split(".")[-1], m.method_name), []
        ).append(m)
        methods_by_name_arity.setdefault((m.method_name, len(m.parameter_types)), []).append(m)
        methods_by_name.setdefault(m.method_name, []).append(m)
        methods_by_full_class.setdefault(m.class_full_name, []).append(m)

    for bucket in (
        methods_by_full_class_and_name,
        methods_by_short_class_and_name,
        methods_by_name_arity,
        methods_by_name,
        methods_by_full_class,
    ):
        for key in bucket:
            bucket[key] = sorted(bucket[key], key=lambda x: x.id)

    all_class_full_names = set(methods_by_full_class.keys()) | {c.full_name for c in graph.classes}

    edges: list[ResolvedCallEdge] = []

    for call in graph.callsites:
        caller_method = method_by_id[call.caller_method_id]
        caller_class = class_by_id[caller_method.class_id]

        normalized, norm_source, wildcard_candidates = _normalize_type(
            call.receiver_type_raw,
            caller_class,
            methods_by_full_class,
            all_class_full_names,
        )
        call.receiver_type_normalized = normalized
        call.receiver_resolution_source = call.receiver_resolution_source or norm_source
        call.receiver_type = normalized

        candidates: list[MethodEntry] = []
        status = "unresolved_method"
        reason = "no candidate methods found"

        # arity_matches helper: skip arity filter for method references (args_count == -1).
        def _arity_filter(ms: list[MethodEntry]) -> list[MethodEntry]:
            if call.argument_count < 0:
                return ms
            return [m for m in ms if len(m.parameter_types) == call.argument_count]

        if call.receiver is None:
            same_class = methods_by_full_class_and_name.get(
                (caller_method.class_full_name, call.callee_name), []
            )
            same_class_arity = _arity_filter(same_class)
            if same_class_arity:
                candidates = same_class_arity
                if len(candidates) == 1:
                    status = "resolved_same_class"
                    reason = "resolved in caller class by name+arity"
                else:
                    status = "ambiguous_overload"
                    reason = "multiple same-class overloads with same arity"
            else:
                # Fall back to inherited method in the caller's own hierarchy.
                inherited, found_in = _find_method_in_hierarchy(
                    caller_method.class_full_name,
                    call.callee_name,
                    methods_by_full_class_and_name,
                    class_by_full_name,
                    all_class_full_names,
                )
                inherited_arity = _arity_filter(inherited)
                if inherited_arity:
                    candidates = inherited_arity
                    status = "resolved_inherited"
                    reason = f"resolved via inheritance from {found_in}"
                else:
                    if call.argument_count >= 0:
                        by_arity = methods_by_name_arity.get(
                            (call.callee_name, call.argument_count), []
                        )
                    else:
                        by_arity = methods_by_name.get(call.callee_name, [])
                    candidates = by_arity
                    if not candidates:
                        status = "unresolved_method"
                        reason = "no method with matching name+arity"
                    elif len(candidates) == 1:
                        status = "candidate_global_name_arity"
                        reason = "single global name+arity candidate; not treated as exact because receiver is implicit"
                    else:
                        status = "ambiguous_global_name_arity"
                        reason = "multiple global name+arity candidates; receiver is implicit"
        else:
            if norm_source == "import_wildcard" and len(wildcard_candidates) > 1:
                status = "ambiguous_type"
                reason = "receiver type matches multiple wildcard imports"
                candidates = []
            elif normalized:
                full_matches = methods_by_full_class_and_name.get(
                    (normalized, call.callee_name), []
                )
                if full_matches:
                    arity_matches = _arity_filter(full_matches)
                    candidates = arity_matches if arity_matches else full_matches

                    if len(candidates) == 1:
                        if norm_source == "import_exact":
                            status = "resolved_via_import"
                            reason = "receiver type normalized via exact import"
                        elif norm_source == "same_package":
                            status = "resolved_same_package"
                            reason = "receiver type normalized via same package"
                        else:
                            status = "resolved_exact"
                            reason = "resolved by normalized full class name"
                    else:
                        status = "ambiguous_overload"
                        reason = "multiple receiver-class overload candidates"
                else:
                    # Method not on receiver class directly — walk the inheritance chain.
                    inherited, found_in = _find_method_in_hierarchy(
                        normalized,
                        call.callee_name,
                        methods_by_full_class_and_name,
                        class_by_full_name,
                        all_class_full_names,
                    )
                    if inherited:
                        arity_matches = _arity_filter(inherited)
                        candidates = arity_matches if arity_matches else inherited
                        if len(candidates) == 1:
                            status = "resolved_inherited"
                            reason = f"resolved via inheritance from {found_in}"
                        else:
                            status = "ambiguous_overload"
                            reason = "multiple inherited overload candidates"
                    else:
                        short_matches = methods_by_short_class_and_name.get(
                            (normalized.split(".")[-1], call.callee_name), []
                        )
                        if short_matches:
                            arity_matches = _arity_filter(short_matches)
                            candidates = arity_matches if arity_matches else short_matches
                            status = "ambiguous_type"
                            reason = "fallback short-class match only"
                        elif normalized in all_class_full_names:
                            status = "unresolved_method"
                            reason = "receiver class found, method not found"
                        else:
                            status = "external_library"
                            reason = "receiver class not present in indexed codebase"
            else:
                status = "unresolved_receiver"
                reason = "could not infer or normalize receiver type"

        call.resolved_candidates = sorted(m.id for m in candidates)
        call.candidate_count = len(call.resolved_candidates)
        call.resolution_status = status
        call.resolution_reason = reason

        for callee in candidates:
            edges.append(
                ResolvedCallEdge(
                    id=resolved_call_edge_id(call.id, callee.id),
                    callsite_id=call.id,
                    caller_method_id=call.caller_method_id,
                    callee_method_id=callee.id,
                )
            )

    graph.resolved_call_edges = sorted(edges, key=lambda e: e.id)


def build_graph(codebase_root: Path, on_progress=None) -> Graph:
    from .ts_filters import detect_language
    if detect_language(codebase_root) == "typescript":
        from .ts_extractor import build_ts_graph
        return build_ts_graph(codebase_root, on_progress=on_progress)

    # Java path
    parser = make_parser()

    all_classes: list[ClassEntry] = []
    all_methods: list[MethodEntry] = []
    all_fields: list[FieldEntry] = []
    all_calls: list[CallSite] = []
    all_inheritance_edges: list[InheritanceEdge] = []

    for file_path in iter_java_files(codebase_root):
        result = _extract_file(file_path, parser)
        all_classes.extend(result.classes)
        all_methods.extend(result.methods)
        all_fields.extend(result.fields)
        all_calls.extend(result.callsites)
        all_inheritance_edges.extend(result.inheritance_edges)

        if on_progress:
            on_progress(len(all_classes))

    graph = Graph(
        classes=all_classes,
        methods=all_methods,
        fields=all_fields,
        callsites=all_calls,
        inheritance_edges=all_inheritance_edges,
        resolved_call_edges=[],
    )
    _resolve_calls(graph)
    return graph
