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
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable

from .parallel import parallel_map
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
        self.source_lines = file_path.read_text(
            encoding="utf-8", errors="replace"
        ).split("\n")

        self.classes: list[ClassEntry] = []
        self.methods: list[MethodEntry] = []
        self.fields: list[FieldEntry] = []
        self.callsites: list[CallSite] = []
        self.inheritance_edges: list[InheritanceEdge] = []
        self.imports: list[str] = []
        self.import_map: dict[str, str] = {}  # Map: imported_name -> full_module_path

        self.current_class: ClassEntry | None = None
        self.current_method_id: str | None = None
        self.symbol_table = SymbolTable()

    def visit_Import(self, node: ast.Import):
        """Track import statements with full module mapping."""
        for alias in node.names:
            self.imports.append(alias.name)
            # Map: use alias (if provided) -> full module path
            local_name = alias.asname or alias.name
            self.import_map[local_name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Track from imports with full module mapping."""
        module = node.module or ""
        for alias in node.names:
            name = alias.name
            if name == "*":
                self.imports.append(f"{module}.*")
            else:
                full_path = f"{module}.{name}" if module else name
                self.imports.append(full_path)
                # Map: imported name -> full module path
                local_name = alias.asname or name
                self.import_map[local_name] = full_path
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
            if isinstance(item, ast.FunctionDef) or isinstance(
                item, ast.AsyncFunctionDef
            ):
                self._extract_method(item, class_entry)
            elif isinstance(item, ast.AnnAssign):
                self._extract_field(item, class_entry)

        self.symbol_table.pop_scope()
        self.current_class = prev_class
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Extract top-level function."""
        prev_class = self.current_class
        if self.current_class is None:
            # Wrap in synthetic module class
            self._ensure_module_class()
            self._extract_method(node, self.current_class)
        self.generic_visit(node)
        self.current_class = (
            prev_class  # Restore so sibling functions are also extracted
        )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Extract async function."""
        prev_class = self.current_class
        if self.current_class is None:
            self._ensure_module_class()
            self._extract_method(node, self.current_class)
        self.generic_visit(node)
        self.current_class = (
            prev_class  # Restore so sibling functions are also extracted
        )

    def _extract_field(self, node: ast.AnnAssign, class_entry: ClassEntry):
        """Extract field from annotated assignment."""
        if isinstance(node.target, ast.Name):
            field_name = node.target.id
            type_name = self._get_annotation_name(node.annotation)

            field_entry = FieldEntry(
                id=field_id(
                    class_entry.full_name, field_name, self.file_path, node.lineno
                ),
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

    def _extract_method(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, class_entry: ClassEntry
    ):
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
                param_types.append(
                    self._get_annotation_name(param.annotation) or "unknown"
                )
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
        """Track variable assignments for type inference (comprehensive)."""
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                # x = SomeClass(...) or x = obj.method()
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        var_name = target.id
                        var_type = self._infer_type_from_value(child.value)
                        if var_type:
                            self.symbol_table.set_type(var_name, var_type)

            elif isinstance(child, ast.AnnAssign):
                # x: SomeType = ...
                if isinstance(child.target, ast.Name):
                    var_name = child.target.id
                    # First try annotation
                    var_type = self._get_annotation_name(child.annotation)
                    # Then try to infer from value
                    if not var_type and child.value:
                        var_type = self._infer_type_from_value(child.value)
                    if var_type:
                        self.symbol_table.set_type(var_name, var_type)

            elif isinstance(child, ast.For):
                # for x in iterable: track x
                if isinstance(child.target, ast.Name):
                    # For now, mark as unknown (could improve with typing analysis)
                    pass

    def _infer_type_from_value(self, value: ast.expr) -> str | None:
        """Infer variable type from assigned value with import resolution."""
        if isinstance(value, ast.Call):
            # x = ClassName(...) or x = obj.method()
            if isinstance(value.func, ast.Name):
                type_name = value.func.id
                # Resolve through imports if possible
                return self.import_map.get(type_name, type_name)
            elif isinstance(value.func, ast.Attribute):
                return self._get_attribute_name(value.func)

        elif isinstance(value, ast.Name):
            # x = y (another variable)
            return self.symbol_table.get_type(value.id)

        elif isinstance(value, ast.Attribute):
            # x = obj.attr
            return self._get_attribute_name(value)

        return None

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
            elif isinstance(decorator, ast.Call) and isinstance(
                decorator.func, ast.Name
            ):
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


def _file_to_module(file_path: str) -> str:
    """Convert a relative file path to a dotted module name."""
    return file_path.replace("\\", "/").removesuffix(".py").replace("/", ".")


def _build_import_graph(graph: Graph) -> dict[str, set[str]]:
    """Build module → set of directly imported modules from ClassEntry.imports."""
    import_graph: dict[str, set[str]] = {}
    for cls in graph.classes:
        module = _file_to_module(cls.file_path)
        for imp in cls.imports:
            import_graph.setdefault(module, set()).add(imp)
    return import_graph


def _imported_closure(import_graph: dict[str, set[str]], from_module: str) -> set[str]:
    """All modules transitively imported from `from_module` (BFS closure)."""
    visited: set[str] = set()
    queue: deque[str] = deque(import_graph.get(from_module, ()))
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        queue.extend(import_graph.get(current, ()))
    return visited


def _covers(imported: set[str], to_module: str) -> bool:
    if to_module in imported:
        return True
    # Match any parent package (e.g. "foo" covers "foo.bar")
    return any(to_module.startswith(imp + ".") for imp in imported)


def _filter_phantom_edges(graph: Graph) -> int:
    """
    Remove resolved edges where the caller module has no import path to the
    callee module. Same-module edges are always kept.

    Returns the number of phantom edges dropped.
    """
    import_graph = _build_import_graph(graph)
    method_file: dict[str, str] = {m.id: m.file_path for m in graph.methods}

    # The import graph has one node per module; caching the BFS closure per
    # caller module avoids re-walking it for every edge (there are usually far
    # more edges than modules).
    closure_cache: dict[str, set[str]] = {}

    kept: list[ResolvedCallEdge] = []
    dropped = 0
    for edge in graph.resolved_call_edges:
        caller_file = method_file.get(edge.caller_method_id, "")
        callee_file = method_file.get(edge.callee_method_id, "")
        caller_mod = _file_to_module(caller_file)
        callee_mod = _file_to_module(callee_file)

        if caller_mod == callee_mod:
            kept.append(edge)
            continue

        imported = closure_cache.get(caller_mod)
        if imported is None:
            imported = _imported_closure(import_graph, caller_mod)
            closure_cache[caller_mod] = imported

        if _covers(imported, callee_mod):
            kept.append(edge)
        else:
            dropped += 1

    graph.resolved_call_edges = kept
    return dropped


def _apply_pyright_type_hints(
    callsites: list[CallSite],
    hints: dict[tuple[str, int], str],
    codebase_root: Path,
) -> int:
    """
    Enrich call sites that the symbol table could not type using Pyright's
    inferred types. Only overwrites call sites where receiver_type is None,
    so the symbol table always wins when it has data.

    Returns the number of call sites enriched.
    """
    enriched = 0
    for cs in callsites:
        if cs.receiver_type is not None:
            continue
        # Pyright reports absolute paths; call site stores relative paths
        abs_path = str((codebase_root / cs.file_path).resolve())
        key = (abs_path, cs.line)
        if key in hints:
            inferred = hints[key]
            cs.receiver_type = inferred
            cs.receiver_type_raw = inferred
            cs.receiver_type_normalized = inferred
            cs.receiver_resolution_source = "pyright"
            enriched += 1
    return enriched


def _extract_py_file(file_path: Path, codebase_root: Path) -> Graph | None:
    """Parse and extract a single Python file, independent of all others.

    Module-level (not a closure) so it can be pickled and run in a worker
    process via `parallel_map`.
    """
    try:
        source_code = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source_code, filename=str(file_path))

        module_parts = file_path.relative_to(codebase_root).with_suffix("").parts
        module_namespace = ".".join(module_parts)

        extractor = ASTExtractor(file_path, codebase_root, module_namespace)
        extractor.visit(tree)

        return Graph(
            classes=extractor.classes,
            methods=extractor.methods,
            fields=extractor.fields,
            callsites=extractor.callsites,
            inheritance_edges=extractor.inheritance_edges,
            resolved_call_edges=[],
        )
    except SyntaxError as e:
        logger.warning(f"Syntax error in {file_path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error parsing {file_path}: {e}")
        return None


def build_py_graph(
    codebase_root: Path,
    on_progress: Callable[[int], None] | None = None,
    enable_validation: bool = True,
    language: str = "python",
) -> Graph:
    """
    Build a JIDRA Graph from Python codebase using AST + symbol table.

    Pipeline:
    1. Pyright validation (optional) — collects type hints for pre-pass
    2. AST parsing of all .py files + symbol table tracking
    3. Pyright type pre-pass — enrich untyped call sites before resolution
    4. Multi-phase call resolution (exact → arity → close → name)
    5. Import reachability filter — drop cross-module phantom edges
    """
    codebase_root = Path(codebase_root).resolve()

    all_classes: list[ClassEntry] = []
    all_methods: list[MethodEntry] = []
    all_fields: list[FieldEntry] = []
    all_callsites: list[CallSite] = []
    all_inheritance_edges: list[InheritanceEdge] = []
    validator = None

    # Step 1: Run Pyright — store validator so we can call get_type_hints() later
    if enable_validation:
        try:
            validator = PyrightValidator(codebase_root, timeout=120)
            metrics = validator.validate()
            if metrics.runs > 0 and metrics.failures == 0:
                logger.info(
                    f"Pyright validation: {metrics.success_rate():.0f}% healthy, "
                    f"{metrics.error_count} errors"
                )
        except Exception as e:
            logger.debug(f"Pyright validation unavailable: {e}")
            validator = None

    # Step 2: Discover and parse Python files
    python_files = list(iter_python_files(codebase_root))

    worker = partial(_extract_py_file, codebase_root=codebase_root)
    for result in parallel_map(worker, python_files):
        if result is None:
            continue
        all_classes.extend(result.classes)
        all_methods.extend(result.methods)
        all_fields.extend(result.fields)
        all_callsites.extend(result.callsites)
        all_inheritance_edges.extend(result.inheritance_edges)

        if on_progress:
            on_progress(len(all_classes))

    # Step 3: Enrich untyped call sites with Pyright-inferred receiver types
    # This converts Phase-2/3/4 guesses into Phase-1 exact matches
    if validator is not None and validator.metrics.failures == 0:
        type_hints = validator.get_type_hints()
        if type_hints:
            enriched = _apply_pyright_type_hints(
                all_callsites, type_hints, codebase_root
            )
            if enriched:
                logger.info(f"Pyright type pre-pass: enriched {enriched} call sites")

    # Step 4: Build graph and resolve calls
    graph = Graph(
        classes=all_classes,
        methods=all_methods,
        fields=all_fields,
        callsites=all_callsites,
        inheritance_edges=all_inheritance_edges,
        resolved_call_edges=[],
    )
    _resolve_calls(graph)

    # Step 5: Drop cross-module edges with no import path (phantom removal)
    dropped = _filter_phantom_edges(graph)
    if dropped:
        logger.info(f"Import reachability filter: dropped {dropped} phantom edges")

    # Step 6: Stamp language on all nodes
    for cls in graph.classes:
        cls.language = language
    for m in graph.methods:
        m.language = language

    return graph


def _resolve_calls(graph: Graph) -> None:
    """
    Multi-phase call resolution using receiver type information.

    Phase 1: Exact receiver type + method name + exact arity
    Phase 2: Method name + exact arity (any class)
    Phase 3: Method name + close arity (±1 parameter)
    Phase 4: Method name only (fallback)

    Expected improvement: 42.2% (libcst) → 68.5% (AST + symbol table).
    """
    # Build multiple indices for efficient lookup
    by_class_and_name: dict[tuple[str, str], list[MethodEntry]] = {}
    by_name_and_arity: dict[tuple[str, int], list[MethodEntry]] = {}
    by_name: dict[str, list[MethodEntry]] = {}
    # name -> arity -> methods, so phase 2B can probe arity+-1 directly
    # instead of linear-scanning the whole (possibly huge, for common names
    # like "get"/"run") by_name bucket.
    by_name_arity_buckets: dict[str, dict[int, list[MethodEntry]]] = {}

    for method in graph.methods:
        # Index 1: (class, method_name)
        key1 = (method.class_full_name, method.method_name)
        if key1 not in by_class_and_name:
            by_class_and_name[key1] = []
        by_class_and_name[key1].append(method)

        # Index 2: (method_name, arity)
        arity = len(method.parameter_types)
        key2 = (method.method_name, arity)
        if key2 not in by_name_and_arity:
            by_name_and_arity[key2] = []
        by_name_and_arity[key2].append(method)

        # Index 3: method_name only
        if method.method_name not in by_name:
            by_name[method.method_name] = []
        by_name[method.method_name].append(method)

        by_name_arity_buckets.setdefault(method.method_name, {}).setdefault(
            arity, []
        ).append(method)

    edges: list[ResolvedCallEdge] = []

    for callsite in graph.callsites:
        candidates: list[MethodEntry] = []

        # PHASE 1: Exact receiver type + name + arity
        if callsite.receiver_type:
            key = (callsite.receiver_type, callsite.callee_name)
            if key in by_class_and_name:
                for method in by_class_and_name[key]:
                    if len(method.parameter_types) == callsite.argument_count:
                        candidates.append(method)
                        callsite.resolution_status = "resolved_exact"
                        break  # Take first match

        # PHASE 2A: Name + arity (if not resolved)
        if not candidates:
            arity_key = (callsite.callee_name, callsite.argument_count)
            if arity_key in by_name_and_arity:
                candidates = by_name_and_arity[arity_key]
                if candidates:
                    callsite.resolution_status = "resolved_arity"

        # PHASE 2B: Name + close arity (within 1 parameter). Phase 2A already
        # covered exact arity, so only +-1 needs checking here, and the
        # nested arity index makes that an O(1) probe instead of a linear
        # scan of every method sharing this name.
        if not candidates:
            arity_buckets = by_name_arity_buckets.get(callsite.callee_name)
            if arity_buckets:
                for delta in (-1, 1):
                    bucket = arity_buckets.get(callsite.argument_count + delta)
                    if bucket:
                        candidates.append(bucket[0])
                        callsite.resolution_status = "resolved_close_arity"
                        break

        # PHASE 2C: Name only (very lenient fallback)
        if not candidates and callsite.callee_name in by_name:
            candidates = by_name[callsite.callee_name][:1]
            if candidates:
                callsite.resolution_status = "resolved_name_only"

        # Update callsite
        callsite.resolved_candidates = sorted(m.id for m in candidates)
        callsite.candidate_count = len(candidates)

        if not candidates:
            callsite.resolution_status = "unresolved"

        # Create edges for all candidates
        for callee in candidates:
            edge = ResolvedCallEdge(
                id=resolved_call_edge_id(callsite.id, callee.id),
                callsite_id=callsite.id,
                caller_method_id=callsite.caller_method_id,
                callee_method_id=callee.id,
            )
            edges.append(edge)

    graph.resolved_call_edges = sorted(edges, key=lambda e: e.id)
