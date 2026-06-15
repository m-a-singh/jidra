from __future__ import annotations

from pathlib import Path

EXCLUDED_DIRS = {
    "node_modules",
    "dist",
    ".next",
    "out",
    "build",
    "coverage",
    ".git",
    ".turbo",
    ".cache",
    "generated",
    "__generated__",
    ".expo",
    "android",
    "ios",
}


def should_include_dir(path: Path) -> bool:
    names = {part.lower() for part in path.parts}
    return not bool(names & EXCLUDED_DIRS)


def iter_ts_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for ext in ("*.ts", "*.tsx"):
        for path in root.rglob(ext):
            if path.name.endswith(".d.ts"):
                continue
            if should_include_dir(path.parent):
                files.append(path)
    return sorted(files)


def detect_language(root: Path) -> str:
    """Return 'typescript' or 'java' by inspecting repo-level manifest files."""
    if (root / "package.json").exists():
        return "typescript"
    if (root / "pom.xml").exists() or (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return "java"
    # Fallback: count source files
    ts_count = sum(
        1 for _ in root.rglob("*.ts")
        if not _.name.endswith(".d.ts") and should_include_dir(_.parent)
    )
    java_count = sum(1 for _ in root.rglob("*.java") if should_include_dir(_.parent))
    return "typescript" if ts_count > java_count else "java"
