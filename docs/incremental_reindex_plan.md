# Plan: Incremental Re-indexing for JIDRA

## Context

Currently every reindex is a full re-parse of every source file. Goal: make reindex fast enough to call after every file edit, and give Claude/Codex a reliable signal when the graph is stale — even without a file watcher running.

**Docker / Actuator strategy:** Never run Docker in the re-index loop. Cache the actuator beans response to `.jidra/actuator_beans.json` on first `jidra validate` / `jidra process`. Incremental reindex uses the cache for edge filtering. Docker only re-run when user explicitly triggers it or bean-affecting annotations appear in changed files.

---

## Staleness detection for Claude/Codex (no file watcher)

Two moments when Claude needs to know the graph is stale:

| Moment | Mechanism |
|--------|-----------|
| **After Claude edits a file** | Claude calls `jidra_reindex(changed_files=[...])` immediately — it already knows what it edited |
| **Session start / out-of-band changes** | `jidra_check_staleness()` MCP tool — fingerprint diff, O(files) but called once |
| **Passive / any tool call** | `"graph_may_be_stale": true` hint embedded in every MCP tool response — O(1) spot-check |

### `jidra_check_staleness()` — new MCP tool

```python
@mcp.tool()
def jidra_check_staleness(graph_path: str | None = None, codebase: str | None = None) -> dict:
    """Check whether the graph is stale — call this at the start of a session
    or when you suspect out-of-band changes. Returns the changed file count and
    a hint to call jidra_reindex if stale."""
```

Returns:
```json
{
  "stale": true,
  "changed_files_count": 3,
  "deleted_files_count": 0,
  "oldest_changed_file": "src/main/java/Foo.java",
  "last_indexed_at": "2026-06-19T10:30:00Z",
  "hint": "Call jidra_reindex() to update the graph."
}
```

Implementation: `diff_fingerprints(compute_fingerprints(...), load_manifest(...))` — reuses reindexer.py functions. Returns `stale: false` if manifest is absent but graph exists (first-time signal to do a full index).

### Passive staleness hint in all tool responses

Store `last_indexed_at_ns` in the manifest. On each MCP tool call, stat one random source file from the manifest. If its `mtime_ns > last_indexed_at_ns`, the graph may be stale. This is O(1) per call — single stat.

```python
def _maybe_add_stale_hint(result: dict, graph_dir: Path) -> dict:
    from .reindexer import quick_stale_check
    if quick_stale_check(graph_dir):
        result["graph_may_be_stale"] = True
        result["staleness_hint"] = "Source files changed since last index. Call jidra_reindex()."
    return result
```

Apply to all 6 MCP tool handlers: `jidra_get_method_context`, `jidra_get_flow`, `jidra_get_agent_flow`, `jidra_get_method_source`, `jidra_get_call_chain`, `jidra_analyze_stack_trace`.

---

## Key design decisions

### method_id keeps `start_line`
Removing `start_line` breaks disambiguation for anonymous inner classes and Python method redefinitions. **`start_line` stays in the hash.** The mini-graph diff matches methods by `(signature, file_path)` and compares `start_line` separately to detect line-shift-only cases.

### Mini-graph diff dispatch

| Case | What changed | Action |
|------|-------------|--------|
| Same `(sig, file_path)` set, only `start_line` shifted | Lines added/removed above methods | Patch `start_line`/`end_line`/`source` in-place. Skip edge re-resolution. |
| Same method set, callsites within method differ | Body edit added/removed calls | Re-resolve edges for those methods only |
| Methods added or removed | Structural change | Strip changed-file records, merge mini graph, re-run full `_resolve_calls()` |

### MCP engine cache
Deferred as a separate feature — multiple Claude instances / parallel MCP processes make in-process cache coherency non-trivial.

---

## Implementation Plan

### Step 1: Actuator beans cache in `jidra/graph_validator.py`

