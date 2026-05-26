# CURRENT_STATE

## 1) What JIDRA Is Today
JIDRA is a Python CLI + MCP server that builds a static Java graph (classes, methods, callsites, resolved call edges), then exposes graph-backed operations for trace, context, stitched flow, prompt construction, and optional LiteLLM-based diagnosis. It is mostly deterministic up to graph/context/flow outputs; only `diagnose` adds LLM-generated analysis.

## 2) Current Architecture
- `jidra/cli.py`: Main CLI entrypoint. Handles `index`, `trace`, `context`, `trace-route`, `flow`, `prompt`, `diagnose`, `mcp`.
- `jidra/extractor.py`: Java parsing/extraction pipeline to build in-memory graph.
- `jidra/models.py`: Core dataclasses (`Graph`, `MethodEntry`, `CallSite`, `ResolvedCallEdge`, etc.).
- `jidra/exporter.py`: Converts graph to JSONL records and writes graph files.
- `jidra/graph_io.py`: Resolves graph file paths and loads JSONL graph into dataclasses.
- `jidra/selector.py`: Method selector resolution + not-found/ambiguous error formatting.
- `jidra/trace_engine.py`: Method/route trace traversal and categorized flow output.
- `jidra/context_builder.py`: Method-scoped context builder with callsite filtering, dedupe, unresolved grouping.
- `jidra/flow_stitcher.py`: Recursive stitched flow over resolved callees, with node ranking/classification and compact agent view.
- `jidra/engine.py`: Thin graph-backed service layer used by MCP tools (`context`, `flow`, `agent_flow`, `method_source`, `call_chain`).
- `jidra/mcp_server.py`: MCP stdio server exposing JIDRA engine tools.
- `jidra/llm_client.py`: JIDRA-owned LiteLLM client with config profile support and usage metrics.
- `jidra/config.yaml`: Runtime config (LLM profiles + optional flow include/exclude rules).

## 3) Current CLI Commands
- `index`
  - Purpose: build graph JSONL from Java repo.
  - Important flags: `--codebase`, `--output`.
  - Output shape: JSON summary (`main_graph`, `main_records`, `test_graph`, `test_records`).

- `trace`
  - Purpose: method call flow trace.
  - Important flags: `--graph`, `--graph-type`, `--method`, `--max-depth`, `--business-only`, `--output`.
  - Output shape: trace dict with `root`, `flow`, call buckets/stats, optional `filters`.

- `context`
  - Purpose: method context extraction.
  - Important flags: `--graph`, `--graph-type`, `--method`, `--max-chars`, `--business-only`, `--output`.
  - Output shape: `method_signature`, `method_source`, class/endpoint metadata, `resolved_callees`, `unresolved_calls`, etc.

- `trace-route`
  - Purpose: route-to-flow trace.
  - Important flags: `--graph`, `--graph-type`, `--route`, `--max-depth`, `--output`.
  - Output shape: trace dict keyed by matched endpoint root and traversed flow.

- `flow`
  - Purpose: stitched recursive flow graph from an entry method.
  - Important flags: `--graph`, `--graph-type`, `--method`, `--depth`, `--business-only|--no-business-only`, `--output`.
  - Output shape: `entry`, `nodes`, `edges`, grouped `uncertain_edges`, `stopped_paths`, tiered views, `agent_view`, `summary`.

- `prompt`
  - Purpose: build prompt text from context or flow references.
  - Important flags: `--graph`, `--graph-type`, `--method`, `--target`, `--max-chars`, `--use-flow`, `--top-n`, `--include-source`, `--verbose-flow`, `--debug-flow`, `--output`.
  - Output shape: text prompt; optional sidecar debug JSON when `--debug-flow` and file output.

- `diagnose`
  - Purpose: build prompt + call LLM + return structured diagnosis.
  - Important flags: `--graph`, `--graph-type`, `--method`, `--target`, `--model`, `--llm-profile`, `--config`, `--max-chars`, `--max-tokens`, `--use-flow|--no-use-flow`, `--top-n`, `--include-source`, `--verbose-flow`, `--debug-flow`, `--show-prompt`, `--quiet`, `--output`.
  - Output shape: JSON with `method`, `analysis`, `llm` (provider/model/usage/latency/limits), `context_summary`, optional `flow_summary`, `debug`, `prompt`.

- `mcp`
  - Purpose: run MCP server over stdio.
  - Important flags: `--graph`, `--graph-type`.
  - Output shape: long-running MCP server process (no one-shot JSON output).

