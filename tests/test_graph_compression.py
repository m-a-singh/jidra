from jidra.exporter import export_jsonl, graph_records
from jidra.graph_io import load_graph_jsonl


def test_zst_roundtrip_matches_plain_jsonl(tmp_path, simple_test_graph):
    records = graph_records(simple_test_graph)

    plain_path = tmp_path / "graph.jsonl"
    zst_path = tmp_path / "graph.jsonl.zst"

    export_jsonl(plain_path, records)
    export_jsonl(zst_path, records)

    assert zst_path.exists()
    assert zst_path.read_bytes()[:4] != plain_path.read_bytes()[:4]

    plain_graph = load_graph_jsonl(plain_path)
    zst_graph = load_graph_jsonl(zst_path)

    def keys(graph):
        return {
            "classes": {c.id for c in graph.classes},
            "methods": {m.id for m in graph.methods},
            "fields": {f.id for f in graph.fields},
            "callsites": {c.id for c in graph.callsites},
            "resolved_call_edges": {e.id for e in graph.resolved_call_edges},
        }

    assert keys(plain_graph) == keys(zst_graph)
