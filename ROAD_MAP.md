# JIDRA Roadmap

## Overview
JIDRA (Java Intelligent Diagnostic & Reasoning Agent) is evolving from a Java code intelligence prototype into a graph-grounded reasoning backend for Enterprise Java.

The long-term goal is not just a debugger or another coding tool. JIDRA should become the reasoning layer that helps coding agents understand distributed Java logic with less hallucination, less token waste, and higher trust.

---

## Product Direction

### Current Product Shape
JIDRA currently works as a CLI-driven diagnostic tool:

```text
Java repo → graph → trace → context → prompt → diagnose
```

### Target Product Shape
JIDRA should evolve into an agent-ready reasoning backend:

```text
Java repo
  → graph data
  → stitched business flow
  → structured diagnostic context
  → MCP/API tools
  → Claude / Codex / Gemini / other agents
```

### Guiding Principle

> Build trust first, then intelligence.

That means:
- deterministic graph data before LLM reasoning
- structured JSON before prose summaries
- uncertainty labels instead of false confidence
- clean business flow before multi-agent orchestration

---

## Phase 0: AST Graph Extraction (Completed)

### Goal
Build a deterministic representation of Java codebases.

### Achievements
- Tree-sitter based Java parsing
- Extraction of:
  - classes
  - methods
  - fields
  - call sites
- Basic call resolution heuristics
- JSONL graph export
- Main/test graph split:
  - `graph.jsonl`
  - `graph_test.jsonl`
- CLI support:
  - `jidra index`

### Outcome
A static, queryable graph of the Java codebase.

---

## Phase 1: CLI Stabilization (Completed)

### Goal
Create a small, stable CLI foundation.

### Achievements
- `jidra index`
- `jidra trace`
- `jidra context`
- `jidra prompt`
- `jidra diagnose`
- Method selector resolution
- `--graph-type main|test`
- `--output` support
- Safe generated filenames

### Outcome
A usable command-line surface for graph-backed Java reasoning.

---

## Phase 2: Signal Quality Improvements (Completed)

### Goal
Improve graph honesty and reduce context noise.

### Achievements
- Introduced `SymbolTable` for receiver/type lookup
- Removed unsafe global name/arity `resolved_exact` assumptions
- Added safer statuses such as:
  - candidate global match
  - ambiguous global match
  - unresolved receiver
- Context filtering:
  - logging/metrics/infrastructure noise filtering
  - lambda-local unresolved noise filtering
  - fluent-chain grouping
  - resolved callee deduplication
- Added `--business-only`

### Outcome
JIDRA now produces higher-signal context suitable for LLM reasoning.

---

## Phase 3: Prompt Generation (Completed)

### Goal
Convert graph context into model-agnostic LLM input.

### Achievements
- `jidra prompt`
- Target modes:
  - generic
  - codex
  - claude
- Prompt sections:
  - task
  - entry method
  - business flow
  - method source
  - unresolved / uncertain calls
  - context notes
- Generic guidance for infrastructure/logging/metrics without repo-specific assumptions

### Outcome
Reusable prompts for external coding agents and LLMs.

---

## Phase 4: LLM Diagnosis (Completed)

### Goal
Run reasoning over graph-grounded context.

### Achievements
- `jidra diagnose`
- LLM-backed diagnosis
- Business-only diagnosis mode
- JSON output support
- Output to terminal or file
- Context summary included in results

### Outcome
End-to-end path from Java method to graph-grounded LLM diagnosis.

---

## Phase 5: Multi-Provider LLM Architecture (Completed)

### Goal
Make LLM integration configurable and provider-agnostic.

### Achievements
- JIDRA-owned `llm_client.py`
- `config.yaml` based model/profile configuration
- Support for:
  - local LiteLLM
  - enterprise LiteLLM
  - future OpenAI / Claude / Gemini direct adapters
- No direct LLM provider logic in CLI

### Outcome
JIDRA can run against different LLM environments without rewriting CLI code.

---

## Phase 6: UX & Observability (Completed)

### Goal
Improve usability, transparency, and cost visibility.

### Achievements
- ANSI terminal output for diagnosis
- Structured JSON output
- Token metrics:
  - input tokens
  - output tokens
  - total tokens
  - reasoning tokens if available
- Latency metrics
- Configurable limits:
  - `--max-chars`
  - `--max-tokens`

### Outcome
Developer-friendly output plus visibility into model cost and performance.

---

