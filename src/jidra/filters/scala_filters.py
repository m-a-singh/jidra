from __future__ import annotations

from pathlib import Path

from .file_filters import COMMON_EXCLUDED_DIRS, apply_filters

EXCLUDED_DIRS = COMMON_EXCLUDED_DIRS | {
    # Build output
    ".bloop",
    ".metals",
    ".scala-build",
    # IDE
    ".idea",
    ".vscode",
    # Dependencies / caches
    "project/target",
}


def _should_include_dir(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return not bool(parts & EXCLUDED_DIRS)


def iter_scala_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.scala"):
        if _should_include_dir(path.parent):
            files.append(path)
    return sorted(apply_filters(files, root))
