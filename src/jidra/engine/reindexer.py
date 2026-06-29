from __future__ import annotations

import json
import time
from pathlib import Path

from ..models import Graph

MANIFEST_FILENAME = "file_manifest.json"


def compute_fingerprints(
    codebase_root: Path, extensions: list[str] | None = None
) -> dict[str, dict]:
    """Compute mtime_ns + size fingerprints for all source files.

    Returns: {abs_path_str: {"mtime_ns": int, "size": int}}
    """
    if extensions is None:
        extensions = [".java", ".py", ".ts", ".tsx", ".js", ".jsx", ".scala", ".go"]

    fingerprints = {}
    for ext in extensions:
        for file_path in codebase_root.rglob(f"*{ext}"):
            if file_path.is_file():
                stat = file_path.stat()
                fingerprints[str(file_path)] = {
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                }
    return fingerprints


def load_manifest(graph_dir: Path) -> dict:
    """Load manifest or return empty dict if absent."""
    path = graph_dir / MANIFEST_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {}


def save_manifest(
    graph_dir: Path, fingerprints: dict[str, dict], last_indexed_at_ns: int
) -> None:
    """Atomically save manifest (fingerprints + timestamp)."""
    graph_dir.mkdir(parents=True, exist_ok=True)
    path = graph_dir / MANIFEST_FILENAME
    manifest = {
        "schema": 1,
        "last_indexed_at_ns": last_indexed_at_ns,
        "codebase_root": str(path.parent.parent),
        "entries": fingerprints,
    }
    # Write to temp file first, then rename atomically
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temp_path.replace(path)


def diff_fingerprints(
    current: dict[str, dict], stored: dict
) -> tuple[set[str], set[str]]:
    """Compare current fingerprints against stored manifest entries.

    Returns: (changed_or_new_files, deleted_files)
    - changed_or_new: files with different mtime_ns/size or not in stored
    - deleted: files in stored but not in current
    """
    stored_entries = stored.get("entries", {})

    changed_or_new = set()
    for path_str, fp in current.items():
        if path_str not in stored_entries:
            changed_or_new.add(path_str)
        elif fp.get("mtime_ns") != stored_entries[path_str].get("mtime_ns") or fp.get(
            "size"
        ) != stored_entries[path_str].get("size"):
            changed_or_new.add(path_str)

    deleted = set()
    for path_str in stored_entries:
        if path_str not in current:
            deleted.add(path_str)

    return changed_or_new, deleted


def diff_graph_records(
    mini_graph,
    existing_graph,
    affected_files: set[str],
) -> dict:
    """Compute diff between mini_graph (changed files) and existing_graph.

    Matches methods by (signature, file_path) and compares start_line separately.

    Returns:
    {
        "change_type": "no_change" | "metadata_only" | "callsite_change" | "structural",
        "added_method_ids": [...],
        "removed_method_ids": [...],
        "line_shifted_methods": [(old_id, new_start_line, delta), ...],
        "callsite_changed_method_ids": [...],
    }
    """
    # Build lookup tables by (signature, file_path)
    existing_by_sig_file = {}
    for m in existing_graph.methods:
        key = (m.signature, m.file_path)
        if key not in existing_by_sig_file:
            existing_by_sig_file[key] = []
        existing_by_sig_file[key].append(m)

    mini_by_sig_file = {}
    for m in mini_graph.methods:
        key = (m.signature, m.file_path)
        if key not in mini_by_sig_file:
            mini_by_sig_file[key] = []
        mini_by_sig_file[key].append(m)

    added_ids = []
    removed_ids = []
    line_shifted = []
    callsite_changed_ids = []

    # Detect additions and line shifts
    for key, mini_methods in mini_by_sig_file.items():
        sig, fpath = key
        if fpath not in affected_files:
            continue

        existing_methods = existing_by_sig_file.get(key, [])

        if not existing_methods:
            # New method(s)
            for m in mini_methods:
                added_ids.append(m.id)
        elif len(mini_methods) == 1 and len(existing_methods) == 1:
            # Single match: check for line shift or callsite changes
            old_m = existing_methods[0]
            new_m = mini_methods[0]

            if old_m.start_line != new_m.start_line:
                delta = new_m.start_line - old_m.start_line
                line_shifted.append((old_m.id, new_m.start_line, delta))

            if old_m.source != new_m.source:
                callsite_changed_ids.append(new_m.id)

    # Detect removals
    for key, existing_methods in existing_by_sig_file.items():
        sig, fpath = key
        if fpath not in affected_files:
            continue

        if key not in mini_by_sig_file:
            # Method(s) removed
            for m in existing_methods:
                removed_ids.append(m.id)

    # Determine overall change type
    if (
        not added_ids
        and not removed_ids
        and not line_shifted
        and not callsite_changed_ids
    ):
        change_type = "no_change"
    elif not removed_ids and not added_ids and callsite_changed_ids:
        change_type = "callsite_change"
    elif (
        not removed_ids and not added_ids and line_shifted and not callsite_changed_ids
    ):
        change_type = "metadata_only"
    else:
        change_type = "structural"

    return {
        "change_type": change_type,
        "added_method_ids": added_ids,
        "removed_method_ids": removed_ids,
        "line_shifted_methods": line_shifted,
        "callsite_changed_method_ids": callsite_changed_ids,
    }


