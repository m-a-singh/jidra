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


SPRING_DATA_REPOSITORY_MARKERS = {
    "Repository",
    "CrudRepository",
    "PagingAndSortingRepository",
    "JpaRepository",
    "MongoRepository",
    "ReactiveCrudRepository",
    "ReactiveMongoRepository",
    "ReactiveSortingRepository",
    "JpaSpecificationExecutor",
    "QuerydslPredicateExecutor",
}

# (method_name, parameter_types, return_type) — the CRUD surface Spring Data
# synthesizes at runtime via a dynamic proxy. Never declared in source, so the
# AST-based extractor would otherwise never see these methods at all.
SPRING_DATA_REPOSITORY_METHODS: list[tuple[str, list[str], str]] = [
    ("findById", ["Object"], "Optional"),
    ("findAll", [], "List"),
    ("save", ["Object"], "Object"),
    ("saveAll", ["Iterable"], "List"),
    ("delete", ["Object"], "void"),
    ("deleteById", ["Object"], "void"),
    ("deleteAll", [], "void"),
    ("existsById", ["Object"], "boolean"),
    ("count", [], "long"),
    ("flush", [], "void"),
]


def _is_spring_data_repository(cls: ClassEntry) -> bool:
    supertypes = set(cls.implements)
    if cls.extends:
        supertypes.add(cls.extends)
    return any(
        _strip_generic(t).split(".")[-1] in SPRING_DATA_REPOSITORY_MARKERS
        for t in supertypes
    )


def _make_synthetic_method(
    cls_full_name: str,
    cls_id: str,
    file_path: str,
    start_line: int,
    name: str,
    param_types: list[str],
    return_type: str,
    language: str,
) -> MethodEntry:
    signature = method_signature(cls_full_name, name, param_types)
    return MethodEntry(
        id=method_id(signature, file_path, start_line),
        class_id=cls_id,
        class_full_name=cls_full_name,
        method_name=name,
        return_type=return_type,
        parameter_types=param_types,
        parameter_names=[f"arg{i}" for i in range(len(param_types))],
        signature=signature,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line,
        source="",
        class_context={},
        annotations=[],
        language=language,
    )


def _synthesize_spring_repository_methods(cls: ClassEntry) -> list[MethodEntry]:
    if not _is_spring_data_repository(cls):
        return []
    return [
        _make_synthetic_method(
            cls.full_name,
            cls.id,
            cls.file_path,
            cls.start_line,
            name,
            param_types,
            return_type,
            cls.language,
        )
        for name, param_types, return_type in SPRING_DATA_REPOSITORY_METHODS
    ]


LOMBOK_CLASS_ANNOTATIONS = {
    "Data",
    "Getter",
    "Setter",
    "Value",
    "Builder",
    "NoArgsConstructor",
    "AllArgsConstructor",
    "RequiredArgsConstructor",
}

LOMBOK_LOGGER_ANNOTATIONS = {
    "Slf4j": "org.slf4j.Logger",
    "Log4j": "org.apache.logging.log4j.Logger",
    "Log4j2": "org.apache.logging.log4j.Logger",
    "CommonsLog": "org.apache.commons.logging.Log",
    "Log": "java.util.logging.Logger",
    "XSlf4j": "org.slf4j.ext.XLogger",
    "JBossLog": "org.jboss.logging.Logger",
    "Flogger": "com.google.common.flogger.FluentLogger",
}


def _lombok_getter_name(field_name: str, type_name: str) -> str:
    if type_name == "boolean":
        if field_name.lower().startswith("is"):
            return field_name
        return "is" + field_name[:1].upper() + field_name[1:]
    return "get" + field_name[:1].upper() + field_name[1:]


def _lombok_setter_name(field_name: str) -> str:
    return "set" + field_name[:1].upper() + field_name[1:]


def _synthesize_lombok_logger_field(cls: ClassEntry) -> FieldEntry | None:
    """Synthesize the `log` field that Lombok @Slf4j/@Log4j2/etc. generate at compile time."""
    names = {_annotation_name(a) for a in cls.annotations}
    for ann_name, logger_type in LOMBOK_LOGGER_ANNOTATIONS.items():
        if ann_name in names:
            return FieldEntry(
                id=field_id(cls.full_name, "log", cls.file_path, cls.start_line),
                class_id=cls.id,
                name="log",
                type_name=logger_type,
                modifiers=["private", "static", "final"],
                file_path=cls.file_path,
                line=cls.start_line,
            )
    return None


