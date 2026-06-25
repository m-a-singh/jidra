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
- `jidra_get_flow` — full stitched flow for a method entrypoint.
- `jidra_get_agent_flow` — compact ranked flow view for agent triage.
- `jidra_get_call_chain` — verifies a path exists between two methods within a depth bound.
- `jidra_analyze_stack_trace` — matches stack-trace frames (Java/Python/TypeScript) to graph methods and returns deterministic debug locations + focused flow summary.

**Impact analysis**
- `jidra_get_file_dependents` — blast radius: which files break if you change this one, ranked by call-site count.
- `jidra_get_file_dependencies` — which files this one depends on (via call edges + class inheritance).

**Maintenance**
- `jidra_graph_health` — resolved/unresolved/external call-site breakdown.
- `jidra_check_staleness` — whether the graph is stale vs. source.
- `jidra_reindex` — incrementally update the graph after file changes.

All tools are graph-backed and deterministic for a fixed graph input. They do not run LLM diagnosis and do not mutate graph structure (except `jidra_reindex`, which rebuilds it).

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