def check_staleness(codebase_root: Path, graph_path: Path) -> dict:
    """Check if graph is stale without reindexing.

    Returns:
    {
        "stale": bool,
        "changed_files_count": int,
        "deleted_files_count": int,
        "oldest_changed_file": str | None,
        "last_indexed_at": str (ISO timestamp) | None,
        "hint": str,
    }
    """
    graph_dir = graph_path if graph_path.is_dir() else graph_path.parent
    manifest = load_manifest(graph_dir)

    if not manifest:
        # No manifest means first-time or manifest lost
        return {
            "stale": True,
            "changed_files_count": 0,
            "deleted_files_count": 0,
            "oldest_changed_file": None,
            "last_indexed_at": None,
            "hint": "No manifest found. Run jidra_reindex() for full rebuild.",
        }

    current = compute_fingerprints(codebase_root)
    changed_files, deleted_files = diff_fingerprints(current, manifest)

    is_stale = bool(changed_files or deleted_files)
    oldest_changed = None
    if changed_files:
        oldest_changed = sorted(changed_files)[0]

    last_indexed_at_ns = manifest.get("last_indexed_at_ns")
    last_indexed_at = None
    if last_indexed_at_ns:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(last_indexed_at_ns / 1_000_000_000, tz=timezone.utc)
        last_indexed_at = dt.isoformat()

    return {
        "stale": is_stale,
        "changed_files_count": len(changed_files),
        "deleted_files_count": len(deleted_files),
        "oldest_changed_file": oldest_changed,
        "last_indexed_at": last_indexed_at,
        "hint": "Call jidra_reindex() to update the graph."
        if is_stale
        else "Graph is current.",
    }


def quick_stale_check(graph_dir: Path) -> bool:
    """O(1) random-file spot-check for staleness.

    Stat one random file from manifest and compare mtime_ns against last_indexed_at_ns.
    Returns True if any file appears newer than the index.
    """
    manifest = load_manifest(graph_dir)
    if not manifest:
        return False

    entries = manifest.get("entries", {})
    if not entries:
        return False

    last_indexed_at_ns = manifest.get("last_indexed_at_ns", 0)

    # Sample one file at random so the check doesn't always land on the same
    # (often stable) file, e.g. whichever sorts/inserts first.
    import random

    sample_path = random.choice(list(entries.keys()))
    try:
        stat = Path(sample_path).stat()
        return stat.st_mtime_ns > last_indexed_at_ns
    except (FileNotFoundError, OSError):
        # File may have been deleted; not stale by this check
        return False


