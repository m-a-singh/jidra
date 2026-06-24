import time
from pathlib import Path

import jidra.cli as cli
from jidra import graph_store


def _record_keys(graph) -> dict:
    return {
        "classes": {c.id for c in graph.classes},
        "methods": {m.id for m in graph.methods},
        "fields": {f.id for f in graph.fields},
        "callsites": {c.id for c in graph.callsites},
        "resolved_call_edges": {e.id for e in graph.resolved_call_edges},
    }


def _write_java(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_multi_file_codebase(root: Path) -> dict:
    base = root / "src" / "main" / "java" / "com" / "example"
    files = {
        "controller": base / "UserController.java",
        "service": base / "UserService.java",
        "repository": base / "UserRepository.java",
    }
    _write_java(
        files["controller"],
        """package com.example;

public class UserController {
    private UserService service;

    public String getUser(String id) {
        return service.fetch(id);
    }
}
""",
    )
    _write_java(
        files["service"],
        """package com.example;

public class UserService {
    private UserRepository repo;

    public String fetch(String id) {
        return repo.find(id);
    }
}
""",
    )
    _write_java(
        files["repository"],
        """package com.example;

public class UserRepository {
    public String find(String id) {
        return id;
    }
}
""",
    )
    return files


def test_single_file_change_equivalent_to_full_rebuild(tmp_path):
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    time.sleep(0.01)
    files["service"].write_text(
        """package com.example;

public class UserService {
    private UserRepository repo;

    public String fetch(String id) {
        return repo.find(id);
    }

    public String fetchTrimmed(String id) {
        return repo.find(id.trim());
    }
}
""",
        encoding="utf-8",
    )

    cli._index(str(codebase), str(output), _quiet=True)

    main_path = output / "graph.db"
    incremental_graph = graph_store.load_graph(
        graph_store.connect(main_path), variant="main"
    )

    # Full rebuild into a separate output dir for comparison.
    full_output = tmp_path / "out_full"
    cli._index(str(codebase), str(full_output), _quiet=True, force=True)
    full_graph = graph_store.load_graph(
        graph_store.connect(full_output / "graph.db"), variant="main"
    )

    assert _record_keys(incremental_graph) == _record_keys(full_graph)


def test_file_deletion_removes_records(tmp_path):
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    files["repository"].unlink()
    cli._index(str(codebase), str(output), _quiet=True)

    graph = graph_store.load_graph(
        graph_store.connect(output / "graph.db"), variant="main"
    )
    repo_path = str(files["repository"])
    assert all(c.file_path != repo_path for c in graph.classes)
    assert all(m.file_path != repo_path for m in graph.methods)


def test_new_file_adds_records(tmp_path):
    codebase = tmp_path / "repo"
    _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    new_file = codebase / "src" / "main" / "java" / "com" / "example" / "AuditLog.java"
    _write_java(
        new_file,
        """package com.example;

public class AuditLog {
    public void record(String msg) {
        System.out.println(msg);
    }
}
""",
    )

    cli._index(str(codebase), str(output), _quiet=True)

    graph = graph_store.load_graph(
        graph_store.connect(output / "graph.db"), variant="main"
    )
    assert any(c.full_name == "com.example.AuditLog" for c in graph.classes)


def test_cross_file_edges_resolve_after_partial_update(tmp_path):
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    time.sleep(0.01)
    files["repository"].write_text(
        """package com.example;

public class UserRepository {
    public String find(String id) {
        return id;
    }

    public String findAll() {
        return "all";
    }
}
""",
        encoding="utf-8",
    )
    files["service"].write_text(
        """package com.example;

public class UserService {
    private UserRepository repo;

    public String fetch(String id) {
        return repo.find(id);
    }

    public String fetchAll() {
        return repo.findAll();
    }
}
""",
        encoding="utf-8",
    )

    cli._index(str(codebase), str(output), _quiet=True)

    graph = graph_store.load_graph(
        graph_store.connect(output / "graph.db"), variant="main"
    )
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch_all = method_by_sig["com.example.UserService#fetchAll()"]
    find_all = method_by_sig["com.example.UserRepository#findAll()"]

    edges = {
        (e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges
    }
    assert (fetch_all.id, find_all.id) in edges


def test_unchanged_caller_edge_into_changed_file_still_resolves(tmp_path):
    """Critical risk case: a method in a file that was NOT re-parsed calls a
    method in a file that WAS re-parsed. The edge must still resolve correctly
    after the partial reindex, since call resolution needs full-graph context."""
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    # Only touch the repository file; service.java (the caller) is untouched.
    time.sleep(0.01)
    files["repository"].write_text(
        """package com.example;

public class UserRepository {
    public String find(String id) {
        return id.trim();
    }
}
""",
        encoding="utf-8",
    )

    cli._index(str(codebase), str(output), _quiet=True)

    graph = graph_store.load_graph(
        graph_store.connect(output / "graph.db"), variant="main"
    )
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch = method_by_sig["com.example.UserService#fetch(String)"]
    find = method_by_sig["com.example.UserRepository#find(String)"]

    edges = {
        (e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges
    }
    assert (fetch.id, find.id) in edges
