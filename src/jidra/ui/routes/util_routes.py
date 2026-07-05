from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()


def resolve_out_dir(repo_path: str, output_path: str | None = None) -> Path:
    """Prefer <repo>/.jidra/ (jidra init/ui model), fall back to legacy output/database dir."""
    if output_path:
        return Path(output_path)
    jidra_dir = Path(repo_path) / ".jidra"
    if jidra_dir.exists():
        return jidra_dir
    from ...cli import _repo_output_dir
    return _repo_output_dir(Path(repo_path))


def _pick_folder_macos() -> str | None:
    script = (
        'tell application "System Events"\n'
        "  activate\n"
        "end tell\n"
        'set chosen to choose folder with prompt "Select repository folder"\n'
        "return POSIX path of chosen"
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip().rstrip("/")


def _pick_folder_tkinter() -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Select repository folder")
        root.destroy()
        return path or None
    except Exception:
        return None


@router.post("/pick-folder")
async def pick_folder() -> dict:
    try:
        if sys.platform == "darwin":
            path = _pick_folder_macos()
        else:
            path = _pick_folder_tkinter()
        if not path:
            return {"cancelled": True}
        return {"path": path, "cancelled": False}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