def _synthesize_lombok_artifacts(
    cls: ClassEntry, class_fields: list[FieldEntry]
) -> tuple[list[ClassEntry], list[MethodEntry]]:
    """Lombok annotations (@Data/@Getter/@Setter/@Value/@Builder/...) generate
    real methods at compile time that never appear as text in the source file,
    so the AST-based extractor never sees them as declarations. Synthesize the
    methods Lombok would emit so calls to them resolve instead of dead-ending
    as 'class found, method not found'."""
    names = {_annotation_name(a) for a in cls.annotations}
    if not names & LOMBOK_CLASS_ANNOTATIONS:
        return [], []

    has_value = "Value" in names
    want_getters = "Data" in names or has_value or "Getter" in names
    want_setters = "Data" in names or ("Setter" in names and not has_value)
    want_builder = "Builder" in names

    methods: list[MethodEntry] = []
    classes: list[ClassEntry] = []
    instance_fields = [f for f in class_fields if "static" not in f.modifiers]

    if want_getters:
        for f in instance_fields:
            methods.append(
                _make_synthetic_method(
                    cls.full_name,
                    cls.id,
                    cls.file_path,
                    cls.start_line,
                    _lombok_getter_name(f.name, f.type_name),
                    [],
                    f.type_name,
                    cls.language,
                )
            )

    if want_setters:
        for f in instance_fields:
            if "final" in f.modifiers:
                continue
            methods.append(
                _make_synthetic_method(
                    cls.full_name,
                    cls.id,
                    cls.file_path,
                    cls.start_line,
                    _lombok_setter_name(f.name),
                    [f.type_name],
                    "void",
                    cls.language,
                )
            )

    if want_builder:
        builder_full_name = f"{cls.full_name}.Builder"
        builder_id = class_id(builder_full_name, cls.file_path)
        classes.append(
            ClassEntry(
                id=builder_id,
                package_name=cls.package_name,
                name=f"{cls.name}.Builder",
                full_name=builder_full_name,
                file_path=cls.file_path,
                start_line=cls.start_line,
                end_line=cls.start_line,
                stereotypes=["lombok_builder"],
                language=cls.language,
            )
        )
        methods.append(
            _make_synthetic_method(
                cls.full_name,
                cls.id,
                cls.file_path,
                cls.start_line,
                "builder",
                [],
                builder_full_name,
                cls.language,
            )
        )
        for f in instance_fields:
            methods.append(
                _make_synthetic_method(
                    builder_full_name,
                    builder_id,
                    cls.file_path,
                    cls.start_line,
                    f.name,
                    [f.type_name],
                    builder_full_name,
                    cls.language,
                )
            )
        methods.append(
            _make_synthetic_method(
                builder_full_name,
                builder_id,
                cls.file_path,
                cls.start_line,
                "build",
                [],
                cls.full_name,
                cls.language,
            )
        )

    return classes, methods


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


def _extract_extends_implements(
    class_node: Node, source: bytes
) -> tuple[str | None, list[str]]:
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


