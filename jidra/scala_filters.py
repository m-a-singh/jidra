from __future__ import annotations

from pathlib import Path

EXCLUDED_DIRS = {
    # Build output
    "target",
    ".bloop",
    ".metals",
    ".scala-build",
    # VCS
    ".git",
    # IDE
    ".idea",
    ".vscode",
    # Dependencies / caches
    "project/target",
    "node_modules",
}


def _should_include_dir(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return not bool(parts & EXCLUDED_DIRS)


def iter_scala_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.scala"):
        if _should_include_dir(path.parent):
            files.append(path)
    return sorted(files)
