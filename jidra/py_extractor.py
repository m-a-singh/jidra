"""
Python extractor for JIDRA.

Uses libcst to parse Python code and build a call graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import libcst as cst

from .models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    ResolvedCallEdge,
    _stable_id,
    class_id,
    method_id,
    field_id,
    callsite_id,
    inheritance_edge_id,
    resolved_call_edge_id,
    method_signature,
)
from .py_filters import iter_python_files


def _get_line_number(node: cst.CSTNode, wrapper: cst.metadata.MetadataWrapper | None = None) -> int:
    """Extract line number from a CST node."""
    if wrapper:
        try:
            pos = wrapper.resolve(cst.metadata.PositionProvider).get(node)
            if pos:
                return pos.start.line
        except (AttributeError, KeyError):
            pass
    return 0


def _get_end_line_number(node: cst.CSTNode, wrapper: cst.metadata.MetadataWrapper | None = None) -> int:
    """Extract end line number from a CST node."""
    if wrapper:
        try:
            pos = wrapper.resolve(cst.metadata.PositionProvider).get(node)
            if pos:
                return pos.end.line
        except (AttributeError, KeyError):
            pass
    return 0


def _get_node_text(node: cst.CSTNode) -> str:
    """Extract source text from a CST node."""
    try:
        return node.deep_clone().deep_replace(lambda _: None).deep_replace(lambda _: None)
    except Exception:
        return ""


def _module_namespace_from_path(file_path: Path, root: Path) -> str:
    """Convert file path to module namespace.

    Example: /src/services/auth.py -> src.services.auth
    """
    rel_path = file_path.relative_to(root)
    module_parts = rel_path.with_suffix("").parts
    return ".".join(module_parts)


def _get_source_lines(source_lines: list[str], start_line: int, end_line: int) -> str:
    """Extract source code lines (1-indexed, inclusive)."""
    if not source_lines or start_line < 1 or end_line < start_line:
        return ""
    start_idx = start_line - 1  # Convert to 0-indexed
    end_idx = min(end_line, len(source_lines))
    return "\n".join(source_lines[start_idx:end_idx])


class PythonExtractor(cst.CSTVisitor):
    """Visitor to extract Python code structure."""

    def __init__(self, file_path: Path, root: Path, module_namespace: str, metadata_wrapper: cst.metadata.MetadataWrapper | None = None):
        self.file_path = str(file_path.relative_to(root))
        self.root = root
        self.module_namespace = module_namespace
        self.source_lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")
        self.metadata_wrapper = metadata_wrapper
        self.classes: list[ClassEntry] = []
        self.methods: list[MethodEntry] = []
        self.fields: list[FieldEntry] = []
        self.callsites: list[CallSite] = []
        self.inheritance_edges: list[InheritanceEdge] = []
        self.imports: list[str] = []
        self.current_class: ClassEntry | None = None
        self.current_method_id: str | None = None

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        """Track import statements."""
        if node.module:
            module_name = self._module_to_string(node.module)
            if isinstance(node.names, cst.ImportStar):
                self.imports.append(f"{module_name}.*")
            else:
                for name in node.names:
                    if isinstance(name, cst.ImportAlias):
                        imported = self._name_to_string(name.name)
                        self.imports.append(f"{module_name}.{imported}")

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        """Extract class definitions."""
        class_name = node.name.value
        full_name = f"{self.module_namespace}.{class_name}"

        # Extract base classes
        bases = []
        for base in node.bases:
            base_text = self._node_to_string(base.value)
            if base_text:
                bases.append(base_text)

        # Create class entry
        class_entry = ClassEntry(
            id=class_id(full_name, self.file_path),
            package_name=self.module_namespace,
            name=class_name,
            full_name=full_name,
            file_path=self.file_path,
            start_line=_get_line_number(node, self.metadata_wrapper),
            end_line=_get_end_line_number(node, self.metadata_wrapper),
            modifiers=[],
            annotations=[],
            extends=bases[0] if bases else None,
            implements=bases[1:] if len(bases) > 1 else [],
            imports=self.imports.copy(),
            stereotypes=self._get_stereotypes(node, class_name),
        )
        self.classes.append(class_entry)

        # Inheritance edges
        if bases:
            for i, base in enumerate(bases):
                relation = "extends" if i == 0 else "implements"
                edge = InheritanceEdge(
                    id=inheritance_edge_id(full_name, base, relation),
                    source_class_id=class_entry.id,
                    source_class=full_name,
                    target_class=base,
                    relation=relation,
                )
                self.inheritance_edges.append(edge)

        # Extract fields and methods
        prev_class = self.current_class
        self.current_class = class_entry

        for statement in node.body.body:
            if isinstance(statement, cst.AnnAssign):
                # Annotated assignment (field with type hint)
                self._extract_field(statement, class_entry)
            elif isinstance(statement, cst.FunctionDef):
                # Method definition
                self._extract_method(statement, class_entry)

        self.current_class = prev_class

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Extract function definitions (only top-level if not in a class)."""
        if self.current_class is None:
            # Top-level function - wrap in synthetic module class
            self._ensure_module_class()
            self._extract_method(node, self.current_class)
        # Return False to not descend into nested functions
        return False

    def _extract_field(self, node: cst.AnnAssign, class_entry: ClassEntry) -> None:
        """Extract field from annotated assignment."""
        if isinstance(node.target, cst.Name):
            field_name = node.target.value
            type_name = self._node_to_string(node.annotation.annotation)

            field_entry = FieldEntry(
                id=field_id(class_entry.full_name, field_name, self.file_path, _get_line_number(node, self.metadata_wrapper)),
                class_id=class_entry.id,
                name=field_name,
                type_name=type_name or "unknown",
                modifiers=[],
                file_path=self.file_path,
                line=_get_line_number(node),
            )
            self.fields.append(field_entry)

    def _extract_method(self, node: cst.FunctionDef, class_entry: ClassEntry) -> None:
        """Extract method definition."""
        method_name = node.name.value
        param_types = []
        param_names = []

        # Extract parameters (skip 'self' for instance methods)
        for param in node.params.params:
            param_name = param.name.value
            # Skip 'self' and 'cls' parameters
            if param_name in ('self', 'cls'):
                continue
            param_names.append(param_name)
            if param.annotation:
                param_types.append(self._node_to_string(param.annotation.annotation) or "unknown")
            else:
                param_types.append("unknown")

        # Extract return type
        return_type = "unknown"
        if node.returns:
            return_type = self._node_to_string(node.returns.annotation) or "unknown"

        # Create method entry
        signature = method_signature(class_entry.full_name, method_name, param_types)
        start_line = _get_line_number(node, self.metadata_wrapper)
        end_line = _get_end_line_number(node, self.metadata_wrapper)
        mid = method_id(signature, self.file_path, start_line)

        method_entry = MethodEntry(
            id=mid,
            class_id=class_entry.id,
            class_full_name=class_entry.full_name,
            method_name=method_name,
            return_type=return_type,
            parameter_types=param_types,
            parameter_names=param_names,
            signature=signature,
            file_path=self.file_path,
            start_line=start_line,
            end_line=end_line,
            source=_get_source_lines(self.source_lines, start_line, end_line),
            class_context={},
            annotations=[],
            local_variable_types={},
            field_reads=[],
            field_writes=[],
            is_endpoint=False,
        )
        self.methods.append(method_entry)

        # Extract call sites
        prev_method_id = self.current_method_id
        self.current_method_id = mid
        self._extract_callsites(node, mid)
        self.current_method_id = prev_method_id

    def _extract_callsites(self, node: cst.FunctionDef, caller_method_id: str) -> None:
        """Extract call sites from a function body."""
        visitor = CallSiteVisitor(caller_method_id, self.file_path, self.metadata_wrapper)
        # Manually walk the function body to extract calls
        self._walk_for_calls(node.body, visitor)
        self.callsites.extend(visitor.callsites)

    def _walk_for_calls(self, node: cst.CSTNode, visitor: 'CallSiteVisitor') -> None:
        """Recursively walk AST nodes to find Call nodes."""
        if isinstance(node, cst.Call):
            visitor.visit_Call(node)
        # Walk children
        for child in node.children:
            self._walk_for_calls(child, visitor)

    def _ensure_module_class(self) -> None:
        """Create synthetic module class for top-level functions."""
        if self.current_class is None:
            module_class_name = self.module_namespace.split(".")[-1]
            self.current_class = ClassEntry(
                id=class_id(self.module_namespace, self.file_path),
                package_name=".".join(self.module_namespace.split(".")[:-1]),
                name=module_class_name,
                full_name=self.module_namespace,
                file_path=self.file_path,
                start_line=1,
                end_line=0,
                modifiers=[],
                annotations=[],
                extends=None,
                implements=[],
                imports=self.imports.copy(),
                stereotypes=["module"],
            )
            self.classes.append(self.current_class)

    def _get_stereotypes(self, node: cst.ClassDef, class_name: str) -> list[str]:
        """Detect stereotypes from decorators and class name."""
        stereotypes = []
        for decorator in node.decorators:
            dec_name = self._node_to_string(decorator.decorator)
            if "dataclass" in dec_name:
                stereotypes.append("dataclass")
            elif "abstractmethod" in dec_name:
                stereotypes.append("abstract")
        return sorted(set(stereotypes)) or ["generic"]

    def _module_to_string(self, node: cst.BaseExpression | cst.Attribute) -> str:
        """Convert a module reference to a string."""
        if isinstance(node, cst.Name):
            return node.value
        elif isinstance(node, cst.Attribute):
            base = self._module_to_string(node.value)
            return f"{base}.{node.attr.value}"
        return ""

    def _name_to_string(self, node: cst.BaseExpression) -> str:
        """Convert a name reference to a string."""
        if isinstance(node, cst.Name):
            return node.value
        elif isinstance(node, cst.Attribute):
            base = self._name_to_string(node.value)
            return f"{base}.{node.attr.value}"
        return ""

    def _node_to_string(self, node: cst.CSTNode | None) -> str:
        """Convert any node to string representation."""
        if node is None:
            return ""
        try:
            code = node.deep_replace(lambda _: None).deep_replace(lambda _: None)
            return code[:200]  # Cap at 200 chars for type strings
        except Exception:
            return ""


