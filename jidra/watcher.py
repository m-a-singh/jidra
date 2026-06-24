"""Filesystem watcher (Phase 6).

Watches the codebase for source-file changes and triggers a debounced
incremental reindex, so the graph stays fresh without manual `jidra reindex`.
Designed to run as a background thread inside the daemon (Phase 5), but usable
standalone too.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

SOURCE_EXTENSIONS = {".java", ".py", ".ts", ".tsx", ".scala", ".go"}
IGNORE_DIRS = {
    "node_modules",
    "build",
    "dist",
    ".git",
    "__pycache__",
    ".jidra",
    ".venv",
}


def is_wsl2_mounted(codebase_root: Path) -> bool:
    """WSL2 has no inotify on Windows-mounted drives (/mnt/...); callers should
    fall back to git-hook-only sync there."""
    try:
        if not Path("/proc/version").exists():
            return False
        version = Path("/proc/version").read_text(errors="ignore").lower()
        if "microsoft" not in version:
            return False
        return str(codebase_root.resolve()).startswith("/mnt/")
    except OSError:
        return False


class JidraWatcher:
    DEBOUNCE_MS = 500  # coalesce editor autosave bursts
    BATCH_SIZE = 50  # cap files handed to one reindex

    def __init__(
        self,
        codebase_root: Path,
        graph_path: Path,
        on_indexed: Callable[[dict], None] | None = None,
    ):
        self.codebase_root = Path(codebase_root)
        self.graph_path = Path(graph_path)
        self.on_indexed = on_indexed
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the observer in a background thread. Returns False if watchdog
        isn't installed or this is an unsupported (WSL2/mnt) environment, so the
        caller can rely on git hooks instead."""
        if is_wsl2_mounted(self.codebase_root):
            return False
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return False

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                watcher._on_change(getattr(event, "dest_path", "") or event.src_path)

        self._observer = Observer()
        self._observer.schedule(_Handler(), str(self.codebase_root), recursive=True)
        self._observer.start()
        return True

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # ── change handling ──────────────────────────────────────────────────────

    def _is_relevant(self, path: str) -> bool:
        p = Path(path)
        if p.suffix not in SOURCE_EXTENSIONS:
            return False
        parts = set(p.parts)
        return not (parts & IGNORE_DIRS)

    def _on_change(self, path: str) -> None:
        if not path or not self._is_relevant(path):
            return
        with self._lock:
            self._pending.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.DEBOUNCE_MS / 1000.0, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            batch = list(self._pending)[: self.BATCH_SIZE]
            self._pending = set(list(self._pending)[self.BATCH_SIZE :])
            self._timer = None
        if not batch:
            return
        try:
            from .reindexer import incremental_reindex

            summary = incremental_reindex(
                self.codebase_root, self.graph_path, hint_changed_files=batch
            )
            if self.on_indexed is not None:
                self.on_indexed(summary)
        except Exception:
            # A failed reindex must not kill the watcher thread; the next change
            # (or a manual reindex) will retry.
            pass
        # If more changes accumulated past the batch cap, schedule another flush.
        with self._lock:
            if self._pending and self._timer is None:
                self._timer = threading.Timer(self.DEBOUNCE_MS / 1000.0, self._flush)
                self._timer.daemon = True
                self._timer.start()


def watch_forever(codebase_root: Path, graph_path: Path) -> None:  # pragma: no cover
    """Blocking standalone watch loop (Ctrl-C to stop)."""
    w = JidraWatcher(codebase_root, graph_path)
    if not w.start():
        raise SystemExit(
            "Watcher unavailable (install `watchdog`, or use git hooks on WSL2/mnt)."
        )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        w.stop()
