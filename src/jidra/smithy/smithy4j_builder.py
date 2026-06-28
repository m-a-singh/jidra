"""Detect and build smithy4j-generated Java sources before indexing.

Scans build.gradle files for the smithy4j-gradle plugin. If found, runs
`./gradlew clean build -x test` so that `build/generated-src/smithy4j/` exists
and can be included in the Java extraction pass.

This is intentionally a one-shot operation: smithy contracts rarely change, so
incremental reindex skips it entirely.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


_SMITHY4J_MARKER = "smithy4j"
_GENERATED_SUBPATH = "build/generated-src/smithy4j/main"


def find_smithy4j_modules(codebase_root: Path) -> list[Path]:
    """Return build.gradle files that reference the smithy4j-gradle plugin."""
    matches = []
    for gradle_file in codebase_root.rglob("build.gradle"):
        try:
            if _SMITHY4J_MARKER in gradle_file.read_text(
                encoding="utf-8", errors="ignore"
            ):
                matches.append(gradle_file.parent)
        except OSError:
            continue
    return matches


def build_smithy4j_sources(codebase_root: Path, timeout: int = 300) -> list[Path]:
    """Run gradle build in modules that use smithy4j and return generated source dirs.

    Returns a list of `build/generated-src/smithy4j/` directories that exist
    after the build. Empty list if no smithy4j modules found or build fails.
    """
    modules = find_smithy4j_modules(codebase_root)
    if not modules:
        return []

    # Run gradle from the repo root — covers all submodules in one pass.
    gradle_wrapper = codebase_root / "gradlew"
    gradle_cmd = str(gradle_wrapper) if gradle_wrapper.exists() else "gradle"

    print(
        f"  smithy4j detected in {len(modules)} module(s) — running gradle build to generate sources...",
        flush=True,
    )
    result = subprocess.run(
        [gradle_cmd, "clean", "build", "-x", "test"],
        cwd=str(codebase_root),
        capture_output=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        print(
            "  Warning: gradle build exited non-zero — smithy4j sources may be incomplete.",
            flush=True,
        )

    generated_dirs = []
    for module_dir in modules:
        generated = module_dir / _GENERATED_SUBPATH
        if generated.exists():
            generated_dirs.append(generated)

    if generated_dirs:
        print(
            f"  Found smithy4j generated sources in {len(generated_dirs)} location(s).",
            flush=True,
        )
    else:
        print(
            "  Warning: gradle build completed but no smithy4j generated sources found.",
            flush=True,
        )

    return generated_dirs
