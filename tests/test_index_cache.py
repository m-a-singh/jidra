import time
from pathlib import Path

import jidra.cli as cli
from jidra.utils.cache import cache_path, load_cache


def _make_codebase(tmp_path: Path) -> Path:
    codebase = tmp_path / "repo"
    codebase.mkdir()
    (codebase / "Foo.java").write_text(
        "package com.example;\npublic class Foo { public void bar() {} }\n",
        encoding="utf-8",
    )
    return codebase


def test_first_run_builds_graph_and_writes_cache(tmp_path, monkeypatch):
    codebase = _make_codebase(tmp_path)
    output = tmp_path / "out"

    calls = []
    real_build_graph = cli.build_graph

    def spy_build_graph(*args, **kwargs):
        calls.append(1)
        return real_build_graph(*args, **kwargs)

    monkeypatch.setattr(cli, "build_graph", spy_build_graph)

    cli._index(str(codebase), str(output), _quiet=True)

    assert calls == [1]
    assert cache_path(output).exists()
    cached = load_cache(output)
    assert "fingerprint" in cached


def test_second_run_with_no_changes_skips_rebuild(tmp_path, monkeypatch, capsys):
    codebase = _make_codebase(tmp_path)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    calls = []
    monkeypatch.setattr(
        cli,
        "build_graph",
        lambda *a, **k: (
            calls.append(1)
            or (_ for _ in ()).throw(AssertionError("build_graph should not be called"))
        ),
    )

    cli._index(str(codebase), str(output))
    captured = capsys.readouterr()

    assert calls == []
    assert "Graph up to date, skipping rebuild." in captured.out


def test_modifying_file_invalidates_cache(tmp_path, monkeypatch):
    codebase = _make_codebase(tmp_path)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    java_file = codebase / "Foo.java"
    # Ensure mtime/size actually differ from the cached fingerprint.
    time.sleep(0.01)
    java_file.write_text(
        "package com.example;\npublic class Foo { public void bar() { int x = 1; } }\n",
        encoding="utf-8",
    )

    calls = []
    real_build_graph = cli.build_graph

    def spy_build_graph(*args, **kwargs):
        calls.append(1)
        return real_build_graph(*args, **kwargs)

    monkeypatch.setattr(cli, "build_graph", spy_build_graph)

    cli._index(str(codebase), str(output), _quiet=True)

    assert calls == [1]


def test_same_codebase_different_output_dir_still_builds(tmp_path):
    """Cache is keyed by codebase root, not output dir — a cache hit must not
    skip writing the graph to a new/different output location."""
    codebase = _make_codebase(tmp_path)
    output_a = tmp_path / "out_a"
    output_b = tmp_path / "out_b"

    cli._index(str(codebase), str(output_a), _quiet=True)
    cli._index(str(codebase), str(output_b), _quiet=True)

    assert (output_b / "graph.db").exists()


def test_missing_output_files_forces_rebuild_even_on_cache_hit(tmp_path, capsys):
    codebase = _make_codebase(tmp_path)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)
    (output / "graph.db").unlink()

    cli._index(str(codebase), str(output))
    captured = capsys.readouterr()

    assert "Graph up to date, skipping rebuild." not in captured.out
    assert (output / "graph.db").exists()


def test_force_always_rebuilds(tmp_path, monkeypatch):
    codebase = _make_codebase(tmp_path)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    calls = []
    real_build_graph = cli.build_graph

    def spy_build_graph(*args, **kwargs):
        calls.append(1)
        return real_build_graph(*args, **kwargs)

    monkeypatch.setattr(cli, "build_graph", spy_build_graph)

    cli._index(str(codebase), str(output), _quiet=True, force=True)

    assert calls == [1]
