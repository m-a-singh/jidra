"""Tests for method_embeddings: payload builder, hash stability, schema."""

from __future__ import annotations

import sqlite3


from jidra.indexing.method_embeddings import (
    build_payload,
    ensure_embeddings_table,
    payload_hash,
)


def _row(**kwargs) -> dict:
    defaults = dict(
        id="m1",
        variant="main",
        module_id="mod",
        method_name="handleRequest",
        signature="handleRequest(HttpServletRequest req): ResponseEntity",
        file_path="src/main/java/com/example/FooController.java",
        source="public ResponseEntity handleRequest(HttpServletRequest req) { return ok(); }",
        class_full_name="com.example.FooController",
        class_context_json=None,
        annotations_json='["@GetMapping", "@Validated"]',
        is_endpoint=1,
        http_method="GET",
        route="/foo",
        full_route="/api/foo",
        language="java",
        framework_role="controller",
        generated=0,
    )
    defaults.update(kwargs)
    return defaults


def test_payload_contains_key_fields():
    row = _row()
    p = build_payload(row)
    assert "lang: java" in p
    assert "method: handleRequest" in p
    assert "endpoint: GET /api/foo" in p
    assert "framework_role: controller" in p
    assert "@GetMapping" in p
    assert "handleRequest" in p


def test_payload_no_endpoint_when_not_set():
    row = _row(is_endpoint=0)
    p = build_payload(row)
    assert "endpoint:" not in p


def test_payload_no_framework_role_when_empty():
    row = _row(framework_role=None)
    p = build_payload(row)
    assert "framework_role:" not in p


def test_payload_source_truncation():
    long_source = "x" * 5000
    row = _row(source=long_source)
    p = build_payload(row)
    # source block should be <= 2000 chars plus the "source:\n" prefix
    src_start = p.index("source:\n") + len("source:\n")
    assert len(p[src_start:]) <= 2000


def test_payload_class_context_parsed():
    ctx = '{"name": "FooController", "stereotypes": ["controller"], "imports": ["java.util.List"]}'
    row = _row(class_context_json=ctx)
    p = build_payload(row)
    assert "FooController" in p
    assert "class_context:" in p


def test_hash_stability():
    row = _row()
    p = build_payload(row)
    h1 = payload_hash(p)
    h2 = payload_hash(build_payload(row))
    assert h1 == h2


def test_hash_changes_on_source_diff():
    row1 = _row(source="return ok();")
    row2 = _row(source="return notFound();")
    assert payload_hash(build_payload(row1)) != payload_hash(build_payload(row2))


def test_ensure_embeddings_table_idempotent():
    conn = sqlite3.connect(":memory:")
    ensure_embeddings_table(conn)
    ensure_embeddings_table(conn)  # second call must not raise
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "method_embeddings" in tables
    conn.close()


def test_schema_columns():
    conn = sqlite3.connect(":memory:")
    ensure_embeddings_table(conn)
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(method_embeddings)").fetchall()
    }
    assert {
        "method_id",
        "variant",
        "module_id",
        "model",
        "embedding",
        "text_hash",
    }.issubset(cols)
    conn.close()
