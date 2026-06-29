from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def history(repo_path: str | None = None, limit: int = 50) -> dict:
    from ...llm.telemetry import (
        fetch_doc_index_history,
        fetch_index_history,
        fetch_reindex_history,
    )

    index_events = fetch_index_history(repo=repo_path, limit=limit)
    reindex_events = fetch_reindex_history(repo=repo_path, limit=limit)
    doc_events = fetch_doc_index_history(limit=limit * 4)
    return {
        "index_events": index_events,
        "reindex_events": reindex_events,
        "doc_events": doc_events,
    }