## Phase 7: Flow Stitcher (Next)

### Goal
Move from single-method context to end-to-end business flow.

### Why This Matters
Single-method context is useful, but agents need execution paths. The flow stitcher should recursively follow business-relevant resolved callees and produce a structured flow graph that agents can traverse.

### Planned
- New module:
  - `flow_stitcher.py`
- New command:
  - `jidra flow --method <selector>`
- Recursive traversal through business calls
- Depth control:
  - `--depth N`
- Cycle detection
- Uncertainty tracking
- Stop reasons:
  - max depth
  - no business callees
  - unresolved edge
  - cycle detected
- JSON-first output:
  - nodes
  - edges
  - uncertain edges
  - stopped paths

### Outcome
A stitched business-flow graph that becomes the backbone for prompt, diagnose, and future MCP tools.

---

## Phase 8: Flow-Aware Prompt and Diagnosis (Next)

### Goal
Use stitched flow instead of only local method context.

### Planned
- Add `--use-flow` to:
  - `jidra prompt`
  - `jidra diagnose`
- Include multi-method context from stitched flow
- Rank methods by importance
- Trim context by token/character budget
- Include uncertainty and stop reasons in prompts

### Outcome
LLMs reason over the real execution path instead of isolated method snippets.

---

## Phase 9: JSON-First Reasoning Backend (Next)

### Goal
Stop treating text as the primary product. Make graph data the core interface.

### Planned
Create a reusable engine layer:

```python
JidraEngine.get_method(...)
JidraEngine.get_context(...)
JidraEngine.get_flow(...)
JidraEngine.diagnose(...)
```

Return structured JSON for:
- method context
- stitched flow
- uncertain edges
- diagnosis
- next methods to inspect

### Outcome
JIDRA becomes callable infrastructure, not just a CLI.

---

## Phase 10: MCP Server (Future / Important)

### Goal
Expose JIDRA as native tools for coding agents.

### Planned MCP tools
- `jidra_get_method_context`
- `jidra_get_business_flow`
- `jidra_get_stitched_flow`
- `jidra_get_uncertain_edges`
- `jidra_diagnose_method`
- `jidra_search_methods`

### Target Consumers
- Claude
- Codex
- Gemini-based agents
- Windsurf
- RooCode
- internal agent frameworks

### Outcome
JIDRA becomes a reasoning backend that agents can call directly.

---

## Phase 11: Error-First Diagnostics (Future)

### Goal
Diagnose from errors, not just methods.

### Planned
- Stack trace parser
- JUnit / Maven / Gradle failure parser
- Error-to-method mapping
- Error-to-flow mapping
- `jidra diagnose --error <file>`

### Outcome
A user can provide an error and receive the likely flow, root-cause hypotheses, and next methods to inspect.

---

## Phase 12: Deep Java Semantics (Future)

### Goal
Improve correctness beyond AST heuristics.

### Planned
- JavaParser / Symbol Solver integration
- Eclipse JDT exploration
- Better overload resolution
- Better interface/implementation mapping
- Framework awareness:
  - Spring
  - Smithy / Smithy4j
  - generated code
  - event and queue handlers

### Outcome
Higher trust graph and fewer ambiguous edges.

---

## Phase 13: Repository Logic Awareness (Future)

### Goal
Understand more than `.java` files.

### Planned
Index and connect:
- YAML
- JSON
- properties files
- shell scripts
- Mustache/templates
- Smithy models
- generated-code metadata

### Outcome
JIDRA understands configuration-driven and generated behavior, not just source-visible Java calls.

---

## Phase 14: Multi-Service Logic Gateway (Future Vision)

### Goal
Extend from single-service Java reasoning to distributed application logic.

### Planned
- Cross-service call edges
- API/client mapping
- queue/event flow mapping
- service ownership metadata
- runtime telemetry integration
- multi-service stitched flow

### Outcome
JIDRA becomes a Multi-Service Logic Gateway: a structured reasoning layer over distributed Enterprise Java systems.

---

## Current State

JIDRA is currently:

- graph-backed
- CLI-stable
- prompt-capable
- LLM-integrated
- provider-configurable
- token/latency observable

Next immediate focus:

1. Flow stitcher
2. Flow-aware prompt/diagnose
3. JSON-first engine layer
4. MCP server

---

## One-Line Product Thesis

JIDRA is a graph-grounded reasoning backend for Enterprise Java that makes coding agents more reliable by giving them structured execution logic instead of raw files.