from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


@router.get("/tools")
async def list_tools(repo_path: str | None = None) -> list[dict]:
    from ...server.mcp_server import visible_tool_names, build_mcp

    try:
        names = visible_tool_names()
        # Build a transient MCP to extract docstrings — no server started
        mcp = build_mcp(default_graph_path=repo_path)
        tool_map = {t.name: t for t in mcp._tool_manager.list_tools()}
        return [
            {
                "name": n,
                "description": tool_map[n].description if n in tool_map else "",
                "input_schema": tool_map[n].parameters if n in tool_map else {},
            }
            for n in names
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class CallRequest(BaseModel):
    tool: str
    params: dict
    repo_path: str | None = None
    output_path: str | None = None


@router.post("/call")
async def call_tool(req: CallRequest) -> dict:
    import asyncio

    from ...server.mcp_server import dispatch_tool
    from ...cli import _repo_output_dir

    if req.output_path:
        graph_path = req.output_path
    elif req.repo_path:
        from ...graph.graph_store import resolve_graph_db_path

        graph_path = str(resolve_graph_db_path(_repo_output_dir(Path(req.repo_path))))
    else:
        graph_path = None

    loop = asyncio.get_event_loop()
    try:
        # strip null values — let dispatch_tool use its defaults instead
        clean_params = {k: v for k, v in req.params.items() if v is not None}
        result = await loop.run_in_executor(
            None,
            lambda: dispatch_tool(
                req.tool,
                clean_params,
                default_graph_path=graph_path,
                codebase_path=req.repo_path,
            ),
        )
        return {"result": result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tool {req.tool!r} not found")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/session-log")
async def session_log(
    repo_path: str, output_path: str | None = None, limit: int = 100
) -> list[dict]:
    from ...cli import _repo_output_dir
    from ...graph.graph_store import resolve_graph_db_path
    from ...server.mcp_server import _resolve_graph_dir

    if output_path:
        graph_path = output_path
    else:
        graph_path = str(resolve_graph_db_path(_repo_output_dir(Path(repo_path))))
    graph_dir = _resolve_graph_dir(graph_path)

    log_path = graph_dir / ".jidra" / "session_log.jsonl"
    if not log_path.exists():
        # fall back to legacy location for entries logged before this fix
        log_path = Path(repo_path) / ".jidra" / "session_log.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return list(reversed(entries))
