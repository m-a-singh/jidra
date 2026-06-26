"""Phase B: bridge real (handwritten) classes to the Smithy operations they
implement, via known public codegen toolchains' naming conventions.

There is no per-company "house" Smithy codegen in meaningful use — almost the
entire ecosystem runs on a small, fixed set of open-source toolchains. We
target exactly those, by name, the same way JIDRA's framework-aware
extraction targets Spring/React/Angular by name rather than asking a caller
to describe their annotation conventions:

  - Java:  smithy-java (AWS). Confirmed naming convention from the project's
    own basic-server example: operation `GetBeer` -> generated interfaces
    `GetBeerOperation` / `GetBeerOperationAsync`, which a handwritten handler
    class implements directly.
  - Scala: smithy4s (the de facto standard; no official AWS Scala codegen
    exists). Confirmed convention: a `service` shape's name becomes the
    generated trait name verbatim (e.g. `AdminService[F[_]]`); a handwritten
    implementation extends it directly.

If a repo's generated code doesn't match either profile, this silently
no-ops for that class rather than guessing at a link — consistent with
JIDRA's existing "don't fabricate edges" stance (e.g. Spring Actuator
validation, business-only filtering).
"""

from __future__ import annotations

from .models import (
    ClassEntry,
    SmithyOperationEntry,
    SmithyOperationLink,
    smithy_operation_link_id,
)

_JAVA_OPERATION_SUFFIXES = ("OperationAsync", "Operation")


def _strip_generics_and_package(name: str) -> str:
    simple = name.split(".")[-1]
    return simple.split("<")[0].split("[")[0].strip()


def _link(
    op: SmithyOperationEntry, cls: ClassEntry, link_type: str, profile: str
) -> SmithyOperationLink:
    return SmithyOperationLink(
        id=smithy_operation_link_id(op.id, cls.id, link_type),
        operation_id=op.id,
        class_id=cls.id,
        class_full_name=cls.full_name,
        link_type=link_type,
        codegen_profile=profile,
        language=cls.language,
        file_path=cls.file_path,
        line=cls.start_line,
    )


def link_operations(
    classes: list[ClassEntry], operations: list[SmithyOperationEntry]
) -> list[SmithyOperationLink]:
    by_operation_name: dict[str, list[SmithyOperationEntry]] = {}
    by_service_name: dict[str, list[SmithyOperationEntry]] = {}
    for op in operations:
        by_operation_name.setdefault(op.name, []).append(op)
        if op.service_name:
            by_service_name.setdefault(op.service_name, []).append(op)

    links: list[SmithyOperationLink] = []
    for cls in classes:
        targets = list(cls.implements)
        if cls.extends:
            targets.append(cls.extends)
        if not targets:
            continue

        for raw_target in targets:
            simple = _strip_generics_and_package(raw_target)

            # smithy-java: implements `<OperationName>Operation[Async]`.
            if cls.language == "java":
                for suffix in _JAVA_OPERATION_SUFFIXES:
                    if simple.endswith(suffix) and len(simple) > len(suffix):
                        op_name = simple[: -len(suffix)]
                        for op in by_operation_name.get(op_name, []):
                            links.append(_link(op, cls, "implements", "smithy_java"))
                        break

            # smithy4s: extends/implements a service trait named exactly like
            # the Smithy `service` shape (e.g. `AdminService[IO]`).
            if cls.language == "scala" and simple in by_service_name:
                for op in by_service_name[simple]:
                    links.append(_link(op, cls, "implements", "smithy4s"))

    return links