class CallSiteVisitor(cst.CSTVisitor):
    """Visitor to extract call sites from function bodies."""

    def __init__(self, caller_method_id: str, file_path: str, metadata_wrapper: cst.metadata.MetadataWrapper | None = None):
        self.caller_method_id = caller_method_id
        self.file_path = file_path
        self.metadata_wrapper = metadata_wrapper
        self.callsites: list[CallSite] = []

    def visit_Call(self, node: cst.Call) -> None:
        """Extract function/method call."""
        callee_name = "unknown"
        receiver = None
        line = _get_line_number(node, self.metadata_wrapper)
        column = 1  # libcst doesn't provide column info easily

        if isinstance(node.func, cst.Name):
            callee_name = node.func.value
        elif isinstance(node.func, cst.Attribute):
            callee_name = node.func.attr.value
            receiver_obj = node.func.value
            try:
                receiver = node.func.value.__class__.__name__
            except Exception:
                pass

        arg_count = len(node.args)

        callsite_id_val = callsite_id(self.caller_method_id, line, column, callee_name)
        callsite = CallSite(
            id=callsite_id_val,
            caller_method_id=self.caller_method_id,
            callee_name=callee_name,
            receiver=receiver,
            argument_count=arg_count,
            file_path=self.file_path,
            line=line,
            column=column,
            text="",
            receiver_type_raw=None,
            receiver_type_normalized=None,
            receiver_resolution_source=None,
            receiver_type=None,
            resolved_candidates=[],
            resolution_status="unresolved",
            resolution_reason="python_call",
            candidate_count=0,
        )
        self.callsites.append(callsite)


