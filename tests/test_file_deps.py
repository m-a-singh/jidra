"""Phase 3 — blast-radius / reverse file-dependency regression tests.

The shared fixture wires TestController -> TestService -> TestRepository, each
in its own file, with resolved call edges between them.
"""

from jidra.engine.engine import JidraEngine

CONTROLLER = "src/main/java/com/example/TestController.java"
SERVICE = "src/main/java/com/example/TestService.java"
REPOSITORY = "src/main/java/com/example/TestRepository.java"


class TestFileDependents:
    def test_service_dependents_include_controller(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_file_dependents(SERVICE)
        files = [d["file"] for d in result["dependents"]]
        assert CONTROLLER in files
        assert result["total_call_sites"] >= 1

    def test_methods_called_listed(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_file_dependents(SERVICE)
        entry = next(d for d in result["dependents"] if d["file"] == CONTROLLER)
        assert "process" in entry["methods_called"]

    def test_leaf_file_has_no_dependencies(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_file_dependencies(REPOSITORY)
        assert result["dependencies"] == []

    def test_unknown_file_returns_note(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_file_dependents("does/not/Exist.java")
        assert result["total_dependent_files"] == 0
        assert "note" in result


class TestFileDependencies:
    def test_controller_depends_on_service(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_file_dependencies(CONTROLLER)
        files = [d["file"] for d in result["dependencies"]]
        assert SERVICE in files

    def test_relative_path_suffix_matches(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        # A repo-relative tail should still resolve to the stored file.
        result = engine.get_file_dependencies("com/example/TestController.java")
        files = [d["file"] for d in result["dependencies"]]
        assert SERVICE in files
