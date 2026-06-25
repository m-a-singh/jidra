"""Phase 1 — FTS5 search/explore + schema migration regression tests."""

from pathlib import Path

import pytest

from jidra import graph_store
from jidra.engine import JidraEngine
from jidra.models import Graph, MethodEntry


class TestFtsSync:
    def test_fts_populated_on_save(self, test_graph_file):
        conn = graph_store.connect(Path(test_graph_file))
        count = conn.execute("SELECT count(*) FROM methods_fts").fetchone()[0]
        assert count == 3

    def test_search_methods_matches_name_and_source(self, test_graph_file):
        conn = graph_store.connect(Path(test_graph_file))
        rows = graph_store.search_methods(conn, "handleRequest")
        assert any(r["method_name"] == "handleRequest" for r in rows)

    def test_search_methods_language_filter(self, test_graph_file):
        conn = graph_store.connect(Path(test_graph_file))
        # Fixture methods carry the default language ("unknown").
        assert graph_store.search_methods(conn, "process", language="python") == []
        assert graph_store.search_methods(conn, "process")
        assert graph_store.search_methods(conn, "process", language="unknown")

    def test_fts_stays_in_sync_after_rewrite(self, simple_test_graph, tmp_path):
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        graph_store.save_full_graph(conn, simple_test_graph)
        # Rewriting with an empty graph deletes the method rows; the delete
        # trigger must drop their FTS entries too.
        graph_store.save_full_graph(conn, Graph([], [], [], [], [], []))
        assert conn.execute("SELECT count(*) FROM methods_fts").fetchone()[0] == 0


class TestMigration:
    def test_upgrade_from_2_0_backfills_fts(self, simple_test_graph, tmp_path):
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        graph_store.save_full_graph(conn, simple_test_graph)
        # Simulate a pre-2.1 DB: drop FTS artifacts, stamp the old version.
        conn.execute("DROP TABLE methods_fts")
        for trig in (
            "methods_fts_insert",
            "methods_fts_delete",
            "methods_fts_update",
        ):
            conn.execute(f"DROP TRIGGER {trig}")
        conn.execute("UPDATE schema_meta SET value='2.0' WHERE key='schema_version'")
        conn.commit()
        conn.close()

        conn2 = graph_store.connect(db)
        version = conn2.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == graph_store.SCHEMA_VERSION
        assert conn2.execute("SELECT count(*) FROM methods_fts").fetchone()[0] == 3

    def test_unknown_version_still_raises(self, test_graph_file):
        conn = graph_store.connect(Path(test_graph_file))
        conn.execute("UPDATE schema_meta SET value='9.9' WHERE key='schema_version'")
        conn.commit()
        conn.close()
        with pytest.raises(graph_store.SchemaVersionMismatch):
            graph_store.connect(Path(test_graph_file))


class TestEngineSearch:
    def test_search_returns_ranked_results(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.search("handleRequest")
        assert result["count"] >= 1
        assert result["results"][0]["method_name"] == "handleRequest"

    def test_explore_tokenizes_camelcase(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.explore("handle request")
        names = [r["method_name"] for r in result["results"]]
        assert "handleRequest" in names
        assert "handle" in result["tokens"]

    def test_explore_demotes_test_files(self, tmp_path):
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        real = MethodEntry(
            id="m1",
            class_id="c1",
            class_full_name="A",
            method_name="validate",
            return_type="boolean",
            parameter_types=[],
            parameter_names=[],
            signature="boolean validate()",
            file_path="src/main/java/A.java",
            start_line=1,
            end_line=2,
            source="boolean validate(){return true;}",
            class_context={},
            language="java",
        )
        test = MethodEntry(
            id="m2",
            class_id="c2",
            class_full_name="ATest",
            method_name="validate",
            return_type="void",
            parameter_types=[],
            parameter_names=[],
            signature="void validate()",
            file_path="src/test/java/ATest.java",
            start_line=1,
            end_line=2,
            source="void validate(){}",
            class_context={},
            language="java",
        )
        graph_store.save_full_graph(conn, Graph([], [real, test], [], [], [], []))
        conn.close()
        engine = JidraEngine(str(db))
        result = engine.explore("validate")
        # The production method must outrank its test counterpart.
        assert result["results"][0]["method_id"] == "m1"

    def test_search_fallback_when_query_empty(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        assert engine.search("!!!")["count"] == 0
