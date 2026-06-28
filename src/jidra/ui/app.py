from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import (
    docs_routes,
    explore_routes,
    graph_routes,
    history_routes,
    index_routes,
    mcp_routes,
    sql_routes,
    util_routes,
)

_UI_DIST = Path(__file__).parent.parent.parent.parent / "ui" / "dist"

app = FastAPI(title="JIDRA UI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(index_routes.router, prefix="/api/index", tags=["index"])
app.include_router(util_routes.router, prefix="/api/util", tags=["util"])
app.include_router(graph_routes.router, prefix="/api/graph", tags=["graph"])
app.include_router(sql_routes.router, prefix="/api/sql", tags=["sql"])
app.include_router(mcp_routes.router, prefix="/api/mcp", tags=["mcp"])
app.include_router(explore_routes.router, prefix="/api/explore", tags=["explore"])
app.include_router(docs_routes.router, prefix="/api/docs", tags=["docs"])
app.include_router(history_routes.router, prefix="/api/history", tags=["history"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


if _UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_UI_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str = "") -> FileResponse:  # noqa: ARG001
        return FileResponse(_UI_DIST / "index.html")
