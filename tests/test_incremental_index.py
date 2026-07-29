import time
from pathlib import Path

from jidra import cli
from jidra.engine import reindexer
from jidra.extractors import extractor
from jidra.graph import graph_store


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
    incremental_graph = graph_store.load_graph(graph_store.connect(main_path), variant="main")

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

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="main")
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

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="main")
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

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="main")
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch_all = method_by_sig["com.example.UserService#fetchAll()"]
    find_all = method_by_sig["com.example.UserRepository#findAll()"]

    edges = {(e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges}
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

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="main")
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch = method_by_sig["com.example.UserService#fetch(String)"]
    find = method_by_sig["com.example.UserRepository#find(String)"]

    edges = {(e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges}
    assert (fetch.id, find.id) in edges


def test_signature_change_invalidates_unchanged_caller_edge(tmp_path):
    """Critical risk case for scoped incremental re-resolution: a method's
    *arity* changes (structural change, not just a body edit), and the only
    caller of that method lives in a file that was NOT touched. The stale
    edge to the now-gone overload must be dropped, not left behind — proving
    re-resolution scope was broadened to include that out-of-file caller
    rather than narrowed to just the changed file."""
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    cli._index(str(codebase), str(output), _quiet=True)

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="main")
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch = method_by_sig["com.example.UserService#fetch(String)"]
    old_find = method_by_sig["com.example.UserRepository#find(String)"]

    edges = {(e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges}
    assert (fetch.id, old_find.id) in edges

    # Change find(String) to find(String, boolean) — service.java (the only
    # caller) is untouched, and still calls repo.find(id) with one argument.
    time.sleep(0.01)
    files["repository"].write_text(
        """package com.example;

public class UserRepository {
    public String find(String id, boolean active) {
        return id;
    }
}
""",
        encoding="utf-8",
    )

    cli._index(str(codebase), str(output), _quiet=True)

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="main")
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch = method_by_sig["com.example.UserService#fetch(String)"]
    assert "com.example.UserRepository#find(String, boolean)" in method_by_sig
    assert "com.example.UserRepository#find(String)" not in method_by_sig

    edges = {(e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges}
    # The stale edge to the removed overload must be gone — it must not
    # silently linger in the DB just because fetch's own file/callsite list
    # was untouched by this change.
    assert (fetch.id, old_find.id) not in edges
    assert old_find.id not in {m.id for m in graph.methods}


def test_incremental_reindex_body_only_edit(tmp_path):
    """Regression test for diff_graph_records crashing on a body-only edit.

    Calls jidra.reindexer.incremental_reindex() directly (not cli._index(),
    which has its own separate incremental path) so this exercises the real
    callsite_changed_ids branch. Previously this raised AttributeError because
    that branch read a nonexistent `MethodEntry.callsites` attribute."""
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    # reindexer.incremental_reindex() keeps its own file_manifest.json, distinct
    # from cli._index()'s incremental path, so the initial index must also go
    # through incremental_reindex() (first call has no manifest -> full_rebuild).
    first = reindexer.incremental_reindex(codebase, output / "graph.db")
    assert first["change_type"] == "full_rebuild"

    # Body-only edit: same signature, same line count, different callsite
    # (find -> findAll) inside an otherwise untouched file.
    time.sleep(0.01)
    files["service"].write_text(
        """package com.example;

public class UserService {
    private UserRepository repo;

    public String fetch(String id) {
        return repo.findAll();
    }
}
""",
        encoding="utf-8",
    )

    result = reindexer.incremental_reindex(codebase, output / "graph.db")

    assert result["change_type"] == "callsite_change"

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="validated")
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch = method_by_sig["com.example.UserService#fetch(String)"]
    callees = {c.callee_name for c in graph.callsites if c.caller_method_id == fetch.id}
    assert callees == {"findAll"}


