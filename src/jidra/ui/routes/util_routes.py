from __future__ import annotations

import subprocess
import sys

from fastapi import APIRouter, HTTPException

router = APIRouter()


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
