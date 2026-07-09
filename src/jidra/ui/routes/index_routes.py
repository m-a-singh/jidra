from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ...indexing.resources_indexer import discover_resource_files, index_resource_file


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
    index_docs: bool = True
    skip_folders: list[str] | None = None


# Folders commonly excluded across the language-specific filter modules
# (filters/filters.py, py_filters.py, go_filters.py, ts_filters.py) — used
# here only to pre-check the UI folder picker's default state. The actual
# exclusion during indexing still goes through each language's own filter
# plus gitignore; this list is a coarse approximation for UX purposes.
_DEFAULT_EXCLUDED_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "vendor",
    ".cache",
    "coverage",
    "venv",
    ".venv",
    "__pycache__",
    "target",
    ".gradle",
    "out",
    ".next",
    ".nuxt",
    "public",
    "bin",
    ".turbo",
    ".output",
    ".svelte-kit",
    "generated",
    ".pytest_cache",
    ".mypy_cache",
    "site-packages",
    ".expo",
    "android",
    "ios",
}


_DOC_EXTENSIONS = (".md", ".mdx", ".txt", ".pdf", ".docx")
_DOC_IGNORE_DIRS = ("node_modules", ".git", "venv", "__pycache__", "dist", "build")


def _discover_doc_files(repo: Path) -> list[Path]:
    return [
        f
        for f in repo.rglob("*")
        if f.is_file()
        and f.suffix.lower() in _DOC_EXTENSIONS
        and not any(p in f.parts for p in _DOC_IGNORE_DIRS)
    ]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _out_dir(repo_path: str, output_path: str | None) -> Path:
    if output_path:
        return Path(output_path)
    jidra_dir = Path(repo_path) / ".jidra"
    jidra_dir.mkdir(exist_ok=True)
    return jidra_dir


