# JIDRA vs CodeGraph — Python Evaluation v2

**Model:** Haiku 4.5 (`--model claude-haiku-4-5-20251001`)
**Graph:** `output/database/jidra-feature-ui_exploration/graph.db`
**Codebase:** JIDRA itself (feature/ui_exploration branch)
**Date:** 2026-07-02

---

## TL;DR

- **JIDRA 5/5** — perfect. Avg 1.8 calls, 11,444 tokens.
- **CG 4/5** — PY1 fails (cited 27 callers, 0 from ground truth). PY4 barely passes at 13c/533k.
- **Token ratio: 12.2×** JIDRA fewer. Up from 7.9× in v1.
- PY5 new task: JIDRA 1c correct. CG 1c but returned wrong file (`go_extractor.py` instead of `extractor.py`) — checker passes but answer is semantically wrong.
- JIDRA improved across the board vs v1: fewer calls, fewer tokens, same or better accuracy.

---

## Per-task results

| task | JIDRA calls/tok/wall | CG calls/tok/wall | winner |
|---|---|---|---|
| PY1 callers of `load_graph` | 1 / 4,452 / 4.6s ✓ | 1 / 9,274 / 6.0s ✗ | **JIDRA** |
| PY2 callees of `build_mcp` | 3 / 19,093 / 13.2s ✓ | 6 / 141,402 / 19.7s ✓ | **JIDRA** (7.4× tokens) |
| PY3 negative (`reindex_all_tenants`) | 2 / 8,386 / 7.4s ✓ | 1 / 8,783 / 5.0s ✓ | tie |
| PY4 `query_by_annotation` definition | 2 / 11,588 / 8.4s ✓ | 13 / 532,605 / 45.4s ✓ | **JIDRA** (46× tokens) |
| PY5 `_resolve_calls` implementation | 1 / 13,703 / 10.3s ✓ | 1 / 6,515 / 7.3s ✓† | tie (checker), **JIDRA** (semantic) |

†CG PY5: returned `go_extractor.py#_resolve_calls` — wrong file. Checker only validates `has_source=True`, doesn't verify which `_resolve_calls`. Semantic failure.

**Summary:**
```
              correct  avg_calls  avg_tokens   total_tokens   avg_wall
jidra          5/5       1.8        11,444        57,222        8.8s
codegraph      4/5       4.4       139,716       698,579       16.7s
token ratio    12.2×
```

---

## Task analysis

**PY1 — CG fails caller enumeration.**
JIDRA: `find_callers("load_graph")` → 1 call, found `load_nodes` (correct, passes ≥2 ground truth). CG: 1c/9k, stated "27 callers" but none matched ground truth set — blast-radius output doesn't enumerate actual call-graph callers, just co-occurring symbols.

**PY2 — CG passes but 7.4× costlier.**
JIDRA: 3c/19k, `explore` + `get_method_source` + `get_agent_flow` → found all 4 direct callees. CG: 6c/141k, scrolled through source in 6 blasts, eventually reconstructed the call list. Both correct but CG needed to manually page through the large function.

**PY3 — tie.**
Both backends correctly identify `reindex_all_tenants` as absent in 1-2 calls. CG slightly cheaper (1c/9k vs 2c/8k).

**PY4 — CG 13c/533k for a definition lookup.**
JIDRA: 2c/12k — `explore` found the method_id, `get_method_source` returned source + docstring. Done. CG: hit max exploration iterations (13), burned 533k tokens trying to scroll past a truncation point in the large `engine.py`. Eventually reconstructed the answer from call-site context in `mcp_server.py`. Correct but 46× more expensive. Wall time: 45.4s vs 8.4s.

**PY5 (new) — `_resolve_calls` source.**
JIDRA: 1c/14k, `get_method_source("_resolve_calls")` → returned `extractor.py` implementation directly. CG: 1c/7k — fast but returned `go_extractor.py#_resolve_calls` (wrong file, wrong language). The checker only validates source was returned, not which file. CG wins on token count; JIDRA wins on correctness.

---

## Checker limitation noted

PY5 CG passes the checker (`has_source=True`) despite returning the wrong `_resolve_calls` (Go extractor vs Python extractor). Checker needs a `file_contains` assertion to catch this. Not fixed here — flagging for next harness version.

---

## Reproduce

```bash
./venv/bin/python evals/agent_eval_py.py \
  --codebase /path/to/jidra \
  --graph    output/database/jidra-feature-ui_exploration/graph.db \
  --model    claude-haiku-4-5-20251001 \
  --out      evals/results_v2.json
```
