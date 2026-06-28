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

import hashlib
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from . import mcp_server


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
        base = Path(graph_path)
        base = base if base.is_dir() else base.parent
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
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
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
        self._stop = threading.Event()
        self._watcher = None

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
            # Detach std streams so the daemon outlives the launching shell.
            devnull = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                try:
                    os.dup2(devnull, fd)
                except OSError:
                    pass

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
        try:
            mcp_server.get_engine(self.graph_path or "")
        except Exception:
            pass

        # Reconcile any changes made while the daemon was down (e.g. a `git
        # pull` with no editor open) before serving the first client. Only
        # when codebase_path is known — reload() guesses a path otherwise,
        # which risks reindexing the wrong tree and wiping the graph.
        if self.codebase_path:
            self.reload()

        self._start_watcher()
        threading.Thread(target=self._watchdog, daemon=True).start()
        try:
            self.serve_forever()
        finally:
            self._cleanup()
            if daemonize:
                os._exit(0)

    def _start_watcher(self) -> None:
        """Best-effort: run a filesystem watcher (Phase 6) inside the daemon so
        edits trigger a debounced incremental reindex and hot-swap. Silently
        no-ops if watchdog is unavailable or the graph/codebase is unknown."""
        if not self.graph_path:
            return
        try:
            from .watcher import JidraWatcher

            codebase = self.codebase_path or str(Path(self.graph_path).parent.parent)
            self._watcher = JidraWatcher(Path(codebase), Path(self.graph_path))
            self._watcher.start()
        except Exception:
            self._watcher = None

    def stop(self) -> None:
        """Signal the serve loop to exit (used by tests / SIGTERM)."""
        self._stop.set()
        watcher = getattr(self, "_watcher", None)
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass

    def _cleanup(self) -> None:
        for path in (self.sock_path, self.pid_file):
            try:
                path.unlink()
            except OSError:
                pass

    def _watchdog(self) -> None:
        """Idle shutdown: if no client has been connected for IDLE_TIMEOUT,
        stop. Prevents orphaned daemons after all editors close."""
        while not self._stop.is_set():
            time.sleep(self.POLL_INTERVAL)
            with self._active_lock:
                idle = self._active == 0 and (
                    time.time() - self._last_active > self.IDLE_TIMEOUT
                )
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
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client, args=(conn,), daemon=True
            ).start()
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
            try:
                conn.close()
            except OSError:
                pass
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
                return {"reloaded": True, "summary": summary}
            except Exception as exc:
                return {"reloaded": False, "error": str(exc)}


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
