from __future__ import annotations

from pathlib import Path

from .file_filters import apply_filters

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
    return sorted(apply_filters(files, root))


_NOISE_DIRS = {"node_modules", ".venv", "venv", ".tox", ".eggs", "site-packages", "__pycache__"}


def _rglob_outside_noise(root: Path, pattern: str) -> list[Path]:
    """rglob that skips common dependency/build directories."""
    return [
        p for p in root.rglob(pattern)
        if not _NOISE_DIRS.intersection(p.parts)
    ][:1]


def detect_languages(root: Path) -> list[str]:
    """Detect all source languages present in a multi-language repo."""
    langs = []
    # Scala before Java — build.sbt is the definitive Scala signal
    if (root / "build.sbt").exists() or (
        root / "project" / "build.properties"
    ).exists() or _rglob_outside_noise(root, "build.sbt"):
        langs.append("scala")
    if (root / "package.json").exists() or _rglob_outside_noise(root, "package.json"):
        langs.append("typescript")
    if (root / "go.mod").exists() or _rglob_outside_noise(root, "go.mod"):
        langs.append("go")
    if (
        (root / "pom.xml").exists()
        or (root / "build.gradle").exists()
        or (root / "build.gradle.kts").exists()
        or _rglob_outside_noise(root, "pom.xml")
        or _rglob_outside_noise(root, "build.gradle")
        or _rglob_outside_noise(root, "build.gradle.kts")
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
        or _rglob_outside_noise(root, "pyproject.toml")
        or _rglob_outside_noise(root, "setup.py")
        or _rglob_outside_noise(root, "requirements.txt")
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
