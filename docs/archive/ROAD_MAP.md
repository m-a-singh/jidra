# JIDRA Roadmap (Updated - Multi-Language Support)

## Overview

**JIDRA = Java/Scala/TypeScript/Python Integrated Graph Reduction & Analysis**

JIDRA is an Enterprise Multi-Language Context Backend for LLM workflows. It transforms raw source code into structured, validated, noise-free context that reduces LLM token costs by 68-95% (depending on language) while maintaining 100% business logic coverage. Supports **Scala** (90% resolution), **Java** (85% resolution), **TypeScript** (80% resolution), **Python** (68.5% resolution), and **Go** (best-effort, not yet benchmarked).

**See [PIVOT_RATIONALE.md](./PIVOT_RATIONALE.md) for the complete strategic pivot from "Multi-Service Agent" to "Context Backend."**

---

## Product Direction

### What JIDRA Is NOT
- ❌ An autonomous agent (Claude/Codex already are)
- ❌ A multi-service distributed logic gateway (requires service mesh)
- ❌ A replacement for IDE exploration (navigational aid)
- ❌ A full semantic Java analyzer (AST + runtime validation is best-effort)

### What JIDRA IS
- ✅ A structured context backend for LLM workflows
- ✅ A graph-validated, noise-reduced code understanding layer
- ✅ 68-95% token reduction (measured, proven across languages)
- ✅ 100% business logic coverage (0% false negatives)
- ✅ Universal LLM compatibility (Claude, Codex, Gemini)
- ✅ Multi-language: Scala (~90%), Java (~85%), TypeScript (~80%), Python (~68.5%), Go (best-effort)

### Current Product Shape (v1.0 - READY)
```text
Java repo   → tree-sitter AST → Spring Actuator validation → 71-78% phantom removal → 85% resolution
Scala repo  → SemanticDB (sbt compile) → compiler-resolved edges → ~90% resolution
TS repo     → ts-morph Docker sidecar → static analysis → ~80% resolution
Python repo → AST + symbol table → Pyright validation → ~68.5% resolution
Go repo     → tree-sitter AST (in-process) → local symbol-table call resolution → best-effort (no interface-satisfaction resolution)

All languages:
  → Graph merge (multi-language repos handled automatically)
  → Context generation (68-95% smaller)
  → Structured JSON output
  → MCP tools for Claude/Codex
  → 68-95% cost reduction
```

### Future Product Shape (v2.0+)
```text
Java repo
  → Everything above, PLUS:
  → Framework config parsing (YAML/JSON)
  → Error trace analysis
  → Multi-service basics (service registry, API contracts)
```

### Guiding Principle

> **Be the best context provider, not the best agent.**

That means:
- ✅ Deterministic graph data (not guesses)
- ✅ Runtime validation (not static analysis alone)
- ✅ Noise removal (71-78% fewer phantom edges)
- ✅ Structured JSON (not prose)
- ✅ Transparent uncertainty (mark what we don't know)
- ✅ Let Claude do the reasoning (we do the context)

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

## Phases: What's Done & What's Next

### COMPLETED (Phases 0-10 + Spring Actuator) ✅

**Phase 0: AST Graph Extraction** ✅
- Tree-sitter based Java parsing
- Classes, methods, fields, callsites extracted
- JSONL graph export (graph.jsonl, graph_test.jsonl)
- CLI: `jidra index`

**Phase 1-9: CLI + Engine + MCP** ✅
- Stable CLI (trace, context, flow, prompt, diagnose)
- JSON-first engine (JidraEngine)
- MCP server with 5 tools
- Multi-provider LLM support
- Token/latency observability

**Phase 10: MCP Server** ✅
- `jidra_get_method_context`
- `jidra_get_flow`
- `jidra_get_agent_flow`
- `jidra_get_method_source`
- `jidra_get_call_chain`

**BONUS: Spring Actuator Validation** ✅ (NEW)
- Docker lifecycle automation
- 411 bean extraction (on Spring Petclinic)
- 71-78% phantom edge removal
- Multi-module Gradle support
- Maven fallback for reliability
- Interactive visualization

**Scala Support** ✅ (NEW)
- SemanticDB two-pass extraction (compiler-resolved call edges)
- Scala 2 + Scala 3 auto-detection
- Docker sidecar mirroring [REDACTED] Artifactory config
- ~90% call resolution — highest of any JIDRA language
- Multi-language merge: Scala + TypeScript + Python in one graph
- Manifest-only language detection (no false positives from node_modules)

---

### NOT DOING (Pivot Away From)

**Phase 11: Error-First Diagnostics** ⏳
- **Reason skipped:** Out of scope for v1. Single-shot, not agent loops.
- **Future:** v2.0+ may add (requires interactive session management)

**Phase 12: Deep Java Semantics** ⏳
- **Reason scoped:** AST + Actuator validation handles 90% of cases
- **Future:** v2.0+ if semantic correctness becomes bottleneck

**Phase 13: Repo Logic Awareness** ⏳
- **Reason deferred:** YAML/JSON parsing is nice-to-have
- **Future:** v2.0 (Phase 16 in new roadmap)

**Phase 14: Multi-Service Logic Gateway** ❌
- **Reason eliminated:** Fundamentally different problem
- **Why:** Multi-service requires service mesh integration, not code analysis
- **Better approach:** Service contracts + API parsing (separate product)

---

### NEW ROADMAP (v1.0 → v2.0)

#### v1.0 - COMPLETE ✅ (PRODUCTION READY)
- ✅ Graph extraction + validation
- ✅ 87-95% token reduction (proven)
- ✅ 0% false negatives (validated)
- ✅ MCP integration
- ✅ Docker + Actuator automation

#### v1.1 - Polish (Next)
- ⏳ Error trace parser (stack trace → root method)
- ⏳ Regression tests (MCP/engine schemas)
- ⏳ Cost/ROI calculator
- ⏳ Deployment guide
- ⏳ Large codebase testing (10k+ classes)

#### v2.0 - Enhancement (Future)
- ⏳ **Phase 16:** YAML/JSON parsing for Spring config
- ⏳ **Phase 15:** Error-first diagnostics (interactive)
- ⏳ **Phase 17:** Multi-service basics (service registry)

---

## One-Line Product Thesis

**JIDRA is the structured context backend for Enterprise polyglot codebases (Scala, Java, TypeScript, Python, Go) that reduces LLM token costs by 68-95% while maintaining 100% business logic coverage and zero false negatives.**

Competitors: None (no other tool combines static analysis + runtime validation + LLM optimization)