## 4) Current MCP Tools
- `jidra_get_method_context`
  - Inputs: `method`, optional `graph_path`, `max_chars`.
  - Output: method context dict from `build_method_context`.
  - LLM call: No.

- `jidra_get_flow`
  - Inputs: `method`, optional `graph_path`, `depth`, `top_n`.
  - Output: full stitched flow JSON (`nodes`, `edges`, `uncertain_edges`, `agent_view`, `summary`, etc.).
  - LLM call: No.

- `jidra_get_agent_flow`
  - Inputs: `method`, optional `graph_path`, `depth`, `top_n`.
  - Output: compact view (`entry`, selected `top_nodes`, `top_edges`, `uncertain_edge_summary`, `stopped_path_summary`, `summary`, `notes`).
  - LLM call: No.

- `jidra_get_method_source`
  - Inputs: `method`, optional `graph_path`.
  - Output: `method_id`, `signature`, `file_path`, `line_start`, `line_end`, `source`.
  - LLM call: No.

- `jidra_get_call_chain`
  - Inputs: `from_method`, `to_method`, optional `graph_path`, `max_depth`.
  - Output: shortest-path style chain (`from`, `to`, `found`, `path`, compact `edges`, `stopped_reason`).
  - LLM call: No.

## 5) Current Data Flow
Java repo
-> `extractor.build_graph(...)`
-> `exporter.graph_records(...)` + source split
-> `graph.jsonl` and `graph_test.jsonl`
-> loaded by `graph_io.load_graph_jsonl(...)`
-> consumed by `trace_engine` / `context_builder` / `flow_stitcher`
-> prompt text built in CLI (`_build_prompt` or `_build_flow_prompt`)
-> optional LLM call in `diagnose` via `JidraLLMClient`
-> MCP tools call `JidraEngine` methods on the same graph-backed outputs.

## 6) What Is Graph-Backed vs LLM-Generated
- Deterministic graph facts:
  - methods/classes/callsites/resolved edges loaded from JSONL
  - selector resolution, trace traversal, context extraction, flow stitching, MCP graph tools
  - method source retrieval by method id
- Heuristic labels/ranking:
  - business-only filtering in CLI
  - context noisy-call filtering + unresolved grouping
  - flow tiering (`primary/supporting/utility`) and `rank_score`
  - compact agent summaries/top-N choices
- LLM-generated:
  - only `diagnose` (`analysis` text), via LiteLLM client.

## 7) Current Strengths
- End-to-end local pipeline from Java source to graph-backed reasoning artifacts.
- Multiple interfaces over same graph: CLI + MCP.
- Deterministic references (`method_id`, signatures, file paths, line ranges) available across outputs.
- Compact MCP agent view (`top_nodes`, `top_edges`, summarized uncertainty/stops) reduces payload size.
- Configurable LLM profiles and token/latency accounting for diagnosis runs.

## 8) Current Limitations
- Static-analysis limits apply: dynamic dispatch/reflection/runtime wiring/lambdas may be unresolved or partially resolved.
- Method selector ambiguity remains possible for overload-heavy codebases.
- Flow ranking/tiering is heuristic, not semantic truth.
- MCP output quality is bounded by graph quality and resolution completeness.
- Source-aware reasoning still depends on agent behavior to call `jidra_get_method_source` at the right times.
- CLI currently has no direct `call-chain` command; call-chain is engine/MCP only.

## 9) What Claims Are Currently Safe
- JIDRA can provide graph-backed method/call references with stable IDs/signatures/file paths.
- JIDRA can fetch method-scoped source by selector (`get_method_source`).
- JIDRA can compute call-chain paths between methods from resolved graph edges (`get_call_chain`).
- JIDRA can provide a compact agent flow view (`get_agent_flow`) without full graph payload.

## 10) What Claims Are NOT Proven Yet
- Any specific token/cost reduction percentage.
- Any measured hallucination reduction percentage.
- General superiority vs Codex/Claude across tasks.
- Full semantic correctness for Java behavior in all cases.
- Guaranteed complete business-flow identification from static graph alone.

## 11) Recommended Next Steps
- Add a small regression test set for engine/MCP output schemas (especially `agent_flow` and `call_chain`).
- Add one reproducible benchmark script for prompt size and latency tracking (before/after changes).
- Decide whether to expose `call-chain` as a CLI command for parity with MCP.