```python
ACTUATOR_CACHE_FILENAME = "actuator_beans.json"

def save_actuator_cache(graph_dir: Path, beans_response: dict) -> None:
    """Atomic write of raw actuator response + metadata to actuator_beans.json."""

def load_actuator_cache(graph_dir: Path) -> dict | None:
    """Returns cached response or None if absent."""

def load_confirmed_beans_for_reindex(graph_dir: Path, graph: Graph) -> tuple[set[str], str]:
    """
    Priority:
    1. actuator_beans.json exists → parse + return, source="cached_actuator"
    2. detect_beans_from_graph() → source="static_annotation"  (~80% accuracy)
    3. No filtering → source="none"
    """

def detect_beans_from_graph(graph: Graph) -> set[str]:
    """Static bean inference from @Service/@Repository/@Controller/@Component/
    @Configuration/@Entity annotations already in the graph.
    Also walks @Bean methods in @Configuration classes."""

def _changed_files_affect_beans(mini_graph: Graph) -> bool:
    """True if any changed file has bean-relevant annotations — triggers cache staleness warning."""
```

Wire `save_actuator_cache()` into `_validate()` and `_process()` in `cli.py`.

---

### Step 2: New file `jidra/reindexer.py`

```python
MANIFEST_FILENAME = "file_manifest.json"
# manifest schema: {schema: 1, last_indexed_at_ns: int, codebase_root: str,
#                   entries: {abs_path: {mtime_ns, size}}}

def compute_fingerprints(codebase_root, extensions) -> dict[str, dict]
def load_manifest(graph_dir) -> dict          # {} if absent
def save_manifest(graph_dir, fingerprints, last_indexed_at_ns) -> None  # atomic write+rename
def diff_fingerprints(current, stored) -> tuple[set[str], set[str]]     # (changed_or_new, deleted)

def diff_graph_records(mini_graph, existing_graph, affected_files) -> dict:
    """
    Match methods by (signature, file_path) between mini_graph and existing records
    scoped to affected_files. Returns:
    {
      change_type: "no_change"|"metadata_only"|"callsite_change"|"structural",
      added_method_ids: [...],
      removed_method_ids: [...],
      line_shifted_methods: [(old_id, new_start_line, delta), ...],
      callsite_changed_method_ids: [...],
    }
    """

def incremental_reindex(codebase_root, graph_path, *, hint_changed_files=None) -> dict:
    """
    1. Load manifest + graph (full rebuild if absent)
    2. Fingerprint diff; union with hint_changed_files
    3. build_graph_for_files(changed_files) → mini_graph
    4. diff_graph_records → dispatch by change_type:
         no_change       → return early
         metadata_only   → patch start_line/end_line/source in-place; re-export
         callsite_change → strip + re-resolve edges for affected methods only
         structural      → strip all records for changed_files, merge mini_graph,
                           _resolve_calls() on full merged graph
    5. For Java: load_confirmed_beans_for_reindex() → filter edges
       (cached actuator response if present, static annotation fallback otherwise)
    6. Re-export JSONL; save manifest with new last_indexed_at_ns
    7. Return {change_type, changed_files, added_methods, removed_methods,
               elapsed_ms, actuator_cache_warning?}
    """

def check_staleness(codebase_root, graph_path) -> dict:
    """Fingerprint diff only — no reindex. Returns {stale, changed_count, deleted_count, ...}."""

def quick_stale_check(graph_dir: Path) -> bool:
    """O(1): stat one random file from manifest, compare mtime_ns vs last_indexed_at_ns."""
```

---

### Step 3: Add `build_graph_for_files()` in `jidra/extractor.py`

```python
def build_graph_for_files(files: set[Path], codebase_root: Path) -> Graph:
    """Multi-language per-file extraction. Groups by extension (.java/.py/.ts/.scala),
    calls existing per-file extractors. Does NOT run _resolve_calls()."""
```

---

### Step 4: Update `jidra/mcp_server.py`

