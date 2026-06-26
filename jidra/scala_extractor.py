"""
Scala extractor for JIDRA.

Runs the scala_sidecar Docker image which compiles the project with semanticdb-scalac,
then reads the emitted .semanticdb protobuf files to build a Graph.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
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
    callsite_id,
    class_id,
    field_id,
    inheritance_edge_id,
    method_id,
    method_signature,
    resolved_call_edge_id,
)

DOCKER_IMAGE = "jidra-scala-sidecar:latest"
SIDECAR_DIR = Path(__file__).resolve().parent.parent / "scala_sidecar"

_CONTROLLER_NAMES = {"Controller", "AbstractController"}
_SERVICE_NAMES = {"Service", "Actor"}
_REPO_SUFFIXES = ("Repository", "Repo")


class ScalaExtractorError(Exception):
    pass


def _ensure_image() -> None:
    check = subprocess.run(
        ["docker", "image", "inspect", DOCKER_IMAGE],
        capture_output=True,
    )
    if check.returncode == 0:
        return

    print(
        "  [jidra] Building scala-sidecar image (first run only — may take a few minutes)...",
        flush=True,
    )
    result = subprocess.run(
        ["docker", "build", "-t", DOCKER_IMAGE, str(SIDECAR_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ScalaExtractorError(
            f"Failed to build scala-sidecar image:\n{result.stderr[-2000:]}"
        )


def _is_new_volume(volume: str) -> bool:
    result = subprocess.run(["docker", "volume", "inspect", volume], capture_output=True)
    return result.returncode != 0


def _workspace_volume(codebase_root: Path) -> str:
    """Stable per-codebase Docker volume name, used to persist sbt's Zinc
    incremental-compile cache across separate `docker run` invocations so
    repeated indexing of the same repo only recompiles what changed."""
    h = hashlib.sha1(str(codebase_root).encode()).hexdigest()[:12]
    return f"jidra-zinc-{h}"


def _run_sidecar(codebase_root: Path, tmp_out: str, timeout: int = 600) -> None:
    try:
        _ensure_image()
    except FileNotFoundError as e:
        raise ScalaExtractorError("Docker is not available on PATH") from e

    volume = _workspace_volume(codebase_root)
    first_run = _is_new_volume(volume)

    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{codebase_root}:/repo:ro",
        "-v",
        f"{tmp_out}:/output:rw",
        "-v",
        f"{volume}:/workspace:rw",
        DOCKER_IMAGE,
        "/repo",
        "/output",
    ]

    msg = "  [jidra] Running sbt compile inside Docker"
    msg += " (this may take 30–120s, first run)..." if first_run else " (incremental via Zinc cache)..."
    print(msg, flush=True)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ScalaExtractorError(
            f"Scala sidecar timed out after {timeout}s — repo may be too large"
        ) from e
    except FileNotFoundError as e:
        raise ScalaExtractorError("Docker is not available on PATH") from e

    if result.returncode != 0:
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = "\n".join(filter(None, [err, out]))
        raise ScalaExtractorError(
            f"Scala sidecar exited with code {result.returncode}:\n{combined[-4000:]}"
        )


def _parse_package(source_path: Path) -> str:
    try:
        with open(source_path, errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                m = re.match(r"^\s*package\s+([\w.]+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def _scala_stereotypes(class_name: str, kind_value: int, pb2) -> list[str]:
    kind = pb2.SymbolInformation.Kind
    if kind_value == kind.TRAIT:
        return ["trait"]
    if kind_value == kind.OBJECT:
        return ["object"]
    if kind_value == kind.INTERFACE:
        return ["interface"]

    # Filename-based heuristics
    if class_name.endswith("Controller"):
        return ["controller"]
    if class_name.endswith("Service"):
        return ["service"]
    if class_name.endswith(("Repository", "Repo")):
        return ["repository"]
    return ["unknown"]


def _symbol_to_class_full_name(symbol: str) -> str:
    """Convert a SemanticDB symbol string to a Java-style fully-qualified class name.

    SemanticDB format: `com/example/UserService#`  →  `com.example.UserService`
    """
    # Strip trailing punctuation (# for classes, . for methods, etc.)
    s = symbol.rstrip("#.()")
    return s.replace("/", ".")


def _symbol_to_method_name(symbol: str) -> str:
    """Extract the method name from a SemanticDB method symbol.

    e.g. `com/example/UserService#findById().` → `findById`
    """
    # Strip trailing parens and dot
    s = symbol.rstrip(".")
    if s.endswith(")"):
        s = s[: s.rfind("(")]
    # Take everything after the last #
    if "#" in s:
        return s.split("#")[-1].rstrip("()")
    return s.split("/")[-1]


def _build_graph_from_semanticdb(output_root: Path, codebase_root: Path) -> Graph:
    from .scala_proto import semanticdb_pb2

    pb2 = semanticdb_pb2
    kind = pb2.SymbolInformation.Kind
    role = pb2.SymbolOccurrence

    classes: list[ClassEntry] = []
    methods: list[MethodEntry] = []
    fields: list[FieldEntry] = []
    callsites: list[CallSite] = []
    inheritance_edges: list[InheritanceEdge] = []
    resolved_call_edges: list[ResolvedCallEdge] = []

    # symbol string → MethodEntry (for call resolution pass)
    symbol_to_method: dict[str, MethodEntry] = {}
    # class symbol → ClassEntry
    symbol_to_class: dict[str, ClassEntry] = {}

    semanticdb_files = list(output_root.rglob("*.semanticdb"))

    # ── Pass 1: Definitions ──────────────────────────────────────────────────
    for sdb_path in semanticdb_files:
        docs = pb2.TextDocuments()
        try:
            docs.ParseFromString(sdb_path.read_bytes())
        except Exception:
            continue

        for doc in docs.documents:
            # Reconstruct the original source path
            uri = (
                doc.uri
            )  # relative path from sourceroot, e.g. src/main/scala/Foo.scala
            source_path = codebase_root / uri
            file_path_str = str(source_path)
            package_name = _parse_package(source_path)

            # Build symbol info lookup for this document
            sym_info: dict[str, pb2.SymbolInformation] = {
                s.symbol: s for s in doc.symbols
            }

            # Collect definition occurrences
            def_occs = [o for o in doc.occurrences if o.role == role.DEFINITION]

            for occ in def_occs:
                sym = occ.symbol
                info = sym_info.get(sym)
                if info is None:
                    continue

                k = info.kind
                line = occ.range.start_line + 1

                # ── Class/trait/object ──
                if k in (kind.CLASS, kind.TRAIT, kind.OBJECT, kind.INTERFACE):
                    cls_name = info.display_name
                    full_name = _symbol_to_class_full_name(sym)
                    if not full_name:
                        continue

                    # Gather parents from ClassSignature
                    extends_name: str | None = None
                    implements_names: list[str] = []
                    if info.signature.HasField("class_signature"):
                        csig = info.signature.class_signature
                        parents = []
                        for parent_type in csig.parents:
                            if parent_type.HasField("type_ref"):
                                parent_sym = parent_type.type_ref.symbol
                                parent_cls = _symbol_to_class_full_name(parent_sym)
                                if parent_cls and parent_cls not in (
                                    "scala.AnyRef",
                                    "java.lang.Object",
                                    "scala.Any",
                                ):
                                    parents.append(parent_cls)
                        if parents:
                            extends_name = parents[0]
                            implements_names = parents[1:]

                    end_line = occ.range.end_line + 1
                    stereotypes = _scala_stereotypes(cls_name, k, pb2)
                    cls = ClassEntry(
                        id=class_id(full_name, file_path_str),
                        package_name=package_name,
                        name=cls_name,
                        full_name=full_name,
                        file_path=file_path_str,
                        start_line=line,
                        end_line=end_line,
                        modifiers=[],
                        annotations=[],
                        extends=extends_name,
                        implements=implements_names,
                        imports=[],
                        stereotypes=stereotypes,
                        language="scala",
                    )
                    classes.append(cls)
                    symbol_to_class[sym] = cls

                    if extends_name:
                        inheritance_edges.append(
                            InheritanceEdge(
                                id=inheritance_edge_id(
                                    full_name, extends_name, "extends"
                                ),
                                source_class_id=cls.id,
                                source_class=full_name,
                                target_class=extends_name,
                                relation="extends",
                            )
                        )
                    for iface in implements_names:
                        inheritance_edges.append(
                            InheritanceEdge(
                                id=inheritance_edge_id(full_name, iface, "implements"),
                                source_class_id=cls.id,
                                source_class=full_name,
                                target_class=iface,
                                relation="implements",
                            )
                        )

                # ── Method / constructor ──
                elif k in (kind.METHOD, kind.CONSTRUCTOR):
                    method_name = info.display_name
                    if not method_name:
                        method_name = _symbol_to_method_name(sym)

                    # Extract owning class from symbol: strip the method part after last #
                    class_sym = sym[: sym.rfind("#") + 1] if "#" in sym else ""
                    owner_full = (
                        _symbol_to_class_full_name(class_sym)
                        if class_sym
                        else package_name
                    )

                    param_types: list[str] = []
                    param_names: list[str] = []
                    return_type = "Unit"
                    if info.signature.HasField("method_signature"):
                        msig = info.signature.method_signature
                        return_type = (
                            msig.return_type.type_ref.symbol.split("/")[-1].rstrip(
                                "#.()"
                            )
                            if msig.return_type.HasField("type_ref")
                            else "Unit"
                        )
                        for pscope in msig.parameter_lists:
                            for plink in pscope.symlinks:
                                pinfo = sym_info.get(plink)
                                if pinfo:
                                    ptype = "Any"
                                    if pinfo.signature.HasField("value_signature"):
                                        vs = pinfo.signature.value_signature
                                        if vs.tpe.HasField("type_ref"):
                                            ptype = vs.tpe.type_ref.symbol.split("/")[
                                                -1
                                            ].rstrip("#.()")
                                    param_types.append(ptype)
                                    param_names.append(pinfo.display_name or "arg")

                    sig = method_signature(owner_full, method_name, param_types)
                    start_line = line
                    end_line = occ.range.end_line + 1
                    mid = method_id(sig, file_path_str, start_line)

                    # Find the owning ClassEntry (may not be indexed yet — handle in pass 2)
                    owner_cls_id = symbol_to_class.get(class_sym, None)
                    owner_class_id = (
                        owner_cls_id.id
                        if owner_cls_id
                        else class_id(owner_full, file_path_str)
                    )

                    m_entry = MethodEntry(
                        id=mid,
                        class_id=owner_class_id,
                        class_full_name=owner_full,
                        method_name=method_name,
                        return_type=return_type,
                        parameter_types=param_types,
                        parameter_names=param_names,
                        signature=sig,
                        file_path=file_path_str,
                        start_line=start_line,
                        end_line=end_line,
                        source="",
                        class_context={},
                        language="scala",
                    )
                    methods.append(m_entry)
                    symbol_to_method[sym] = m_entry

                # ── Field / val / var ──
                elif k in (kind.FIELD, kind.LOCAL):
                    if k == kind.LOCAL:
                        continue  # skip local variables
                    field_name = info.display_name
                    if not field_name:
                        continue
                    class_sym = sym[: sym.rfind("#") + 1] if "#" in sym else ""
                    owner_full = (
                        _symbol_to_class_full_name(class_sym) if class_sym else ""
                    )
                    owner_class_id = (
                        symbol_to_class[class_sym].id
                        if class_sym in symbol_to_class
                        else class_id(owner_full, file_path_str)
                    )
                    type_name = "Any"
                    if info.signature.HasField("value_signature"):
                        vs = info.signature.value_signature
                        if vs.tpe.HasField("type_ref"):
                            type_name = vs.tpe.type_ref.symbol.split("/")[-1].rstrip(
                                "#.()"
                            )

                    fields.append(
                        FieldEntry(
                            id=field_id(owner_full, field_name, file_path_str, line),
                            class_id=owner_class_id,
                            name=field_name,
                            type_name=type_name,
                            modifiers=[],
                            file_path=file_path_str,
                            line=line,
                        )
                    )

    # ── Pass 2: Call sites ───────────────────────────────────────────────────
    # We need to map each definition occ back to its enclosing method for caller_method_id.
    # Re-scan docs to find reference occurrences and correlate with enclosing method.
    for sdb_path in semanticdb_files:
        docs = pb2.TextDocuments()
        try:
            docs.ParseFromString(sdb_path.read_bytes())
        except Exception:
            continue

        for doc in docs.documents:
            uri = doc.uri
            source_path = codebase_root / uri
            file_path_str = str(source_path)

            # Build an ordered list of (start_line, method_entry) for enclosing lookup
            file_methods = sorted(
                [m for m in methods if m.file_path == file_path_str],
                key=lambda m: m.start_line,
            )

            def _enclosing_method(line_num: int) -> MethodEntry | None:
                best: MethodEntry | None = None
                for m in file_methods:
                    if m.start_line <= line_num <= m.end_line:
                        if best is None or m.start_line > best.start_line:
                            best = m
                return best

            for occ in doc.occurrences:
                if occ.role != role.REFERENCE:
                    continue
                callee_sym = occ.symbol
                callee_m = symbol_to_method.get(callee_sym)
                if callee_m is None:
                    continue

                ref_line = occ.range.start_line + 1
                ref_col = occ.range.start_character + 1
                caller_m = _enclosing_method(ref_line)
                if caller_m is None:
                    continue

                callee_name = callee_m.method_name
                cid = callsite_id(caller_m.id, ref_line, ref_col, callee_name)
                cs = CallSite(
                    id=cid,
                    caller_method_id=caller_m.id,
                    callee_name=callee_name,
                    receiver=None,
                    argument_count=len(callee_m.parameter_types),
                    file_path=file_path_str,
                    line=ref_line,
                    column=ref_col,
                    text=f"{callee_m.class_full_name}#{callee_name}",
                    receiver_type_raw=callee_m.class_full_name,
                    receiver_type_normalized=callee_m.class_full_name,
                    receiver_resolution_source="semanticdb",
                    receiver_type=callee_m.class_full_name,
                    resolved_candidates=[callee_m.id],
                    resolution_status="resolved_exact",
                    resolution_reason="compiler-resolved via SemanticDB",
                    candidate_count=1,
                )
                callsites.append(cs)
                resolved_call_edges.append(
                    ResolvedCallEdge(
                        id=resolved_call_edge_id(cid, callee_m.id),
                        callsite_id=cid,
                        caller_method_id=caller_m.id,
                        callee_method_id=callee_m.id,
                    )
                )

    return Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=callsites,
        inheritance_edges=inheritance_edges,
        resolved_call_edges=resolved_call_edges,
    )


def build_scala_graph(
    codebase_root: Path,
    on_progress: Callable[[int], None] | None = None,
    timeout: int = 600,
) -> Graph:
    """Build a JIDRA Graph from a Scala codebase using SemanticDB."""
    if on_progress:
        on_progress(0)

    with tempfile.TemporaryDirectory() as tmp_out:
        _run_sidecar(codebase_root, tmp_out, timeout=timeout)
        graph = _build_graph_from_semanticdb(Path(tmp_out), codebase_root)

    if on_progress:
        on_progress(len(graph.classes))

    return graph
