from __future__ import annotations

import asyncio
import concurrent.futures
import concurrent.futures.thread as _cft
import contextlib
import json
import os
import signal
import threading
import weakref
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...filters.filters import EXCLUDED_DIRS as _JAVA_EXCLUDED_DIRS
from ...filters.go_filters import EXCLUDED_DIRS as _GO_EXCLUDED_DIRS
from ...filters.py_filters import EXCLUDED_DIRS as _PY_EXCLUDED_DIRS
from ...filters.scala_filters import EXCLUDED_DIRS as _SCALA_EXCLUDED_DIRS
from ...filters.ts_filters import EXCLUDED_DIRS as _TS_EXCLUDED_DIRS
from ...indexing.resources_indexer import discover_resource_files, index_resource_file

router = APIRouter()


class _DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """ThreadPoolExecutor whose threads are daemon — won't block process exit."""

    def _adjust_thread_count(self):
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = f"{self._thread_name_prefix or self}_{num_threads}"

            # --- Python 3.14+ vs 3.13 Compatibility Branching ---
            if hasattr(self, "_create_worker_context"):
                # Python 3.14+ uses the worker context manager
                worker_args = (
                    weakref.ref(self, weakref_cb),
                    self._create_worker_context(),
                    self._work_queue,
                )
            else:
                # Python <= 3.13 fallback
                initializer = getattr(self, "_initializer", None)
                initargs = getattr(self, "_initargs", ())
                worker_args = (
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    initializer,
                    initargs,
                )
            # ----------------------------------------------------

            t = threading.Thread(
                name=thread_name,
                target=_cft._worker,
                args=worker_args,
            )
            t.daemon = True
            t.start()
            self._threads.add(t)
            _cft._threads_queues[t] = self._work_queue


_bg_executor = _DaemonThreadPoolExecutor(max_workers=4, thread_name_prefix="jidra-bg")


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
    force: bool = False


