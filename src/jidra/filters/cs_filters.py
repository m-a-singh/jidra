from __future__ import annotations

from pathlib import Path

from .file_filters import COMMON_EXCLUDED_DIRS, apply_filters

EXCLUDED_DIRS = COMMON_EXCLUDED_DIRS | {
    "bin",
    "obj",
    ".vs",
    "packages",
    "TestResults",
}


def should_include_dir(path: Path) -> bool:
    names = {part.lower() for part in path.parts}
    return not bool(names & EXCLUDED_DIRS)


def iter_cs_files(root: Path, skip_folders: set[str] | None = None) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.cs"):
        if should_include_dir(path.parent):
            files.append(path)
    return sorted(apply_filters(files, root, skip_folders=skip_folders))
