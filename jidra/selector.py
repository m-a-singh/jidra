from __future__ import annotations

from .models import Graph, MethodEntry


def _format_candidate(method: MethodEntry) -> str:
    return (
        f"{method.id} | {method.class_full_name}.{method.method_name} | "
        f"{method.signature} | {method.file_path}:{method.start_line}"
    )


def _resolve_method_selector(graph: Graph, selector: str) -> list[MethodEntry]:
    methods = graph.methods

    by_id = [m for m in methods if m.id == selector]
    if by_id:
        return by_id

    by_signature = [m for m in methods if m.signature == selector]
    if by_signature:
        return by_signature

    if "." in selector:
        class_sel, method_sel = selector.rsplit(".", 1)
        by_full_class = [
            m for m in methods if m.class_full_name == class_sel and m.method_name == method_sel
        ]
        if by_full_class:
            return by_full_class

        by_short_class = [
            m
            for m in methods
            if m.class_full_name.split(".")[-1] == class_sel and m.method_name == method_sel
        ]
        if by_short_class:
            return by_short_class

    return [m for m in methods if m.method_name == selector]


def _method_not_found_error(selector: str) -> str:
    return (
        f"No methods matched selector: {selector}\n"
        "Supported selectors: MethodEntry.id, MethodEntry.signature, "
        "full Class.method, short Class.method, bare method name (if unique)."
    )


def _method_ambiguous_error(
    selector: str, candidates: list[MethodEntry], use_flag: str = "--method"
) -> str:
    lines = [
        f"Selector matched {len(candidates)} methods: {selector}",
        "Showing first 25 candidates:",
    ]
    for method in candidates[:25]:
        lines.append(_format_candidate(method))

    lines.append("use:")
    for method in candidates[:25]:
        lines.append(f"  {use_flag} {method.id}")

    return "\n".join(lines)