_REINDEX_VARIANT = "validated"


def incremental_reindex(
    codebase_root: Path,
    graph_path: Path,
    *,
    hint_changed_files: list[str] | None = None,
) -> dict:
    """Incremental reindex orchestration.

    1. Load manifest + graph (full rebuild if absent)
    2. Fingerprint diff; union with hint_changed_files
    3. build_graph_for_files(changed_files) → mini_graph
    4. diff_graph_records → dispatch by change_type
    5. For Java: load_confirmed_beans_for_reindex() → filter edges
    6. Persist via targeted SQL deletes/inserts scoped to changed_files (not a
       full-graph rewrite); save manifest with new last_indexed_at_ns.

    Note: edge re-resolution (`_resolve_calls`) still needs enough graph context
    to be correct, so a full `validated`-variant load is used for that in-memory
    step. What this removes is the *persistence* cost — only rows for changed
    files are deleted/rewritten in the DB, never the whole graph.

    Returns:
    {
        "change_type": str,
        "changed_files": list[str],
        "added_methods": int,
        "removed_methods": int,
        "elapsed_ms": float,
        "actuator_cache_warning": str | None,
    }
    """
    start_ns = time.perf_counter_ns()

    from ..extractors.extractor import build_graph
    from ..graph import graph_store

    graph_dir = graph_path if graph_path.is_dir() else graph_path.parent
    graph_dir.mkdir(parents=True, exist_ok=True)

    db_path = graph_store.resolve_graph_db_path(graph_path)
    conn = graph_store.connect(db_path)

    # Load manifest
    manifest = load_manifest(graph_dir)

    def _full_rebuild(changed_files: list[str]) -> dict:
        full_graph = build_graph(codebase_root)
        last_indexed_at_ns = int(time.time_ns())
        fps = compute_fingerprints(codebase_root)
        save_manifest(graph_dir, fps, last_indexed_at_ns)
        graph_store.save_full_graph(conn, full_graph, variant=_REINDEX_VARIANT)
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
        return {
            "change_type": "full_rebuild",
            "changed_files": changed_files,
            "added_methods": len(full_graph.methods),
            "removed_methods": 0,
            "elapsed_ms": elapsed_ms,
            "actuator_cache_warning": None,
        }

    if not manifest:
        current_fps = compute_fingerprints(codebase_root)
        return _full_rebuild(list(current_fps.keys()))

    # Fingerprint diff
    current_fps = compute_fingerprints(codebase_root)
    changed_files_set, deleted_files_set = diff_fingerprints(current_fps, manifest)

    # Union with hint
    if hint_changed_files:
        changed_files_set.update(hint_changed_files)

    if not changed_files_set and not deleted_files_set:
        # No changes
        return {
            "change_type": "no_change",
            "changed_files": [],
            "added_methods": 0,
            "removed_methods": 0,
            "elapsed_ms": (time.perf_counter_ns() - start_ns) / 1_000_000,
            "actuator_cache_warning": None,
        }

    # Load existing graph (full read is fine — it's a SQL query, not a file parse)
    existing_graph = graph_store.load_graph(conn, variant=_REINDEX_VARIANT)
    if not existing_graph.classes and not existing_graph.methods:
        return _full_rebuild(list(changed_files_set))

    # Build mini-graph for changed files
    from ..extractors.extractor import build_graph_for_files

    changed_files_paths = {Path(f) for f in changed_files_set if Path(f).exists()}

    if not changed_files_paths:
        # All changed files deleted; full rebuild
        return _full_rebuild(list(changed_files_set))

    mini_graph = build_graph_for_files(changed_files_paths, codebase_root)

    # Diff records
    diff_result = diff_graph_records(mini_graph, existing_graph, changed_files_set)
    change_type = diff_result["change_type"]

    # Dispatch by change_type
    if change_type == "no_change":
        result_graph = existing_graph
    elif change_type == "metadata_only":
        # Patch start_line/end_line in-place; skip edge re-resolve
        result_graph = _patch_metadata_only(
            existing_graph, mini_graph, diff_result["line_shifted_methods"]
        )
    elif change_type == "callsite_change":
        # Strip + re-resolve edges for affected methods only
        result_graph = _update_callsite_edges(
            existing_graph, mini_graph, diff_result["callsite_changed_method_ids"]
        )
    else:  # structural
        # Strip all records for changed_files, merge mini_graph, full re-resolve
        result_graph = _do_structural_reindex(
            existing_graph, mini_graph, changed_files_set
        )

    # Bean filtering for Java (if applicable)
    from ..graph.graph_validator import load_confirmed_beans_for_reindex

    confirmed_beans, bean_source = load_confirmed_beans_for_reindex(
        graph_dir, result_graph
    )
    actuator_warning = None
    if bean_source == "static_annotation":
        actuator_warning = (
            "Using static bean detection fallback (no cached actuator response)"
        )

    if confirmed_beans:
        # Filter edges
        confirmed_ids = {
            c.id for c in result_graph.classes if c.full_name in confirmed_beans
        }
        result_graph.resolved_call_edges = [
            e
            for e in result_graph.resolved_call_edges
            if any(
                m.class_id in confirmed_ids
                for m in result_graph.methods
                if m.id == e.callee_method_id
            )
        ]

    # Persist via targeted SQL deletes/inserts scoped to what actually changed.
    last_indexed_at_ns = int(time.time_ns())
    save_manifest(graph_dir, current_fps, last_indexed_at_ns)

    method_by_id = {m.id: m for m in result_graph.methods}

    if change_type == "metadata_only":
        for old_id, new_line, _delta in diff_result["line_shifted_methods"]:
            m = method_by_id.get(old_id)
            if m is not None:
                graph_store.update_method_lines(
                    conn,
                    old_id,
                    m.start_line,
                    m.end_line,
                    m.source,
                    variant=_REINDEX_VARIANT,
                )
        graph_store.replace_resolved_call_edges(
            conn, result_graph.resolved_call_edges, variant=_REINDEX_VARIANT
        )
    elif change_type == "callsite_change":
        changed_method_ids = diff_result["callsite_changed_method_ids"]
        graph_store.delete_callsites_by_caller(
            conn, changed_method_ids, variant=_REINDEX_VARIANT
        )
        graph_store.delete_methods(conn, changed_method_ids, variant=_REINDEX_VARIANT)
        graph_store.insert_methods(
            conn,
            [m for m in result_graph.methods if m.id in changed_method_ids],
            variant=_REINDEX_VARIANT,
        )
        graph_store.insert_callsites(
            conn,
            [
                c
                for c in result_graph.callsites
                if c.caller_method_id in changed_method_ids
            ],
            variant=_REINDEX_VARIANT,
        )
        graph_store.replace_resolved_call_edges(
            conn, result_graph.resolved_call_edges, variant=_REINDEX_VARIANT
        )
        conn.commit()
    elif change_type == "structural":
        fragment = Graph(
            classes=[
                c for c in result_graph.classes if c.file_path in changed_files_set
            ],
            methods=[
                m for m in result_graph.methods if m.file_path in changed_files_set
            ],
            fields=[f for f in result_graph.fields if f.file_path in changed_files_set],
            callsites=[
                c for c in result_graph.callsites if c.file_path in changed_files_set
            ],
            inheritance_edges=[
                e
                for e in result_graph.inheritance_edges
                if e.source_class_id
                in {
                    c.id
                    for c in result_graph.classes
                    if c.file_path in changed_files_set
                }
            ],
            resolved_call_edges=[],
        )
        graph_store.upsert_for_files(
            conn, fragment, changed_files_set, variant=_REINDEX_VARIANT
        )
        graph_store.replace_resolved_call_edges(
            conn, result_graph.resolved_call_edges, variant=_REINDEX_VARIANT
        )
    # no_change: nothing to persist

    added_count = len(diff_result.get("added_method_ids", []))
    removed_count = len(diff_result.get("removed_method_ids", []))

    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
    return {
        "change_type": change_type,
        "changed_files": list(changed_files_set),
        "added_methods": added_count,
        "removed_methods": removed_count,
        "elapsed_ms": elapsed_ms,
        "actuator_cache_warning": actuator_warning,
    }


