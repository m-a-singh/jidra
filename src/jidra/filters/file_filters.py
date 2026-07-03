"""Cross-language file filtering shared by all `iter_*_files` helpers.

Two checks that apply regardless of language: skip files above a size cap
(minified bundles, vendored dumps, etc. aren't source) and skip anything
`git` would ignore, so generated/ignored directories that aren't in any
language's hardcoded EXCLUDED_DIRS list don't get indexed or watched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_FILE_SIZE_BYTES = 1_000_000


def is_too_large(path: Path) -> bool:
    try:
        return path.stat().st_size > MAX_FILE_SIZE_BYTES
    except OSError:
        return False


def gitignored_paths(root: Path, paths: list[Path]) -> set[Path]:
    """Subset of `paths` that `git` would ignore under `root`.

    Best-effort: returns an empty set if `root` isn't a git repo or `git`
    isn't on PATH, so callers degrade to "include everything" instead of
    failing indexing outright.
    """
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=root,
            input="\0".join(str(p) for p in paths),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode not in (0, 1):
        return set()
    out = proc.stdout.strip("\0")
    if not out:
        return set()
    return {Path(p) for p in out.split("\0") if p}


def excluded_by_skip_folders(
    files: list[Path], root: Path, skip_folders: set[str] | None
) -> set[Path]:
    """Subset of `files` that fall under a user-supplied skip-folder prefix.

    `skip_folders` entries are relative POSIX paths from `root` (e.g.
    `"ui/src/components/ui"`), matched as path prefixes — not bare folder
    names — so excluding `legacy` doesn't also exclude an unrelated
    `vendor/legacy` elsewhere in the tree.
    """
    if not skip_folders:
        return set()
    prefixes = tuple(s.strip("/") + "/" for s in skip_folders if s.strip("/"))
    if not prefixes:
        return set()
    excluded = set()
    for f in files:
        try:
            rel = f.resolve().relative_to(root.resolve()).as_posix() + "/"
        except ValueError:
            continue
        if rel.startswith(prefixes):
            excluded.add(f)
    return excluded


def apply_filters(
    files: list[Path], root: Path, skip_folders: set[str] | None = None
) -> list[Path]:
    """Drop oversized files, git-ignored ones, and user-supplied skip-folders."""
    kept = [f for f in files if not is_too_large(f)]
    ignored = gitignored_paths(root, kept)
    kept = [f for f in kept if f not in ignored]
    skipped = excluded_by_skip_folders(kept, root, skip_folders)
    return [f for f in kept if f not in skipped]
