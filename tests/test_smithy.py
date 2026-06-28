"""Smithy IDL parsing (Phase A) + codegen bridging (Phase B) regression tests.

The fixture model text below is the real example from smithy-java's own
`examples/basic-server` (https://github.com/smithy-lang/smithy-java), not an
invented fixture -- parsing it caught two real bugs during development
(trait-argument syntax mistaken for a member, and the @http trait being
searched in the wrong text span) that a hand-written toy fixture wouldn't
have surfaced.
"""

from jidra.graph import graph_store
from jidra.engine.engine import JidraEngine
from jidra.models import ClassEntry
from jidra.smithy.smithy_bridge import link_operations
from jidra.extractors.smithy_extractor import parse_smithy_text

BEER_SERVICE_SMITHY = """
$version: "2"

namespace smithy.example

use aws.protocols#restJson1

@restJson1
service BeerService {
    operations: [
        GetBeer
        AddBeer
    ]
}

@http(method: "POST", uri: "/get-beer")
operation GetBeer {
    input := {
        id: Long
    }
    output := {
        beer: Beer
    }
}

structure Beer {
    @length(min: 3)
    name: String

    quantity: Long
}

@http(method: "POST", uri: "/add-beer")
operation AddBeer {
    input := {
        @required
        beer: Beer
    }

    output := {
        id: Long
    }
}
"""


class TestSmithyParsing:
    def test_namespace_and_shape_ids(self):
        shapes, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        op_ids = {o.id for o in operations}
        assert op_ids == {"smithy.example#GetBeer", "smithy.example#AddBeer"}

    def test_operations_attached_to_service_despite_no_commas(self):
        # The real example separates `operations: [...]` entries by newlines,
        # not commas -- valid Smithy syntax that a naive comma-split misses.
        _, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        services = {o.service_name for o in operations}
        assert services == {"BeerService"}

    def test_http_trait_captured(self):
        _, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        get_beer = next(o for o in operations if o.name == "GetBeer")
        assert get_beer.http_method == "POST"
        assert get_beer.http_uri == "/get-beer"

    def test_inline_input_output_become_shapes(self):
        shapes, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        shape_ids = {s.id for s in shapes}
        get_beer = next(o for o in operations if o.name == "GetBeer")
        assert get_beer.input_shape_id == "smithy.example#GetBeerInput"
        assert get_beer.output_shape_id == "smithy.example#GetBeerOutput"
        assert get_beer.input_shape_id in shape_ids
        assert get_beer.output_shape_id in shape_ids

    def test_trait_arguments_not_mistaken_for_members(self):
        # `@length(min: 3)` on the `name` member must not be parsed as a
        # member named `min` -- this was a real bug caught against this exact
        # fixture during development.
        shapes, _ = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        beer = next(s for s in shapes if s.name == "Beer")
        member_names = {m.name for m in beer.members}
        assert member_names == {"name", "quantity"}

    def test_required_member_flag(self):
        shapes, _ = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        add_beer_input = next(s for s in shapes if s.name == "AddBeerInput")
        beer_member = next(m for m in add_beer_input.members if m.name == "beer")
        assert beer_member.required is True

    def test_no_smithy_files_yields_nothing(self, tmp_path):
        from jidra.extractors.smithy_extractor import build_smithy_graph

        shapes, operations = build_smithy_graph(tmp_path)
        assert shapes == []
        assert operations == []


class TestSmithyBridge:
    def _operations(self):
        _, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        return operations

    def test_smithy_java_operation_suffix_links(self):
        handler = ClassEntry(
            id="cls1",
            package_name="com.example",
            name="GetBeerHandler",
            full_name="com.example.GetBeerHandler",
            file_path="GetBeerHandler.java",
            start_line=10,
            end_line=20,
            implements=["GetBeerOperationAsync"],
            language="java",
        )
        links = link_operations([handler], self._operations())
        assert len(links) == 1
        assert links[0].operation_id == "smithy.example#GetBeer"
        assert links[0].codegen_profile == "smithy_java"
        assert links[0].link_type == "implements"

    def test_smithy4s_service_trait_links_all_its_operations(self):
        impl = ClassEntry(
            id="cls2",
            package_name="com.example",
            name="BeerServiceImpl",
            full_name="com.example.BeerServiceImpl",
            file_path="BeerServiceImpl.scala",
            start_line=5,
            end_line=30,
            extends="BeerService[IO]",
            language="scala",
        )
        links = link_operations([impl], self._operations())
        op_ids = {link.operation_id for link in links}
        assert op_ids == {"smithy.example#GetBeer", "smithy.example#AddBeer"}
        assert all(link.codegen_profile == "smithy4s" for link in links)

    def test_unrelated_class_produces_no_link(self):
        unrelated = ClassEntry(
            id="cls3",
            package_name="com.example",
            name="Unrelated",
            full_name="com.example.Unrelated",
            file_path="Unrelated.java",
            start_line=1,
            end_line=2,
            implements=["java.io.Serializable"],
            language="java",
        )
        assert link_operations([unrelated], self._operations()) == []

    def test_wrong_language_does_not_cross_match(self):
        # A Java class extending a name that matches a Smithy *service* (the
        # smithy4s/Scala convention) must not link -- profiles are
        # language-scoped, not name-scoped, to avoid accidental cross-matches.
        java_cls = ClassEntry(
            id="cls4",
            package_name="com.example",
            name="Wrong",
            full_name="com.example.Wrong",
            file_path="Wrong.java",
            start_line=1,
            end_line=2,
            extends="BeerService",
            language="java",
        )
        assert link_operations([java_cls], self._operations()) == []


class TestSmithyPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        shapes, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        handler = ClassEntry(
            id="cls1",
            package_name="com.example",
            name="GetBeerHandler",
            full_name="com.example.GetBeerHandler",
            file_path="GetBeerHandler.java",
            start_line=10,
            end_line=20,
            implements=["GetBeerOperationAsync"],
            language="java",
        )
        links = link_operations([handler], operations)

        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        graph_store.save_smithy_graph(conn, shapes, operations, links)

        loaded_ops = graph_store.load_smithy_operations(conn)
        loaded_shapes = graph_store.load_smithy_shapes(conn)
        loaded_links = graph_store.load_smithy_operation_links(conn)

        assert {o.id for o in loaded_ops} == {o.id for o in operations}
        assert {s.id for s in loaded_shapes} == {s.id for s in shapes}
        assert len(loaded_links) == 1
        assert loaded_links[0].class_full_name == "com.example.GetBeerHandler"

        beer_input = next(s for s in loaded_shapes if s.name == "AddBeerInput")
        beer_member = next(m for m in beer_input.members if m.name == "beer")
        assert beer_member.required is True

    def test_save_smithy_graph_replaces_not_accumulates(self, tmp_path):
        shapes, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        graph_store.save_smithy_graph(conn, shapes, operations, [])
        graph_store.save_smithy_graph(conn, shapes, operations, [])
        assert len(graph_store.load_smithy_operations(conn)) == len(operations)

    def test_upgrade_from_2_1_creates_smithy_tables(self, simple_test_graph, tmp_path):
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        graph_store.save_full_graph(conn, simple_test_graph)
        conn.execute("DROP TABLE smithy_shapes")
        conn.execute("DROP TABLE smithy_operations")
        conn.execute("DROP TABLE smithy_operation_links")
        conn.execute("UPDATE schema_meta SET value='2.1' WHERE key='schema_version'")
        conn.commit()
        conn.close()

        conn2 = graph_store.connect(db)
        version = conn2.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == graph_store.SCHEMA_VERSION
        # Tables exist and are queryable post-upgrade (empty is fine -- the
        # migration only needs to make them queryable, not backfill them,
        # since there's no prior smithy data to have lost).
        assert (
            conn2.execute("SELECT count(*) FROM smithy_operations").fetchone()[0] == 0
        )


class TestEngineOperationGraph:
    def _graph_db(self, tmp_path) -> str:
        shapes, operations = parse_smithy_text(BEER_SERVICE_SMITHY, "main.smithy")
        handler = ClassEntry(
            id="cls1",
            package_name="com.example",
            name="GetBeerHandler",
            full_name="com.example.GetBeerHandler",
            file_path="GetBeerHandler.java",
            start_line=10,
            end_line=20,
            implements=["GetBeerOperationAsync"],
            language="java",
        )
        links = link_operations([handler], operations)
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        graph_store.save_smithy_graph(conn, shapes, operations, links)
        return str(db)

    def test_get_operation_graph_by_name(self, tmp_path):
        engine = JidraEngine(self._graph_db(tmp_path))
        result = engine.get_operation_graph("GetBeer")
        assert result["found"] is True
        assert result["service"] == "BeerService"
        assert result["http_method"] == "POST"
        assert result["handler_count"] == 1
        assert result["handlers"][0]["class_full_name"] == "com.example.GetBeerHandler"

    def test_get_operation_graph_by_shape_id(self, tmp_path):
        engine = JidraEngine(self._graph_db(tmp_path))
        result = engine.get_operation_graph("smithy.example#AddBeer")
        assert result["found"] is True
        # AddBeer has no Java handler in this fixture -- 0 handlers, not an error.
        assert result["handler_count"] == 0

    def test_get_operation_graph_unknown_returns_not_found(self, tmp_path):
        engine = JidraEngine(self._graph_db(tmp_path))
        result = engine.get_operation_graph("NoSuchOperation")
        assert result["found"] is False

    def test_list_operations_filters_by_service(self, tmp_path):
        engine = JidraEngine(self._graph_db(tmp_path))
        result = engine.list_operations(service="BeerService")
        assert result["count"] == 2
        names = {o["name"] for o in result["operations"]}
        assert names == {"GetBeer", "AddBeer"}