def _extract_fields(
    class_node: Node, source: bytes, cls: ClassEntry
) -> list[FieldEntry]:
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
            type_name = _strip_generic(
                _first_type_text(node, source, default="unknown")
            )
            for decl in _children_by_type(node, "variable_declarator"):
                ident = _find_child(decl, "identifier")
                if ident:
                    out[_text(ident, source)] = type_name

        elif node.type == "resource":
            # try-with-resources: try (Type var = expr) { ... }
            type_name = _strip_generic(
                _first_type_text(node, source, default="unknown")
            )
            ident = _find_child(node, "identifier")
            if ident:
                out[_text(ident, source)] = type_name

        elif node.type == "catch_formal_parameter":
            # catch (ExceptionType e)
            type_name = _strip_generic(
                _first_type_text(node, source, default="unknown")
            )
            ident = _find_child(node, "identifier")
            if ident:
                out[_text(ident, source)] = type_name

        elif node.type == "enhanced_for_statement":
            # for (Type item : collection)
            type_name = _strip_generic(
                _first_type_text(node, source, default="unknown")
            )
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
            else:
                # Single-param lambda without parens: `x -> x.foo()` — tree-sitter
                # puts the identifier directly as first child of lambda_expression.
                first = node.children[0] if node.children else None
                if first and first.type == "identifier":
                    out.setdefault(_text(first, source), "unknown")
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
    if receiver_text == "this":
        return cls.full_name, "this"
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
            args_count = sum(
                1 for c in arguments.children if c.type not in {",", "(", ")"}
            )

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
            {"name": f.name, "type": f.type_name, "modifiers": f.modifiers}
            for f in class_fields
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
            body_node = _find_child(node, "block") or _find_child(
                node, "constructor_body"
            )
            method_annotations = _extract_annotations(node, source)

        signature = method_signature(cls.full_name, method_name, parameter_types)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        mid = method_id(signature, cls.file_path, start_line)

        local_types = (
            _extract_local_variable_types(body_node, source) if body_node else {}
        )
        params_map = dict(zip(parameter_names, parameter_types))
        field_reads, field_writes = (
            _extract_field_accesses(
                body_node,
                set(fields_map.keys()),
                set(parameter_names),
                local_types,
                source,
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
                    receiver_text = (
                        _text(receiver_node, source) if receiver_node else None
                    )
                    receiver_type_raw, receiver_source = _infer_receiver_type_raw(
                        receiver_text, cls, symbols
                    )
                calls.append(
                    _extract_callsite(
                        invocation,
                        source,
                        mid,
                        cls.file_path,
                        receiver_type_raw,
                        receiver_source,
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
            stereotypes=_class_stereotypes(
                cls_name, class_annotations, class_node.type
            ),
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
        logger_field = _synthesize_lombok_logger_field(cls)
        if logger_field:
            class_fields.append(logger_field)
        fields.extend(class_fields)

        class_methods, class_calls = _extract_methods(
            class_node, source, cls, class_fields
        )
        methods.extend(class_methods)
        calls.extend(class_calls)

        synthetic_classes, synthetic_methods = _synthesize_lombok_artifacts(
            cls, class_fields
        )
        classes.extend(synthetic_classes)
        methods.extend(synthetic_methods)
        methods.extend(_synthesize_spring_repository_methods(cls))

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

    same_pkg = (
        f"{caller_class.package_name}.{short}" if caller_class.package_name else short
    )
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
    methods_by_full_class: dict[str, list[MethodEntry]] | None = None,
) -> tuple[list[MethodEntry], str | None]:
    """BFS over extends + implements to find a method not declared on the direct receiver type."""
    visited: set[str] = set()
    queue: list[str] = [normalized_class]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        matches = methods_by_full_class_and_name.get((current, callee_name), [])
        if matches:
            return matches, current
        cls = class_by_full_name.get(current)
        if not cls:
            continue
        parents: list[str] = []
        if cls.extends:
            parents.append(cls.extends)
        parents.extend(cls.implements)
        for raw_parent in parents:
            if "." in raw_parent:
                candidate = raw_parent
            elif cls.package_name:
                candidate = f"{cls.package_name}.{raw_parent}"
                if candidate not in all_class_full_names:
                    if methods_by_full_class is not None:
                        normalized, _, _ = _normalize_type(
                            raw_parent, cls, methods_by_full_class, all_class_full_names
                        )
                        candidate = normalized or raw_parent
                    else:
                        candidate = raw_parent
            else:
                candidate = raw_parent
            if candidate in all_class_full_names and candidate not in visited:
                queue.append(candidate)
    return [], None


def _resolve_dotted_receiver(
    receiver_text: str,
    caller_method: MethodEntry,
    caller_class: ClassEntry,
    fields_by_class: dict[str, dict[str, str]],
    class_by_full_name: dict[str, ClassEntry],
    all_class_full_names: set[str],
    methods_by_full_class: dict[str, list[MethodEntry]],
) -> tuple[str | None, str | None]:
    """Resolve a dotted receiver expression like `this.repo`, `svc.helper`, or `a.b.c`.

    Walks the chain segment by segment using field type information from the
    indexed codebase. Returns (resolved_type, source_label) or (None, None).
    """
    parts = receiver_text.split(".")
    if len(parts) < 2:
        return None, None

    first = parts[0]
    params_map = dict(zip(caller_method.parameter_names, caller_method.parameter_types))
    local_types = caller_method.local_variable_types or {}

    if first == "this":
        current_type: str | None = caller_class.full_name
    else:
        raw = (
            local_types.get(first)
            or params_map.get(first)
            or fields_by_class.get(caller_class.full_name, {}).get(first)
        )
        if not raw:
            return None, None
        current_type, _, _ = _normalize_type(
            raw, caller_class, methods_by_full_class, all_class_full_names
        )

    if current_type is None:
        return None, None

    for part in parts[1:]:
        owner = class_by_full_name.get(current_type)
        if owner is None:
            # External type — still return current_type so it becomes external_library.
            return current_type, "dotted_chain_external"
        raw_field_type = fields_by_class.get(current_type, {}).get(part)
        if not raw_field_type:
            return None, None
        current_type, _, _ = _normalize_type(
            raw_field_type, owner, methods_by_full_class, all_class_full_names
        )
        if current_type is None:
            return None, None

    return current_type, "dotted_field_chain"


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
        methods_by_full_class_and_name.setdefault(
            (m.class_full_name, m.method_name), []
        ).append(m)
        methods_by_short_class_and_name.setdefault(
            (m.class_full_name.split(".")[-1], m.method_name), []
        ).append(m)
        methods_by_name_arity.setdefault(
            (m.method_name, len(m.parameter_types)), []
        ).append(m)
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

    all_class_full_names = set(methods_by_full_class.keys()) | {
        c.full_name for c in graph.classes
    }

    fields_by_class: dict[str, dict[str, str]] = {}
    for f in graph.fields:
        owner = class_by_id.get(f.class_id)
        if owner:
            fields_by_class.setdefault(owner.full_name, {})[f.name] = f.type_name

    # Interface/abstract-class -> concrete implementing class(es), keyed by the
    # short name as captured on the `implements` clause (rarely an FQCN in source).
    implementers_by_target_short: dict[str, list[str]] = {}
    for edge in graph.inheritance_edges:
        if edge.relation != "implements":
            continue
        key = edge.target_class.split(".")[-1]
        implementers = implementers_by_target_short.setdefault(key, [])
        if edge.source_class not in implementers:
            implementers.append(edge.source_class)

    # Chained/fluent calls (`a.b().c()`) have a receiver that is itself a call
    # expression's full text, not a declared variable — there's no type to look
    # up until the inner call (`a.b()`) is itself resolved. Index callsites by
    # their own full text so a later pass can borrow the inner call's resolved
    # return type as the outer call's receiver type.
    chain_index: dict[tuple[str, str], CallSite] = {}
    for c in graph.callsites:
        chain_index[(c.caller_method_id, c.text)] = c

    def _resolve_one(call: CallSite) -> None:
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
                    methods_by_full_class,
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
                sole_implementers = implementers_by_target_short.get(
                    normalized.split(".")[-1], []
                )
                sole_impl_matches: list[MethodEntry] = []
                sole_implementer = None
                if len(sole_implementers) == 1:
                    impl_candidates = methods_by_full_class_and_name.get(
                        (sole_implementers[0], call.callee_name), []
                    )
                    sole_impl_matches = (
                        _arity_filter(impl_candidates) or impl_candidates
                    )
                    if len(sole_impl_matches) == 1:
                        sole_implementer = sole_implementers[0]

                full_matches = methods_by_full_class_and_name.get(
                    (normalized, call.callee_name), []
                )
                if sole_implementer:
                    # Prefer the concrete sole implementation over the
                    # interface/abstract-class method declaration itself,
                    # since the latter has no real body to point a reader at.
                    candidates = sole_impl_matches
                    status = "resolved_via_sole_implementation"
                    reason = (
                        f"receiver type {normalized} is an interface/abstract "
                        f"class with a single implementer {sole_implementer}"
                    )
                elif full_matches:
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
                        methods_by_full_class,
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
                            candidates = (
                                arity_matches if arity_matches else short_matches
                            )
                            status = "ambiguous_type"
                            reason = "fallback short-class match only"
                        elif normalized in all_class_full_names:
                            status = "unresolved_method"
                            reason = "receiver class found, method not found"
                        else:
                            status = "external_library"
                            reason = "receiver class not present in indexed codebase"
            elif (
                call.receiver
                and "." in call.receiver
                and not call.receiver.rstrip().endswith(")")
            ):
                # Dotted field chain: `this.service.repo`, `svc.helper`, etc.
                dotted_type, dotted_source = _resolve_dotted_receiver(
                    call.receiver,
                    caller_method,
                    caller_class,
                    fields_by_class,
                    class_by_full_name,
                    all_class_full_names,
                    methods_by_full_class,
                )
                if dotted_type:
                    call.receiver_type_raw = dotted_type
                    call.receiver_resolution_source = dotted_source
                    call.receiver_type = dotted_type
                    _resolve_one(call)
                    return
                else:
                    status = "unresolved_receiver"
                    reason = "could not infer or normalize receiver type"
            else:
                status = "unresolved_receiver"
                reason = "could not infer or normalize receiver type"

        call.resolved_candidates = sorted(m.id for m in candidates)
        call.candidate_count = len(call.resolved_candidates)
        call.resolution_status = status
        call.resolution_reason = reason

    # First pass: resolve everything as before. Then repeatedly retry only the
    # chain-receiver callsites that are still stuck, using newly-resolved inner
    # calls' return types — bounded so we don't loop forever on a malformed chain.
    for call in graph.callsites:
        _resolve_one(call)

    MAX_CHAIN_PASSES = 6
    for _ in range(MAX_CHAIN_PASSES):
        changed = False
        for call in graph.callsites:
            if call.resolution_status != "unresolved_receiver":
                continue
            receiver = call.receiver
            if not receiver or not receiver.rstrip().endswith(")"):
                continue
            inner = chain_index.get((call.caller_method_id, receiver))
            if inner is None or not inner.resolved_candidates:
                continue
            inner_method = method_by_id.get(inner.resolved_candidates[0])
            if not inner_method or not inner_method.return_type:
                continue
            new_raw = _strip_generic(inner_method.return_type)
            if new_raw == call.receiver_type_raw:
                continue
            call.receiver_type_raw = new_raw
            call.receiver_resolution_source = None
            _resolve_one(call)
            changed = True
        if not changed:
            break

    edges: list[ResolvedCallEdge] = []
    for call in graph.callsites:
        for callee_id in call.resolved_candidates:
            edges.append(
                ResolvedCallEdge(
                    id=resolved_call_edge_id(call.id, callee_id),
                    callsite_id=call.id,
                    caller_method_id=call.caller_method_id,
                    callee_method_id=callee_id,
                )
            )

    graph.resolved_call_edges = sorted(edges, key=lambda e: e.id)


def _build_java_graph(codebase_root: Path, on_progress=None) -> Graph:
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


def _merge_graphs(graphs: list[Graph]) -> Graph:
    return Graph(
        classes=sum([g.classes for g in graphs], []),
        methods=sum([g.methods for g in graphs], []),
        fields=sum([g.fields for g in graphs], []),
        callsites=sum([g.callsites for g in graphs], []),
        inheritance_edges=sum([g.inheritance_edges for g in graphs], []),
        resolved_call_edges=sum([g.resolved_call_edges for g in graphs], []),
    )


def build_graph(
    codebase_root: Path,
    on_progress=None,
    changed_files: set[Path] | None = None,
    previous_graph: Graph | None = None,
) -> Graph:
    if changed_files is not None and previous_graph is not None:
        changed_paths_str = {str(p) for p in changed_files}

        mini_graph = build_graph_for_files(changed_files, codebase_root)

        merged = Graph(
            classes=[
                c
                for c in previous_graph.classes
                if c.file_path not in changed_paths_str
            ]
            + mini_graph.classes,
            methods=[
                m
                for m in previous_graph.methods
                if m.file_path not in changed_paths_str
            ]
            + mini_graph.methods,
            fields=[
                f for f in previous_graph.fields if f.file_path not in changed_paths_str
            ]
            + mini_graph.fields,
            callsites=[
                c
                for c in previous_graph.callsites
                if c.file_path not in changed_paths_str
            ]
            + mini_graph.callsites,
            inheritance_edges=previous_graph.inheritance_edges
            + mini_graph.inheritance_edges,
            resolved_call_edges=[],
        )
        if on_progress:
            on_progress(len(merged.classes))
        _resolve_calls(merged)
        return merged

    from .ts_filters import detect_languages

    langs = detect_languages(codebase_root)
    if not langs:
        langs = ["java"]  # backward-compat fallback

    graphs: list[Graph] = []

    if "typescript" in langs:
        from .ts_extractor import build_ts_graph

        graphs.append(build_ts_graph(codebase_root, on_progress=on_progress))

    if "python" in langs:
        from .py_extractor import build_py_graph

        graphs.append(build_py_graph(codebase_root, on_progress=on_progress))

    if "scala" in langs:
        from .scala_extractor import build_scala_graph

        scala_graph = build_scala_graph(codebase_root, on_progress=on_progress)
        graphs.append(scala_graph)

    if "java" in langs:
        java_graph = _build_java_graph(codebase_root, on_progress=on_progress)
        for cls in java_graph.classes:
            cls.language = "java"
        for m in java_graph.methods:
            m.language = "java"
        graphs.append(java_graph)

    if len(graphs) == 1:
        return graphs[0]

    return _merge_graphs(graphs)


def build_graph_for_files(files: set[Path], codebase_root: Path) -> Graph:
    """Build graph for specific set of files without running _resolve_calls().

    Used for incremental reindexing on changed files only.
    Returns unresolved graph (resolved_call_edges will be empty).
    """

    graphs: list[Graph] = []
    parser = make_parser()

    # Filter files by language and extract per language
    java_files = {f for f in files if f.suffix == ".java" or ".java" in str(f)}
    py_files = {f for f in files if f.suffix == ".py" or ".py" in str(f)}
    ts_files = {f for f in files if f.suffix in {".ts", ".tsx"}}
    scala_files = {f for f in files if f.suffix == ".scala"}

    # Extract Java files
    if java_files:
        all_classes: list[ClassEntry] = []
        all_methods: list[MethodEntry] = []
        all_fields: list[FieldEntry] = []
        all_calls: list[CallSite] = []
        all_inheritance_edges: list[InheritanceEdge] = []

        for file_path in java_files:
            if not file_path.exists():
                continue
            result = _extract_file(file_path, parser)
            all_classes.extend(result.classes)
            all_methods.extend(result.methods)
            all_fields.extend(result.fields)
            all_calls.extend(result.callsites)
            all_inheritance_edges.extend(result.inheritance_edges)

        graphs.append(
            Graph(
                classes=all_classes,
                methods=all_methods,
                fields=all_fields,
                callsites=all_calls,
                inheritance_edges=all_inheritance_edges,
                resolved_call_edges=[],
            )
        )

    # Extract Python files
    if py_files:
        try:
            from .py_extractor import build_py_graph_for_files

            py_graph = build_py_graph_for_files(py_files, codebase_root)
            graphs.append(py_graph)
        except (ImportError, AttributeError):
            pass

    # Extract TypeScript files
    if ts_files:
        try:
            from .ts_extractor import build_ts_graph_for_files

            ts_graph = build_ts_graph_for_files(ts_files, codebase_root)
            graphs.append(ts_graph)
        except (ImportError, AttributeError):
            pass

    # Extract Scala files
    if scala_files:
        try:
            from .scala_extractor import build_scala_graph_for_files

            scala_graph = build_scala_graph_for_files(scala_files, codebase_root)
            graphs.append(scala_graph)
        except (ImportError, AttributeError):
            pass

    if len(graphs) == 1:
        return graphs[0]
    if graphs:
        return _merge_graphs(graphs)

    # No files found, return empty graph
    return Graph(
        classes=[],
        methods=[],
        fields=[],
        callsites=[],
        inheritance_edges=[],
        resolved_call_edges=[],
    )


def build_graph_partitioned(
    codebase_root: Path,
    output_dir: Path,
    on_progress=None,
) -> dict:
    """Build one graph.jsonl per detected build module, plus a composed index.

    Falls back to a single graph.jsonl (identical to build_graph()) when no
    multi-module structure is detected.

    Returns:
        {"multi_module": bool, "modules": {module_name: graph_path}, "index_path": str | None}
    """
    import json as _json

    from .actuator_client import _detect_build_directories
    from .exporter import export_jsonl, graph_records

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    modules = _detect_build_directories(str(codebase_root))

    if len(modules) <= 1:
        graph = build_graph(codebase_root, on_progress=on_progress)
        graph_path = output_dir / "graph.jsonl"
        export_jsonl(graph_path, graph_records(graph))
        return {"multi_module": False, "modules": {}, "index_path": None}

    index: dict[str, str] = {}
    for _tool, module_dir in modules:
        module_name = module_dir.name
        module_graph = build_graph(module_dir, on_progress=on_progress)
        module_output_dir = output_dir / module_name
        module_output_dir.mkdir(parents=True, exist_ok=True)
        graph_path = module_output_dir / "graph.jsonl"
        export_jsonl(graph_path, graph_records(module_graph))
        index[module_name] = str(graph_path)

    index_path = output_dir / "modules_index.json"
    index_path.write_text(_json.dumps(index, indent=2), encoding="utf-8")

    return {"multi_module": True, "modules": index, "index_path": str(index_path)}