async def _stream_process(req: ProcessRequest):
    from ...cli import _process

    out_dir = _out_dir(req.repo_path, req.output_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    yield _sse(
        "status",
        {"msg": f"Starting pipeline for {Path(req.repo_path).name}…", "phase": "start"},
    )

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
                skip_folders=set(req.skip_folders) if req.skip_folders else None,
            ),
        )
        yield _sse("status", {"msg": "Graph indexed and validated", "phase": "indexed"})

        try:
            from collections import Counter
            from ...graph import graph_store as _gs

            _db = _gs.resolve_graph_db_path(out_dir)
            _conn = _gs.connect(_db)
            _graph = _gs.load_graph(_conn, variant="main")
            _res = dict(Counter(c.resolution_status for c in _graph.callsites))
            _rescued = sum(
                1
                for c in _graph.callsites
                if (c.resolution_reason or "").startswith("second-pass")
            )
            # Diagnose unresolved_receiver: what does the receiver look like?
            _unres_recv = [
                c
                for c in _graph.callsites
                if c.resolution_status == "unresolved_receiver"
            ]
            _recv_no_dot = sum(
                1 for c in _unres_recv if c.receiver and "." not in c.receiver
            )
            _recv_dotted = sum(
                1
                for c in _unres_recv
                if c.receiver
                and "." in c.receiver
                and not c.receiver.rstrip().endswith(")")
            )
            _recv_chain = sum(
                1
                for c in _unres_recv
                if c.receiver and c.receiver.rstrip().endswith(")")
            )
            _recv_none = sum(1 for c in _unres_recv if not c.receiver)
            _impl_suffix = _res.get("resolved_impl_suffix", 0)
            _cha = _res.get("resolved_cha", 0)
            _total_cs = len(_graph.callsites)
            _resolved = sum(
                v for k, v in _res.items() if not k.startswith("unresolved")
            )
            _unresolved = sum(v for k, v in _res.items() if k.startswith("unresolved"))
            yield _sse(
                "status",
                {
                    "msg": (
                        f"[resolution] total={_total_cs} resolved={_resolved} unresolved={_unresolved} "
                        f"second-pass={_rescued} impl-suffix={_impl_suffix} cha={_cha}"
                    ),
                    "phase": "indexed",
                },
            )
            yield _sse(
                "status",
                {
                    "msg": (
                        f"[unresolved_receiver breakdown] simple_var={_recv_no_dot} "
                        f"dotted_field={_recv_dotted} method_chain={_recv_chain} no_receiver={_recv_none}"
                    ),
                    "phase": "indexed",
                },
            )
        except Exception:
            pass

        if req.index_docs:
            repo = Path(req.repo_path).resolve()
            doc_files = _discover_doc_files(repo)
            if doc_files:
                yield _sse(
                    "status",
                    {
                        "msg": f"Found {len(doc_files)} document(s) — indexing…",
                        "phase": "docs",
                    },
                )
                try:
                    from ...graph import graph_store
                    from ...indexing import doc_store as _doc_store
                    from ...indexing.doc_indexer import (
                        extract_graph_names,
                        index_document,
                    )

                    graph_path = graph_store.resolve_graph_db_path(out_dir)
                    conn = graph_store.connect(graph_path)
                    _doc_store.migrate(conn)
                    graph = graph_store.load_graph(conn, variant="main")
                    class_names, method_names = extract_graph_names(graph)

                    total_chunks = 0
                    for f in doc_files:
                        try:
                            n_chunks = await loop.run_in_executor(
                                None,
                                lambda f=f: index_document(
                                    conn, str(f), class_names, method_names
                                ),
                            )
                            total_chunks += n_chunks
                            yield _sse(
                                "status",
                                {
                                    "msg": f"  {f.name} → {n_chunks} chunks",
                                    "phase": "docs",
                                },
                            )
                        except Exception as doc_err:
                            yield _sse(
                                "warn", {"msg": f"  {f.name} skipped: {doc_err}"}
                            )

                    # Index Spring resources files (YAML/JSON/XML)
                    resource_files = discover_resource_files(
                        repo,
                        skip_folders=set(req.skip_folders)
                        if req.skip_folders
                        else None,
                    )
                    for rf in resource_files:
                        try:
                            n = index_resource_file(
                                conn, str(rf), class_names, method_names
                            )
                            if n:
                                total_chunks += n
                        except Exception:
                            pass

                    conn.close()
                    yield _sse(
                        "status",
                        {
                            "msg": f"Indexed {len(doc_files)} document(s), {total_chunks} chunks total",
                            "phase": "docs",
                        },
                    )
                except Exception as docs_err:
                    yield _sse(
                        "warn", {"msg": f"Document indexing skipped: {docs_err}"}
                    )

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
                            "args": [
                                "-m",
                                "jidra.server.mcp_server",
                                "--mode",
                                "proxy",
                                "--graph",
                                str(graph_path),
                                "--codebase",
                                str(repo),
                            ],
                        }
                    }
                }
                settings_path = repo / ".mcp.json"
                settings_path.write_text(json.dumps(mcp_entry, indent=2))
                yield _sse(
                    "status",
                    {"msg": f"MCP config written → {settings_path}", "phase": "mcp"},
                )
            except Exception as mcp_err:
                yield _sse("warn", {"msg": f"MCP config skipped: {mcp_err}"})

        # Install jidra-investigator agent + skills (same as jidra init)
        try:
            from ...cli import _install_agent

            repo = Path(req.repo_path).resolve()
            await loop.run_in_executor(None, lambda: _install_agent(repo))
            yield _sse(
                "status",
                {
                    "msg": "Agent + skills installed (.claude/agents, .claude/skills)",
                    "phase": "agents",
                },
            )
        except Exception as agent_err:
            yield _sse("warn", {"msg": f"Agent install skipped: {agent_err}"})

        yield _sse("status", {"msg": "Done", "phase": "complete"})

    except SystemExit as exc:
        yield _sse("error", {"msg": str(exc)})
    except Exception as exc:
        yield _sse("error", {"msg": str(exc)})


@router.post("/run")
async def run_pipeline(req: ProcessRequest) -> StreamingResponse:
    return StreamingResponse(_stream_process(req), media_type="text/event-stream")


