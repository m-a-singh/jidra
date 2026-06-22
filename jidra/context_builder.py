from __future__ import annotations

from pathlib import Path


NOISY_RECEIVER_PREFIXES = (
    "java.",
    "javax.",
    "jakarta.",
    "org.slf4j.",
    "ch.qos.logback.",
    "reactor.core.publisher.",
    "reactor.util.",
    "io.micrometer.",
)

PY_NOISY_RECEIVER_PREFIXES = (
    "logging.",
    "typing.",
    "collections.abc.",
    "functools.",
    "itertools.",
)

TS_NOISY_RECEIVER_PREFIXES = (
    "console.",
    "rxjs.",
    "lodash.",
    "util.",
)

_NOISY_RECEIVER_PREFIXES_BY_LANGUAGE = {
    "java": NOISY_RECEIVER_PREFIXES,
    "python": PY_NOISY_RECEIVER_PREFIXES,
    "typescript": TS_NOISY_RECEIVER_PREFIXES,
}

NOISY_RECEIVER_TYPES = {
    "String",
    "Boolean",
    "Integer",
    "Long",
    "Double",
    "Float",
    "Object",
    "List",
    "Map",
    "Set",
    "Collection",
    "Optional",
    "Stream",
    "Mono",
    "Flux",
}

NOISY_METHOD_NAMES = {
    "debug",
    "info",
    "warn",
    "error",
    "trace",
    "isDebugEnabled",
    "isInfoEnabled",
    "map",
    "flatMap",
    "filter",
    "forEach",
    "stream",
    "collect",
    "toList",
    "isPresent",
    "orElse",
    "orElseGet",
    "orElseThrow",
    "get",
    "put",
    "add",
    "remove",
    "clear",
    "size",
    "isEmpty",
    "toString",
    "equals",
    "hashCode",
}

FLUENT_CHAIN_CALLS = {
    "and",
    "map",
    "flatMap",
    "filter",
    "doOnNext",
    "doOnError",
    "doOnCancel",
    "doOnTerminate",
    "subscribeOn",
    "elapsed",
}

LOW_PRIORITY_SIGNATURE_PARTS = (
    ".metrics.",
    ".datadog.",
    ".config.datadog.",
    ".prometheus.",
    ".utils.",
    ".model.",
)

LAMBDA_LOCAL_RECEIVER_NAMES = {
    "p",
    "t",
    "ex",
    "e",
    "err",
    "error",
    "throwable",
    "result",
    "response",
    "item",
    "entry",
}

NOISY_UNRESOLVED_CALL_NAMES = {
    "containerResponse",
    "entityId",
    "entityType",
    "getT1",
    "getT2",
    "getClass",
    "getMessage",
    "getSimpleName",
    "isCacheHit",
    "value",
}


def _simple_type_name(type_name):
    if not type_name:
        return ""
    base = type_name.split("<", 1)[0]
    return base.split(".")[-1]


def _is_noisy_callsite(callsite, language: str = "java"):
    receiver_type = (
        getattr(callsite, "receiver_type_normalized", None)
        or getattr(callsite, "receiver_type", None)
        or getattr(callsite, "receiver_type_raw", None)
    )
    callee_name = getattr(callsite, "callee_name", "")
    status = getattr(callsite, "resolution_status", "")
    receiver = str(getattr(callsite, "receiver", "") or "")

    if callee_name in NOISY_METHOD_NAMES:
        return True

    prefixes = _NOISY_RECEIVER_PREFIXES_BY_LANGUAGE.get(
        language, NOISY_RECEIVER_PREFIXES
    )

    if receiver_type:
        simple = _simple_type_name(receiver_type)
        if simple in NOISY_RECEIVER_TYPES:
            return True
        if any(str(receiver_type).startswith(prefix) for prefix in prefixes):
            return True

    if receiver and any(
        receiver == prefix.rstrip(".") or receiver.startswith(prefix)
        for prefix in prefixes
    ):
        return True

    if status == "external_library":
        return True

    return False


def _filter_context_calls(callsites, language: str = "java"):
    return [c for c in callsites if not _is_noisy_callsite(c, language=language)]


def _language_for_file_path(file_path: str) -> str:
    ext = Path(file_path or "").suffix.lower()
    if ext == ".py":
        return "python"
    if ext in (".ts", ".tsx"):
        return "typescript"
    return "java"


def _resolved_priority(item: dict) -> int:
    sig = (item.get("target_signature") or "").lower()
    if any(part in sig for part in LOW_PRIORITY_SIGNATURE_PARTS):
        return 2
    if ".util." in sig or ".utils." in sig:
        return 1
    return 0


