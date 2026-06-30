from __future__ import annotations

from pathlib import Path

from .file_filters import apply_filters

EXCLUDED_DIRS = {
    "vendor",
    "node_modules",
    ".git",
    "dist",
    "build",
    "bin",
    ".cache",
}


def should_include_dir(path: Path) -> bool:
    names = {part.lower() for part in path.parts}
    return not bool(names & EXCLUDED_DIRS)


def iter_go_files(
    root: Path, skip_folders: set[str] | None = None
) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.go"):
        if should_include_dir(path.parent):
            files.append(path)
    return sorted(apply_filters(files, root, skip_folders=skip_folders))