def test_reindexer_signature_change_invalidates_unchanged_caller_edge(tmp_path):
    """Same scenario as test_signature_change_invalidates_unchanged_caller_edge,
    but through reindexer.incremental_reindex() directly rather than
    cli._index() — that test exercises cli._index()'s own separate incremental
    path, not the _resolve_calls(only_caller_ids=...) scoping added to
    reindexer.py's _update_callsite_edges/_do_structural_reindex. This confirms
    the scoping fix (limiting re-resolution to callers in the changed file) does
    not leave dangling edges from callers in UNTOUCHED files pointing at a
    removed overload."""
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    first = reindexer.incremental_reindex(codebase, output / "graph.db")
    assert first["change_type"] == "full_rebuild"

    # service.java (caller of find(String)) is left untouched; only
    # repository.java changes, and only its signature (arity).
    time.sleep(0.01)
    files["repository"].write_text(
        """package com.example;

public class UserRepository {
    public String find(String id, boolean active) {
        return id;
    }
}
""",
        encoding="utf-8",
    )

    result = reindexer.incremental_reindex(codebase, output / "graph.db")
    assert result["change_type"] == "structural"

    graph = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="validated")
    method_by_sig = {m.signature: m for m in graph.methods}
    fetch = method_by_sig["com.example.UserService#fetch(String)"]
    assert "com.example.UserRepository#find(String, boolean)" in method_by_sig
    assert "com.example.UserRepository#find(String)" not in method_by_sig

    live_method_ids = {m.id for m in graph.methods}
    edges = {(e.caller_method_id, e.callee_method_id) for e in graph.resolved_call_edges}
    stale_edges = [e for e in edges if e[0] == fetch.id and e[1] not in live_method_ids]
    assert not stale_edges, (
        "scoped _resolve_calls left a dangling edge from an untouched caller "
        f"to a removed overload: {stale_edges}"
    )


def test_parse_failure_does_not_delete_file_methods(tmp_path, monkeypatch):
    """A file that fails to parse mid-batch must not have its existing methods
    treated as 'removed' by diff_graph_records — that would phantom-delete real,
    still-on-disk code just because this reindex cycle couldn't parse it (e.g. an
    agent saved it mid-edit with a syntax error). Regression test for the bug
    exposed by adding per-file fault isolation to parallel_map/build_graph_for_files:
    isolating the failure (skip-and-continue) is only safe if the failed file is
    also excluded from diff_graph_records' affected_files, otherwise "mini_graph
    has zero methods for this file" reads as "all its methods were deleted"."""
    codebase = tmp_path / "repo"
    files = _make_multi_file_codebase(codebase)
    output = tmp_path / "out"

    first = reindexer.incremental_reindex(codebase, output / "graph.db")
    assert first["change_type"] == "full_rebuild"

    # _make_multi_file_codebase always produces exactly this one method in
    # repository.java — hardcoded rather than read from graph.db between calls,
    # since reading immediately after a fresh full_rebuild via a brand-new
    # connection (outside incremental_reindex's own copy2+connect+load_graph
    # flow) is a separate, unrelated timing hazard not exercised by real usage;
    # every other test in this file only reads graph.db after the final call.
    repo_methods_before = {"com.example.UserRepository#find(String)"}

    # Touch repository.java (changes its mtime/fingerprint so it's picked up as
    # "changed") but make _extract_file raise for it specifically, simulating a
    # transient parse failure (e.g. mid-edit syntax error) without depending on
    # tree-sitter actually throwing on malformed source (it error-recovers instead).
    time.sleep(0.01)
    files["repository"].write_text(
        files["repository"].read_text(encoding="utf-8") + "\n// touched\n",
        encoding="utf-8",
    )
    # Also make a real, valid change to a different file in the same batch, to
    # confirm one file's failure doesn't abort the other file's update.
    files["service"].write_text(
        """package com.example;

public class UserService {
    private UserRepository repo;

    public String fetch(String id) {
        return repo.findAll();
    }
}
""",
        encoding="utf-8",
    )

    real_extract_file = extractor._extract_file

    def _flaky_extract_file(file_path, parser=None):
        if file_path == files["repository"]:
            raise ValueError("simulated parse failure")
        return real_extract_file(file_path, parser=parser)

    monkeypatch.setattr(extractor, "_extract_file", _flaky_extract_file)

    result = reindexer.incremental_reindex(codebase, output / "graph.db")
    assert result["change_type"] != "skipped"

    after = graph_store.load_graph(graph_store.connect(output / "graph.db"), variant="validated")
    repo_methods_after = {
        m.signature for m in after.methods if m.file_path == str(files["repository"])
    }
    assert repo_methods_after == repo_methods_before, (
        "repository.java's methods must survive untouched when it fails to parse, "
        f"got {repo_methods_after!r} instead of {repo_methods_before!r}"
    )

    service_method_by_sig = {
        m.signature: m for m in after.methods if m.file_path == str(files["service"])
    }
    fetch = service_method_by_sig["com.example.UserService#fetch(String)"]
    callees = {c.callee_name for c in after.callsites if c.caller_method_id == fetch.id}
    assert callees == {"findAll"}, (
        "the OTHER file's valid change should still persist even though "
        "repository.java failed to parse in the same batch"
    )
