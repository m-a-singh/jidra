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
            m
            for m in methods
            if m.class_full_name == class_sel and m.method_name == method_sel
        ]
        if by_full_class:
            return by_full_class

        by_short_class = [
            m
            for m in methods
            if m.class_full_name.split(".")[-1] == class_sel
            and m.method_name == method_sel
        ]
        if by_short_class:
            return by_short_class

    return [m for m in methods if m.method_name == selector]


def _fuzzy_suggestions(graph: Graph, selector: str, top_n: int = 5) -> list[dict]:
    """
    Return top_n fuzzy matches for selector across method names, class names, and file paths.
    Scored by: exact substring match > class name contains selector > token overlap.
    Claude should pick the best match and retry with the suggested selector.
    """
    needle = selector.lower()
    needle_tokens = set(needle.replace(".", " ").replace("_", " ").split())

    scored: list[tuple[int, MethodEntry]] = []
    for m in graph.methods:
        method_lower = m.method_name.lower()
        class_lower = m.class_full_name.lower()
        class_short = m.class_full_name.split(".")[-1].lower()
        file_lower = m.file_path.lower()

        score = 0
        # Exact substring in method name
        if needle in method_lower:
            score += 100
        # Exact substring in short class name
        if needle in class_short:
            score += 80
        # Exact substring in full class name
        if needle in class_lower:
            score += 60
        # Selector is a prefix of the method name
        if method_lower.startswith(needle):
            score += 40
        # Selector appears in file path
        if needle in file_lower:
            score += 30
        # Token overlap
        candidate_tokens = set(
            class_lower.replace(".", " ").replace("_", " ").split()
            + method_lower.replace("_", " ").split()
        )
        overlap = len(needle_tokens & candidate_tokens)
        score += overlap * 10

        if score > 0:
            scored.append((score, m))

    scored.sort(key=lambda x: -x[0])
    return [
        {
            "selector": m.id,
            "method_name": m.method_name,
            "class": m.class_full_name,
            "file": m.file_path,
            "score": s,
        }
        for s, m in scored[:top_n]
    ]


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
