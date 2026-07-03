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
    "node_modules",
}


def should_include_dir(path: Path) -> bool:
    names = {part.lower() for part in path.parts}
    return not bool(names & EXCLUDED_DIRS)


def iter_java_files(
    root: Path,
    extra_roots: list[Path] | None = None,
    skip_folders: set[str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.java"):
        if should_include_dir(path.parent):
            files.append(path)

    # Extra roots (e.g. smithy4j generated sources in build/) bypass both
    # EXCLUDED_DIRS and the gitignore filter since build/ is typically gitignored.
    extra_files: list[Path] = []
    for extra in extra_roots or []:
        for path in extra.rglob("*.java"):
            extra_files.append(path)

    return sorted(apply_filters(files, root, skip_folders=skip_folders) + extra_files)
