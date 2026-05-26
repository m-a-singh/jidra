# PROJECT_STATUS

## 1) What currently works
- Static graph extraction from Java source into JSONL (`graph.jsonl`, `graph_test.jsonl`).
- Best-effort call-chain resolution over resolved graph edges (`jidra_get_call_chain`).
- Method-to-file and line mapping (`method_id`, `signature`, `file_path`, `line_start`, `line_end`) and source lookup (`jidra_get_method_source`).
- MCP tools are functional:
  - `jidra_get_agent_flow`
  - `jidra_get_call_chain`
  - `jidra_get_method_source`

These provide structural visibility into code paths and relationships based on static analysis.

---

## 2) Current limitations
- Important calls can still be under-surfaced or require manual follow-up (e.g., collaborator calls like `templateProcessor.process`).
- Static graph does not capture runtime behavior (dynamic dispatch, config, experiments, async flows).
- Ranking signals (including path entropy) help but are not sufficient to consistently highlight what matters most.
- Deeper reasoning about filtering, ranking, and containerization still requires reading downstream code.
- Does not replace full repo exploration for complex debugging scenarios.

---

## 3) Experiments performed

### Tooling / Setup Observations

- Codex:
  - Baseline:
    - ~47k context tokens used
    - ~8 files opened during exploration
    - ~15 methods inspected
    - Broad, more complete answer
  - JIDRA:
    - ~33k context tokens used (~30% reduction from baseline)
    - 0 non-JIDRA files opened
    - ~5 method sources fetched
    - Narrower answer (missed deeper downstream logic)
  - Conclusion:
    - JIDRA reduces context and exploration
    - Does not outperform Codex on completeness

- Claude:
  - Baseline:
    - ~66k context tokens used
    - Broad repo exploration
    - Reasonably complete answer
  - JIDRA (after correct MCP setup):
    - ~41k context tokens used (~38% reduction from baseline)
    - MCP tools used (agent_flow + method_source)
    - More structured but stopped early (did not go beyond cache layer)
  - Conclusion:
    - JIDRA significantly reduces context
    - Improves structure and reduces drift
    - Still requires guidance for deeper traversal

Observation:
- JIDRA provides measurable context reduction (~30–38%)
- JIDRA benefits Claude more than Codex
- MCP setup correctness directly impacts results

## 4) Key takeaways
- Static structure helps constrain exploration but does not explain behavior.
- Surfacing uncertainty (unresolved calls) is important.
- JIDRA is currently strongest as:
  - a navigation layer
  - a context reduction tool
- It is not yet:
  - a full reasoning system
  - a root-cause analysis tool

---

## 5) Current direction (pivot)
Shift from:
- “complete flow understanding”

To:
- “uncertainty-aware navigation and context pruning”

Meaning:
- highlight resolved paths
- expose unresolved but important calls
- guide what to inspect next
- reduce unnecessary code reads

---

## 6) Improvements made
- `important_unresolved_calls` includes field-level collaborators.
- Noise filtering reduces low-value calls.
- `receiver_type` is exposed for better context.
- Initial `possible_targets` support added.

---

## 7) Known gaps
- `possible_targets` resolution needs better type normalization.
- No modeling of runtime behavior.
- Depth guidance still depends on agent decisions.
- Ranking remains heuristic.

---

## 8) Status
Current baseline validated.

This project has reached a solid, portfolio-ready baseline:
- deterministic static graph extraction
- uncertainty-aware navigation
- measurable context reduction in LLM workflows

### What the next phase would require
To reliably answer deeper “why did this happen at runtime?” debugging questions, the next step is not incremental heuristics.
It would require adding **runtime and/or semantic modeling**, e.g.:
- dynamic dispatch / polymorphism resolution beyond static types
- framework/runtime semantics (Spring wiring, DI, config, annotations)
- async/reactive execution modeling
- experiment/feature-flag/config-driven branches

That scope is a distinct phase of work with different techniques and data sources.

---

## 9) Summary
JIDRA provides structured, graph-based navigation and reduces context needed to explore code.

It improves how agents and developers move through large codebases, but does not replace full reasoning or debugging workflows.

- Added deterministic recursive flow-doc generation to produce reusable debugging documents from controller/method flows.
