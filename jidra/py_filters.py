from __future__ import annotations

from pathlib import Path

from .file_filters import apply_filters

EXCLUDED_DIRS = {
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
    ".eggs",
    ".pytest_cache",
    ".mypy_cache",
    ".coverage",
    "htmlcov",
    ".venv-build",
    "site-packages",
    ".git",
    "node_modules",
}


def should_include_dir(path: Path) -> bool:
    names = {part.lower() for part in path.parts}
    return not bool(names & EXCLUDED_DIRS)


def iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if should_include_dir(path.parent):
            files.append(path)
    return sorted(apply_filters(files, root))
