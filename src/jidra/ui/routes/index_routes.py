from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class ProcessRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    actuator_url: str | None = None
    port: int = 8080
    timeout: int = 60
    skip_build: bool = False
    build_dir: str | None = None
    use_docker: bool = False
    write_mcp_config: bool = True


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _out_dir(repo_path: str, output_path: str | None) -> Path:
    from ...cli import _repo_output_dir
    return Path(output_path) if output_path else _repo_output_dir(Path(repo_path))


async def _stream_process(req: ProcessRequest):
    from ...cli import _process

    out_dir = _out_dir(req.repo_path, req.output_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    yield _sse("status", {"msg": f"Starting pipeline for {Path(req.repo_path).name}…", "phase": "start"})

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: _process(
                codebase=req.repo_path,
                actuator_url=req.actuator_url or None,
                port=req.port,
                timeout=req.timeout,
                output=str(out_dir),
                skip_build=req.skip_build,
                build_dir=req.build_dir or None,
                use_docker=req.use_docker,
            ),
        )
        yield _sse("status", {"msg": "Graph indexed and validated", "phase": "indexed"})

        if req.write_mcp_config:
            try:
                import sys as _sys
                from ...graph.graph_store import resolve_graph_db_path
                repo = Path(req.repo_path).resolve()
                graph_path = resolve_graph_db_path(out_dir)
                pkg_dir = Path(__file__).resolve().parents[3]
                venv_py = pkg_dir / "venv" / "bin" / "python"
                python = str(venv_py) if venv_py.exists() else _sys.executable
                mcp_entry = {
                    "mcpServers": {
                        "jidra": {
                            "type": "stdio",
                            "command": python,
                            "args": ["-m", "jidra.mcp_server", "--mode", "proxy",
                                     "--graph", str(graph_path), "--codebase", str(repo)],
                        }
                    }
                }
                settings_path = repo / ".mcp.json"
                settings_path.write_text(json.dumps(mcp_entry, indent=2))
                yield _sse("status", {"msg": f"MCP config written → {settings_path}", "phase": "mcp"})
            except Exception as mcp_err:
                yield _sse("warn", {"msg": f"MCP config skipped: {mcp_err}"})

        yield _sse("status", {"msg": "Done", "phase": "complete"})

    except SystemExit as exc:
        yield _sse("error", {"msg": str(exc)})
    except Exception as exc:
        yield _sse("error", {"msg": str(exc)})


@router.post("/run")
async def run_pipeline(req: ProcessRequest) -> StreamingResponse:
    return StreamingResponse(_stream_process(req), media_type="text/event-stream")


@router.get("/status")
async def index_status(repo_path: str, output_path: str | None = None) -> dict:
    import sqlite3
    from ...graph.graph_store import resolve_graph_db_path

    out_dir = _out_dir(repo_path, output_path)
    if not out_dir.exists():
        return {"indexed": False}
    try:
        db = resolve_graph_db_path(out_dir)
        if not db.exists():
            return {"indexed": False}
        conn = sqlite3.connect(str(db))
        validated = conn.execute("SELECT COUNT(*) FROM methods WHERE variant='validated'").fetchone()[0]
        main = conn.execute("SELECT COUNT(*) FROM methods WHERE variant='main'").fetchone()[0]
        classes = conn.execute("SELECT COUNT(*) FROM classes WHERE variant='main'").fetchone()[0]
        conn.close()
        variant = "validated" if validated > 0 else "main"
        count = validated if validated > 0 else main
        return {
            "indexed": count > 0,
            "variant": variant,
            "node_count": count,
            "class_count": classes,
            "validated": validated > 0,
        }
    except Exception:
        return {"indexed": False}
