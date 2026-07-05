#!/usr/bin/env python3
"""One-shot migration: flat jidra/ → src/jidra/ with subpackages."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
OLD_PKG = ROOT / "jidra"
NEW_PKG = ROOT / "src" / "jidra"

# module_stem → subpackage (None = stays at src/jidra/ root)
MODULE_MAP: dict[str, str | None] = {
    "__init__": None,
    "cli": None,
    "models": None,
    # extractors
    "extractor": "extractors",
    "go_extractor": "extractors",
    "py_extractor": "extractors",
    "scala_extractor": "extractors",
    "smithy_extractor": "extractors",
    "ts_extractor": "extractors",
    "ts_treesitter": "extractors",
    # filters
    "filters": "filters",
    "file_filters": "filters",
    "go_filters": "filters",
    "py_filters": "filters",
    "py_type_provider": "filters",
    "scala_filters": "filters",
    "ts_filters": "filters",
    # graph
    "graph_store": "graph",
    "graph_validator": "graph",
    "graph_visualizer": "graph",
    "graph_rag": "graph",
    # indexing (doc store/indexer)
    "doc_indexer": "indexing",
    "doc_store": "indexing",
    "doc_graph_visualizer": "indexing",
    # flow
    "flow_doc_agent": "flow",
    "flow_stitcher": "flow",
    # engine
    "engine": "engine",
    "reindexer": "engine",
    "watcher": "engine",
    "daemon": "engine",
    "parallel": "engine",
    # server
    "mcp_server": "server",
    "actuator_client": "server",
    "proxy": "server",
    # llm
    "llm_client": "llm",
    "cost_calculator": "llm",
    "telemetry": "llm",
    "trace_engine": "llm",
    # smithy
    "smithy_bridge": "smithy",
    "smithy4j_builder": "smithy",
    # utils
    "cache": "utils",
    "parser": "utils",
    "selector": "utils",
    "context_builder": "utils",
    "git_hooks": "utils",
    "ui": "utils",
}

SUBPACKAGES = sorted(set(v for v in MODULE_MAP.values() if v is not None))

POC_DOCS = [
    "ENTERPRISE_GO_PROOF.md",
    "ENTERPRISE_PROOF.md",
    "ENTERPRISE_PYTHON_PROOF.md",
    "ENTERPRISE_SCALA_PROOF.md",
    "ENTERPRISE_TYPESCRIPT_PROOF.md",
    "FINDINGS_jidra_vs_codegraph.md",
    "FINDINGS_jidra_vs_codegraph_PYTHON.md",
    "FINDINGS_jidra_vs_codegraph_TYPESCRIPT.md",
    "COST_ROI_CALCULATOR.md",
    "DEMO.md",
    "PIVOT_RATIONALE.md",
    "CURRENT_STATE.md",
    "PROJECT_STATUS.md",
    "ROAD_MAP.md",
    "SCRIPT_SUMMARY.md",
    "REGRESSION_TESTS.md",
    "SCALA_PLAN.md",
    "EXAMPLES.md",
    "VALIDATE_JIDRA_README.md",
    "MCP.md",
]


# ── git helpers ──────────────────────────────────────────────────────────────


def git_mv(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "mv", str(src), str(dst)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"  WARN git mv failed: {src.relative_to(ROOT)} → {r.stderr.strip()}")
    else:
        print(f"  mv  {src.relative_to(ROOT)!s:55s} → {dst.relative_to(ROOT)}")


# ── import rewriting ─────────────────────────────────────────────────────────


def _new_relative(mod: str, file_subpkg: str | None) -> str:
    """Return the new relative-import prefix+module for `from .MOD import ...`."""
    target = MODULE_MAP.get(mod)
    if file_subpkg is None:
        # file is at src/jidra/ root
        if target is None:
            return f".{mod}"  # both at root → unchanged
        return f".{target}.{mod}"  # root → subpackage
    else:
        # file is inside a subpackage
        if target is None:
            return f"..{mod}"  # up to root
        if target == file_subpkg:
            return f".{mod}"  # same subpackage → unchanged
        return f"..{target}.{mod}"  # different subpackage


def rewrite_relative_imports(content: str, file_subpkg: str | None) -> str:
    lines = content.split("\n")
    out = []
    for line in lines:
        # Pattern 1: from .MODULE import STUFF  (specific symbol import)
        m = re.match(r"^(\s*)from \.([\w]+)( import .+)$", line)
        if m:
            indent, mod, rest = m.groups()
            if mod in MODULE_MAP:
                new_prefix = _new_relative(mod, file_subpkg)
                line = f"{indent}from {new_prefix}{rest}"
            out.append(line)
            continue

        # Pattern 2: from . import MOD [as ALIAS] [, MOD2 ...]  (module import)
        m2 = re.match(r"^(\s*)from \. import (.+)$", line)
        if m2:
            indent, imports_str = m2.groups()
            pieces = [p.strip() for p in imports_str.split(",")]
            new_lines = []
            for piece in pieces:
                am = re.match(r"(\w+)(.*)", piece)
                if not am:
                    new_lines.append(f"{indent}from . import {piece}")
                    continue
                mod, alias_rest = am.group(1), am.group(2)
                if mod not in MODULE_MAP:
                    new_lines.append(f"{indent}from . import {piece}")
                    continue
                new_prefix = _new_relative(mod, file_subpkg)
                # new_prefix: e.g. ..graph.graph_store or ..models
                # Emit: from PACKAGE import MODULE[alias]
                if "." in new_prefix.lstrip("."):
                    pkg_part = new_prefix.rsplit(".", 1)[0]
                    name_part = new_prefix.rsplit(".", 1)[1]
                else:
                    # root-level module: e.g. ..models → from .. import models
                    dots_str = "." * (len(new_prefix) - len(new_prefix.lstrip(".")))
                    pkg_part = dots_str
                    name_part = new_prefix.lstrip(".")
                new_lines.append(
                    f"{indent}from {pkg_part} import {name_part}{alias_rest}"
                )
            line = "\n".join(new_lines)
            out.append(line)
            continue

        out.append(line)
    return "\n".join(out)


def rewrite_absolute_imports(content: str) -> str:
    """Update `from jidra.X import Y` and `from jidra import X` in tests/scripts."""
    lines = content.split("\n")
    out = []
    for line in lines:
        # from jidra.MODULE import ...
        m = re.match(r"^(\s*)from jidra\.([\w]+)( import .+)$", line)
        if m:
            indent, mod, rest = m.groups()
            target = MODULE_MAP.get(mod)
            if target is not None:
                line = f"{indent}from jidra.{target}.{mod}{rest}"
            # else: stays (models, cli at root)
            out.append(line)
            continue

        # from jidra import MOD [, MOD2 ...]
        m2 = re.match(r"^(\s*)from jidra import (.+)$", line)
        if m2:
            indent, imports_str = m2.groups()
            pieces = [p.strip() for p in imports_str.split(",")]
            new_lines = []
            for piece in pieces:
                alias_match = re.match(r"(\w+)(.*)", piece)
                if not alias_match:
                    new_lines.append(f"{indent}from jidra import {piece}")
                    continue
                mod = alias_match.group(1)
                alias_rest = alias_match.group(2)
                target = MODULE_MAP.get(mod)
                if target is not None:
                    new_lines.append(
                        f"{indent}from jidra.{target} import {mod}{alias_rest}"
                    )
                else:
                    new_lines.append(f"{indent}from jidra import {mod}{alias_rest}")
            line = "\n".join(new_lines)
            out.append(line)
            continue

        # import jidra.MODULE
        m3 = re.match(r"^(\s*)import jidra\.([\w]+)(.*)$", line)
        if m3:
            indent, mod, rest = m3.groups()
            target = MODULE_MAP.get(mod)
            if target is not None:
                line = f"{indent}import jidra.{target}.{mod}{rest}"
            out.append(line)
            continue

        out.append(line)
    return "\n".join(out)


# ── main ─────────────────────────────────────────────────────────────────────


def phase(name: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {name}")
    print(f"{'═' * 60}")


def main() -> None:
    # ── 1. Create subpackage __init__.py stubs ──────────────────────────────
    phase("1. Create subpackage __init__.py stubs")
    for subpkg in SUBPACKAGES:
        init = NEW_PKG / subpkg / "__init__.py"
        init.parent.mkdir(parents=True, exist_ok=True)
        if not init.exists():
            init.write_text("")
            print(f"  created {init.relative_to(ROOT)}")

    # Also ensure src/jidra/ exists
    NEW_PKG.mkdir(parents=True, exist_ok=True)

    # ── 2. git mv Python files ──────────────────────────────────────────────
    phase("2. Move Python files into subpackages")
    for py_file in sorted(OLD_PKG.glob("*.py")):
        mod = py_file.stem
        subpkg = MODULE_MAP.get(mod)
        if subpkg is None:
            dst = NEW_PKG / py_file.name
        else:
            dst = NEW_PKG / subpkg / py_file.name
        git_mv(py_file, dst)

    # ── 3. git mv config.yaml ───────────────────────────────────────────────
    phase("3. Move config.yaml")
    cfg = OLD_PKG / "config.yaml"
    if cfg.exists():
        git_mv(cfg, NEW_PKG / "config.yaml")

    # ── 4. git mv jidra/ subdirs ────────────────────────────────────────────
    phase("4. Move jidra/ subdirectories")
    experiments_src = OLD_PKG / "experiments"
    if experiments_src.exists():
        git_mv(experiments_src, ROOT / "experiments")

    scala_proto_src = OLD_PKG / "scala_proto"
    if scala_proto_src.exists():
        git_mv(scala_proto_src, ROOT / "sidecar" / "scala" / "proto")

    output_src = OLD_PKG / "output"
    if output_src.exists():
        git_mv(output_src, ROOT / "output")

    # ── 5. git mv sidecars ──────────────────────────────────────────────────
    phase("5. Move sidecar directories")
    ts_sidecar = ROOT / "ts_sidecar"
    if ts_sidecar.exists():
        git_mv(ts_sidecar, ROOT / "sidecar" / "typescript")

    scala_sidecar = ROOT / "scala_sidecar"
    if scala_sidecar.exists():
        git_mv(scala_sidecar, ROOT / "sidecar" / "scala" / "src")

    # ── 6. scripts → evals ─────────────────────────────────────────────────
    phase("6. scripts/ → evals/")
    scripts_dir = ROOT / "scripts"
    if scripts_dir.exists():
        git_mv(scripts_dir, ROOT / "evals")
    root_eval = ROOT / "validate_jidra_analysis.py"
    if root_eval.exists():
        git_mv(root_eval, ROOT / "evals" / "validate_jidra_analysis.py")

    # ── 7. sample_graph.jsonl → tests/fixtures/ ────────────────────────────
    phase("7. sample_graph.jsonl → tests/fixtures/")
    sgj = ROOT / "sample_graph.jsonl"
    if sgj.exists():
        (ROOT / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
        git_mv(sgj, ROOT / "tests" / "fixtures" / "sample_graph.jsonl")

    # ── 8. Archive POC docs ─────────────────────────────────────────────────
    phase("8. Archive POC docs → docs/archive/")
    archive = ROOT / "docs" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    for doc in POC_DOCS:
        src = ROOT / doc
        if src.exists():
            git_mv(src, archive / doc)

    # ── 9. Rewrite internal relative imports ────────────────────────────────
    phase("9. Rewrite internal relative imports in src/jidra/")
    for py_file in sorted(NEW_PKG.rglob("*.py")):
        rel = py_file.relative_to(NEW_PKG)
        parts = rel.parts
        file_subpkg: str | None = parts[0] if len(parts) > 1 else None
        content = py_file.read_text()
        new_content = rewrite_relative_imports(content, file_subpkg)
        if new_content != content:
            py_file.write_text(new_content)
            print(f"  updated {py_file.relative_to(ROOT)}")

    # ── 10. Rewrite absolute imports in tests/ and evals/ ──────────────────
    phase("10. Rewrite absolute imports in tests/ and evals/")
    for search_dir in [ROOT / "tests", ROOT / "evals"]:
        if not search_dir.exists():
            continue
        for py_file in sorted(search_dir.rglob("*.py")):
            content = py_file.read_text()
            new_content = rewrite_absolute_imports(content)
            if new_content != content:
                py_file.write_text(new_content)
                print(f"  updated {py_file.relative_to(ROOT)}")

    # ── 11. Update pyproject.toml ───────────────────────────────────────────
    phase("11. Update pyproject.toml for src/ layout")
    pyproject = ROOT / "pyproject.toml"
    content = pyproject.read_text()
    new_content = content.replace(
        'where = ["."]\ninclude = ["jidra", "jidra.*"]',
        'where = ["src"]\ninclude = ["jidra", "jidra.*"]',
    )
    if new_content != content:
        pyproject.write_text(new_content)
        print("  updated pyproject.toml")
    else:
        print("  pyproject.toml — no match found, check manually")

    phase("DONE")
    print("  Review changes: git diff --stat")
    print("  Test: python -m pytest tests/")


if __name__ == "__main__":
    main()
