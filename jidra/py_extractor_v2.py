"""
Python code extractor using AST + symbol table for better type inference.

Pipeline:
1. AST parsing
2. Symbol table + assignment tracking
3. Call resolution using scope context
4. Pyright validation
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    ResolvedCallEdge,
    class_id,
    method_id,
    field_id,
    callsite_id,
    inheritance_edge_id,
    resolved_call_edge_id,
    method_signature,
)
from .py_filters import iter_python_files
from .py_type_provider import PyrightValidator

logger = logging.getLogger(__name__)


@dataclass
class SymbolTable:
    """Tracks variable types and scopes."""
    scope_stack: list[dict[str, str]] = field(default_factory=list)

    def __init__(self):
        self.scope_stack = [{}]  # Start with global scope

    def push_scope(self):
        """Enter a new scope (function, class, etc)."""
        self.scope_stack.append({})

    def pop_scope(self):
        """Exit current scope."""
        if len(self.scope_stack) > 1:
            self.scope_stack.pop()

    def set_type(self, name: str, type_name: str):
        """Record that a variable has a type."""
        self.scope_stack[-1][name] = type_name

    def get_type(self, name: str) -> str | None:
        """Look up variable type, searching up the scope stack."""
        # Search from current scope upward to global
        for scope in reversed(self.scope_stack):
            if name in scope:
                return scope[name]
        return None


class ASTExtractor(ast.NodeVisitor):
    """Extract code structure using Python's AST."""

    def __init__(self, file_path: Path, root: Path, module_namespace: str):
        self.file_path = str(file_path.relative_to(root))
        self.root = root
        self.module_namespace = module_namespace
        self.source_lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")

        self.classes: list[ClassEntry] = []
        self.methods: list[MethodEntry] = []
        self.fields: list[FieldEntry] = []
        self.callsites: list[CallSite] = []
        self.inheritance_edges: list[InheritanceEdge] = []
        self.imports: list[str] = []

        self.current_class: ClassEntry | None = None
        self.current_method_id: str | None = None
        self.symbol_table = SymbolTable()

    def visit_Import(self, node: ast.Import):
        """Track import statements."""
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Track from imports."""
        module = node.module or ""
        for alias in node.names:
            name = alias.name
            if name == "*":
                self.imports.append(f"{module}.*")
            else:
                self.imports.append(f"{module}.{name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        """Extract class definition."""
        class_name = node.name
        full_name = f"{self.module_namespace}.{class_name}"

        # Extract base classes
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(self._get_attribute_name(base))

        # Create class entry
        class_entry = ClassEntry(
            id=class_id(full_name, self.file_path),
            package_name=self.module_namespace,
            name=class_name,
            full_name=full_name,
            file_path=self.file_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            modifiers=[],
            annotations=[],
            extends=bases[0] if bases else None,
            implements=bases[1:] if len(bases) > 1 else [],
            imports=self.imports.copy(),
            stereotypes=self._get_stereotypes(node),
        )
        self.classes.append(class_entry)

        # Inheritance edges
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

        # Extract methods and fields
        prev_class = self.current_class
        self.current_class = class_entry
        self.symbol_table.push_scope()

        for item in node.body:
            if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                self._extract_method(item, class_entry)
            elif isinstance(item, ast.AnnAssign):
                self._extract_field(item, class_entry)

        self.symbol_table.pop_scope()
        self.current_class = prev_class
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Extract top-level function."""
        if self.current_class is None:
            # Wrap in synthetic module class
            self._ensure_module_class()
            self._extract_method(node, self.current_class)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Extract async function."""
        if self.current_class is None:
            self._ensure_module_class()
            self._extract_method(node, self.current_class)
        self.generic_visit(node)

    def _extract_field(self, node: ast.AnnAssign, class_entry: ClassEntry):
        """Extract field from annotated assignment."""
        if isinstance(node.target, ast.Name):
            field_name = node.target.id
            type_name = self._get_annotation_name(node.annotation)

            field_entry = FieldEntry(
                id=field_id(class_entry.full_name, field_name, self.file_path, node.lineno),
                class_id=class_entry.id,
                name=field_name,
                type_name=type_name or "unknown",
                modifiers=[],
                file_path=self.file_path,
                line=node.lineno,
            )
            self.fields.append(field_entry)
            # Track in symbol table
            self.symbol_table.set_type(field_name, type_name or "unknown")

    def _extract_method(self, node: ast.FunctionDef | ast.AsyncFunctionDef, class_entry: ClassEntry):
        """Extract method definition."""
        method_name = node.name
        param_types = []
        param_names = []

        # Extract parameters (skip 'self' and 'cls')
        for param in node.args.args:
            param_name = param.arg
            if param_name in ("self", "cls"):
                continue
            param_names.append(param_name)

            if param.annotation:
                param_types.append(self._get_annotation_name(param.annotation) or "unknown")
            else:
                param_types.append("unknown")

        # Extract return type
        return_type = "unknown"
        if node.returns:
            return_type = self._get_annotation_name(node.returns) or "unknown"

        # Create method entry
        signature = method_signature(class_entry.full_name, method_name, param_types)
        mid = method_id(signature, self.file_path, node.lineno)

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
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            source=self._get_source_lines(node.lineno, node.end_lineno or node.lineno),
            class_context={},
            annotations=[],
            local_variable_types={},
            field_reads=[],
            field_writes=[],
            is_endpoint=False,
        )
        self.methods.append(method_entry)

        # Extract call sites from method body
        prev_method_id = self.current_method_id
        self.current_method_id = mid
        self.symbol_table.push_scope()

        # Track parameter types in scope
        for pname, ptype in zip(param_names, param_types):
            self.symbol_table.set_type(pname, ptype)

        # Visit body to extract calls and assignments
        for stmt in node.body:
            self._extract_calls_from_node(stmt, mid)
            self._track_assignments(stmt)

        self.symbol_table.pop_scope()
        self.current_method_id = prev_method_id

    def _extract_calls_from_node(self, node: ast.AST, caller_method_id: str):
        """Extract call sites from a node."""
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                self._extract_call(child, caller_method_id)

    def _extract_call(self, node: ast.Call, caller_method_id: str):
        """Extract a single call site."""
        callee_name = "unknown"
        receiver = None

        if isinstance(node.func, ast.Name):
            callee_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee_name = node.func.attr
            receiver = self._get_attribute_name(node.func.value)

        arg_count = len(node.args)

        # Infer receiver type from symbol table
        receiver_type = None
        if receiver:
            receiver_type = self.symbol_table.get_type(receiver)

        callsite = CallSite(
            id=callsite_id(caller_method_id, node.lineno, 1, callee_name),
            caller_method_id=caller_method_id,
            callee_name=callee_name,
            receiver=receiver,
            argument_count=arg_count,
            file_path=self.file_path,
            line=node.lineno,
            column=1,
            text="",
            receiver_type_raw=receiver_type,
            receiver_type_normalized=receiver_type,
            receiver_resolution_source="symbol_table" if receiver_type else None,
            receiver_type=receiver_type,
            resolved_candidates=[],
            resolution_status="unresolved",
            resolution_reason="python_call",
            candidate_count=0,
        )
        self.callsites.append(callsite)

    def _track_assignments(self, node: ast.AST):
        """Track variable assignments for type inference."""
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                # x = SomeClass(...)
                if isinstance(child.targets[0], ast.Name):
                    var_name = child.targets[0].id
                    var_type = None

                    if isinstance(child.value, ast.Call):
                        if isinstance(child.value.func, ast.Name):
                            var_type = child.value.func.id
                        elif isinstance(child.value.func, ast.Attribute):
                            var_type = self._get_attribute_name(child.value.func)

                    if var_type:
                        self.symbol_table.set_type(var_name, var_type)

            elif isinstance(child, ast.AnnAssign):
                # x: SomeType = ...
                if isinstance(child.target, ast.Name):
                    var_name = child.target.id
                    var_type = self._get_annotation_name(child.annotation)
                    if var_type:
                        self.symbol_table.set_type(var_name, var_type)

    def _ensure_module_class(self):
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

    def _get_stereotypes(self, node: ast.ClassDef) -> list[str]:
        """Detect stereotypes from decorators."""
        stereotypes = []
        for decorator in node.decorator_list:
            dec_name = ""
            if isinstance(decorator, ast.Name):
                dec_name = decorator.id
            elif isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                dec_name = decorator.func.id

            if "dataclass" in dec_name.lower():
                stereotypes.append("dataclass")

        return stereotypes or ["generic"]

    def _get_annotation_name(self, node: ast.expr | None) -> str | None:
        """Extract type name from annotation."""
        if node is None:
            return None

        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return self._get_attribute_name(node)
        elif isinstance(node, ast.Subscript):
            return self._get_annotation_name(node.value)

        return None

    def _get_attribute_name(self, node: ast.expr) -> str:
        """Get dotted name from attribute access."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_attribute_name(node.value)}.{node.attr}"
        return "unknown"

    def _get_source_lines(self, start: int, end: int) -> str:
        """Extract source code lines."""
        if start < 1 or end < start or start > len(self.source_lines):
            return ""
        return "\n".join(self.source_lines[start - 1 : end])


