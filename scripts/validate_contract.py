#!/usr/bin/env python3
"""
validate_contract.py — Validate the Python→Rust data contract before implementing the Rust crate.

Usage:
    python scripts/validate_contract.py <path-to-graph.db>

Checks that MethodData, ClassData, and CallSiteData fields are fully populated
according to the contract Rust will receive, and prints a detailed report with
population rates, warnings, and samples of sparse fields.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Allow importing from src/
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jidra.graph.graph_store import load_graph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BOLD = "\033[1m"
CYAN = "\033[36m"


def _pct(num: int, denom: int) -> float:
    return 100.0 * num / denom if denom else 0.0


def _color(pct: float) -> str:
    if pct >= 90:
        return GREEN
    if pct >= 50:
        return YELLOW
    return RED


def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    c = _color(pct)
    return f"{c}{'█' * filled}{'░' * (width - filled)}{RESET}"


def _print_field(name: str, populated: int, total: int, extra: str = "") -> None:
    pct = _pct(populated, total)
    c = _color(pct)
    bar = _bar(pct)
    print(f"  {bar} {c}{pct:5.1f}%{RESET}  {name:<40} {populated}/{total}{extra}")


def _section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {msg}")


def _error(msg: str) -> None:
    print(f"  {RED}[ERROR]{RESET} {msg}")


def _ok(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def _sample(items: list[Any], label: str, n: int = 3) -> None:
    if not items:
        return
    print(f"    {BOLD}Sample {label} (first {min(n, len(items))} of {len(items)}):{RESET}")
    for item in items[:n]:
        print(f"      {item!r}")


# ---------------------------------------------------------------------------
# MethodData validation
# ---------------------------------------------------------------------------


def validate_methods(methods: list) -> None:
    _section("MethodData")
    total = len(methods)
    print(f"  Total methods: {BOLD}{total}{RESET}")
    if total == 0:
        _warn("No methods found.")
        return

    # Field population counters
    counts: dict[str, int] = {
        f: 0
        for f in [
            "id",
            "class_id",
            "class_full_name",
            "method_name",
            "return_type",
            "parameter_types",
            "parameter_names",
            "file_path",
            "language",
            "local_variable_types",
            "field_reads",
        ]
    }

    # Specific issue tracking
    return_type_none: list[str] = []
    param_len_mismatch: list[tuple[str, int, int]] = []  # (method id, len(types), len(names))
    lvt_bad_values: list[str] = []  # method ids where lvt has non-str values
    sparse_return: list[str] = []
    sparse_param_types: list[str] = []
    sparse_param_names: list[str] = []
    sparse_file_path: list[str] = []
    sparse_language: list[str] = []

    for m in methods:
        mid = m.id or "<no-id>"

        if m.id:
            counts["id"] += 1
        if m.class_id:
            counts["class_id"] += 1
        if m.class_full_name:
            counts["class_full_name"] += 1
        if m.method_name:
            counts["method_name"] += 1

        if m.return_type is not None:
            counts["return_type"] += 1
        else:
            return_type_none.append(mid)
            sparse_return.append(mid)

        pt = m.parameter_types or []
        pn = m.parameter_names or []

        if pt:
            counts["parameter_types"] += 1
        else:
            sparse_param_types.append(mid)

        if pn:
            counts["parameter_names"] += 1
        else:
            sparse_param_names.append(mid)

        if len(pt) != len(pn):
            param_len_mismatch.append((mid, len(pt), len(pn)))

        if m.file_path:
            counts["file_path"] += 1
        else:
            sparse_file_path.append(mid)

        if m.language and m.language != "unknown":
            counts["language"] += 1
        else:
            sparse_language.append(mid)

        lvt = m.local_variable_types or {}
        if lvt:
            counts["local_variable_types"] += 1
        bad = [k for k, v in lvt.items() if not isinstance(v, str)]
        if bad:
            lvt_bad_values.append(mid)

        if m.field_reads:
            counts["field_reads"] += 1

    # Print field-level table
    print()
    for field_name in [
        "id",
        "class_id",
        "class_full_name",
        "method_name",
        "return_type",
        "parameter_types",
        "parameter_names",
        "file_path",
        "language",
        "local_variable_types",
        "field_reads",
    ]:
        # pct = _pct(counts[field_name], total)
        extra = ""
        if field_name == "return_type" and return_type_none:
            extra = f"  [{len(return_type_none)} None]"
        _print_field(field_name, counts[field_name], total, extra)

    # Warnings
    print()
    if return_type_none:
        _error(
            f"return_type is None on {len(return_type_none)} method(s) — contract requires non-None"
        )
        _sample(return_type_none, "method ids with None return_type")

    if param_len_mismatch:
        _error(
            f"parameter_types/parameter_names length mismatch on {len(param_len_mismatch)} method(s)"
        )
        for mid, nt, nn in param_len_mismatch[:3]:
            print(f"      id={mid!r}  types={nt}  names={nn}")

    if lvt_bad_values:
        _error(f"local_variable_types has non-str values on {len(lvt_bad_values)} method(s)")
        _sample(lvt_bad_values, "method ids")

    # Samples for sparse (<50%) fields
    field_sparse_map = {
        "parameter_types": sparse_param_types,
        "parameter_names": sparse_param_names,
        "file_path": sparse_file_path,
        "language": sparse_language,
    }
    for fname, sparse_list in field_sparse_map.items():
        # pct = _pct(len(sparse_list), total)
        if (total - len(sparse_list)) / total < 0.5:
            _sample(sparse_list, f"method ids missing {fname}")

    if not return_type_none and not param_len_mismatch and not lvt_bad_values:
        _ok("All critical method contract fields look good.")


# ---------------------------------------------------------------------------
# ClassData validation
# ---------------------------------------------------------------------------


def validate_classes(classes: list) -> None:
    _section("ClassData")
    total = len(classes)
    print(f"  Total classes: {BOLD}{total}{RESET}")
    if total == 0:
        _warn("No classes found.")
        return

    counts: dict[str, int] = {
        f: 0
        for f in [
            "id",
            "full_name",
            "package_name",
            "file_path",
            "stereotypes",
            "implements",
            "extends",
            "imports",
        ]
    }

    java_total = 0
    java_imports_empty: list[str] = []
    sparse_imports: list[str] = []
    sparse_package: list[str] = []
    sparse_file: list[str] = []

    for c in classes:
        cid = c.id or "<no-id>"

        if c.id:
            counts["id"] += 1
        if c.full_name:
            counts["full_name"] += 1
        if c.package_name:
            counts["package_name"] += 1
        else:
            sparse_package.append(cid)
        if c.file_path:
            counts["file_path"] += 1
        else:
            sparse_file.append(cid)
        if c.stereotypes:
            counts["stereotypes"] += 1
        if c.implements:
            counts["implements"] += 1
        if c.extends is not None:
            counts["extends"] += 1
        if c.imports:
            counts["imports"] += 1
        else:
            sparse_imports.append(cid)

        lang = getattr(c, "language", "unknown")
        if lang == "java":
            java_total += 1
            if not c.imports:
                java_imports_empty.append(cid)

    print()
    for field_name in [
        "id",
        "full_name",
        "package_name",
        "file_path",
        "stereotypes",
        "implements",
        "extends",
        "imports",
    ]:
        # pct = _pct(counts[field_name], total)
        extra = ""
        if field_name == "imports":
            extra = f"  [{total - counts['imports']} empty]"
        _print_field(field_name, counts[field_name], total, extra)

    print()
    if java_imports_empty:
        _error(
            f"[CRITICAL] imports is empty on {len(java_imports_empty)}/{java_total} Java class(es)"
        )
        _sample(java_imports_empty, "Java class ids with empty imports")
    elif java_total > 0:
        _ok(f"imports populated on all {java_total} Java class(es)")

    pct_imports = _pct(counts["imports"], total)
    if pct_imports < 50:
        _warn(f"imports < 50% populated overall ({pct_imports:.1f}%)")
        _sample(sparse_imports, "class ids with empty imports")

    pct_pkg = _pct(counts["package_name"], total)
    if pct_pkg < 50:
        _warn(f"package_name < 50% populated ({pct_pkg:.1f}%)")
        _sample(sparse_package, "class ids with no package_name")


# ---------------------------------------------------------------------------
# CallSiteData validation
# ---------------------------------------------------------------------------


def validate_callsites(callsites: list) -> None:
    _section("CallSiteData")
    total = len(callsites)
    print(f"  Total call sites: {BOLD}{total}{RESET}")
    if total == 0:
        _warn("No call sites found.")
        return

    counts: dict[str, int] = {
        f: 0
        for f in [
            "id",
            "caller_method_id",
            "callee_name",
            "receiver",
            "receiver_type_raw",
            "argument_count",
            "argument_types",
            "text",
            "file_path",
        ]
    }

    receiver_none: int = 0
    arg_count_mismatch: list[str] = []
    sparse_receiver_type: list[str] = []
    sparse_text: list[str] = []
    sparse_file: list[str] = []
    resolution_counter: Counter = Counter()

    for cs in callsites:
        csid = cs.id or "<no-id>"

        if cs.id:
            counts["id"] += 1
        if cs.caller_method_id:
            counts["caller_method_id"] += 1
        if cs.callee_name:
            counts["callee_name"] += 1

        if cs.receiver is not None:
            counts["receiver"] += 1
        else:
            receiver_none += 1

        if cs.receiver_type_raw is not None:
            counts["receiver_type_raw"] += 1
        else:
            sparse_receiver_type.append(csid)

        if cs.argument_count is not None:
            counts["argument_count"] += 1

        at = cs.argument_types or []
        if at:
            counts["argument_types"] += 1

        ac = cs.argument_count or 0
        if len(at) != ac:
            arg_count_mismatch.append(csid)

        if cs.text:
            counts["text"] += 1
        else:
            sparse_text.append(csid)

        if cs.file_path:
            counts["file_path"] += 1
        else:
            sparse_file.append(csid)

        resolution_counter[cs.resolution_status] += 1

    print()
    for field_name in [
        "id",
        "caller_method_id",
        "callee_name",
        "receiver",
        "receiver_type_raw",
        "argument_count",
        "argument_types",
        "text",
        "file_path",
    ]:
        pct = _pct(counts[field_name], total)
        extra = ""
        if field_name == "receiver":
            extra = f"  [{receiver_none} None — no receiver vs static/constructor call]"
        _print_field(field_name, counts[field_name], total, extra)

    # Resolution status distribution
    print(f"\n  {BOLD}Resolution status distribution:{RESET}")
    for status, cnt in sorted(resolution_counter.items(), key=lambda x: -x[1]):
        pct = _pct(cnt, total)
        bar = _bar(pct)
        print(f"  {bar} {pct:5.1f}%  {status:<30} {cnt}")

    print()
    if arg_count_mismatch:
        _error(f"argument_types length != argument_count on {len(arg_count_mismatch)} call site(s)")
        _sample(arg_count_mismatch, "callsite ids with arg mismatch")

    pct_receiver = _pct(counts["receiver"], total)
    if pct_receiver < 50:
        _warn(
            f"receiver is None on {receiver_none}/{total} call sites ({100 - pct_receiver:.1f}%) — "
            "expected for static/constructors; verify it's not a parse gap"
        )

    pct_rtype = _pct(counts["receiver_type_raw"], total)
    if pct_rtype < 50:
        _warn(
            f"receiver_type_raw < 50% populated ({pct_rtype:.1f}%) — "
            "type resolution may be incomplete"
        )
        _sample(sparse_receiver_type, "callsite ids missing receiver_type_raw")

    if not arg_count_mismatch:
        _ok("argument_types length matches argument_count on all call sites.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-graph.db>", file=sys.stderr)
        sys.exit(1)

    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"Error: {db_path} does not exist.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{BOLD}Jidra Python→Rust Contract Validator{RESET}")
    print(f"Database: {db_path.resolve()}")

    conn = sqlite3.connect(db_path)
    try:
        graph = load_graph(conn)
    finally:
        conn.close()

    print(f"\n{BOLD}Entity counts:{RESET}")
    print(f"  Methods:    {len(graph.methods)}")
    print(f"  Classes:    {len(graph.classes)}")
    print(f"  Fields:     {len(graph.fields)}")
    print(f"  CallSites:  {len(graph.callsites)}")
    print(f"  InhEdges:   {len(graph.inheritance_edges)}")
    print(f"  ResolvedEdges: {len(graph.resolved_call_edges)}")

    validate_methods(graph.methods)
    validate_classes(graph.classes)
    validate_callsites(graph.callsites)

    _section("Done")
    print(
        f"  Validation complete. Review {RED}[ERROR]{RESET} and {YELLOW}[WARN]{RESET} lines above."
    )
    print()


if __name__ == "__main__":
    main()