def build_py_graph(
    codebase_root: Path,
    on_progress: Callable[[int], None] | None = None,
) -> Graph:
    """Build a JIDRA Graph from a Python codebase."""
    codebase_root = Path(codebase_root).resolve()

    all_classes: list[ClassEntry] = []
    all_methods: list[MethodEntry] = []
    all_fields: list[FieldEntry] = []
    all_callsites: list[CallSite] = []
    all_inheritance_edges: list[InheritanceEdge] = []

    # Discover and parse Python files
    python_files = iter_python_files(codebase_root)

    for file_path in python_files:
        try:
            source_code = file_path.read_text(encoding="utf-8", errors="replace")
            module = cst.parse_module(source_code)

            # Wrap module with metadata provider for position tracking
            wrapper = cst.metadata.MetadataWrapper(module)

            module_namespace = _module_namespace_from_path(file_path, codebase_root)
            extractor = PythonExtractor(file_path, codebase_root, module_namespace, wrapper)

            # Visit the module to extract structure with metadata
            for statement in wrapper.module.body:
                if isinstance(statement, cst.ClassDef):
                    extractor.visit_ClassDef(statement)
                elif isinstance(statement, cst.FunctionDef):
                    extractor.visit_FunctionDef(statement)
                elif isinstance(statement, cst.SimpleStatementLine):
                    for stmt in statement.body:
                        if isinstance(stmt, cst.ImportFrom):
                            extractor.visit_ImportFrom(stmt)

            all_classes.extend(extractor.classes)
            all_methods.extend(extractor.methods)
            all_fields.extend(extractor.fields)
            all_callsites.extend(extractor.callsites)
            all_inheritance_edges.extend(extractor.inheritance_edges)

            if on_progress:
                on_progress(len(all_classes))

        except Exception:
            # Skip files that fail to parse
            pass

    # Build graph
    graph = Graph(
        classes=all_classes,
        methods=all_methods,
        fields=all_fields,
        callsites=all_callsites,
        inheritance_edges=all_inheritance_edges,
        resolved_call_edges=[],
    )

    # Resolve calls (two-phase approach)
    _resolve_calls(graph)

    return graph


def _resolve_calls(graph: Graph) -> None:
    """Two-phase call resolution: collect methods, then match call sites."""
    method_registry: dict[str, str] = {}
    for method in graph.methods:
        method_registry[method.signature] = method.id

    edges: list[ResolvedCallEdge] = []

    for callsite in graph.callsites:
        # Try to match by signature
        for method in graph.methods:
            if method.method_name == callsite.callee_name:
                if len(method.parameter_types) == callsite.argument_count:
                    edge = ResolvedCallEdge(
                        id=resolved_call_edge_id(callsite.id, method.id),
                        callsite_id=callsite.id,
                        caller_method_id=callsite.caller_method_id,
                        callee_method_id=method.id,
                    )
                    edges.append(edge)
                    callsite.resolution_status = "resolved_exact"
                    callsite.resolved_candidates.append(method.id)

    graph.resolved_call_edges = sorted(edges, key=lambda e: e.id)
