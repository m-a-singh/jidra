# CURRENT_STATE

## 1) What JIDRA Is Today

**JIDRA is an Enterprise Java Context Backend for LLM Workflows.**

Core function: Extract a static Java call graph, validate it with Spring Actuator, reduce context by 87-95% while maintaining 100% business logic coverage, then expose that structured context to LLMs (Claude, Codex, Gemini).

JIDRA is a Python CLI + MCP server that builds a static Java graph (classes, methods, callsites, resolved call edges), then exposes graph-backed operations for trace, context, stitched flow, prompt construction, and optional LiteLLM-based diagnosis. 

**New capability:** Spring Actuator validation removes 71-78% phantom edges, ensuring the graph reflects real runtime beans, not just static code analysis artifacts.

It is mostly deterministic up to graph/context/flow outputs; Spring Actuator validation adds runtime ground-truth; only `diagnose` and error-analysis add LLM-generated reasoning.

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

## 9) What Claims Are NOW PROVEN ✅

**Token Reduction (Real Claude API Testing)**
- Search-service: 95.9% reduction (10,811 → 869 input tokens)
- Spring Petclinic: 87.4% average (2,736-5,304 → 320-383 input tokens)
- Consistency: 85-96% range across diverse projects

**Business Logic Coverage**
- 100% of business logic present in validated graph
- 0% false negatives (manual code tracing verification)
- 71-78% phantom edges safely removed (Spring Actuator validation)

**Multi-Project Validation**
- Proprietary codebase: search-service (complex, 768 classes)
- Public codebase: Spring Petclinic (simple, 25 classes)
- Both show consistent token reduction with zero false negatives

**Production Readiness**
- Docker + Spring Actuator automation (one-command pipeline)
- Multi-module Gradle + Maven support
- Interactive visualization with 3 export formats
- Validation reports with metrics

## 10) What Claims Are NOT Proven Yet
- Measured hallucination reduction percentage (qualitative evidence: less noise = better reasoning)
- General superiority vs Codex/Claude across tasks (we're a tool FOR them, not replacement)
- Full semantic correctness for Java behavior in all cases (AST + Actuator validates beans, not all runtime behavior)
- Multi-service distributed reasoning (out of scope for v1, requires service mesh integration)

## 11) What We're NOT Doing (v1 Scope)

❌ **Autonomous agent loops** - Claude already does this better; we provide context
❌ **Multi-service distributed reasoning** - Requires service mesh, not code analysis
❌ **Real-time error diagnostics** - Would need interactive loops (Phase 15 future work)
❌ **Framework-specific config parsing** - Can add YAML/JSON support later (Phase 16)
❌ **Full semantic correctness** - AST + Actuator validation is best-effort for single service

## 12) Recommended Next Steps (Priority Order)

### Immediate (v1.0 - Production)
- ✅ Document pivot rationale (PIVOT_RATIONALE.md)
- ⏳ Update ROAD_MAP.md with new vision
- ⏳ Create deployment guide (Docker + Actuator automation)
- ⏳ Add regression tests for MCP/engine schemas
- ⏳ Create cost/ROI calculator

### Short-term (v1.1 - Polish)
- Error trace parser (stack trace → root method)
- Production hardening (timeout, error handling)
- Large codebase testing (10k+ class projects)
- Cost analytics dashboard

### Medium-term (v2.0 - Enhancement)
- YAML/JSON parsing for framework config
- Error-first diagnostics with interactive loops
- Multi-service basics (service registry, API contracts)
