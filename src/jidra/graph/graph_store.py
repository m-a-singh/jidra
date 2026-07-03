from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..models import (
    CallSite,
    ClassEntry,
    FieldEntry,
    Graph,
    InheritanceEdge,
    MethodEntry,
    ResolvedCallEdge,
    SmithyMemberEntry,
    SmithyOperationEntry,
    SmithyOperationLink,
    SmithyShapeEntry,
)

SCHEMA_VERSION = "2.3"

# Older schema versions whose on-disk layout this code can upgrade in place via
# `_run_migrations` (additive columns / virtual tables only). A DB stamped with
# one of these is migrated and re-stamped to SCHEMA_VERSION instead of raising.
_MIGRATABLE_FROM = {"2.0", "2.1", "2.2"}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS classes (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    package_name TEXT,
    name TEXT,
    full_name TEXT,
    file_path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    modifiers_json TEXT,
    annotations_json TEXT,
    extends TEXT,
    implements_json TEXT,
    imports_json TEXT,
    stereotypes_json TEXT,
    language TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_classes_scope ON classes (variant, module_id, file_path);

CREATE TABLE IF NOT EXISTS methods (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    class_id TEXT,
    class_full_name TEXT,
    method_name TEXT,
    return_type TEXT,
    parameter_types_json TEXT,
    parameter_names_json TEXT,
    signature TEXT,
    file_path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    source TEXT,
    class_context_json TEXT,
    annotations_json TEXT,
    local_variable_types_json TEXT,
    field_reads_json TEXT,
    field_writes_json TEXT,
    is_endpoint INTEGER,
    http_method TEXT,
    route TEXT,
    controller_route TEXT,
    full_route TEXT,
    language TEXT,
    framework_role TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_methods_scope ON methods (variant, module_id, file_path);

CREATE TABLE IF NOT EXISTS fields (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    class_id TEXT,
    name TEXT,
    type_name TEXT,
    modifiers_json TEXT,
    file_path TEXT,
    line INTEGER,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_fields_scope ON fields (variant, module_id, file_path);

CREATE TABLE IF NOT EXISTS callsites (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    caller_method_id TEXT,
    callee_name TEXT,
    receiver TEXT,
    argument_count INTEGER,
    file_path TEXT,
    line INTEGER,
    column_no INTEGER,
    text TEXT,
    receiver_type_raw TEXT,
    receiver_type_normalized TEXT,
    receiver_resolution_source TEXT,
    receiver_type TEXT,
    resolved_candidates_json TEXT,
    resolution_status TEXT,
    resolution_reason TEXT,
    candidate_count INTEGER,
    argument_types_json TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_callsites_scope ON callsites (variant, module_id, file_path);
CREATE INDEX IF NOT EXISTS idx_callsites_caller ON callsites (caller_method_id);

CREATE TABLE IF NOT EXISTS inheritance_edges (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    source_class_id TEXT,
    source_class TEXT,
    target_class TEXT,
    relation TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_inheritance_scope ON inheritance_edges (variant, module_id, source_class_id);

CREATE TABLE IF NOT EXISTS resolved_call_edges (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    callsite_id TEXT,
    caller_method_id TEXT,
    callee_method_id TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_resolved_call_scope ON resolved_call_edges (variant, module_id);
CREATE INDEX IF NOT EXISTS idx_resolved_call_caller ON resolved_call_edges (caller_method_id);
CREATE INDEX IF NOT EXISTS idx_resolved_call_callee ON resolved_call_edges (callee_method_id);

CREATE TABLE IF NOT EXISTS modules (
    module_id TEXT PRIMARY KEY,
    module_dir TEXT,
    tool TEXT
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Full-text search index over methods (Phase 1). Self-contained (not
-- external-content) so the sync triggers stay simple and corruption-proof; the
-- cost is a duplicated copy of `source`. `rowid` mirrors methods.rowid so search
-- results can join straight back to the methods table. id/language/variant are
-- UNINDEXED -- stored for retrieval/filtering but not tokenized.
CREATE VIRTUAL TABLE IF NOT EXISTS methods_fts USING fts5(
    id UNINDEXED,
    method_name,
    signature,
    class_full_name,
    source,
    language UNINDEXED,
    variant UNINDEXED
);

CREATE TRIGGER IF NOT EXISTS methods_fts_insert AFTER INSERT ON methods BEGIN
    INSERT INTO methods_fts(
        rowid, id, method_name, signature, class_full_name, source, language, variant
    ) VALUES (
        new.rowid, new.id, new.method_name, new.signature, new.class_full_name,
        new.source, new.language, new.variant
    );
END;

CREATE TRIGGER IF NOT EXISTS methods_fts_delete AFTER DELETE ON methods BEGIN
    DELETE FROM methods_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER IF NOT EXISTS methods_fts_update AFTER UPDATE ON methods BEGIN
    DELETE FROM methods_fts WHERE rowid = old.rowid;
    INSERT INTO methods_fts(
        rowid, id, method_name, signature, class_full_name, source, language, variant
    ) VALUES (
        new.rowid, new.id, new.method_name, new.signature, new.class_full_name,
        new.source, new.language, new.variant
    );
END;

-- Smithy IDL model data (Phase A). `id` is the Smithy shape ID itself
-- (namespace#Name) rather than a hash -- it's already globally unique and is
-- the natural cross-repo join key for a future org-level graph.
CREATE TABLE IF NOT EXISTS smithy_shapes (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    namespace TEXT,
    name TEXT,
    kind TEXT,
    file_path TEXT,
    line INTEGER,
    members_json TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_smithy_shapes_scope ON smithy_shapes (variant, module_id);

CREATE TABLE IF NOT EXISTS smithy_operations (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    namespace TEXT,
    name TEXT,
    service_id TEXT,
    service_name TEXT,
    input_shape_id TEXT,
    output_shape_id TEXT,
    file_path TEXT,
    line INTEGER,
    errors_json TEXT,
    http_method TEXT,
    http_uri TEXT,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_smithy_operations_scope ON smithy_operations (variant, module_id);
CREATE INDEX IF NOT EXISTS idx_smithy_operations_service ON smithy_operations (service_name);

-- Phase B: real classes bridged to the Smithy operation they implement, via
-- a known codegen toolchain's naming convention (smithy-java, smithy4s, ...).
CREATE TABLE IF NOT EXISTS smithy_operation_links (
    id TEXT NOT NULL,
    variant TEXT NOT NULL,
    module_id TEXT,
    operation_id TEXT,
    class_id TEXT,
    class_full_name TEXT,
    link_type TEXT,
    codegen_profile TEXT,
    language TEXT,
    file_path TEXT,
    line INTEGER,
    PRIMARY KEY (id, variant, module_id)
);
CREATE INDEX IF NOT EXISTS idx_smithy_links_operation ON smithy_operation_links (operation_id);
CREATE INDEX IF NOT EXISTS idx_smithy_links_class ON smithy_operation_links (class_id);
"""


class SchemaVersionMismatch(RuntimeError):
    """Raised when an existing graph.db was built with an incompatible schema."""


_ENTITY_TABLES = (
    "classes",
    "methods",
    "fields",
    "callsites",
    "inheritance_edges",
    "resolved_call_edges",
)


def resolve_graph_db_path(output: Path) -> Path:
    """Resolve the on-disk path of the SQLite graph database for `output`.

    - `output` is a directory (existing, or has no file suffix) -> `output/graph.db`
    - `output` already names a `.db` file -> used as-is
    - anything else (including legacy `.jsonl` paths from old configs) ->
      `graph.db` in the same parent directory
    """
    if output.is_dir() or not output.suffix:
        return output / "graph.db"
    if output.suffix.lower() == ".db":
        return output
    return output.parent / "graph.db"


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False so a cached engine's connection (used for FTS
    # search) can be queried from the daemon's per-connection handler threads.
    # The engine serializes those queries with its own lock; reindex uses a
    # separate connection.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    _run_migrations(conn)
    _check_schema_version(conn)
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == column for r in cur.fetchall())


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a key-value pair in schema_meta."""
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a value from schema_meta, or None if not found."""
    cur = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Idempotent, additive upgrades applied on every `connect()`.

    Runs after `executescript` (which creates any missing tables/triggers via
    IF NOT EXISTS) and before `_check_schema_version`. Safe to run on both fresh
    and pre-existing databases. Keep every step guarded so re-running is a no-op.
    """
    # Phase 4: framework_role column on methods. Appended last to match where a
    # fresh `_SCHEMA_SQL` build also places it, so the positional INSERT lines up.
    if not _column_exists(conn, "methods", "framework_role"):
        conn.execute("ALTER TABLE methods ADD COLUMN framework_role TEXT")
        conn.commit()

    # Phase 5: argument_types_json column on callsites. Appended last to match
    # where a fresh `_SCHEMA_SQL` build also places it, so the positional
    # INSERT lines up.
    if not _column_exists(conn, "callsites", "argument_types_json"):
        conn.execute("ALTER TABLE callsites ADD COLUMN argument_types_json TEXT")
        conn.commit()

    # Phase 1: backfill the FTS index for DBs created before it existed. The
    # sync triggers only cover rows written after the trigger was created, so an
    # upgraded DB needs a one-time bulk load of its existing methods.
    fts_count = conn.execute("SELECT count(*) FROM methods_fts").fetchone()[0]
    if fts_count == 0:
        method_count = conn.execute("SELECT count(*) FROM methods").fetchone()[0]
        if method_count > 0:
            conn.execute(
                "INSERT INTO methods_fts("
                "rowid, id, method_name, signature, class_full_name, "
                "source, language, variant) "
                "SELECT rowid, id, method_name, signature, class_full_name, "
                "source, language, variant FROM methods"
            )
            conn.commit()


def _check_schema_version(conn: sqlite3.Connection) -> None:
    """Stamp a fresh DB with the current SCHEMA_VERSION; raise on a stale one.

    A stale version means the on-disk table layout predates this code's
    expectations. Versions listed in `_MIGRATABLE_FROM` are upgraded in place by
    `_run_migrations` (already run by the time we get here) and re-stamped;
    anything else (e.g. a newer/unknown layout) raises rather than risk reading
    wrong or missing data.
    """
    cur = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        return
    if row[0] == SCHEMA_VERSION:
        return
    if row[0] in _MIGRATABLE_FROM:
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        return
    raise SchemaVersionMismatch(
        f"graph.db schema_version={row[0]!r} does not match expected "
        f"{SCHEMA_VERSION!r}. Delete the database and re-run `jidra index` "
        f"to rebuild it with the current schema."
    )


def infer_variant_split(file_path: str) -> str:
    """Classify a file as 'main' or 'test' production-vs-test code."""
    normalized = (file_path or "").replace("\\", "/")
    if "/src/test/" in normalized:
        return "test"
    return "main"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [])


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _class_row(cls: ClassEntry, variant: str, module_id: str | None) -> tuple:
    return (
        cls.id,
        variant,
        module_id,
        cls.package_name,
        cls.name,
        cls.full_name,
        cls.file_path,
        cls.start_line,
        cls.end_line,
        _dumps(cls.modifiers),
        _dumps(cls.annotations),
        cls.extends,
        _dumps(cls.implements),
        _dumps(cls.imports),
        _dumps(cls.stereotypes),
        cls.language,
    )


def _method_row(m: MethodEntry, variant: str, module_id: str | None) -> tuple:
    return (
        m.id,
        variant,
        module_id,
        m.class_id,
        m.class_full_name,
        m.method_name,
        m.return_type,
        _dumps(m.parameter_types),
        _dumps(m.parameter_names),
        m.signature,
        m.file_path,
        m.start_line,
        m.end_line,
        m.source,
        json.dumps(m.class_context or {}),
        _dumps(m.annotations),
        json.dumps(m.local_variable_types or {}),
        _dumps(m.field_reads),
        _dumps(m.field_writes),
        1 if m.is_endpoint else 0,
        m.http_method,
        m.route,
        m.controller_route,
        m.full_route,
        m.language,
        m.framework_role,
    )


def _field_row(f: FieldEntry, variant: str, module_id: str | None) -> tuple:
    return (
        f.id,
        variant,
        module_id,
        f.class_id,
        f.name,
        f.type_name,
        _dumps(f.modifiers),
        f.file_path,
        f.line,
    )


def _callsite_row(c: CallSite, variant: str, module_id: str | None) -> tuple:
    return (
        c.id,
        variant,
        module_id,
        c.caller_method_id,
        c.callee_name,
        c.receiver,
        c.argument_count,
        c.file_path,
        c.line,
        c.column,
        c.text,
        c.receiver_type_raw,
        c.receiver_type_normalized,
        c.receiver_resolution_source,
        c.receiver_type,
        _dumps(c.resolved_candidates),
        c.resolution_status,
        c.resolution_reason,
        c.candidate_count,
        _dumps(c.argument_types),
    )


def _inheritance_row(e: InheritanceEdge, variant: str, module_id: str | None) -> tuple:
    return (
        e.id,
        variant,
        module_id,
        e.source_class_id,
        e.source_class,
        e.target_class,
        e.relation,
    )


def _resolved_call_row(
    e: ResolvedCallEdge, variant: str, module_id: str | None
) -> tuple:
    return (
        e.id,
        variant,
        module_id,
        e.callsite_id,
        e.caller_method_id,
        e.callee_method_id,
    )


def _file_path_for_field(f: FieldEntry) -> str:
    return f.file_path


def _file_path_for_inheritance(
    e: InheritanceEdge, class_file_by_id: dict[str, str]
) -> str:
    return class_file_by_id.get(e.source_class_id, "")


def _file_path_for_resolved_call(
    e: ResolvedCallEdge, method_file_by_id: dict[str, str]
) -> str:
    return method_file_by_id.get(e.caller_method_id, "")


def _smithy_shape_row(
    s: SmithyShapeEntry, variant: str, module_id: str | None
) -> tuple:
    members_json = json.dumps(
        [
            {"name": m.name, "target_shape": m.target_shape, "required": m.required}
            for m in s.members
        ]
    )
    return (
        s.id,
        variant,
        module_id,
        s.namespace,
        s.name,
        s.kind,
        s.file_path,
        s.line,
        members_json,
    )


def _smithy_operation_row(
    o: SmithyOperationEntry, variant: str, module_id: str | None
) -> tuple:
    return (
        o.id,
        variant,
        module_id,
        o.namespace,
        o.name,
        o.service_id,
        o.service_name,
        o.input_shape_id,
        o.output_shape_id,
        o.file_path,
        o.line,
        _dumps(o.errors),
        o.http_method,
        o.http_uri,
    )


def _smithy_link_row(
    link: SmithyOperationLink, variant: str, module_id: str | None
) -> tuple:
    return (
        link.id,
        variant,
        module_id,
        link.operation_id,
        link.class_id,
        link.class_full_name,
        link.link_type,
        link.codegen_profile,
        link.language,
        link.file_path,
        link.line,
    )


def _row_to_smithy_shape(row: sqlite3.Row) -> SmithyShapeEntry:
    members = [
        SmithyMemberEntry(
            name=m["name"], target_shape=m["target_shape"], required=m["required"]
        )
        for m in _loads(row["members_json"], [])
    ]
    return SmithyShapeEntry(
        id=row["id"],
        namespace=row["namespace"],
        name=row["name"],
        kind=row["kind"],
        file_path=row["file_path"],
        line=row["line"],
        members=members,
    )


def _row_to_smithy_operation(row: sqlite3.Row) -> SmithyOperationEntry:
    return SmithyOperationEntry(
        id=row["id"],
        namespace=row["namespace"],
        name=row["name"],
        service_id=row["service_id"],
        service_name=row["service_name"],
        input_shape_id=row["input_shape_id"],
        output_shape_id=row["output_shape_id"],
        file_path=row["file_path"],
        line=row["line"],
        errors=_loads(row["errors_json"], []),
        http_method=row["http_method"],
        http_uri=row["http_uri"],
    )


def _row_to_smithy_link(row: sqlite3.Row) -> SmithyOperationLink:
    return SmithyOperationLink(
        id=row["id"],
        operation_id=row["operation_id"],
        class_id=row["class_id"],
        class_full_name=row["class_full_name"],
        link_type=row["link_type"],
        codegen_profile=row["codegen_profile"],
        language=row["language"],
        file_path=row["file_path"],
        line=row["line"],
    )


def save_smithy_graph(
    conn: sqlite3.Connection,
    shapes: list[SmithyShapeEntry],
    operations: list[SmithyOperationEntry],
    links: list[SmithyOperationLink],
    *,
    variant: str = "main",
    module_id: str | None = None,
) -> None:
    """Replace all Smithy rows for the given scope. Unlike `save_full_graph`,
    there's no main/test split for IDL model files -- everything is `main` by
    default. Always a full replace: `.smithy` files are few and small relative
    to source code, so re-parsing+replacing on every `jidra index` run is
    cheap and avoids needing a separate incremental path for this data."""
    conn.execute(
        "DELETE FROM smithy_shapes WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    conn.execute(
        "DELETE FROM smithy_operations WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    conn.execute(
        "DELETE FROM smithy_operation_links WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    conn.executemany(
        "INSERT INTO smithy_shapes VALUES (?,?,?,?,?,?,?,?,?)",
        [_smithy_shape_row(s, variant, module_id) for s in shapes],
    )
    conn.executemany(
        "INSERT INTO smithy_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_smithy_operation_row(o, variant, module_id) for o in operations],
    )
    conn.executemany(
        "INSERT INTO smithy_operation_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [_smithy_link_row(link, variant, module_id) for link in links],
    )
    conn.commit()


def load_smithy_operations(
    conn: sqlite3.Connection, *, variant: str = "main", module_id: str | None = None
) -> list[SmithyOperationEntry]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM smithy_operations WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    ).fetchall()
    return [_row_to_smithy_operation(r) for r in rows]


def load_smithy_shapes(
    conn: sqlite3.Connection, *, variant: str = "main", module_id: str | None = None
) -> list[SmithyShapeEntry]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM smithy_shapes WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    ).fetchall()
    return [_row_to_smithy_shape(r) for r in rows]


def load_smithy_operation_links(
    conn: sqlite3.Connection,
    *,
    operation_id: str | None = None,
    class_id: str | None = None,
    variant: str = "main",
    module_id: str | None = None,
) -> list[SmithyOperationLink]:
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM smithy_operation_links WHERE variant = ? AND module_id IS ?"
    params: list[Any] = [variant, module_id]
    if operation_id is not None:
        query += " AND operation_id = ?"
        params.append(operation_id)
    if class_id is not None:
        query += " AND class_id = ?"
        params.append(class_id)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_smithy_link(r) for r in rows]


def _insert_graph(
    conn: sqlite3.Connection,
    graph: Graph,
    *,
    variant_of: Any,
    module_id: str | None,
) -> None:
    """Insert all rows of `graph`, classifying each row's variant via `variant_of(file_path)`."""
    class_file_by_id = {c.id: c.file_path for c in graph.classes}
    method_file_by_id = {m.id: m.file_path for m in graph.methods}

    conn.executemany(
        "INSERT INTO classes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_class_row(c, variant_of(c.file_path), module_id) for c in graph.classes],
    )
    conn.executemany(
        "INSERT INTO methods VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_method_row(m, variant_of(m.file_path), module_id) for m in graph.methods],
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?,?,?,?,?,?,?,?,?)",
        [
            _field_row(f, variant_of(_file_path_for_field(f)), module_id)
            for f in graph.fields
        ],
    )
    conn.executemany(
        "INSERT INTO callsites VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_callsite_row(c, variant_of(c.file_path), module_id) for c in graph.callsites],
    )
    conn.executemany(
        "INSERT INTO inheritance_edges VALUES (?,?,?,?,?,?,?)",
        [
            _inheritance_row(
                e,
                variant_of(_file_path_for_inheritance(e, class_file_by_id)),
                module_id,
            )
            for e in graph.inheritance_edges
        ],
    )
    conn.executemany(
        "INSERT INTO resolved_call_edges VALUES (?,?,?,?,?,?)",
        [
            _resolved_call_row(
                e,
                variant_of(_file_path_for_resolved_call(e, method_file_by_id)),
                module_id,
            )
            for e in graph.resolved_call_edges
        ],
    )


def save_full_graph(
    conn: sqlite3.Connection,
    graph: Graph,
    *,
    variant: str | None = None,
    module_id: str | None = None,
) -> None:
    """Replace all rows for the given scope with the contents of `graph`.

    If `variant` is given, every row is written with that fixed variant
    (used for `validate` output, which writes a single `validated` variant).
    If `variant` is None, rows are auto-classified into `main`/`test` per
    `infer_variant_split(file_path)` and both variants are replaced in one
    transaction (used by the indexer).
    """
    if variant is not None:
        for table in _ENTITY_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE variant = ? AND module_id IS ?",
                (variant, module_id),
            )
        _insert_graph(conn, graph, variant_of=lambda _fp: variant, module_id=module_id)
    else:
        for table in _ENTITY_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE variant IN ('main', 'test') AND module_id IS ?",
                (module_id,),
            )
        _insert_graph(conn, graph, variant_of=infer_variant_split, module_id=module_id)
    conn.commit()


def _row_to_class(row: sqlite3.Row) -> ClassEntry:
    return ClassEntry(
        id=row["id"],
        package_name=row["package_name"],
        name=row["name"],
        full_name=row["full_name"],
        file_path=row["file_path"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        modifiers=_loads(row["modifiers_json"], []),
        annotations=_loads(row["annotations_json"], []),
        extends=row["extends"],
        implements=_loads(row["implements_json"], []),
        imports=_loads(row["imports_json"], []),
        stereotypes=_loads(row["stereotypes_json"], []),
        language=row["language"] or "unknown",
    )


def _row_to_method(row: sqlite3.Row) -> MethodEntry:
    return MethodEntry(
        id=row["id"],
        class_id=row["class_id"],
        class_full_name=row["class_full_name"],
        method_name=row["method_name"],
        return_type=row["return_type"],
        parameter_types=_loads(row["parameter_types_json"], []),
        parameter_names=_loads(row["parameter_names_json"], []),
        signature=row["signature"],
        file_path=row["file_path"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        source=row["source"],
        class_context=_loads(row["class_context_json"], {}),
        annotations=_loads(row["annotations_json"], []),
        local_variable_types=_loads(row["local_variable_types_json"], {}),
        field_reads=_loads(row["field_reads_json"], []),
        field_writes=_loads(row["field_writes_json"], []),
        is_endpoint=bool(row["is_endpoint"]),
        http_method=row["http_method"],
        route=row["route"],
        controller_route=row["controller_route"],
        full_route=row["full_route"],
        language=row["language"] or "unknown",
        framework_role=row["framework_role"],
    )


def _row_to_field(row: sqlite3.Row) -> FieldEntry:
    return FieldEntry(
        id=row["id"],
        class_id=row["class_id"],
        name=row["name"],
        type_name=row["type_name"],
        modifiers=_loads(row["modifiers_json"], []),
        file_path=row["file_path"],
        line=row["line"],
    )


def _row_to_callsite(row: sqlite3.Row) -> CallSite:
    return CallSite(
        id=row["id"],
        caller_method_id=row["caller_method_id"],
        callee_name=row["callee_name"],
        receiver=row["receiver"],
        argument_count=row["argument_count"],
        file_path=row["file_path"],
        line=row["line"],
        column=row["column_no"],
        text=row["text"],
        receiver_type_raw=row["receiver_type_raw"],
        receiver_type_normalized=row["receiver_type_normalized"],
        receiver_resolution_source=row["receiver_resolution_source"],
        receiver_type=row["receiver_type"],
        resolved_candidates=_loads(row["resolved_candidates_json"], []),
        resolution_status=row["resolution_status"] or "unresolved",
        resolution_reason=row["resolution_reason"] or "",
        candidate_count=row["candidate_count"] or 0,
        argument_types=_loads(row["argument_types_json"], []),
    )


def _row_to_inheritance(row: sqlite3.Row) -> InheritanceEdge:
    return InheritanceEdge(
        id=row["id"],
        source_class_id=row["source_class_id"],
        source_class=row["source_class"],
        target_class=row["target_class"],
        relation=row["relation"],
    )


def _row_to_resolved_call(row: sqlite3.Row) -> ResolvedCallEdge:
    return ResolvedCallEdge(
        id=row["id"],
        callsite_id=row["callsite_id"],
        caller_method_id=row["caller_method_id"],
        callee_method_id=row["callee_method_id"],
    )


def load_graph(
    conn: sqlite3.Connection,
    *,
    variant: str = "main",
    module_id: str | None = None,
) -> Graph:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM classes WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    classes = [_row_to_class(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT * FROM methods WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    methods = [_row_to_method(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT * FROM fields WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    fields = [_row_to_field(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT * FROM callsites WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    callsites = [_row_to_callsite(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT * FROM inheritance_edges WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    inheritance_edges = [_row_to_inheritance(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT * FROM resolved_call_edges WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    resolved_call_edges = [_row_to_resolved_call(r) for r in cur.fetchall()]

    return Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=callsites,
        inheritance_edges=inheritance_edges,
        resolved_call_edges=resolved_call_edges,
    )


_CAMEL_SPLIT_RE = re.compile(r"[A-Z][a-z0-9]+|[A-Z]+(?=[A-Z]|$)|[a-z0-9]+")


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Each alphanumeric token is double-quoted with a trailing prefix `*` and
    OR-ed.  CamelCase identifiers are also split into sub-tokens so that
    e.g. "getSearchFilters" matches even if the FTS index tokenised it as
    separate words ("get", "search", "filters").
    """
    raw_tokens = re.findall(r"[A-Za-z0-9_]+", text)
    if not raw_tokens:
        return ""
    token_set: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        for part in [tok] + _CAMEL_SPLIT_RE.findall(tok):
            low = part.lower()
            if low and low not in seen:
                seen.add(low)
                token_set.append(part)
    return " OR ".join(f'"{t}"*' for t in token_set)


def search_methods(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    language: str | None = None,
    variant: str = "main",
) -> list[dict]:
    """FTS5 keyword search over method name/signature/class/source.

    Returns raw candidate rows ordered by bm25 relevance (best first). Callers
    that want richer ranking (e.g. `engine.explore`) re-score these. Returns an
    empty list when the query has no usable tokens or the FTS table is absent.
    """
    match = _fts_query(query)
    if not match:
        return []
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT m.id AS id, m.method_name AS method_name, m.signature AS signature, "
        "m.class_full_name AS class_full_name, m.file_path AS file_path, "
        "m.language AS language, bm25(methods_fts) AS score "
        "FROM methods_fts JOIN methods m ON m.rowid = methods_fts.rowid "
        "WHERE methods_fts MATCH ? AND methods_fts.variant = ?"
    )
    params: list[Any] = [match, variant]
    if language:
        sql += " AND m.language = ?"
        params.append(language)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        cur = conn.execute(sql, params)
    except sqlite3.OperationalError:
        # FTS table missing (e.g. very old DB the migration didn't touch) —
        # let the caller fall back to an in-memory scan.
        return []
    return [dict(r) for r in cur.fetchall()]


def fetch_methods_by_ids(
    conn: sqlite3.Connection,
    method_ids: list[str],
    variant: str = "main",
) -> list[dict]:
    """Fetch method metadata rows for a list of IDs."""
    if not method_ids:
        return []
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(method_ids))
    cur = conn.execute(
        f"SELECT id, method_name, signature, class_full_name, file_path, language "
        f"FROM methods WHERE id IN ({placeholders}) AND variant = ?",
        method_ids + [variant],
    )
    return [dict(r) for r in cur.fetchall()]


def load_graph_for_files(
    conn: sqlite3.Connection,
    file_paths: Iterable[str],
    *,
    variant: str = "main",
    module_id: str | None = None,
) -> Graph:
    """Load only the rows whose file_path is in `file_paths` (entity tables) plus
    the inheritance/resolved-call edges owned by the classes/methods in scope.

    Used by the reindexer to narrow loads to the files under consideration instead
    of pulling the entire graph into memory.
    """
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    paths = list(file_paths)
    if not paths:
        return Graph([], [], [], [], [], [])
    placeholders = ",".join("?" for _ in paths)

    cur.execute(
        f"SELECT * FROM classes WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
        (variant, module_id, *paths),
    )
    classes = [_row_to_class(r) for r in cur.fetchall()]
    class_ids = [c.id for c in classes]

    cur.execute(
        f"SELECT * FROM methods WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
        (variant, module_id, *paths),
    )
    methods = [_row_to_method(r) for r in cur.fetchall()]
    method_ids = [m.id for m in methods]

    cur.execute(
        f"SELECT * FROM fields WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
        (variant, module_id, *paths),
    )
    fields = [_row_to_field(r) for r in cur.fetchall()]

    cur.execute(
        f"SELECT * FROM callsites WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
        (variant, module_id, *paths),
    )
    callsites = [_row_to_callsite(r) for r in cur.fetchall()]

    inheritance_edges: list[InheritanceEdge] = []
    if class_ids:
        cph = ",".join("?" for _ in class_ids)
        cur.execute(
            f"SELECT * FROM inheritance_edges WHERE variant = ? AND module_id IS ? AND source_class_id IN ({cph})",
            (variant, module_id, *class_ids),
        )
        inheritance_edges = [_row_to_inheritance(r) for r in cur.fetchall()]

    resolved_call_edges: list[ResolvedCallEdge] = []
    if method_ids:
        mph = ",".join("?" for _ in method_ids)
        cur.execute(
            f"SELECT * FROM resolved_call_edges WHERE variant = ? AND module_id IS ? AND caller_method_id IN ({mph})",
            (variant, module_id, *method_ids),
        )
        resolved_call_edges = [_row_to_resolved_call(r) for r in cur.fetchall()]

    return Graph(
        classes=classes,
        methods=methods,
        fields=fields,
        callsites=callsites,
        inheritance_edges=inheritance_edges,
        resolved_call_edges=resolved_call_edges,
    )


def load_nodes(
    conn: sqlite3.Connection,
    *,
    variant: str = "validated",
    module_id: str | None = None,
) -> dict[str, dict]:
    """Reconstruct the enriched raw-dict node structure that the old
    `exporter.graph_records()` produced (id, qualified_name, file_path,
    start_line, end_line, payload, calls, called_by), keyed by node id.

    Needed by cost_calculator.py, which walks `calls`/`called_by`/`payload.source`
    on raw nodes rather than just the typed Graph dataclass.
    """
    graph = load_graph(conn, variant=variant, module_id=module_id)

    method_by_id = {m.id: m for m in graph.methods}
    class_by_id = {c.id: c for c in graph.classes}
    field_by_id = {f.id: f for f in graph.fields}

    called_by: dict[str, list[str]] = {}
    for edge in graph.resolved_call_edges:
        called_by.setdefault(edge.callee_method_id, []).append(edge.caller_method_id)

    callsites_by_caller: dict[str, list[CallSite]] = {}
    for c in graph.callsites:
        callsites_by_caller.setdefault(c.caller_method_id, []).append(c)

    nodes: dict[str, dict] = {}

    for cls in graph.classes:
        nodes[cls.id] = {
            "id": cls.id,
            "type": "class",
            "node_type": "class",
            "qualified_name": cls.full_name,
            "file_path": cls.file_path,
            "payload": {
                "package_name": cls.package_name,
                "name": cls.name,
                "full_name": cls.full_name,
                "file_path": cls.file_path,
                "modifiers": cls.modifiers,
                "annotations": cls.annotations,
                "extends": cls.extends,
                "implements": cls.implements,
                "imports": cls.imports,
                "stereotypes": cls.stereotypes,
                "language": cls.language,
            },
        }

    for f in graph.fields:
        owner = field_by_id and class_by_id.get(f.class_id)
        qname = f"{owner.full_name}.{f.name}" if owner else f.name
        nodes[f.id] = {
            "id": f.id,
            "type": "field",
            "node_type": "field",
            "qualified_name": qname,
            "file_path": f.file_path,
            "payload": {
                "class_id": f.class_id,
                "name": f.name,
                "type_name": f.type_name,
                "modifiers": f.modifiers,
                "file_path": f.file_path,
                "line": f.line,
            },
        }

    for m in graph.methods:
        calls = []
        for c in callsites_by_caller.get(m.id, []):
            target_id = c.resolved_candidates[0] if c.resolved_candidates else None
            calls.append(
                {
                    "name": c.callee_name,
                    "receiver": c.receiver,
                    "target_id": target_id,
                    "target_qualified_name": method_by_id[target_id].signature
                    if target_id and target_id in method_by_id
                    else None,
                    "resolution": c.receiver_resolution_source or "unresolved",
                    "confidence": 1.0
                    if (c.resolution_status or "").startswith("resolved")
                    else 0.5,
                }
            )
        nodes[m.id] = {
            "id": m.id,
            "type": "method",
            "node_type": "method",
            "qualified_name": m.signature,
            "file_path": m.file_path,
            "start_line": m.start_line,
            "end_line": m.end_line,
            "calls": calls,
            "called_by": sorted(set(called_by.get(m.id, []))),
            "payload": {
                "id": m.id,
                "class_id": m.class_id,
                "class_full_name": m.class_full_name,
                "method_name": m.method_name,
                "return_type": m.return_type,
                "parameter_types": m.parameter_types,
                "parameter_names": m.parameter_names,
                "signature": m.signature,
                "file_path": m.file_path,
                "start_line": m.start_line,
                "end_line": m.end_line,
                "source": m.source,
                "class_context": m.class_context,
                "annotations": m.annotations,
                "local_variable_types": m.local_variable_types,
                "field_reads": m.field_reads,
                "field_writes": m.field_writes,
                "is_endpoint": m.is_endpoint,
                "http_method": m.http_method,
                "route": m.route,
                "controller_route": m.controller_route,
                "full_route": m.full_route,
                "language": m.language,
            },
        }

    for c in graph.callsites:
        nodes[c.id] = {
            "id": c.id,
            "type": "callsite",
            "node_type": "callsite",
            "qualified_name": c.text,
            "file_path": c.file_path,
            "payload": {
                "id": c.id,
                "caller_method_id": c.caller_method_id,
                "callee_name": c.callee_name,
                "receiver": c.receiver,
                "argument_count": c.argument_count,
                "file_path": c.file_path,
                "line": c.line,
                "column": c.column,
                "text": c.text,
                "resolved_candidates": c.resolved_candidates,
                "resolution_status": c.resolution_status,
                "resolution_reason": c.resolution_reason,
                "candidate_count": c.candidate_count,
            },
        }

    return nodes


def delete_for_files(
    conn: sqlite3.Connection,
    file_paths: Iterable[str],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    """Delete all rows owned by `file_paths` (entity tables), plus any
    inheritance/resolved-call edges owned by the deleted classes/methods."""
    paths = list(file_paths)
    if not paths:
        return
    placeholders = ",".join("?" for _ in paths)

    cur = conn.cursor()
    cur.execute(
        f"SELECT id FROM classes WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
        (variant, module_id, *paths),
    )
    class_ids = [r[0] for r in cur.fetchall()]

    cur.execute(
        f"SELECT id FROM methods WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
        (variant, module_id, *paths),
    )
    method_ids = [r[0] for r in cur.fetchall()]

    for table in ("classes", "methods", "fields", "callsites"):
        conn.execute(
            f"DELETE FROM {table} WHERE variant = ? AND module_id IS ? AND file_path IN ({placeholders})",
            (variant, module_id, *paths),
        )

    if class_ids:
        cph = ",".join("?" for _ in class_ids)
        conn.execute(
            f"DELETE FROM inheritance_edges WHERE variant = ? AND module_id IS ? AND source_class_id IN ({cph})",
            (variant, module_id, *class_ids),
        )

    if method_ids:
        mph = ",".join("?" for _ in method_ids)
        conn.execute(
            f"DELETE FROM resolved_call_edges WHERE variant = ? AND module_id IS ? AND caller_method_id IN ({mph})",
            (variant, module_id, *method_ids),
        )

    conn.commit()


def upsert_for_files(
    conn: sqlite3.Connection,
    fragment: Graph,
    file_paths: Iterable[str],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    """Delete rows for `file_paths` then insert `fragment`'s rows in their place.

    `resolved_call_edges` are intentionally not part of `fragment` here — callers
    should recompute and persist those separately via `replace_resolved_call_edges`
    once edge resolution has run against enough context.
    """
    delete_for_files(conn, file_paths, variant=variant, module_id=module_id)
    _insert_graph(conn, fragment, variant_of=lambda _fp: variant, module_id=module_id)
    conn.commit()


def delete_methods(
    conn: sqlite3.Connection,
    method_ids: Iterable[str],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    ids = list(method_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"DELETE FROM methods WHERE variant = ? AND module_id IS ? AND id IN ({placeholders})",
        (variant, module_id, *ids),
    )


def delete_callsites_by_caller(
    conn: sqlite3.Connection,
    caller_method_ids: Iterable[str],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    ids = list(caller_method_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"DELETE FROM callsites WHERE variant = ? AND module_id IS ? AND caller_method_id IN ({placeholders})",
        (variant, module_id, *ids),
    )


def insert_methods(
    conn: sqlite3.Connection,
    methods: list[MethodEntry],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    conn.executemany(
        "INSERT INTO methods VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_method_row(m, variant, module_id) for m in methods],
    )


def insert_callsites(
    conn: sqlite3.Connection,
    callsites: list[CallSite],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    conn.executemany(
        "INSERT INTO callsites VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_callsite_row(c, variant, module_id) for c in callsites],
    )


def update_method_lines(
    conn: sqlite3.Connection,
    method_id: str,
    start_line: int,
    end_line: int,
    source: str,
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    conn.execute(
        "UPDATE methods SET start_line = ?, end_line = ?, source = ? "
        "WHERE id = ? AND variant = ? AND module_id IS ?",
        (start_line, end_line, source, method_id, variant, module_id),
    )
    conn.commit()


def replace_resolved_call_edges(
    conn: sqlite3.Connection,
    edges: list[ResolvedCallEdge],
    *,
    variant: str,
    module_id: str | None = None,
) -> None:
    conn.execute(
        "DELETE FROM resolved_call_edges WHERE variant = ? AND module_id IS ?",
        (variant, module_id),
    )
    conn.executemany(
        "INSERT INTO resolved_call_edges VALUES (?,?,?,?,?,?)",
        [_resolved_call_row(e, variant, module_id) for e in edges],
    )
    conn.commit()


def save_module_metadata(
    conn: sqlite3.Connection, module_id: str, module_dir: str, tool: str
) -> None:
    conn.execute(
        "INSERT INTO modules (module_id, module_dir, tool) VALUES (?, ?, ?) "
        "ON CONFLICT(module_id) DO UPDATE SET module_dir = excluded.module_dir, tool = excluded.tool",
        (module_id, module_dir, tool),
    )
    conn.commit()


def list_modules(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT module_id, module_dir, tool FROM modules")
    return [dict(r) for r in cur.fetchall()]