def build_py_graph_v2(
    codebase_root: Path,
    on_progress: Callable[[int], None] | None = None,
    enable_validation: bool = True,
) -> Graph:
    """
    Build a JIDRA Graph from Python codebase using AST + symbol table.

    Expected: 70-75% call resolution (vs 42% with libcst alone).
    """
    codebase_root = Path(codebase_root).resolve()

    all_classes: list[ClassEntry] = []
    all_methods: list[MethodEntry] = []
    all_fields: list[FieldEntry] = []
    all_callsites: list[CallSite] = []
    all_inheritance_edges: list[InheritanceEdge] = []
    validation_metrics = None

    # Run Pyright validation
    if enable_validation:
        try:
            validator = PyrightValidator(codebase_root, timeout=120)
            validation_metrics = validator.validate()
            if validation_metrics.runs > 0 and validation_metrics.failures == 0:
                logger.info(
                    f"Pyright validation: {validation_metrics.success_rate():.0f}% healthy, "
                    f"{validation_metrics.error_count} errors"
                )
        except Exception as e:
            logger.debug(f"Pyright validation unavailable: {e}")

    # Discover and parse Python files
    python_files = iter_python_files(codebase_root)

    for file_path in python_files:
        try:
            source_code = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source_code, filename=str(file_path))

            module_parts = file_path.relative_to(codebase_root).with_suffix("").parts
            module_namespace = ".".join(module_parts)

            extractor = ASTExtractor(file_path, codebase_root, module_namespace)
            extractor.visit(tree)

            all_classes.extend(extractor.classes)
            all_methods.extend(extractor.methods)
            all_fields.extend(extractor.fields)
            all_callsites.extend(extractor.callsites)
            all_inheritance_edges.extend(extractor.inheritance_edges)

            if on_progress:
                on_progress(len(all_classes))

        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
        except Exception as e:
            logger.warning(f"Error parsing {file_path}: {e}")

    # Build graph
    graph = Graph(
        classes=all_classes,
        methods=all_methods,
        fields=all_fields,
        callsites=all_callsites,
        inheritance_edges=all_inheritance_edges,
        resolved_call_edges=[],
    )

    # Resolve calls (enhanced with symbol table context)
    _resolve_calls_v2(graph)

    return graph


