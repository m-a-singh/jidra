# Python Eval: v1 → v2 Delta

**v1:** JIDRA codebase, ~891 methods, 4 tasks (PY1-PY4), date 2026-06-27
**v2:** JIDRA feature/ui_exploration branch, 5 tasks (PY1-PY5), date 2026-07-02

---

## Score

| metric | v1 | v2 | delta |
|---|---|---|---|
| JIDRA correct | 4/4 | 5/5 | = (perfect both) |
| CG correct | 1/4 | 4/5 | **+3** (CG improved) |
| JIDRA avg calls | 3.25 | 1.8 | **-45%** |
| JIDRA avg tokens | 24,419 | 11,444 | **-53%** |
| CG avg calls | 11.25 | 4.4 | **-61%** |
| CG avg tokens | 192,716 | 139,716 | **-27%** |
| Token ratio | 7.9× | 12.2× | JIDRA relatively further ahead |
| **JIDRA cost** | ~$0.092† | **$0.054** | **-41%** |
| **CG cost** | ~$0.629† | **$0.573** | -9% |
| **Cost ratio** | 6.8× | **10.6×** | JIDRA savings grew |

†v1 cost estimated from avg-token totals (no raw JSON); v2 exact from API telemetry. Haiku 4.5: $0.80/MTok in, $4.00/MTok out.

---

## Per-task comparison (PY1-PY4 overlap)

| task | v1 JIDRA | v2 JIDRA | v1 CG | v2 CG |
|---|---|---|---|---|
| PY1 callers | ✓ 1c/5k | ✓ 1c/4k | ✗ **14c/276k** | ✗ 1c/9k |
| PY2 callees | ✓ 5c/40k | ✓ **3c/19k** | ✗ **14c/264k** | ✓ 6c/141k |
| PY3 negative | ✓ 4c/34k | ✓ **2c/8k** | ✓ 3c/21k | ✓ 1c/9k |
| PY4 definition | ✓ 3c/18k | ✓ **2c/12k** | ✗ **14c/210k** | ✓ 13c/533k |

---

## Key movements

**CG: no more 14-call spirals on PY2/PY4.**
v1 CG hit the 14-call max on 3 of 4 tasks. v2 CG doesn't spiral on PY2 (6c) or PY4 (13c). Why: the codebase grew (more code indexed), so `codegraph_explore` returns different truncation points; and the agent adapted query strategy. But PY4 is still 13c/533k — expensive and slow (45s).

**PY1 CG: still fails, differently.**
v1: 14c/276k spiral → returned nothing. v2: 1c/9k, stated "27 callers" but 0 from ground truth. CG blast-radius output lists dependent symbols, not actual call-graph callers. Fundamental design gap — not a run-variance issue.

**JIDRA: halved tokens and calls across the board.**
Every JIDRA task is faster and cheaper in v2. Graph improvements (selector fix, better resolution) mean agents navigate more directly.

**PY5 CG: wrong file, passes checker.**
New task exposed a checker blind spot: CG returned `go_extractor.py#_resolve_calls` instead of `extractor.py#_resolve_calls`. Checker only validates source returned. Semantic failure masked as pass.

---

## What changed between v1 and v2

1. **Codebase grew** — feature/ui_exploration branch has more files (resources indexer, doc indexer additions). More indexed methods means better graph connectivity and explore precision.
2. **`#` selector fix** (v11 Java eval) — applies to Python too. Agents using `Class#method` Python notation now resolve directly instead of falling to FTS.
3. **Fix D** (generated column) — minimal Python impact, generated paths not relevant for Python JIDRA source.

---

## Persistent gaps

| gap | v1 | v2 | trend |
|---|---|---|---|
| PY1 CG caller enumeration | ✗ 14c spiral | ✗ 1c wrong | design limit — blast-radius ≠ call graph |
| PY4 CG large file | ✗ 14c/210k spiral | ✓ 13c/533k expensive | "fixed" but fragile and costly |
| PY5 CG wrong file | — | ✓ (checker)/✗ (semantic) | new gap discovered |
