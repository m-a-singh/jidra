# JIDRA vs CodeGraph — Python Evaluation v3

**Changes:** `_resolve_one` `.get()` guards + Smithy `_is_generated_path` allowlist; graph re-indexed  
**Model:** Haiku 4.5 (`--model claude-haiku-4-5-20251001`)  
**Graph:** `output/database/jidra-feature-ui_exploration/graph.db`  
**Codebase:** JIDRA itself (feature/ui_exploration branch)  
**Date:** 2026-07-03

---

## TL;DR

- **JIDRA 5/5** — unchanged. Avg 2.2 calls (up from 1.8), 14,621 tokens (up from 11,444). PY3 over-searched this run.
- **CG 4/5** — PY1 still fails. PY4 still spirals (12c/470k).
- **Token ratio: 8.8×** JIDRA fewer — down from 12.2×. JIDRA PY3 variance (+2 calls); CG PY2 improved significantly.
- PY5 CG semantic failure persists: returned `go_extractor.py#_resolve_calls` again.

---

## Per-task results

| task | JIDRA calls/tok/cost | CG calls/tok/cost | token ratio | winner |
|---|---|---|---|---|
| PY1 callers of `load_graph` | 1 / 4,460 / $0.0043 ✓ | 6 / 135,440 / $0.1300 ✗ | JIDRA 30× | **JIDRA** |
| PY2 callees of `build_mcp` | 3 / 17,844 / $0.0171 ✓ | 2 / 23,135 / $0.0222 ✓ | JIDRA 1.3× | **JIDRA** |
| PY3 negative (`reindex_all_tenants`) | 4 / 24,016 / $0.0231 ✓ | 1 / 9,021 / $0.0087 ✓ | CG 2.7× | **CG** |
| PY4 `query_by_annotation` definition | 2 / 11,590 / $0.0111 ✓ | 12 / 469,773 / $0.4510 ✓ | JIDRA 40.5× | **JIDRA** |
| PY5 `_resolve_calls` source | 1 / 15,197 / $0.0146 ✓ | 1 / 6,470 / $0.0062 ✓† | CG 2.3× | **JIDRA** (semantic) |

†CG PY5: returned `go_extractor.py#_resolve_calls` (Go extractor). Same semantic failure as v2.

**Summary:**
```
              correct  avg_calls  avg_tokens   total_cost   halluc
jidra          5/5       2.2        14,621       $0.0702       0/5
codegraph      4/5       4.4       128,768       $0.6181       0/5
token ratio    8.8×
```

**Estimated cost (Haiku 4.5: $0.80/MTok in, $4.00/MTok out):**
```
              total_tokens   est_cost
jidra           73,105       ~$0.069
codegraph      643,839       ~$0.526
cost ratio     7.6×
```

---

## Task analysis

**PY1 — CG regressed: 1c → 6c, still wrong.**  
CG spiraled through 6 queries, caller_hit=1 (need ≥2). JIDRA stable 1c/4.5k. Structural gap unchanged: `find_callers` gives exact call-graph data; CG blast-radius lists co-occurring symbols.

**PY2 — CG improved: 6c/141k → 2c/23k.**  
Agent found all 4 callees in 2 blasts this run instead of 6. Pure variance — same backend. JIDRA stable at 3c/18k. Gap narrowed to 1.3× tokens this run.

**PY3 — JIDRA regressed: 2c/8k → 4c/24k.**  
Agent probed four query variants (reindex_all_tenants → reindex tenant → reindex → all_tenants) before concluding absent. v2 needed only 2. CG stable at 1c/9k. CG wins PY3 this run.

**PY4 — CG 13c/533k → 12c/470k (still spiraling).**  
One fewer call, slightly fewer tokens. Same truncation wall in `engine.py`. JIDRA stable at 2c/12k.

**PY5 — stable; CG semantic failure persists.**  
JIDRA 1c/15k → correct `extractor.py`. CG 1c/6.5k → `go_extractor.py` again. Checker blind to this.

---

## Open issues

| # | issue | status |
|---|---|---|
| 1 | PY5 CG returns `go_extractor.py` (wrong file); checker `has_source` doesn't verify file | Open — needs `file_contains` assertion |
| 2 | PY4 CG truncation spiral in `engine.py` | Open (CG design limit) |
| 3 | PY1 CG blast-radius / caller gap | Open (CG design limit) |
| 4 | PY3 JIDRA over-search variance (2c→4c on negative tasks) | Low priority — correct either way |

---

## Reproduce

```bash
./venv/bin/python evals/harness/python/agent_eval_py_v2.py \
  --graph    output/database/jidra-feature-ui_exploration/graph.db \
  --codebase /Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra \
  --config   evals/harness/python/jidra_python.json \
  --model    claude-haiku-4-5-20251001 \
  --out      evals/harness/python/results/results_py_v3.json
```
