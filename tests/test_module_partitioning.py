from pathlib import Path

from jidra.graph import graph_store
from jidra.extractors.extractor import build_graph_partitioned


def _write_java(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_single_module_fallback_matches_current_behavior(tmp_path):
    codebase = tmp_path / "repo"
    _write_java(
        codebase / "src" / "main" / "java" / "com" / "example" / "Foo.java",
        "package com.example;\npublic class Foo { public void bar() {} }\n",
    )

    output = tmp_path / "out"
    result = build_graph_partitioned(codebase, output)

    assert result["multi_module"] is False
    assert result["modules"] == {}
    conn = graph_store.connect(Path(result["db_path"]))
    graph = graph_store.load_graph(conn, variant="main", module_id=None)
    assert any(c.full_name == "com.example.Foo" for c in graph.classes)


def test_multi_module_fixture_produces_one_db_with_module_ids(tmp_path):
    codebase = tmp_path / "repo"
    (codebase / "module-a").mkdir(parents=True)
    (codebase / "module-a" / "build.gradle").write_text("// a", encoding="utf-8")
    _write_java(
        codebase / "module-a" / "src" / "main" / "java" / "com" / "a" / "Foo.java",
        "package com.a;\npublic class Foo { public void bar() {} }\n",
    )

    (codebase / "module-b").mkdir(parents=True)
    (codebase / "module-b" / "build.gradle").write_text("// b", encoding="utf-8")
    _write_java(
        codebase / "module-b" / "src" / "main" / "java" / "com" / "b" / "Baz.java",
        "package com.b;\npublic class Baz { public void qux() {} }\n",
    )

    output = tmp_path / "out"
    result = build_graph_partitioned(codebase, output)

    assert result["multi_module"] is True
    assert set(result["modules"].keys()) == {"module-a", "module-b"}

    conn = graph_store.connect(Path(result["db_path"]))
    modules = graph_store.list_modules(conn)
    assert {m["module_id"] for m in modules} == {"module-a", "module-b"}

    graph_a = graph_store.load_graph(conn, variant="main", module_id="module-a")
    graph_b = graph_store.load_graph(conn, variant="main", module_id="module-b")
    assert any(c.full_name == "com.a.Foo" for c in graph_a.classes)
    assert any(c.full_name == "com.b.Baz" for c in graph_b.classes)
    # Each module's graph should not contain the other module's classes.
    assert all(c.full_name != "com.b.Baz" for c in graph_a.classes)
    assert all(c.full_name != "com.a.Foo" for c in graph_b.classes)
