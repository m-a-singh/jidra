from jidra.cli import compute_graph_health
from jidra.mcp_server import graph_health
from jidra import graph_store


def test_compute_graph_health_breakdown(simple_test_graph):
    health = compute_graph_health(simple_test_graph)

    assert health["total_callsites"] == 2
    assert health["resolved"] + health["unresolved"] + health["external"] == 2
    assert "resolved" in health["by_status"]
    assert health["resolved_pct"] == 100.0
    assert health["unresolved_pct"] == 0.0
    assert health["external_pct"] == 0.0


def test_compute_graph_health_empty_graph():
    from jidra.models import Graph

    empty = Graph(
        classes=[],
        methods=[],
        fields=[],
        callsites=[],
        inheritance_edges=[],
        resolved_call_edges=[],
    )
    health = compute_graph_health(empty)
    assert health["total_callsites"] == 0
    assert health["resolved_pct"] == 0.0


def test_mcp_graph_health_matches_cli(tmp_path, simple_test_graph):
    graph_path = tmp_path / "graph.db"
    conn = graph_store.connect(graph_path)
    graph_store.save_full_graph(conn, simple_test_graph, variant="validated")

    cli_health = compute_graph_health(simple_test_graph)
    mcp_health = graph_health(graph_path=str(graph_path))

    assert mcp_health == cli_health
