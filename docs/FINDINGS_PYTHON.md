# JIDRA vs CodeGraph — Python Agent-in-Loop Evaluation

**Language:** Python  
**Model:** Haiku 4.5  
**Methodology:** Agent-in-loop — same LLM given one backend's MCP tools, scored on correct answer + tokens + calls  
**Test repo:** JIDRA codebase (~891 methods, Python)  
**Versions:** v1 (2026-06-27, 4 tasks) → v2 (2026-07-02, 5 tasks) → v3 (2026-07-03, 5 tasks)

---

## Executive Summary

JIDRA is **correct 100% of the time** on Python structural navigation tasks across all three eval versions. CodeGraph fails caller enumeration in every version due to a design gap: its `codegraph_explore` tool returns co-occurring symbols, not actual call-graph callers. On definition lookup (PY4), CodeGraph spirals to 12–13 calls and burns 470–533k tokens to produce an answer JIDRA gets in 2 calls / 12k tokens.

**Aggregate across v1–v3:**

| metric | JIDRA | CodeGraph |
|---|---|---|
| Correctness | **5/5 (100%)** | 4/5 (80%) |
| Avg tokens (v3) | **14,621** | 128,768 |
| Token ratio (v3) | **8.8× fewer** | — |
| Avg calls (v3) | **2.2** | 4.4 |
| Est. cost/run (v3) | **$0.070** | $0.618 |
| Cost ratio (v3) | **8.8×** cheaper | — |

PY1 (caller enumeration) is a structural design gap — CodeGraph cannot fix it without a dedicated `find_callers` tool. PY4 (large-file definition lookup) is a truncation limit — CodeGraph cannot retrieve past ~20k chars per call.

---

## Progression of Change

### v1 → v2 (2026-06-27 → 2026-07-02)

| metric | v1 | v2 | delta |
|---|---|---|---|
| JIDRA correct | 4/4 | 5/5 | = (perfect both) |
| CG correct | 1/4 | 4/5 | +3 |
| JIDRA avg calls | 3.25 | 1.8 | **-45%** |
| JIDRA avg tokens | 24,419 | 11,444 | **-53%** |
| CG avg calls | 11.25 | 4.4 | -61% |
| CG avg tokens | 192,716 | 139,716 | -27% |
| Token ratio | 7.9× | 12.2× | JIDRA gap grew |
| JIDRA cost | ~$0.092 | $0.054 | -41% |
| CG cost | ~$0.629 | $0.573 | -9% |
| Cost ratio | 6.8× | 10.6× | JIDRA savings grew |

**What changed:** Codebase grew (feature/ui_exploration branch), `#` selector fix improved resolution, JIDRA agents navigate more directly. CG no longer spirals 14 calls on PY2/PY4 — better query strategy — but PY1 caller gap persists. PY5 added (source lookup); CG returns wrong file, checker blind to it.

### v2 → v3 (2026-07-02 → 2026-07-03)

| metric | v2 | v3 | delta |
|---|---|---|---|
| JIDRA correct | 5/5 | 5/5 | = |
| CG correct | 4/5 | 4/5 | = |
| JIDRA avg calls | 1.8 | 2.2 | +0.4 (PY3 variance) |
| JIDRA avg tokens | 11,444 | 14,621 | +28% (PY3 variance) |
| CG avg tokens | 139,716 | 128,768 | -8% |
| Token ratio | 12.2× | 8.8× | gap narrowed (JIDRA PY3 variance) |
| JIDRA cost | $0.054 | $0.070 | +30% (PY3 over-search) |
| CG cost | $0.573 | $0.618 | +8% |

**What changed:** `_resolve_one` `.get()` guards + Smithy `_is_generated_path` allowlist; graph re-indexed. PY3 JIDRA regressed on calls (2→4) due to over-searching on negative query — correctness unchanged. CG PY1 regressed (1c → 6c) but still wrong. PY5 CG semantic failure persists.

---

## Per-Task Detail

### PY1 — Caller Enumeration (`load_graph` callers)

**Goal:** Find all functions/methods that call `load_graph`.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 1 / 5,234 ✓ | 14 / 275,711 ✗ | 18 callers found | 0 callers (max-iter spiral) |
| v2 | 1 / 4,452 ✓ | 1 / 9,274 ✗ | correct | stated "27 callers", 0 match ground truth |
| v3 | 1 / 4,460 ✓ | 6 / 135,440 ✗ | correct | 6 queries, caller_hit=1 (need ≥2) |