**New tool `jidra_check_staleness`:**
```python
@mcp.tool()
def jidra_check_staleness(graph_path=None, codebase=None) -> dict:
    """Check if the graph is stale before starting analysis. Call at session start
    or after suspecting out-of-band changes. Returns stale status and file counts."""
    from .reindexer import check_staleness
    return check_staleness(Path(codebase or default_codebase), Path(graph_path or default_path))
```

**Updated `jidra_reindex`:**
```python
@mcp.tool()
def jidra_reindex(graph_path=None, codebase=None, changed_files=None) -> dict:
    """Call after editing files. Pass changed_files for fastest path.
    Falls back to fingerprint scan when omitted."""
    from .reindexer import incremental_reindex
    return incremental_reindex(
        Path(codebase or default_codebase), Path(graph_path or default_path),
        hint_changed_files=changed_files,
    )
```

---

### Step 5: Update watch mode in `jidra/cli.py`

Replace `_process()` in `SourceFileHandler.on_modified` with:
```python
from .reindexer import incremental_reindex
incremental_reindex(
    codebase_path,
    graph_validated_path,
    hint_changed_files=[event.src_path],
)
```

Wire `save_actuator_cache()` into `_validate()` and `_process()` after fetching actuator response.

---

## File layout after implementation

```
.jidra/
  graph.jsonl
  graph_test.jsonl
  graph_validated.jsonl
  file_manifest.json        # NEW: fingerprints + last_indexed_at_ns
  actuator_beans.json       # NEW: cached actuator response (Java repos only)
  validation_report.json
  graph.html
```

---

## Files to modify

| File | Change |
|------|--------|
| `jidra/reindexer.py` | **New** — fingerprinting, manifest, diff, incremental dispatch, staleness checks |
| `jidra/extractor.py` | Add `build_graph_for_files()` |
| `jidra/graph_validator.py` | Add `save/load_actuator_cache()`, `load_confirmed_beans_for_reindex()`, `detect_beans_from_graph()` |
| `jidra/mcp_server.py` | Add `jidra_check_staleness` tool; update `jidra_reindex`; add passive stale hint to all tools |
| `jidra/cli.py` | Wire `save_actuator_cache()` into validate/process; update watch mode |

**Reused (do not rewrite):**
- `jidra/cache.py:_fingerprint()` — mtime+size fingerprint logic
- `jidra/extractor.py:_extract_file()` + `_resolve_calls()` — parsing + resolution
- `jidra/graph_validator.py:parse_actuator_beans()` + `validate_graph()` — bean parsing + edge filtering
- `jidra/graph_io.py:load_graph_jsonl()` + `jidra/exporter.py:export_jsonl()` — I/O

---

## Assumptions

1. `(signature, file_path)` is unique per method per snapshot
2. `_resolve_calls()` on full merged graph is < 200ms for typical repos (pure dict lookups, no I/O)
3. Static bean annotation detection covers ~80% of Spring beans — sufficient fallback when actuator cache absent
4. O(1) random-file spot-check is sufficient for passive stale detection (false-positive-safe — over-warns rather than under-warns)
5. First run after deploy always does full rebuild (manifest absent) — one-time cost

---

## Verification

1. **After Claude edits:** `jidra_reindex(changed_files=["Foo.java"])` → correct `change_type`, `elapsed_ms < 200`
2. **Staleness tool:** Change file without reindexing → `jidra_check_staleness()` returns `stale: true` with correct count
3. **Passive hint:** Change file, call `jidra_get_flow` without reindexing → response contains `graph_may_be_stale: true`
4. **Line-shift only:** Add blank line above method → `change_type: metadata_only`, no edge changes, edges unchanged
5. **Body edit:** Change method body to add a call → `change_type: callsite_change`, that method's edges updated, others untouched
6. **Structural:** Add new method → `change_type: structural`, `added_methods: 1`, visible in `jidra_get_flow`
7. **Actuator cache:** After `jidra validate`, confirm `actuator_beans.json` written; incremental reindex uses it, no Docker spawned
8. **Bean warning:** Edit a `@Service` class → reindex result includes `actuator_cache_warning`
9. **Deletion:** Delete source file → its methods absent from graph after reindex