# Folders commonly excluded across the language-specific filter modules
# (filters/filters.py, py_filters.py, go_filters.py, ts_filters.py) — used
# here only to pre-check the UI folder picker's default state. The actual
# exclusion during indexing still goes through each language's own filter
# plus gitignore; this list is a coarse approximation for UX purposes.
# Union of every language's real exclusion set, so this UI hint never drifts
# from what actually gets skipped during indexing/reindexing.
_DEFAULT_EXCLUDED_DIRS = (
    _JAVA_EXCLUDED_DIRS
    | _GO_EXCLUDED_DIRS
    | _PY_EXCLUDED_DIRS
    | _SCALA_EXCLUDED_DIRS
    | _TS_EXCLUDED_DIRS
)


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
    if req.force:
        from ...graph import graph_store as _gs_force

        _force_db = _gs_force.resolve_graph_db_path(out_dir)
        if _force_db.exists():
            _force_db.unlink()
    try:
        await loop.run_in_executor(
            _bg_executor,
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
                force=req.force,
            ),
        )
        yield _sse("status", {"msg": "Graph indexed and validated", "phase": "indexed"})

        try:
            from collections import Counter

            from ...graph import graph_store as _gs

            _db = _gs.resolve_graph_db_path(out_dir)
            _conn = _gs.connect(_db)
            try:
                _graph = _gs.load_graph(_conn, variant="main")
                _res = dict(Counter(c.resolution_status for c in _graph.callsites))
                _rescued = sum(
                    1
                    for c in _graph.callsites
                    if (c.resolution_reason or "").startswith("second-pass")
                )
                _unres_recv = [
                    c for c in _graph.callsites if c.resolution_status == "unresolved_receiver"
                ]
                _recv_no_dot = sum(1 for c in _unres_recv if c.receiver and "." not in c.receiver)
                _recv_dotted = sum(
                    1
                    for c in _unres_recv
                    if c.receiver and "." in c.receiver and not c.receiver.rstrip().endswith(")")
                )
                _recv_chain = sum(
                    1 for c in _unres_recv if c.receiver and c.receiver.rstrip().endswith(")")
                )
                _recv_none = sum(1 for c in _unres_recv if not c.receiver)
                _impl_suffix = _res.get("resolved_impl_suffix", 0)
                _cha = _res.get("resolved_cha", 0)
                _total_cs = len(_graph.callsites)
                _resolved = sum(v for k, v in _res.items() if not k.startswith("unresolved"))
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
            finally:
                _conn.close()
        except Exception:
            pass

        # Auto-embed with default model after indexing
        _emb_start = asyncio.get_event_loop().time()
        try:
            from ...graph import graph_store as _emb_gs
            from ...indexing.method_embeddings import (
                DEFAULT_EMBED_MODEL,
                build_method_embeddings,
            )

            yield _sse(
                "status",
                {
                    "msg": f"Embedding methods ({DEFAULT_EMBED_MODEL})…",
                    "phase": "embedding",
                },
            )
            _emb_db = _emb_gs.resolve_graph_db_path(out_dir)
            _emb_conn = _emb_gs.connect(_emb_db)
            try:
                _emb_stats = await loop.run_in_executor(
                    _bg_executor,
                    lambda: build_method_embeddings(_emb_conn, model_name=DEFAULT_EMBED_MODEL),
                )
            finally:
                _emb_conn.close()
            _emb_ms = int((asyncio.get_event_loop().time() - _emb_start) * 1000)
            yield _sse(
                "status",
                {
                    "msg": f"Embedded {_emb_stats.get('embedded', 0)} methods ({DEFAULT_EMBED_MODEL})",
                    "phase": "embedding",
                },
            )
            try:
                from ...llm.telemetry import update_last_index_elapsed

                update_last_index_elapsed(req.repo_path, _emb_ms)
            except Exception:
                pass
        except Exception as _emb_err:
            yield _sse("warn", {"msg": f"Embedding skipped: {_emb_err}"})

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
                    import time as _time

                    from ...llm.telemetry import record_doc_index_event as _rec_doc

                    for f in doc_files:
                        _doc_t0 = _time.time()
                        try:
                            n_chunks = await loop.run_in_executor(
                                _bg_executor,
                                lambda f=f: index_document(conn, str(f), class_names, method_names),
                            )
                            total_chunks += n_chunks
                            _doc_elapsed = int((_time.time() - _doc_t0) * 1000)
                            ext = f.suffix.lower().lstrip(".")
                            _rec_doc(
                                source_path=str(f),
                                source_type=ext,
                                chunks=n_chunks,
                                linked_classes=0,
                                file_size_bytes=f.stat().st_size,
                                elapsed_ms=_doc_elapsed,
                                status="ok",
                            )
                            yield _sse(
                                "status",
                                {
                                    "msg": f"  {f.name} → {n_chunks} chunks",
                                    "phase": "docs",
                                },
                            )
                        except Exception as doc_err:
                            _doc_elapsed = int((_time.time() - _doc_t0) * 1000)
                            _rec_doc(
                                source_path=str(f),
                                source_type=f.suffix.lower().lstrip("."),
                                chunks=0,
                                linked_classes=0,
                                file_size_bytes=f.stat().st_size if f.exists() else 0,
                                elapsed_ms=_doc_elapsed,
                                status="error",
                                error=str(doc_err),
                            )
                            yield _sse("warn", {"msg": f"  {f.name} skipped: {doc_err}"})

                    # Index Spring resources files (YAML/JSON/XML)
                    resource_files = discover_resource_files(
                        repo,
                        skip_folders=set(req.skip_folders) if req.skip_folders else None,
                    )
                    for rf in resource_files:
                        try:
                            n = index_resource_file(conn, str(rf), class_names, method_names)
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
                    yield _sse("warn", {"msg": f"Document indexing skipped: {docs_err}"})

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
                                "direct",
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
        subdirs = sorted((d for d in target.iterdir() if d.is_dir()), key=lambda d: d.name)
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
        main = conn.execute("SELECT COUNT(*) FROM methods WHERE variant='main'").fetchone()[0]
        classes = conn.execute("SELECT COUNT(*) FROM classes WHERE variant='main'").fetchone()[0]
        doc_count = 0
        with contextlib.suppress(sqlite3.OperationalError):
            doc_count = conn.execute(
                "SELECT COUNT(DISTINCT source_path) FROM doc_chunks"
            ).fetchone()[0]
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
    summary = incremental_reindex(codebase, graph_path, hint_changed_files=req.changed_files)
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
    pid_file = pid_path(str(out_dir))

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

        dt = datetime.fromtimestamp(manifest["last_indexed_at_ns"] / 1_000_000_000, tz=timezone.utc)
        last_indexed_at = dt.isoformat()
    else:
        # Fall back to graph.db mtime — set by initial full index pipeline
        # even when incremental reindexer manifest is absent.
        graph_db = out_dir / "graph.db"
        if graph_db.exists():
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(graph_db.stat().st_mtime, tz=timezone.utc)
            last_indexed_at = dt.isoformat()

    return {"running": running, "pid": pid, "last_indexed_at": last_indexed_at}


