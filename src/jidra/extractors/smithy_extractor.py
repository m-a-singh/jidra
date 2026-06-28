"""Smithy IDL extraction (Phase A).

Parses `.smithy` model files directly — the source of truth for service
contracts — rather than chasing whatever a given codegen toolchain happens to
emit per language. This is intentionally a best-effort scanner over the
common subset of the Smithy 2.0 grammar (namespace, service, operation,
structure, inline `input :=`/`output :=`, `@http`, `@required`), not a full
IDL parser: mixins, resource shapes, traits beyond `@http`/`@required`, and
list/map member types are not modeled. Unparsed constructs are skipped, never
guessed at — consistent with the rest of JIDRA only emitting edges it can
support from the source.

See https://smithy.io/2.0/spec/idl.html for the grammar this approximates.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import SmithyMemberEntry, SmithyOperationEntry, SmithyShapeEntry

_EXCLUDED_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".jidra"}

_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.MULTILINE)
# Matches `[@trait(...) ...] (service|operation|structure|union) Name {`
_SHAPE_HEADER_RE = re.compile(
    r"(?:@\w+(?:\([^)]*\))?\s*)*\b(service|operation|structure|union)\s+(\w+)\s*\{"
)
_HTTP_TRAIT_RE = re.compile(
    r'@http\(\s*method:\s*"(\w+)"\s*,\s*uri:\s*"([^"]+)"\s*\)'
)
_OPERATIONS_LIST_RE = re.compile(r"operations\s*:\s*\[([^\]]*)\]", re.DOTALL)
_INPUT_NAMED_RE = re.compile(r"\binput\s*:\s*(\w+)")
_OUTPUT_NAMED_RE = re.compile(r"\boutput\s*:\s*(\w+)")
_ERRORS_RE = re.compile(r"\berrors\s*:\s*\[([^\]]*)\]", re.DOTALL)
_MEMBER_RE = re.compile(
    r"(@required\s*)?\b(\w+)\s*:\s*([\w.#]+)", re.MULTILINE
)


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _find_matching_brace(text: str, open_brace_index: int) -> int:
    """Index just past the `{` at `open_brace_index`'s matching `}`."""
    depth = 0
    for i in range(open_brace_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _extract_inline_block(body: str, keyword: str) -> str | None:
    """For `input := { ... }` / `output := { ... }` inline syntax, return the
    block's inner text, or None if not present in this form."""
    m = re.search(rf"\b{keyword}\s*:=\s*\{{", body)
    if not m:
        return None
    open_idx = body.index("{", m.end() - 1)
    end_idx = _find_matching_brace(body, open_idx)
    return body[open_idx + 1 : end_idx - 1]


def _parse_members(body: str) -> list[SmithyMemberEntry]:
    # Strip traits-with-arguments (e.g. `@length(min: 3)`) before scanning for
    # members -- their `key: value` argument syntax otherwise looks identical
    # to a member declaration. Bare traits like `@required` are left alone;
    # `_MEMBER_RE` captures that one specifically as the `required` group.
    body = re.sub(r"@\w+\([^)]*\)\s*", "", body)
    members = []
    for m in _MEMBER_RE.finditer(body):
        required, name, target = m.group(1), m.group(2), m.group(3)
        if name in ("input", "output", "errors", "operations", "resources"):
            continue
        members.append(
            SmithyMemberEntry(name=name, target_shape=target, required=bool(required))
        )
    return members


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def parse_smithy_text(text: str, file_path: str) -> tuple[
    list[SmithyShapeEntry], list[SmithyOperationEntry]
]:
    """Parse one `.smithy` file's text. Best-effort: malformed or unsupported
    constructs are skipped rather than raised on, since a model file mixing
    supported and unsupported shapes should still yield what it can."""
    text = _strip_comments(text)
    ns_match = _NAMESPACE_RE.search(text)
    namespace = ns_match.group(1) if ns_match else "unknown"

    shapes: list[SmithyShapeEntry] = []
    operations: list[SmithyOperationEntry] = []
    # service_name -> list of operation simple names, filled in as we see
    # `service` shapes, then back-attached to operations in a second pass.
    service_operations: dict[str, list[str]] = {}
    inline_shapes: list[SmithyShapeEntry] = []
    pending_operations: list[tuple[str, str, dict]] = []  # (name, body, meta)

    for header in _SHAPE_HEADER_RE.finditer(text):
        kind, name = header.group(1), header.group(2)
        open_idx = text.index("{", header.end() - 1)
        end_idx = _find_matching_brace(text, open_idx)
        body = text[open_idx + 1 : end_idx - 1]
        line = _line_of(text, header.start())
        shape_id = f"{namespace}#{name}"

        if kind == "service":
            ops_match = _OPERATIONS_LIST_RE.search(body)
            op_names = []
            if ops_match:
                # Smithy allows whitespace-separated list items with no commas.
                op_names = [n for n in re.split(r"[,\s]+", ops_match.group(1)) if n]
            service_operations[name] = op_names
            continue

        if kind in ("structure", "union"):
            shapes.append(
                SmithyShapeEntry(
                    id=shape_id,
                    namespace=namespace,
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    line=line,
                    members=_parse_members(body),
                )
            )
            continue

        if kind == "operation":
            # The header regex's leading trait group already consumes any
            # `@http(...)` immediately preceding `operation Name {`, so it's
            # part of header.group(0), not text before header.start().
            http_match = _HTTP_TRAIT_RE.search(header.group(0))
            http_method = http_match.group(1) if http_match else None
            http_uri = http_match.group(2) if http_match else None

            input_shape_id = None
            inline_input = _extract_inline_block(body, "input")
            if inline_input is not None:
                inline_name = f"{name}Input"
                input_shape_id = f"{namespace}#{inline_name}"
                inline_shapes.append(
                    SmithyShapeEntry(
                        id=input_shape_id,
                        namespace=namespace,
                        name=inline_name,
                        kind="structure",
                        file_path=file_path,
                        line=line,
                        members=_parse_members(inline_input),
                    )
                )
            else:
                named = _INPUT_NAMED_RE.search(body)
                if named:
                    input_shape_id = f"{namespace}#{named.group(1)}"

            output_shape_id = None
            inline_output = _extract_inline_block(body, "output")
            if inline_output is not None:
                inline_name = f"{name}Output"
                output_shape_id = f"{namespace}#{inline_name}"
                inline_shapes.append(
                    SmithyShapeEntry(
                        id=output_shape_id,
                        namespace=namespace,
                        name=inline_name,
                        kind="structure",
                        file_path=file_path,
                        line=line,
                        members=_parse_members(inline_output),
                    )
                )
            else:
                named = _OUTPUT_NAMED_RE.search(body)
                if named:
                    output_shape_id = f"{namespace}#{named.group(1)}"

            errors_match = _ERRORS_RE.search(body)
            errors = (
                [e for e in re.split(r"[,\s]+", errors_match.group(1)) if e]
                if errors_match
                else []
            )

            pending_operations.append(
                (
                    name,
                    shape_id,
                    {
                        "line": line,
                        "input_shape_id": input_shape_id,
                        "output_shape_id": output_shape_id,
                        "errors": errors,
                        "http_method": http_method,
                        "http_uri": http_uri,
                    },
                )
            )

    op_to_service: dict[str, str] = {}
    for service_name, op_names in service_operations.items():
        for op_name in op_names:
            op_to_service[op_name] = service_name

    for op_name, shape_id, meta in pending_operations:
        service_name = op_to_service.get(op_name)
        operations.append(
            SmithyOperationEntry(
                id=shape_id,
                namespace=namespace,
                name=op_name,
                service_id=f"{namespace}#{service_name}" if service_name else None,
                service_name=service_name,
                input_shape_id=meta["input_shape_id"],
                output_shape_id=meta["output_shape_id"],
                file_path=file_path,
                line=meta["line"],
                errors=meta["errors"],
                http_method=meta["http_method"],
                http_uri=meta["http_uri"],
            )
        )

    return shapes + inline_shapes, operations


def iter_smithy_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.smithy"):
        if not (set(path.parts) & _EXCLUDED_DIRS):
            files.append(path)
    return sorted(files)


def parse_smithy_files(
    files: list[Path],
) -> tuple[list[SmithyShapeEntry], list[SmithyOperationEntry]]:
    all_shapes: list[SmithyShapeEntry] = []
    all_operations: list[SmithyOperationEntry] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        shapes, operations = parse_smithy_text(text, str(path))
        all_shapes.extend(shapes)
        all_operations.extend(operations)
    return all_shapes, all_operations


def build_smithy_graph(
    codebase_root: Path,
) -> tuple[list[SmithyShapeEntry], list[SmithyOperationEntry]]:
    files = iter_smithy_files(codebase_root)
    if not files:
        return [], []
    return parse_smithy_files(files)
