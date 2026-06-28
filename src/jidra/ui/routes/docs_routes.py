from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _connect(repo_path: str, output_path: str | None):
    from ...cli import _repo_output_dir
    from ...graph import graph_store

    out_dir = Path(output_path) if output_path else _repo_output_dir(Path(repo_path))
    db_path = graph_store.resolve_graph_db_path(out_dir)
    if not db_path.exists():
        raise HTTPException(
            status_code=404, detail="Repository not indexed. Run the pipeline first."
        )
    conn = graph_store.connect(db_path)
    return conn, db_path


@router.get("/sources")
async def list_sources(repo_path: str, output_path: str | None = None) -> dict:
    from ...indexing import doc_store

    conn, _ = _connect(repo_path, output_path)
    doc_store.migrate(conn)
    sources = doc_store.list_sources(conn)
    conn.close()
    return {"sources": sources}


@router.get("/graph")
async def doc_graph(repo_path: str, output_path: str | None = None) -> dict:
    from ...graph import graph_store
    from ...indexing import doc_store
    from ...indexing.doc_graph_visualizer import build_doc_graph_data

    conn, db_path = _connect(repo_path, output_path)
    doc_store.migrate(conn)
    sources = doc_store.list_sources(conn)
    if not sources:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="No documents indexed yet. Enable 'index docs' on the IDX tab and run the pipeline.",
        )
    graph = graph_store.load_graph(conn, variant="main")
    data = build_doc_graph_data(conn, graph)
    conn.close()
    return data