def _patch_metadata_only(
    existing_graph,
    mini_graph,
    line_shifted_methods: list[tuple[str, int, int]],
):
    """Patch start_line/end_line/source in-place for line-shifted methods."""
    line_shift_map = {
        old_id: (new_line, delta) for old_id, new_line, delta in line_shifted_methods
    }

    # Update methods
    for method in existing_graph.methods:
        if method.id in line_shift_map:
            new_line, delta = line_shift_map[method.id]
            method.start_line = new_line

            # Prefer the mini-graph's own end_line (accounts for body growth
            # or shrinkage); fall back to a plain shift if unavailable.
            mini_m = next((m for m in mini_graph.methods if m.id == method.id), None)
            if mini_m and mini_m.end_line is not None:
                method.end_line = mini_m.end_line
            else:
                method.end_line = (method.end_line or 0) + delta

            if mini_m:
                method.source = mini_m.source

    return existing_graph


def _update_callsite_edges(
    existing_graph,
    mini_graph,
    callsite_changed_ids: list[str],
):
    """Re-resolve edges for methods with changed callsites."""
    from ..extractors.extractor import _resolve_calls

    # Replace methods in existing_graph with updated versions from mini_graph
    for method_id in callsite_changed_ids:
        # Find and remove old method's callsites
        existing_graph.callsites = [
            c for c in existing_graph.callsites if c.caller_method_id != method_id
        ]
        # Remove old method
        existing_graph.methods = [
            m for m in existing_graph.methods if m.id != method_id
        ]

        # Add updated method and callsites from mini_graph
        for m in mini_graph.methods:
            if m.id == method_id:
                existing_graph.methods.append(m)
        for c in mini_graph.callsites:
            if c.caller_method_id == method_id:
                existing_graph.callsites.append(c)

    # Re-resolve calls for the full graph (edges will be rebuilt)
    _resolve_calls(existing_graph)
    return existing_graph


