"""Git hook installer (Phase 6).

Installs post-commit / post-merge / post-checkout hooks that incrementally
reindex the JIDRA graph after the working tree changes. Hook bodies are wrapped
in delimited `# BEGIN JIDRA` / `# END JIDRA` blocks so they compose with other
hook managers (Husky, lefthook, pre-commit) and can be cleanly removed.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

BEGIN = "# BEGIN JIDRA"
END = "# END JIDRA"

_EXT_RE = r"\.(java|py|ts|tsx|scala|go)$"

HOOK_NAMES = ("post-commit", "post-merge", "post-checkout")


def _reindex_invocation(graph_path: Path) -> str:
    """The command hooks run. Uses the current interpreter so it works inside a
    venv without the user activating it."""
    python = sys.executable or "python3"
    return f'"{python}" -m jidra.cli reindex --graph "{graph_path}"'


def _hook_body(name: str, graph_path: Path) -> str:
    reindex = _reindex_invocation(graph_path)
    if name == "post-commit":
        diff = "git diff-tree --no-commit-id -r --name-only HEAD"
    elif name == "post-merge":
        diff = "git diff-tree --no-commit-id -r --name-only ORIG_HEAD HEAD"
    else:  # post-checkout: $3 == 1 means a branch checkout (not a file checkout)
        diff = 'git diff --name-only "HEAD@{1}" HEAD'

    guard = ""
    if name == "post-checkout":
        # post-checkout receives prev-HEAD new-HEAD branch-flag; only act on
        # branch switches, and run in the background so checkout stays snappy.
        guard = '[ "$3" = "1" ] || exit 0\n'

    return (
        f"{BEGIN}\n"
        f"{guard}"
        f"changed=$({diff} 2>/dev/null | grep -E '{_EXT_RE}')\n"
        f'[ -z "$changed" ] && exit 0\n'
        f"{reindex} --changed-files $changed >/dev/null 2>&1 &\n"
        f"{END}\n"
    )


def _git_hooks_dir(repo: Path) -> Path:
    # Honor core.hooksPath if configured; fall back to .git/hooks.
    hooks_path = None
    try:
        import subprocess

        out = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            hooks_path = Path(out.stdout.strip())
    except Exception:
        pass
    if hooks_path is None:
        hooks_path = repo / ".git" / "hooks"
    if not hooks_path.is_absolute():
        hooks_path = repo / hooks_path
    return hooks_path


def _strip_block(text: str) -> str:
    """Remove a single BEGIN..END JIDRA block (and a trailing blank line)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == BEGIN:
            skipping = True
            continue
        if line.strip() == END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def install_hooks(repo: Path, graph_path: Path) -> list[str]:
    """Write/refresh the JIDRA block in each hook, preserving other content.
    Returns the hook names written."""
    hooks_dir = _git_hooks_dir(repo)
    if not (repo / ".git").exists() and not hooks_dir.exists():
        raise SystemExit(f"Not a git repository (no .git in {repo}).")
    hooks_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for name in HOOK_NAMES:
        path = hooks_dir / name
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not existing:
            existing = "#!/bin/sh\n"
        else:
            existing = _strip_block(existing)  # refresh any prior JIDRA block
        if not existing.endswith("\n"):
            existing += "\n"
        body = existing + _hook_body(name, graph_path)
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(name)
    return written


def uninstall_hooks(repo: Path) -> list[str]:
    """Strip the JIDRA block from each hook, leaving everything else intact.
    Removes a hook file entirely if nothing but the shebang remains."""
    hooks_dir = _git_hooks_dir(repo)
    removed: list[str] = []
    for name in HOOK_NAMES:
        path = hooks_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if BEGIN not in text:
            continue
        stripped = _strip_block(text)
        if stripped.strip() in ("", "#!/bin/sh"):
            path.unlink()
        else:
            path.write_text(stripped, encoding="utf-8")
            os.chmod(path, path.stat().st_mode | stat.S_IXUSR)
        removed.append(name)
    return removed