def _resolve_calls_v2(graph: Graph) -> None:
    """
    Two-phase call resolution using receiver type information.

    Enhanced with symbol table to match receiver types.
    Expected improvement: 42% → 70-75%.
    """
    method_registry: dict[tuple[str, str], list[MethodEntry]] = {}

    # Build registry: (class, method_name) -> [MethodEntry]
    for method in graph.methods:
        key = (method.class_full_name, method.method_name)
        if key not in method_registry:
            method_registry[key] = []
        method_registry[key].append(method)

    edges: list[ResolvedCallEdge] = []

    for callsite in graph.callsites:
        candidates: list[MethodEntry] = []

        # If we know the receiver type, try to resolve to that class's method
        if callsite.receiver_type:
            key = (callsite.receiver_type, callsite.callee_name)
            if key in method_registry:
                for method in method_registry[key]:
                    if len(method.parameter_types) == callsite.argument_count:
                        candidates.append(method)
                        callsite.resolution_status = "resolved_exact"

        # Fallback: match by name + arity across all classes
        if not candidates:
            for (class_name, method_name), methods in method_registry.items():
                if method_name == callsite.callee_name:
                    for method in methods:
                        if len(method.parameter_types) == callsite.argument_count:
                            candidates.append(method)

        # Update callsite
        callsite.resolved_candidates = sorted(m.id for m in candidates)
        callsite.candidate_count = len(candidates)

        if candidates and not callsite.resolution_status:
            callsite.resolution_status = "resolved_fallback"
        elif not candidates:
            callsite.resolution_status = "unresolved"

        # Create edges
        for callee in candidates:
            edge = ResolvedCallEdge(
                id=resolved_call_edge_id(callsite.id, callee.id),
                callsite_id=callsite.id,
                caller_method_id=callsite.caller_method_id,
                callee_method_id=callee.id,
            )
            edges.append(edge)

    graph.resolved_call_edges = sorted(edges, key=lambda e: e.id)