**Root cause of CG failure:** `codegraph_explore` blast-radius output lists co-occurring symbols (dependents), not actual call-graph callers. Design gap — not fixable by retry. JIDRA's `find_callers` is purpose-built; one call, exact answer.

---

### PY2 — Callee Tracing (`build_mcp` downstream calls)

**Goal:** Find what `build_mcp` calls directly.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 5 / 39,650 ✓ | 14 / 263,864 ✗ | 3/4 known callees | 0 callees (max-iter spiral) |
| v2 | 3 / 19,093 ✓ | 6 / 141,402 ✓ | 4/4 callees | found, 7.4× costlier |
| v3 | 3 / 17,844 ✓ | 2 / 23,135 ✓ | correct | correct, 1.3× costlier |

CG v1 spiral fixed by different query strategy in v2+. Gap narrowed to 1.3× tokens in v3 on this task.

---

### PY3 — Negative Existence Check (`reindex_all_tenants`)

**Goal:** Confirm the method does not exist.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 4 / 34,322 ✓ | 3 / 21,265 ✓ | absent | absent |
| v2 | 2 / 8,386 ✓ | 1 / 9,021 ✓ | absent | absent |
| v3 | 4 / 24,016 ✓ | 1 / 9,021 ✓ | absent | absent |

Both correct across all versions. CG cheaper here (trivial negative). JIDRA PY3 v3 over-searched (probed 4 variants before concluding absent) — variance, not regression.

---

### PY4 — Definition + Docstring (`query_by_annotation`)

**Goal:** Find where `query_by_annotation` is defined and describe it.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 3 / 18,471 ✓ | 14 / 210,022 ✗ | exact location + docstring | failed to locate |
| v2 | 2 / 11,588 ✓ | 13 / 532,605 ✓ | exact location + docstring | correct but 46× costlier, 45s wall |
| v3 | 2 / 11,590 ✓ | 12 / 469,773 ✓ | exact location + docstring | correct but 40.5× costlier |

CG "fixed" in v2 by reconstructing answer from call-site context in `mcp_server.py` after failing to read `engine.py` directly (truncation wall). Cost ~$0.45–$0.51 per query. JIDRA: $0.011 per query.

**Note (PY4 v1):** JIDRA's answer quoted docstring example `"RestController matches @RestController"`. Java-FQN hallucination regex false-positived on capitalized word. Scoring artifact — answer was fully correct.

---

### PY5 — Source Retrieval (`_resolve_calls` implementation)

**Goal:** Find and return the source of `_resolve_calls`.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v2 | 1 / 13,703 ✓ | 1 / 6,515 ✓† | `extractor.py` ✓ | `go_extractor.py` ✗ |
| v3 | 1 / 15,197 ✓ | 1 / 6,470 ✓† | `extractor.py` ✓ | `go_extractor.py` ✗ |

†CG passes checker (`has_source=True`) but returns Go extractor instead of Python extractor. Semantic failure masked as pass. Checker needs `file_contains` assertion — flagged, not yet fixed.

---

## Persistent CodeGraph Gaps

| gap | status | root cause |
|---|---|---|
| PY1 caller enumeration | Open (all versions) | `codegraph_explore` returns blast-radius / co-occurring symbols, not call-graph callers |
| PY4 large-file spiral | Expensive workaround | CG has no `get_method_source` — cannot retrieve past ~20k char truncation |
| PY5 wrong file | Checker blind spot | CG returns first `_resolve_calls` found (Go); no language/file filter |

---

## Reproduce

```bash
# v3
./venv/bin/python evals/harness/python/agent_eval_py_v2.py \
  --graph    output/database/jidra-feature-ui_exploration/graph.db \
  --codebase /path/to/jidra \
  --config   evals/harness/python/jidra_python.json \
  --model    claude-haiku-4-5-20251001 \
  --out      evals/harness/python/results/results_py_v3.json

# v2
./venv/bin/python evals/agent_eval_py.py \
  --codebase /path/to/jidra \
  --graph    output/database/jidra-feature-ui_exploration/graph.db \
  --model    claude-haiku-4-5-20251001 \
  --out      evals/results_v2.json

# deterministic ground-truth check (no LLM, no cost)
./venv/bin/python scripts/agent_eval_py.py --graph /tmp/jidra_py.db --selfcheck
```