@router.get("/list-folders")
async def list_folders(repo_path: str, subpath: str = "") -> dict:
    """One level of subdirectories under `repo_path/subpath`, with each
    pre-marked as default-excluded (gitignored or in the common default
    exclude set) so the UI tree picker can pre-check its starting state.
    The frontend calls this again with a deeper `subpath` to lazy-expand."""
    from ...filters.file_filters import gitignored_paths

    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {repo}")

    target = (repo / subpath).resolve() if subpath else repo
    if target != repo and repo not in target.parents:
        raise HTTPException(status_code=400, detail="subpath escapes repo_path")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")

    try:
        subdirs = sorted(
            (d for d in target.iterdir() if d.is_dir()), key=lambda d: d.name
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ignored = gitignored_paths(repo, subdirs)
    folders = [
        {
            "name": d.name,
            "path": d.relative_to(repo).as_posix(),
            "default_excluded": d in ignored or d.name in _DEFAULT_EXCLUDED_DIRS,
        }
        for d in subdirs
    ]
    return {"path": subpath, "folders": folders}


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
        validated = conn.execute(
            "SELECT COUNT(*) FROM methods WHERE variant='validated'"
        ).fetchone()[0]
        main = conn.execute(
            "SELECT COUNT(*) FROM methods WHERE variant='main'"
        ).fetchone()[0]
        classes = conn.execute(
            "SELECT COUNT(*) FROM classes WHERE variant='main'"
        ).fetchone()[0]
        doc_count = 0
        try:
            doc_count = conn.execute(
                "SELECT COUNT(DISTINCT source_path) FROM doc_chunks"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        conn.close()
        count = main if main > 0 else validated
        variant = "main" if main > 0 else "validated"
        return {
            "indexed": count > 0,
            "variant": variant,
            "node_count": count,
            "class_count": classes,
            "validated": validated > 0,
            "doc_count": doc_count,
        }
    except Exception:
        return {"indexed": False}


class ReindexRequest(BaseModel):
    repo_path: str
    output_path: str | None = None
    changed_files: list[str] | None = None


@router.post("/reindex")
async def reindex(req: ReindexRequest) -> dict:
    from ...engine.reindexer import incremental_reindex
    from ...graph.graph_store import resolve_graph_db_path

    out_dir = _out_dir(req.repo_path, req.output_path)
    graph_path = resolve_graph_db_path(out_dir)
    codebase = Path(req.repo_path).resolve()
    summary = incremental_reindex(
        codebase, graph_path, hint_changed_files=req.changed_files
    )
    return {"summary": summary}


class DaemonRequest(BaseModel):
    repo_path: str
    output_path: str | None = None


def _daemon_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@router.get("/daemon/status")
async def daemon_status(repo_path: str, output_path: str | None = None) -> dict:
    from ...engine.daemon import pid_path
    from ...engine.reindexer import load_manifest

    out_dir = _out_dir(repo_path, output_path)
    pid_file = pid_path(str(out_dir / "graph.db"))

    pid = None
    running = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            running = _daemon_pid_alive(pid)
            if not running:
                pid_file.unlink(missing_ok=True)
                pid = None
        except (ValueError, OSError):
            pid = None

    manifest = load_manifest(out_dir)
    last_indexed_at = None
    if manifest.get("last_indexed_at_ns"):
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(
            manifest["last_indexed_at_ns"] / 1_000_000_000, tz=timezone.utc
        )
        last_indexed_at = dt.isoformat()

    return {"running": running, "pid": pid, "last_indexed_at": last_indexed_at}


@router.post("/daemon/start")
async def daemon_start(req: DaemonRequest) -> dict:
    from ...engine.daemon import JidraDaemon, pid_path

    out_dir = _out_dir(req.repo_path, req.output_path)
    graph_db = out_dir / "graph.db"
    if not graph_db.exists():
        raise HTTPException(status_code=400, detail="graph.db not found — run index first")

    pid_file = pid_path(str(graph_db))
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _daemon_pid_alive(pid):
                return {"started": False, "reason": "already running", "pid": pid}
        except (ValueError, OSError):
            pass

    JidraDaemon(str(graph_db), req.repo_path).start(daemonize=True)
    return {"started": True}


@router.post("/daemon/stop")
async def daemon_stop(req: DaemonRequest) -> dict:
    from ...engine.daemon import pid_path

    out_dir = _out_dir(req.repo_path, req.output_path)
    pid_file = pid_path(str(out_dir / "graph.db"))

    if not pid_file.exists():
        return {"stopped": False, "reason": "not running"}

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        return {"stopped": True, "pid": pid}
    except (ProcessLookupError, ValueError):
        pid_file.unlink(missing_ok=True)
        return {"stopped": False, "reason": "process not found"}
    except PermissionError:
        return {"stopped": False, "reason": "permission denied"}


@router.get("/daemon/log")
async def daemon_log(repo_path: str, output_path: str | None = None, limit: int = 50) -> dict:
    out_dir = _out_dir(repo_path, output_path)
    log_path = out_dir / "reindex.log"
    if not log_path.exists():
        return {"entries": []}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        entries.reverse()
        return {"entries": entries}
    except OSError:
        return {"entries": []}


@router.get("/daemon/stale")
async def daemon_stale(repo_path: str, output_path: str | None = None) -> dict:
    from ...engine.reindexer import check_staleness

    out_dir = _out_dir(repo_path, output_path)
    graph_db = out_dir / "graph.db"
    if not graph_db.exists():
        return {"stale": False, "reason": "not indexed"}
    codebase = Path(repo_path).resolve()
    return check_staleness(codebase, out_dir)