def _do_structural_reindex(
    existing_graph,
    mini_graph,
    changed_files_set: set[str],
):
    """Strip all records for changed_files, merge mini_graph, full re-resolve."""
    from ..extractors.extractor import _resolve_calls

    # Strip records for changed files
    removed_class_ids = {
        c.id for c in existing_graph.classes if c.file_path in changed_files_set
    }
    existing_graph.classes = [
        c for c in existing_graph.classes if c.file_path not in changed_files_set
    ]
    existing_graph.methods = [
        m for m in existing_graph.methods if m.file_path not in changed_files_set
    ]
    existing_graph.fields = [
        f for f in existing_graph.fields if f.file_path not in changed_files_set
    ]
    existing_graph.callsites = [
        c for c in existing_graph.callsites if c.file_path not in changed_files_set
    ]
    existing_graph.inheritance_edges = [
        e
        for e in existing_graph.inheritance_edges
        if e.source_class_id not in removed_class_ids
    ]

    # Merge mini_graph
    existing_graph.classes.extend(mini_graph.classes)
    existing_graph.methods.extend(mini_graph.methods)
    existing_graph.fields.extend(mini_graph.fields)
    existing_graph.callsites.extend(mini_graph.callsites)
    existing_graph.inheritance_edges.extend(mini_graph.inheritance_edges)

    # Clear and re-resolve all edges
    existing_graph.resolved_call_edges = []
    _resolve_calls(existing_graph)

    return existing_graph
