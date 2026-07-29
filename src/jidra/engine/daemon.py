"""JIDRA daemon — a detached, single-instance process that holds the code graph
in memory and serves N proxy clients over a Unix domain socket (Phase 5).

Protocol: newline-delimited JSON request/response. Each request is one JSON
object with an "id" and a "method":

    {"id": 1, "method": "ping"}                      -> {"id": 1, "result": "pong"}
    {"id": 2, "method": "tools/list"}                -> {"id": 2, "result": [names]}
    {"id": 3, "method": "tools/call",
     "tool": "jidra_search", "params": {...}}        -> {"id": 3, "result": {...}}
    {"id": 4, "method": "jidra/reload"}              -> {"id": 4, "result": {...}}

The MCP protocol itself lives in the proxy's FastMCP server; the daemon only
speaks this small RPC, which keeps it simple and unit-testable.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from ..server import mcp_server


def _graph_key(graph_path: str | None) -> str:
    """Stable short hash of the resolved graph path — identifies one daemon."""
    base = str(Path(graph_path).resolve()) if graph_path else os.getcwd()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _jidra_dir(graph_path: str | None) -> Path:
    """Project `.jidra/` directory that holds the lock/pid files.

    Anchored on the graph's parent so every proxy/daemon for the same graph
    agrees, regardless of each client's CWD.
    """
    if graph_path:
        base = Path(graph_path).resolve()
        if not base.is_dir():
            base = base.parent
        # If handed the .jidra dir itself (or a file inside it), step up to repo root
        # so we never nest .jidra/.jidra/.
        if base.name == ".jidra":
            base = base.parent
    else:
        base = Path.cwd()
    d = base / ".jidra"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _runtime_dir() -> Path:
    """Short per-user directory for the Unix socket. AF_UNIX paths are capped
    (~104 chars on macOS), so the socket can't live under a deep project path —
    it goes in $XDG_RUNTIME_DIR or /tmp, namespaced per user, mode 0700."""
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    uid = getattr(os, "getuid", lambda: "u")()
    d = Path(base) / f"jidra-{uid}"
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d


def socket_path(graph_path: str | None) -> Path:
    return _runtime_dir() / f"{_graph_key(graph_path)}.sock"


def pid_path(graph_path: str | None) -> Path:
    return _jidra_dir(graph_path) / "jidra.pid"


def lock_path(graph_path: str | None) -> Path:
    return _jidra_dir(graph_path) / "jidra.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class JidraDaemon:
    POLL_INTERVAL = 5.0  # seconds between watchdog checks
    IDLE_TIMEOUT = 60.0  # shut down after this long with zero connections

    def __init__(self, graph_path: str | None, codebase_path: str | None):
        self.graph_path = graph_path
        self.codebase_path = codebase_path
        self.sock_path = socket_path(graph_path)
        self.pid_file = pid_path(graph_path)
        self.lock_file = lock_path(graph_path)
        self._lock_fd: int | None = None
        self._reload_lock = threading.Lock()
        self._active = 0
        self._active_lock = threading.Lock()
        self._last_active = time.time()
        self._start_time = time.time()
        self._stop = threading.Event()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """Exclusive file lock so only one daemon runs per graph."""
        import fcntl

        self._lock_fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_fd)
            self._lock_fd = None
            return False
        return True

    def start(self, *, daemonize: bool = True) -> None:
        """Double-fork to fully detach, then serve. If another daemon already
        holds the lock, exit quietly."""
        if daemonize and os.fork() > 0:
            return  # original process returns to caller
        if daemonize:
            os.setsid()
            if os.fork() > 0:
                os._exit(0)  # first child exits; grandchild is the daemon
            # Redirect stdin to /dev/null; stdout+stderr to daemon.log so
            # startup crashes are visible instead of silently lost.
            devnull = os.open(os.devnull, os.O_RDONLY)
            with contextlib.suppress(OSError):
                os.dup2(devnull, 0)
            log_path = _jidra_dir(self.graph_path) / "daemon.log"
            log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            for fd in (1, 2):
                with contextlib.suppress(OSError):
                    os.dup2(log_fd, fd)
            os.close(log_fd)

        if not self._acquire_lock():
            os._exit(0) if daemonize else None
            return

        self.pid_file.write_text(str(os.getpid()))
        # signal.signal only works from the main thread; in tests the daemon may
        # run in a background thread, so don't make signals a hard requirement.
        try:
            signal.signal(signal.SIGHUP, lambda *_: self.reload())
            signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        except ValueError:
            pass

        # Warm the engine once so the first client doesn't pay the load cost.
        with contextlib.suppress(Exception):
            mcp_server.get_engine(self.graph_path or "")

        # Integrity check: if DB is corrupt, force a full reindex before serving.
        if self.graph_path:
            try:
                from ..graph import graph_store as _gs

                _db = _gs.resolve_graph_db_path(Path(self.graph_path))
                if _db.exists():
                    _c = _gs.connect(_db)
                    _ok = _c.execute("PRAGMA integrity_check(1)").fetchone()
                    _c.close()
                    if _ok and _ok[0] != "ok":
                        import logging

                        logging.getLogger(__name__).warning(
                            "graph.db integrity check failed (%s) — triggering full reindex",
                            _ok[0],
                        )
                        self.reload()
            except Exception:
                pass

        # Reconcile any changes made while the daemon was down (e.g. a `git
        # pull` with no editor open) before serving the first client. Only
        # when codebase_path is known — reload() guesses a path otherwise,
        # which risks reindexing the wrong tree and wiping the graph.
        if self.codebase_path:
            self.reload()

        threading.Thread(target=self._watchdog, daemon=True).start()
        try:
            self.serve_forever()
        finally:
            self._cleanup()
            if daemonize:
                os._exit(0)

    def stop(self) -> None:
        """Signal the serve loop to exit (used by tests / SIGTERM)."""
        self._stop.set()

    def _cleanup(self) -> None:
        for path in (self.sock_path, self.pid_file):
            with contextlib.suppress(OSError):
                path.unlink()

    def _watchdog(self) -> None:
        """Idle shutdown: if no client has been connected for IDLE_TIMEOUT,
        stop. Prevents orphaned daemons after all editors close."""
        while not self._stop.is_set():
            time.sleep(self.POLL_INTERVAL)
            idle = self._active == 0 and (time.time() - self._last_active > self.IDLE_TIMEOUT)
            if idle:
                self._stop.set()
                try:  # nudge the accept() loop awake
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.connect(str(self.sock_path))
                except OSError:
                    pass
                return

    # ── serving ──────────────────────────────────────────────────────────────

    def serve_forever(self) -> None:
        if self.sock_path.exists():
            self.sock_path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.sock_path))
        server.listen(16)
        server.settimeout(1.0)
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
        server.close()

    def _handle_client(self, conn: socket.socket) -> None:
        with self._active_lock:
            self._active += 1
            self._last_active = time.time()
        try:
            conn_file = conn.makefile("rb")
            for raw in conn_file:
                line = raw.strip()
                if not line:
                    continue
                resp = self._handle_request(line)
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()
            with self._active_lock:
                self._active -= 1
                self._last_active = time.time()

    def _handle_request(self, line: bytes) -> dict:
        try:
            req = json.loads(line)
        except ValueError:
            return {"id": None, "error": "invalid_json"}
        rid = req.get("id")
        method = req.get("method")
        try:
            if method == "ping":
                return {"id": rid, "result": "pong"}
            if method == "jidra/status":
                with self._active_lock:
                    active = self._active
                return {
                    "id": rid,
                    "result": {
                        "pid": os.getpid(),
                        "graph_path": self.graph_path,
                        "codebase_path": self.codebase_path,
                        "watcher_running": False,
                        "active_connections": active,
                        "uptime_s": int(time.time() - self._start_time),
                    },
                }
            if method == "tools/list":
                return {"id": rid, "result": mcp_server.visible_tool_names()}
            if method == "jidra/reload":
                return {"id": rid, "result": self.reload()}
            if method == "tools/call":
                result = mcp_server.dispatch_tool(
                    req["tool"],
                    req.get("params") or {},
                    default_graph_path=self.graph_path,
                    codebase_path=self.codebase_path,
                )
                return {"id": rid, "result": result}
            return {"id": rid, "error": f"unknown_method:{method}"}
        except KeyError as exc:
            return {"id": rid, "error": f"bad_request:{exc}"}
        except Exception as exc:  # never let one bad call kill the connection
            return {"id": rid, "error": f"tool_error:{type(exc).__name__}:{exc}"}

    def reload(self) -> dict:
        """Re-run incremental reindex. The engine cache (`get_engine`) detects
        the changed graph.db via its mtime fingerprint and reloads on the next
        tool call, so the swap is automatic. Writes are serialized here; reads
        remain lock-free."""
        with self._reload_lock:
            try:
                from .reindexer import incremental_reindex

                graph = self.graph_path or ""
                codebase = self.codebase_path or str(Path(graph).parent.parent)
                summary = incremental_reindex(Path(codebase), Path(graph))
                result = {"reloaded": True, "summary": summary}
            except Exception as exc:
                result = {"reloaded": False, "error": str(exc)}

            self._append_reindex_log(result)
            return result

    def _append_reindex_log(self, result: dict) -> None:
        if not self.graph_path:
            return
        try:
            graph_dir = Path(self.graph_path)
            graph_dir = graph_dir if graph_dir.is_dir() else graph_dir.parent
            log_path = graph_dir / "reindex.log"
            entry = {
                "ts": time.time(),
                "reloaded": result.get("reloaded"),
                "summary": result.get("summary"),
                "error": result.get("error"),
            }
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - CLI entry
    import argparse

    parser = argparse.ArgumentParser(description="Run the JIDRA daemon")
    parser.add_argument("--graph", default=None)
    parser.add_argument("--codebase", default=None)
    parser.add_argument(
        "--foreground", action="store_true", help="Do not daemonize (for debugging)."
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    JidraDaemon(args.graph, args.codebase).start(daemonize=not args.foreground)


if __name__ == "__main__":  # pragma: no cover
    main()
