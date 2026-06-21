from __future__ import annotations

from pathlib import Path

EXCLUDED_DIRS = {
    # Package managers / dependencies
    "node_modules",
    "vendor",
    # Compiled / build output
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".output",
    "storybook-static",
    # CI / deployment artifacts
    ".vercel",
    ".turbo",
    ".cache",
    "coverage",
    # VCS
    ".git",
    # Generated code
    "generated",
    "__generated__",
    # Static assets — never source code
    "public",
    "static",
    # Mobile (monorepo)
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


def detect_languages(root: Path) -> list[str]:
    """Detect all source languages present in a multi-language repo."""
    langs = []
    # Scala before Java — build.sbt is the definitive Scala signal
    if (
        (root / "build.sbt").exists()
        or (root / "project" / "build.properties").exists()
        or list(root.rglob("build.sbt"))[:1]
    ):
        langs.append("scala")
    if (root / "package.json").exists() or list(root.rglob("package.json"))[:1]:
        langs.append("typescript")
    if (
        (root / "pom.xml").exists()
        or (root / "build.gradle").exists()
        or (root / "build.gradle.kts").exists()
        or list(root.rglob("pom.xml"))[:1]
        or list(root.rglob("build.gradle"))[:1]
        or list(root.rglob("build.gradle.kts"))[:1]
    ):
        langs.append("java")
    if (
        (root / "pyproject.toml").exists()
        or (root / "setup.py").exists()
        or (root / "setup.cfg").exists()
        or (root / "Pipfile").exists()
        or (root / ".venv").exists()
        or (root / "venv").exists()
        or (root / "requirements.txt").exists()
        or list(root.rglob("pyproject.toml"))[:1]
        or list(root.rglob("setup.py"))[:1]
        or list(root.rglob("requirements.txt"))[:1]
    ):
        langs.append("python")
    return langs


def detect_language(root: Path) -> str:
    """Return 'typescript', 'java', or 'python' by inspecting repo-level manifest files."""
    if (root / "package.json").exists():
        return "typescript"
    if (
        (root / "pom.xml").exists()
        or (root / "build.gradle").exists()
        or (root / "build.gradle.kts").exists()
    ):
        return "java"
    # Python checks
    if (
        (root / "pyproject.toml").exists()
        or (root / "setup.py").exists()
        or (root / "setup.cfg").exists()
        or (root / "Pipfile").exists()
    ):
        return "python"

    # Fallback: count source files
    ts_count = sum(
        1
        for _ in root.rglob("*.ts")
        if not _.name.endswith(".d.ts") and should_include_dir(_.parent)
    )
    java_count = sum(1 for _ in root.rglob("*.java") if should_include_dir(_.parent))
    py_count = sum(1 for _ in root.rglob("*.py") if should_include_dir(_.parent))

    if py_count > 0 and py_count >= max(ts_count, java_count):
        return "python"
    return "typescript" if ts_count > java_count else "java"