@router.post("/daemon/start")
async def daemon_start(req: DaemonRequest) -> dict:
    from ...engine.daemon import JidraDaemon, pid_path

    out_dir = _out_dir(req.repo_path, req.output_path)
    graph_db = out_dir / "graph.db"
    if not graph_db.exists():
        raise HTTPException(status_code=400, detail="graph.db not found — run index first")

    pid_file = pid_path(str(out_dir))
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
    pid_file = pid_path(str(out_dir))

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


@router.get("/daemon/startup-log")
async def daemon_startup_log(
    repo_path: str, output_path: str | None = None, lines: int = 50
) -> dict:
    from ...engine.daemon import _jidra_dir

    out_dir = _out_dir(repo_path, output_path)
    graph_db = out_dir / "graph.db"
    log_path = _jidra_dir(str(graph_db)) / "daemon.log"
    if not log_path.exists():
        return {"lines": []}
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return {"lines": text.splitlines()[-lines:]}
    except OSError:
        return {"lines": []}


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
            with contextlib.suppress(json.JSONDecodeError):
                entries.append(json.loads(line))
        entries.reverse()
        return {"entries": entries}
    except OSError:
        return {"entries": []}


@router.get("/daemons")
async def list_daemons() -> dict:
    """Scan the runtime dir for all running JIDRA daemon sockets and return their status."""
    import socket as _socket

    from ...engine.daemon import _runtime_dir

    rt = _runtime_dir()
    socks = list(rt.glob("*.sock"))
    daemons = []
    for sock_path in socks:
        entry: dict = {"sock": str(sock_path), "alive": False}
        try:
            with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(str(sock_path))
                # send status request
                s.sendall(b'{"id":1,"method":"jidra/status"}\n')
                data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in data:
                        break
            raw = data.split(b"\n")[0]
            if not raw:
                raise OSError("empty response from daemon")
            resp = json.loads(raw)
            result = resp.get("result", {})
            entry = {
                "sock": str(sock_path),
                "alive": True,
                "pid": result.get("pid"),
                "graph_path": result.get("graph_path"),
                "codebase_path": result.get("codebase_path"),
                "watcher_running": result.get("watcher_running", False),
                "active_connections": result.get("active_connections", 0),
                "uptime_s": result.get("uptime_s", 0),
            }
        except OSError:
            entry["alive"] = False
        daemons.append(entry)
    return {"daemons": [d for d in daemons if d["alive"]]}


@router.get("/daemon/stale")
async def daemon_stale(repo_path: str, output_path: str | None = None, full: bool = False) -> dict:
    from ...engine.reindexer import check_staleness, quick_stale_check

    out_dir = _out_dir(repo_path, output_path)
    graph_db = out_dir / "graph.db"
    if not graph_db.exists():
        return {"stale": False, "reason": "not indexed"}

    if not full:
        # O(1) spot-check — safe to poll every 5s
        stale = quick_stale_check(out_dir)
        return {
            "stale": stale,
            "hint": "Call with ?full=true for changed file details.",
        }

    codebase = Path(repo_path).resolve()
    return check_staleness(codebase, out_dir)
