from __future__ import annotations

from pathlib import Path

from .file_filters import apply_filters

EXCLUDED_DIRS = {
    "build",
    "target",
    ".gradle",
    "generated",
    "generated-sources",
    "generated_sources",
}


def should_include_dir(path: Path) -> bool:
    names = {part.lower() for part in path.parts}
    return not bool(names & EXCLUDED_DIRS)


def iter_java_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.java"):
        if should_include_dir(path.parent):
            files.append(path)
    return sorted(apply_filters(files, root))
