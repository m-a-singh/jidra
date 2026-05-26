# JIDRA MCP Layer

JIDRA includes a minimal MCP server so agents can call core graph-backed methods as tools.

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
jidra mcp --graph /path/to/graph.jsonl
```

## Exposed Tools

- `jidra_get_method_context`
- `jidra_get_flow`
- `jidra_get_agent_flow`
- `jidra_get_method_source`
- `jidra_get_call_chain`
- `jidra_analyze_stack_trace`

## Tool Intent and Usage

- `jidra_get_method_context`:
  deterministic per-method context summary (resolved/unresolved calls, source excerpt metadata).
- `jidra_get_flow`:
  full stitched flow for a method entrypoint.
- `jidra_get_agent_flow`:
  compact ranked flow view for agent-facing triage.
- `jidra_get_method_source`:
  source location + source text for a selected method.
- `jidra_get_call_chain`:
  verifies path existence between two methods within depth bound.
- `jidra_analyze_stack_trace`:
  analyzes raw Java stack trace text, matches frames to graph methods, identifies primary anchor, and returns deterministic debug locations plus focused flow map summary.

All tools are graph-backed and deterministic for a fixed graph input. They do not run LLM diagnosis and do not mutate graph structure.

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