def _dedupe_and_sort_resolved(callsites, method_by_id: dict) -> list[dict]:
    grouped: dict[str, dict] = {}
    for callsite in callsites:
        if not callsite.resolved_candidates:
            continue
        target_id = callsite.resolved_candidates[0]
        target_method = method_by_id.get(target_id)
        target_signature = target_method.signature if target_method else None
        key = target_signature or target_id or callsite.callee_name
        entry = grouped.get(key)
        if not entry:
            entry = {
                "call": callsite.callee_name,
                "target_id": target_id,
                "target_signature": target_signature,
                "_lines": set(),
                "_count": 0,
            }
            grouped[key] = entry
        entry["_count"] += 1
        if hasattr(callsite, "line") and callsite.line is not None:
            entry["_lines"].add(callsite.line)

    items: list[dict] = []
    for entry in grouped.values():
        lines = sorted(entry.pop("_lines"))
        count = entry.pop("_count")
        if lines:
            entry["lines"] = lines
        if count > 1:
            entry["count"] = count
        items.append(entry)

    return sorted(
        items,
        key=lambda item: (
            _resolved_priority(item),
            item.get("target_signature") or item.get("call") or "",
        ),
    )


def _dedupe_and_group_unresolved(callsites) -> list[dict]:
    grouped_fluent: dict[tuple[str, str], int] = {}
    grouped_other: dict[tuple[str, str, str], dict] = {}

    for callsite in callsites:
        if callsite.resolved_candidates:
            continue
        call = callsite.callee_name
        reason = callsite.resolution_status
        receiver = callsite.receiver

        if _is_noisy_unresolved_lambda_call(call, reason, receiver):
            continue

        if call in FLUENT_CHAIN_CALLS:
            key = (call, reason)
            grouped_fluent[key] = grouped_fluent.get(key, 0) + 1
            continue

        key = (call, str(receiver), reason)
        entry = grouped_other.get(key)
        if not entry:
            entry = {"call": call, "receiver": receiver, "reason": reason, "_count": 0}
            grouped_other[key] = entry
        entry["_count"] += 1

    unresolved: list[dict] = []
    for entry in grouped_other.values():
        count = entry.pop("_count")
        if count > 1:
            entry["count"] = count
        unresolved.append(entry)

    unresolved.sort(key=lambda item: item.get("call") or "")

    fluent_items = []
    for (call, reason), count in grouped_fluent.items():
        fluent_items.append(
            {
                "call": call,
                "reason": reason,
                "count": count,
                "category": "fluent_chain_unresolved",
            }
        )
    fluent_items.sort(key=lambda item: item["call"])
    return unresolved + fluent_items


def _is_noisy_unresolved_lambda_call(call: str, reason: str, receiver) -> bool:
    if reason != "unresolved_receiver":
        return False

    if call in NOISY_UNRESOLVED_CALL_NAMES:
        return True

    recv = str(receiver or "").strip()
    if not recv:
        return False

    recv_lower = recv.lower()
    if recv_lower in LAMBDA_LOCAL_RECEIVER_NAMES:
        return True

    for name in LAMBDA_LOCAL_RECEIVER_NAMES:
        if recv_lower.startswith(f"{name}."):
            return True

    return False


def build_method_context(
    graph, method_id: str, max_chars: int = 12000, language: str | None = None
) -> dict:
    method = next((m for m in graph.methods if m.id == method_id), None)
    if not method:
        return {"error": f"method_not_found:{method_id}"}
    if language is None:
        language = _language_for_file_path(getattr(method, "file_path", ""))
    class_entry = next((c for c in graph.classes if c.id == method.class_id), None)
    callsites = [c for c in graph.callsites if c.caller_method_id == method_id]
    callsites = _filter_context_calls(callsites, language=language)
    method_by_id = {m.id: m for m in graph.methods}
    resolved = _dedupe_and_sort_resolved(callsites, method_by_id)
    unresolved = _dedupe_and_group_unresolved(callsites)
    ctx = {
        "method_signature": method.signature,
        "method_source": method.source,
        "class_annotations": class_entry.annotations if class_entry else [],
        "class_stereotype": (
            class_entry.stereotypes[0]
            if class_entry and class_entry.stereotypes
            else "unknown"
        ),
        "endpoint": {
            "is_endpoint": method.is_endpoint,
            "http_method": method.http_method,
            "route": method.route,
            "full_route": method.full_route,
        },
        "resolved_callees": resolved,
        "unresolved_calls": unresolved,
        "referenced_fields": sorted(set(method.field_reads + method.field_writes)),
        "relevant_imports": (class_entry.imports[:20] if class_entry else []),
    }
    text = str(ctx)
    if len(text) > max_chars:
        keep = max(300, max_chars // 3)
        ctx["method_source"] = _truncate_method_source(method.source or "", keep)
    return ctx


def _truncate_method_source(source: str, keep: int) -> str:
    lines = source.splitlines()
    tail_lines = lines[-3:] if len(lines) > 3 else lines
    tail_text = "\n".join(tail_lines)

    marker_template = "\n... [{n} lines omitted] ...\n"
    reserve = len(tail_text) + len(marker_template.format(n=len(lines)))
    head_budget = max(0, keep - reserve)
    head = source[:head_budget]

    omitted = max(0, len(lines) - len(head.splitlines()) - len(tail_lines))
    if omitted == 0:
        return source

    marker = marker_template.format(n=omitted)
    return head + marker + tail_text
