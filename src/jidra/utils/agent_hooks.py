"""Install / remove Claude Code PostToolUse hook for jidra auto-reindex."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HOOK_COMMAND = "jidra hook post-tool-use"
_HOOK_MATCHER = "Write|Edit|MultiEdit"
_SETTINGS_FILE = ".claude/settings.json"

# Matches watcher.py's own debounce window, so a burst of rapid edits (an
# agent touching several files in a row) collapses into one reindex process
# instead of spawning one subprocess per edit.
_DEBOUNCE_MS = 500
_PENDING_DIRNAME = "reindex.pending"
_DEBOUNCE_MARKER = "reindex.debounce.lock"


def install_agent_hooks(project_root: Path) -> bool:
    """Write PostToolUse hook into <project_root>/.claude/settings.json.

    Returns True if written/updated, False if already present.
    """
    settings_path = project_root / _SETTINGS_FILE
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        data: dict = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    post: list = data.setdefault("hooks", {}).setdefault("PostToolUse", [])

    for entry in post:
        for h in entry.get("hooks", []):
            if h.get("command") == _HOOK_COMMAND:
                return False  # already installed

    post.append(
        {
            "matcher": _HOOK_MATCHER,
            "hooks": [{"type": "command", "command": _HOOK_COMMAND}],
        }
    )
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def uninstall_agent_hooks(project_root: Path) -> bool:
    """Remove jidra PostToolUse hook entry from settings.json.

    Returns True if anything was removed.
    """
    settings_path = project_root / _SETTINGS_FILE
    if not settings_path.exists():
        return False

    try:
        data: dict = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    post: list = data.get("hooks", {}).get("PostToolUse", [])
    new_post = []
    changed = False
    for entry in post:
        filtered = [h for h in entry.get("hooks", []) if h.get("command") != _HOOK_COMMAND]
        if filtered:
            entry = dict(entry)
            entry["hooks"] = filtered
            new_post.append(entry)
        else:
            changed = True

    if not changed:
        return False

    data["hooks"]["PostToolUse"] = new_post
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def handle_post_tool_use(graph_path: str, codebase: str) -> None:
    """Called by the Claude Code PostToolUse hook.

    Reads hook payload from stdin (newline-delimited JSON from Claude Code),
    extracts the edited file path, and queues it for a debounced reindex.

    A burst of rapid edits (an agent touching several files in a row within
    the debounce window) collapses into ONE background reindex process
    covering the whole batch, instead of spawning one subprocess per edit:
    every call records its file in a pending-set directory; only the call
    that wins the atomic marker-file race actually spawns the (sleep, then
    drain-and-reindex) subprocess — everyone else just adds to the pending
    set and returns, trusting the winner to pick their file up.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, OSError):
        return

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return

    tool_input: dict = payload.get("tool_input") or {}
    file_path: str | None = tool_input.get("file_path") or tool_input.get("path")
    if not file_path:
        return

    try:
        jidra_dir = Path(codebase) / ".jidra"
        pending_dir = jidra_dir / _PENDING_DIRNAME
        pending_dir.mkdir(parents=True, exist_ok=True)

        # Record this file in the pending set (filename = hash of the path,
        # so concurrent writers never collide; content = the real path).
        name = hashlib.sha1(file_path.encode("utf-8")).hexdigest()
        (pending_dir / name).write_text(file_path, encoding="utf-8")

        # Atomically claim the debounce window: only the first hook call in a
        # burst creates this marker successfully; later calls in the same
        # window see it already exists and just return.
        marker_path = jidra_dir / _DEBOUNCE_MARKER
        try:
            fd = os.open(str(marker_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            return  # another hook call already owns this debounce window

        log_path = jidra_dir / "reindex.log"
        with open(log_path, "a") as log_file:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "jidra.cli",
                    "hook",
                    "debounced-reindex",
                    "--graph",
                    graph_path,
                    "--codebase",
                    codebase,
                ],
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception:
        pass


def run_debounced_reindex(graph_path: str, codebase: str) -> None:
    """Sleep out the debounce window, then reindex every file queued by
    handle_post_tool_use during that window in a single incremental_reindex
    call. Runs in the detached subprocess spawned by the hook winner."""
    time.sleep(_DEBOUNCE_MS / 1000)

    jidra_dir = Path(codebase) / ".jidra"
    pending_dir = jidra_dir / _PENDING_DIRNAME
    marker_path = jidra_dir / _DEBOUNCE_MARKER

    changed_files: list[str] = []
    try:
        if pending_dir.exists():
            for entry in pending_dir.iterdir():
                try:
                    changed_files.append(entry.read_text(encoding="utf-8").strip())
                except OSError:
                    continue
                finally:
                    entry.unlink(missing_ok=True)
    except OSError:
        pass

    marker_path.unlink(missing_ok=True)

    if not changed_files:
        return

    try:
        from ..engine.reindexer import incremental_reindex

        incremental_reindex(
            Path(codebase),
            Path(graph_path),
            hint_changed_files=changed_files,
        )
    except Exception:
        pass
