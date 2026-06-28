"""JIDRA proxy — a thin client that forwards MCP tool calls to the shared
daemon over a Unix domain socket, spawning the daemon on demand (Phase 5).

The proxy itself holds no graph. It runs inside the FastMCP server built by
`mcp_server.build_mcp(..., invoke=proxy.call)`: FastMCP handles the MCP protocol
on stdio, and each tool body calls `proxy.call(name, params)`, which round-trips
to the daemon and returns the result dict.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time

from ..engine import daemon as _daemon


class JidraProxy:
    STARTUP_TIMEOUT = 6.0  # seconds to wait for the daemon socket to appear
    POLL_INTERVAL = 0.2

    def __init__(self, graph_path: str | None, codebase_path: str | None):
        self.graph_path = graph_path
        self.codebase_path = codebase_path
        self.sock_path = _daemon.socket_path(graph_path)
        self._id = 0
        self._id_lock = threading.Lock()
        self._send_lock = threading.Lock()

    def available(self) -> bool:
        """Unix-domain sockets are required. Returns False on platforms (e.g.
        Windows) where they're unavailable, so the caller can fall back to
        direct mode rather than failing the MCP handshake."""
        return hasattr(socket, "AF_UNIX")

    # ── connection management ────────────────────────────────────────────────

    def _connect(self) -> socket.socket | None:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(30.0)
            s.connect(str(self.sock_path))
            return s
        except OSError:
            return None

    def _ensure_daemon(self) -> socket.socket:
        s = self._connect()
        if s is not None:
            return s
        self._spawn_daemon()
        deadline = time.time() + self.STARTUP_TIMEOUT
        while time.time() < deadline:
            s = self._connect()
            if s is not None:
                return s
            time.sleep(self.POLL_INTERVAL)
        raise RuntimeError(
            f"JIDRA daemon did not come up within {self.STARTUP_TIMEOUT}s "
            f"(socket {self.sock_path})"
        )

    def _spawn_daemon(self) -> None:
        cmd = [sys.executable, "-m", "jidra.daemon"]
        if self.graph_path:
            cmd += ["--graph", self.graph_path]
        if self.codebase_path:
            cmd += ["--codebase", self.codebase_path]
        # start_new_session detaches the daemon from the proxy's process group so
        # it survives this proxy exiting.
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # ── RPC ──────────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def _rpc(self, payload: dict) -> dict:
        # One request/response per short-lived connection keeps framing trivial
        # and avoids interleaving across FastMCP's concurrent tool calls.
        with self._send_lock:
            s = self._ensure_daemon()
            try:
                s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            finally:
                s.close()
        if not buf:
            return {"error": "empty_daemon_response"}
        return json.loads(buf.decode("utf-8"))

    def call(self, name: str, params: dict) -> dict:
        """Forward a tool call to the daemon; shape matches local dispatch."""
        resp = self._rpc(
            {
                "id": self._next_id(),
                "method": "tools/call",
                "tool": name,
                "params": params,
            }
        )
        if "error" in resp and "result" not in resp:
            return {"error": resp["error"]}
        return resp.get("result", {})

    def ping(self) -> bool:
        try:
            return (
                self._rpc({"id": self._next_id(), "method": "ping"}).get("result")
                == "pong"
            )
        except (OSError, RuntimeError, ValueError):
            return False

    def reload(self) -> dict:
        return self._rpc({"id": self._next_id(), "method": "jidra/reload"}).get(
            "result", {}
        )
