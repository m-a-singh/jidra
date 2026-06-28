# JIDRA MCP Layer

JIDRA includes a minimal MCP server so agents can call core graph-backed methods as tools. Works identically across all supported languages: **Java**, **Scala**, **TypeScript**, **Python**, and **Go**.

## Install

```bash
pip install -e ".[mcp]"
```

If you already installed editable mode without extras:

```bash
pip install mcp
```

## Run

```bash
jidra mcp --graph-type main
```

Optional custom graph path:

```bash
jidra mcp --graph /path/to/graph.db
```

## Server Modes

`python -m jidra.mcp_server` accepts `--mode`:

| Mode | Behavior |
|------|----------|
| `direct` (default) | Loads the graph in-process. Simplest; used by `jidra mcp` and as the Windows / no-socket fallback. |
| `proxy` | A thin stdio↔socket bridge. Spawns a shared **daemon** on demand and forwards tool calls to it, so multiple editor windows share **one** in-memory graph instead of each loading its own. Degrades to `direct` where Unix sockets are unavailable. |
| `daemon` | The detached background server itself (normally spawned by the proxy, not run by hand). Holds the graph in RAM, serves N proxies over a Unix socket, and — when watching is enabled — hot-reloads on file changes. |

`jidra up` writes an `.mcp.json` that launches the server in `--mode proxy`.

## Environment Variables

| Variable | Default | Behavior |
|----------|---------|----------|
| `JIDRA_FULL_TOOLS` | unset | If set to `1`, exposes all 25+ tools. By default only the **primary tier** (5 high-confidence tools) are visible: `jidra_explore`, `jidra_get_method_source`, `jidra_find_callers`, `jidra_get_implementations`, `jidra_analyze_stack_trace`. Full surface includes lower-precision variants (`jidra_get_flow`, `jidra_search`, etc.) and the two new grounding tools (`jidra_query_by_annotation`, `jidra_field_access`). Reversible — toggle any time. |

## Exposed Tools

**Discovery / search**
- `jidra_search` — FTS5 keyword search over method names, signatures, and source. Use when you don't know the exact symbol (e.g. `jidra_search("token validation")`).
- `jidra_explore` — natural-language exploration: tokenizes CamelCase/snake_case, ranks by relevance, attaches class/endpoint context. Good first call from a vague description.
- `jidra_get_framework_summary` — counts of framework roles, class stereotypes, and languages. A fast way to orient in a new codebase.
- `jidra_get_endpoints` — all HTTP endpoints (Spring, NestJS, Flask, FastAPI, Django) with method/route/role; optional `framework` filter.
- `jidra_get_components` — UI/framework components and hooks (React, Vue, Angular, NestJS); optional `kind` filter.

**Retrieval / tracing**
- `jidra_get_method_context` — per-method context (resolved/unresolved calls, source excerpt, class hierarchy).
- `jidra_get_method_source` — source location + text for a selected method.
- `jidra_find_callers` — reverse call lookup: all methods that call the given method. `depth` walks N levels up the call graph (default 1 = direct callers).
- `jidra_get_flow` — full stitched flow for a method entrypoint.
- `jidra_get_agent_flow` — compact ranked flow view for agent triage.
- `jidra_get_call_chain` — verifies a path exists between two methods within a depth bound.
- `jidra_analyze_stack_trace` — matches stack-trace frames (Java/Python/TypeScript) to graph methods and returns deterministic debug locations + focused flow summary.

**Structure / resolution**
- `jidra_get_implementations` — list ALL concrete implementations of an interface/abstract class in one call (optional `transitive` to follow the subtype chain). Use instead of repeated searches for "what implements X".
- `jidra_get_class_members` — list every method and field of a class in one call. Use before repeated `jidra_get_method_source` calls on the same class.
- `jidra_query_by_annotation` — find classes/methods by annotation. `kind`: 'class', 'method', or 'any' (default). Example: `query_by_annotation("RestController")`, `query_by_annotation("async_task", kind="method")`.
- `jidra_field_access` — find field access patterns. Query by field name or method signature. Field format: "ClassName#fieldName" or just "fieldName" to search all classes. Returns readers (methods that read the field) and writers (methods that write it). Example: `field_access(field="Cache#config")`, `field_access(method="processData(String)")`.

**Impact analysis**
- `jidra_get_file_dependents` — blast radius: which files break if you change this one, ranked by call-site count.
- `jidra_get_file_dependencies` — which files this one depends on (via call edges + class inheritance).

**Smithy (when the codebase uses it)**
- `jidra_get_operation_graph` — Smithy operation lookup: the operation's contract (service, HTTP binding, input/output shape ids, errors) plus the real handler class implementing it.
- `jidra_list_operations` — list all Smithy operations in the graph; optional `service` filter.

**Docs / spec**
- `jidra_get_docs` — search indexed spec/design documents for context relevant to a query or class.
- `jidra_index_docs` — index a document or directory (MD, PDF, DOCX) into the doc store for later retrieval via `jidra_get_docs`.

**Maintenance**
- `jidra_graph_health` — resolved/unresolved/external call-site breakdown.
- `jidra_check_staleness` — whether the graph is stale vs. source.
- `jidra_reindex` — incrementally update the graph after file changes.

All tools are graph-backed and deterministic for a fixed graph input. They do not run LLM diagnosis and do not mutate state (except `jidra_reindex`, which rebuilds the graph, and `jidra_index_docs`, which writes to the doc store).

## Budget-Tiered Output

Responses auto-scale to graph size. Every context/flow response includes `budget_tier`
(`XS`…`XL`, keyed on method count) and `graph_size`, so the caller can see why output was
truncated. Pass explicit `max_chars` / `depth` / `top_n` to override the tier defaults.

## Relation to CLI Docs

- `flow-doc` and `error-doc` are CLI markdown report generators built on the same graph-backed primitives.
- MCP exposes lower-level retrieval/tracing capabilities; it does not generate the full markdown report formats directly.

## Example Call

```json
{
  "tool": "jidra_analyze_stack_trace",
  "arguments": {
    "stack_trace": "java.lang.RuntimeException: boom\n\tat com.example.app.health.HealthIndicator.doHealthCheck(HealthIndicator.java:37)",
    "depth": 6,
    "max_nodes": 80
  }
}
```
