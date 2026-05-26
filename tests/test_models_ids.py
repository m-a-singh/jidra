import pytest

from jidra.graph_io import load_graph_jsonl





def test_method_ids_are_unique_and_stable(sample_graph):
    methods = list(sample_graph.methods)
    assert methods

    ids = [m.id for m in methods]
    assert len(ids) == len(set(ids)), "method ids must be unique"

    # Re-load and ensure IDs for the same method signatures are stable.
    # conftest loads from absolute repo-root path; reload using the same helper.
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    g2 = load_graph_jsonl(repo_root / "sample_graph.jsonl")
    methods2 = list(g2.methods)

    # Map by signature+file_path+start_line to avoid depending on ordering.
    key = lambda m: (m.signature, m.file_path, m.start_line, m.end_line)
    map1 = {key(m): m.id for m in methods}
    map2 = {key(m): m.id for m in methods2}

    shared = set(map1.keys()) & set(map2.keys())
    assert shared, "expected some shared methods between loads"
    for k in list(shared)[:50]:
        assert map1[k] == map2[k]
