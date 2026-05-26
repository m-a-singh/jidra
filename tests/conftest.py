from pathlib import Path

import pytest

from jidra.graph_io import load_graph_jsonl


@pytest.fixture(scope="session")
def sample_graph():
    """Session-scoped fixture graph loaded from repo root."""
    repo_root = Path(__file__).resolve().parents[1]
    return load_graph_jsonl(repo_root / "sample_graph.jsonl")
