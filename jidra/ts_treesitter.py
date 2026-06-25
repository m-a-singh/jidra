"""In-process TypeScript/TSX extraction via tree-sitter (Phase 7).

A Docker-free alternative to the ts-morph sidecar. Syntax-only (like the Java/
Python/Go extractors), so call resolution quality is lower (~65% vs ~80%) — the
shared resolver in `extractor._resolve_calls` resolves the call sites this emits.
Produces the same ClassEntry/MethodEntry/CallSite/... shapes the sidecar does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    callsite_id,
    class_id,
    field_id,
    inheritance_edge_id,
    method_id,
    method_signature,
)
from .file_filters import apply_filters
from .parser import make_ts_parser

_SOURCE_GLOBS = ("*.ts", "*.tsx")
_IGNORE_DIRS = {"node_modules", "build", "dist", ".git", "__pycache__", ".jidra"}
_REACT_HOOK_RE = re.compile(r"^use[A-Z]")
_HTTP_DECORATORS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All"}
_ANGULAR_CLASS_STEREOTYPES = {
    "Component": "angular_component",
    "Injectable": "service",
    "NgModule": "angular_module",
    "Directive": "angular_directive",
    "Controller": "controller",  # NestJS
}
_JSX_TYPES = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _child(node, type_name: str):
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _children(node, type_name: str):
    return [c for c in node.children if c.type == type_name]


def _namespace(rel_path: str) -> str:
    parts = re.sub(r"\.(ts|tsx)$", "", rel_path).split("/")
    parts = parts[:-1]
    return ".".join(parts) or "<root>"


def _class_name_from_path(rel_path: str) -> str:
    return re.sub(r"\.(ts|tsx)$", "", rel_path.split("/")[-1])


def _path_stereotypes(rel_path: str) -> list[str]:
    out: list[str] = []
    if re.search(r"\.service\.tsx?$", rel_path) or re.search(r"/services?/", rel_path):
        out.append("service")
    if re.search(r"\.controller\.tsx?$", rel_path) or re.search(
        r"/controllers?/", rel_path
    ):
        out.append("controller")
    if re.search(r"\.component\.tsx?$", rel_path) or re.search(
        r"/components?/", rel_path
    ):
        out.append("component")
    if re.search(r"/hooks/use[A-Z]", rel_path):
        out.append("hook")
    if re.search(r"/composables/use[A-Z]", rel_path):
        out.append("vue_composable")
    return out


def _decorator_names(node, src: bytes) -> list[str]:
    """Decorator identifiers attached to a class/method node.

    Decorators precede the declaration as sibling `decorator` nodes (export-
    wrapped or direct); we collect them from the node's own children too.
    """
    names: list[str] = []
    for c in node.children:
        if c.type == "decorator":
            inner = _text(c, src).lstrip("@")
            names.append(re.split(r"[(.<]", inner)[0])
    return names


def _has_jsx(node) -> bool:
    stack = list(node.children)
    while stack:
        n = stack.pop()
        if n.type in _JSX_TYPES:
            return True
        stack.extend(n.children)
    return False


def _framework_role(name: str, body, decorators: list[str]) -> str | None:
    if any(d in _HTTP_DECORATORS for d in decorators):
        return "http_handler"
    if "Component" in decorators:
        return "component"
    if _REACT_HOOK_RE.match(name):
        return "hook"
    if body is not None and _has_jsx(body):
        return "component"
    return None


def _params(formal_parameters, src: bytes) -> tuple[list[str], list[str]]:
    names: list[str] = []
    types: list[str] = []
    if formal_parameters is None:
        return types, names
    for p in formal_parameters.children:
        if p.type not in ("required_parameter", "optional_parameter"):
            continue
        ident = _child(p, "identifier")
        names.append(_text(ident, src) if ident else "_")
        ann = _child(p, "type_annotation")
        if ann is not None:
            # type_annotation = ": <type>" → take the last child as the type
            type_node = ann.children[-1] if ann.children else None
            t = _text(type_node, src) if type_node is not None else "unknown"
            types.append(re.sub(r"<.*>", "", t).strip())
        else:
            types.append("unknown")
    return types, names


def _return_type(node, src: bytes) -> str:
    ann = _child(node, "type_annotation")
    if ann is not None and ann.children:
        return re.sub(r"<.*>", "", _text(ann.children[-1], src)).strip()
    return "unknown"


class _FileExtractor:
    def __init__(self, rel_path: str, src: bytes):
        self.rel = rel_path
        self.src = src
        self.namespace = _namespace(rel_path)
        self.classes: list[ClassEntry] = []
        self.methods: list[MethodEntry] = []
        self.fields: list[FieldEntry] = []
        self.callsites: list[CallSite] = []
        self.inheritance: list[InheritanceEdge] = []
        self._module_class: ClassEntry | None = None
        self.imports: list[str] = []

    # ── classes ───────────────────────────────────────────────────────────────

    def _heritage(self, class_node):
        extends = None
        implements: list[str] = []
        heritage = _child(class_node, "class_heritage")
        if heritage is None:
            return extends, implements
        ext = _child(heritage, "extends_clause")
        if ext is not None:
            for c in ext.children:
                if c.type in ("identifier", "type_identifier", "member_expression"):
                    extends = _text(c, self.src)
                    break
        impl = _child(heritage, "implements_clause")
        if impl is not None:
            for c in impl.children:
                if c.type in ("type_identifier", "identifier"):
                    implements.append(_text(c, self.src))
        return extends, implements

    def _emit_class(self, class_node, decorators: list[str], is_interface=False):
        name_node = _child(class_node, "type_identifier")
        if name_node is None:
            return None
        name = _text(name_node, self.src)
        full_name = f"{self.namespace}.{name}"
        cid = class_id(full_name, self.rel)
        extends, implements = self._heritage(class_node)
        stereotypes = list(_path_stereotypes(self.rel))
        for d in decorators:
            if d in _ANGULAR_CLASS_STEREOTYPES:
                stereotypes.append(_ANGULAR_CLASS_STEREOTYPES[d])
        if is_interface:
            stereotypes.append("interface")
        cls = ClassEntry(
            id=cid,
            package_name=self.namespace,
            name=name,
            full_name=full_name,
            file_path=self.rel,
            start_line=class_node.start_point[0] + 1,
            end_line=class_node.end_point[0] + 1,
            modifiers=[],
            annotations=decorators,
            extends=extends,
            implements=implements,
            imports=list(self.imports),
            stereotypes=sorted(set(stereotypes)),
            language="typescript",
        )
        self.classes.append(cls)
        for base, relation in ([(extends, "extends")] if extends else []) + [
            (i, "implements") for i in implements
        ]:
            self.inheritance.append(
                InheritanceEdge(
                    id=inheritance_edge_id(full_name, base, relation),
                    source_class_id=cid,
                    source_class=full_name,
                    target_class=base,
                    relation=relation,
                )
            )
        body = _child(class_node, "class_body")
        if body is not None:
            for member in body.children:
                if member.type == "method_definition":
                    self._emit_method(member, cls)
                elif member.type == "public_field_definition":
                    self._emit_field(member, cls)
        return cls

    def _module_class_entry(self) -> ClassEntry:
        if self._module_class is None:
            name = _class_name_from_path(self.rel)
            full_name = f"{self.namespace}.{name}"
            self._module_class = ClassEntry(
                id=class_id(full_name, self.rel),
                package_name=self.namespace,
                name=name,
                full_name=full_name,
                file_path=self.rel,
                start_line=1,
                end_line=1,
                imports=list(self.imports),
                stereotypes=sorted(set(_path_stereotypes(self.rel))) or ["module"],
                language="typescript",
            )
            self.classes.append(self._module_class)
        return self._module_class

    # ── methods / fields ──────────────────────────────────────────────────────

    def _emit_field(self, node, cls: ClassEntry):
        name_node = _child(node, "property_identifier")
        if name_node is None:
            return
        ann = _child(node, "type_annotation")
        type_name = "unknown"
        if ann is not None and ann.children:
            type_name = _text(ann.children[-1], self.src)
        line = node.start_point[0] + 1
        self.fields.append(
            FieldEntry(
                id=field_id(cls.full_name, _text(name_node, self.src), self.rel, line),
                class_id=cls.id,
                name=_text(name_node, self.src),
                type_name=type_name,
                modifiers=[],
                file_path=self.rel,
                line=line,
            )
        )

    def _emit_method(self, node, cls: ClassEntry, name_override: str | None = None):
        if name_override is not None:
            name = name_override
        else:
            nn = _child(node, "property_identifier") or _child(node, "identifier")
            if nn is None:
                return
            name = _text(nn, self.src)
        formal = _child(node, "formal_parameters")
        param_types, param_names = _params(formal, self.src)
        body = _child(node, "statement_block") or _child(node, "arrow_function")
        decorators = _decorator_names(node, self.src)
        sig = method_signature(cls.full_name, name, param_types)
        start_line = node.start_point[0] + 1
        mid = method_id(sig, self.rel, start_line)
        # Param-name -> declared type, so a `param.method()` receiver can be typed
        # for the resolver (the main lever for syntax-only resolution quality).
        local_types = {
            n: t for n, t in zip(param_names, param_types) if t and t != "unknown"
        }
        self.methods.append(
            MethodEntry(
                id=mid,
                class_id=cls.id,
                class_full_name=cls.full_name,
                method_name=name,
                return_type=_return_type(node, self.src),
                parameter_types=param_types,
                parameter_names=param_names,
                signature=sig,
                file_path=self.rel,
                start_line=start_line,
                end_line=node.end_point[0] + 1,
                source=_text(node, self.src),
                class_context={"stereotypes": cls.stereotypes},
                annotations=decorators,
                local_variable_types=local_types,
                is_endpoint=any(d in _HTTP_DECORATORS for d in decorators),
                language="typescript",
                framework_role=_framework_role(name, body, decorators),
            )
        )
        if body is not None:
            self._emit_callsites(body, mid, local_types)

    def _emit_function(self, node):
        """Top-level function_declaration -> method on the synthetic module class."""
        ident = _child(node, "identifier")
        if ident is None:
            return
        self._emit_method(
            node, self._module_class_entry(), name_override=_text(ident, self.src)
        )

    def _emit_lexical(self, node):
        """const X = () => {...} / function () {...} -> module-level method."""
        for decl in _children(node, "variable_declarator"):
            ident = _child(decl, "identifier")
            if ident is None:
                continue
            value = decl.children[-1] if decl.children else None
            if value is None or value.type not in ("arrow_function", "function"):
                continue
            self._emit_method(
                value, self._module_class_entry(), name_override=_text(ident, self.src)
            )

    # ── call sites ────────────────────────────────────────────────────────────

    def _emit_callsites(self, root, caller_mid: str, local_types: dict[str, str]):
        stack = list(root.children)
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                self._emit_one_call(n, caller_mid, local_types)
            stack.extend(n.children)

    def _emit_one_call(self, node, caller_mid: str, local_types: dict[str, str]):
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        receiver = None
        receiver_type_raw = None
        if fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            prop = fn.child_by_field_name("property")
            callee = _text(prop, self.src) if prop is not None else "<unknown>"
            obj_text = _text(obj, self.src) if obj is not None else None
            # `this.foo()` is an implicit same-class call — the shared resolver
            # resolves a None receiver within the caller's class. For a typed
            # local/param receiver, hand the resolver its declared type.
            if obj_text == "this":
                receiver = None
            else:
                receiver = obj_text
                if obj_text in local_types:
                    receiver_type_raw = local_types[obj_text]
        elif fn.type == "identifier":
            callee = _text(fn, self.src)
        else:
            return
        args = node.child_by_field_name("arguments")
        argc = (
            len([c for c in args.children if c.type not in ("(", ")", ",")])
            if args is not None
            else 0
        )
        line = node.start_point[0] + 1
        col = node.start_point[1]
        self.callsites.append(
            CallSite(
                id=callsite_id(caller_mid, line, col, callee),
                caller_method_id=caller_mid,
                callee_name=callee,
                receiver=receiver,
                argument_count=argc,
                file_path=self.rel,
                line=line,
                column=col,
                text=_text(node, self.src)[:200],
                receiver_type_raw=receiver_type_raw,
            )
        )

    # ── imports ────────────────────────────────────────────────────────────────

    def _collect_import(self, node):
        src_str = _child(node, "string")
        if src_str is not None:
            frag = _child(src_str, "string_fragment")
            if frag is not None:
                self.imports.append(_text(frag, self.src))

    # ── top-level walk ─────────────────────────────────────────────────────────

    def run(self, root):
        # First pass: imports (so classes/methods can carry them).
        for n in root.children:
            if n.type == "import_statement":
                self._collect_import(n)
        for n in root.children:
            self._dispatch_top(n)

    def _dispatch_top(self, n, decorators: list[str] | None = None):
        decorators = decorators or []
        if n.type == "export_statement":
            inner_decorators = _decorator_names(n, self.src)
            for c in n.children:
                if c.type in (
                    "class_declaration",
                    "interface_declaration",
                    "function_declaration",
                    "lexical_declaration",
                ):
                    self._dispatch_top(c, inner_decorators)
            return
        if n.type == "class_declaration":
            self._emit_class(n, decorators or _decorator_names(n, self.src))
        elif n.type == "interface_declaration":
            self._emit_class(n, decorators, is_interface=True)
        elif n.type == "function_declaration":
            self._emit_function(n)
        elif n.type == "lexical_declaration":
            self._emit_lexical(n)


def _iter_ts_files(codebase_root: Path):
    candidates = []
    for path in codebase_root.rglob("*"):
        if path.suffix not in (".ts", ".tsx"):
            continue
        if path.name.endswith(".d.ts"):
            continue
        if _IGNORE_DIRS & set(path.parts):
            continue
        candidates.append(path)
    yield from apply_filters(candidates, codebase_root)


def build_ts_graph_treesitter(
    codebase_root: Path, on_progress: Callable[[int], None] | None = None
) -> Graph:
    """Build a TypeScript Graph in-process. Call sites are emitted unresolved;
    `extractor._resolve_calls` resolves them after the merge, like other
    tree-sitter languages."""
    parser_ts = make_ts_parser(tsx=False)
    parser_tsx = make_ts_parser(tsx=True)

    classes: list[ClassEntry] = []
    methods: list[MethodEntry] = []
    fields: list[FieldEntry] = []
    callsites: list[CallSite] = []
    inheritance: list[InheritanceEdge] = []

    count = 0
    for path in _iter_ts_files(codebase_root):
        try:
            src = path.read_bytes()
        except OSError:
            continue
        rel = path.relative_to(codebase_root).as_posix()
        parser = parser_tsx if path.suffix == ".tsx" else parser_ts
        tree = parser.parse(src)
        fx = _FileExtractor(rel, src)
        fx.run(tree.root_node)
        classes.extend(fx.classes)
        methods.extend(fx.methods)
        fields.extend(fx.fields)
        callsites.extend(fx.callsites)
        inheritance.extend(fx.inheritance)
        count += 1
        if on_progress:
            on_progress(count)

    graph = Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=callsites,
        inheritance_edges=inheritance,
        resolved_call_edges=[],
    )
    # Reuse the shared resolver (our record shapes match the Java extractor's).
    # Lazy import avoids the extractor -> ts_extractor -> ts_treesitter cycle at
    # module load. The merge path in build_graph re-resolves for multi-language
    # repos; for TS-only it returns this graph directly, so resolve here too.
    from .extractor import _resolve_calls

    _resolve_calls(graph)
    return graph
