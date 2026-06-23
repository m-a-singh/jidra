import json
from pathlib import Path

from jidra.extractor import build_graph_partitioned
from jidra.graph_io import load_graph_jsonl


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
    assert result["index_path"] is None
    graph = load_graph_jsonl(output / "graph.jsonl")
    assert any(c.full_name == "com.example.Foo" for c in graph.classes)


def test_multi_module_fixture_produces_one_graph_per_module_plus_index(tmp_path):
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

    index = json.loads(Path(result["index_path"]).read_text(encoding="utf-8"))
    assert set(index.keys()) == {"module-a", "module-b"}

    graph_a = load_graph_jsonl(Path(index["module-a"]))
    graph_b = load_graph_jsonl(Path(index["module-b"]))
    assert any(c.full_name == "com.a.Foo" for c in graph_a.classes)
    assert any(c.full_name == "com.b.Baz" for c in graph_b.classes)
    # Each module's graph should not contain the other module's classes.
    assert all(c.full_name != "com.b.Baz" for c in graph_a.classes)
    assert all(c.full_name != "com.a.Foo" for c in graph_b.classes)
