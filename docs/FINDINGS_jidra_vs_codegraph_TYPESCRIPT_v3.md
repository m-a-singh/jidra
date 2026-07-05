# JIDRA vs CodeGraph — TypeScript Evaluation v3 (MTKruto)

**Changes:** `_resolve_one` `.get()` guards — eliminates `KeyError` on sidecar collision-guard orphans; TS call edges now resolve cleanly  
**Model:** Haiku 4.5 (`--model claude-haiku-4-5-20251001`)  
**Graph:** `output/database/MTKruto-feature-caveman_installation/graph.db`  
**Codebase:** `MTKruto` TypeScript monorepo  
**Date:** 2026-07-03

---

## TL;DR

- **JIDRA 5/5** — unchanged. Avg 2.0 calls (down from 2.4), 13,085 tokens (down from 16,108).
- **CG 3/5** — TS1 still fails. TS5 still DNF (max iters).
- **Token ratio: 23.1×** JIDRA fewer — improved from 21.9×.
- TS2 JIDRA 5c/37k → 3c/20k: richer call-edge graph after `_resolve_calls` fix reduced hops.
- CG hallucinations cleared on TS1 this run; JIDRA still hallucinating short filenames.

---

## Per-task results

| task | JIDRA calls/tok/cost | CG calls/tok/cost | token ratio | winner |
|---|---|---|---|---|
| TS1 callers of `getDb` | 1 / 11,081 / $0.0106 ✓ | 1 / 6,985 / $0.0067 ✗ | CG 1.6× | **JIDRA** |
| TS2 callees of `spawnSession` | 3 / 19,608 / $0.0188 ✓ | 8 / 240,385 / $0.2308 ✓ | JIDRA 12.3× | **JIDRA** |
| TS3 negative (`purgeStaleSessions`) | 3 / 18,243 / $0.0175 ✓ | 8 / 178,710 / $0.1716 ✓ | JIDRA 9.8× | **JIDRA** |
| TS4 locate `enforceBudget` | 2 / 11,027 / $0.0106 ✓ | 13 / 580,187 / $0.5570 ✓ | JIDRA 52.6× | **JIDRA** |
| TS5 source of `enforceBudget` | 1 / 5,468 / $0.0053 ✓ | 14 / 504,745 / $0.4846 ✗ | JIDRA 92.3× | **JIDRA** |

**Summary:**
```
              correct  avg_calls  avg_tokens   total_cost   halluc
jidra          5/5       2.0        13,085       $0.0628       2/5
codegraph      3/5       8.8       302,202       $1.4507       1/5
token ratio    23.1×
```

**Estimated cost (Haiku 4.5: $0.80/MTok in, $4.00/MTok out):**
```
              total_tokens   est_cost
jidra           65,427       ~$0.062
codegraph    1,511,012       ~$1.233
cost ratio     19.9×
```

---

## Task analysis

**TS1 — CG caller_hit=1 again (need ≥2).**  
JIDRA: `find_callers("getDb")` → 59 callers, caller_hit=37 (✓). CG: 1c/7k, stated "80 callers" but caller_hit=1 — blast-radius lists dependent symbols, not call-graph callers. CG shorter run (1 call vs 6 in v2) but same wrong answer. Both backends still hallucinating short filenames (adapter.ts, worker.ts).

**TS2 — JIDRA 5c/37k → 3c/20k.**  
Richer call-edge resolution after `_resolve_calls` fix: agent got enough from `explore` + `get_agent_flow` without extra `get_method_source` fallbacks. Callee_hit 7/17 (passes ≥3 threshold). CG: 8c/240k vs 10c/344k in v2 — improvement but still 12.3× costlier.

**TS3 — stable. Both correct.**  
JIDRA 3c/18k (same as v2). CG 8c/179k (down from 11c/346k). Both conclude absent. JIDRA 9.8× fewer tokens.

**TS4 — CG deepened: 10c/368k → 13c/580k.**  
Three more calls cycling the same truncated output. JIDRA unchanged 2c/11k. JIDRA 52.6× fewer tokens.

**TS5 — CG DNF again 14 iters / 505k.**  
Same structural failure: processManager.ts truncation wall ~line 813. Target at line 1127 unreachable via explore. JIDRA: 1c/5k `get_method_source("enforceBudget")` → direct hit.

---

## Open issues

| # | issue | status |
|---|---|---|
| 1 | TS5 CG: processManager.ts truncation wall — target at line 1127 unreachable | Open (CG design limit) |
| 2 | TS4 CG spiral deepened 10c→13c this run | Open (CG design limit) |
| 3 | TS1 JIDRA: short-form filename hallucinations (adapter.ts, worker.ts) | Open — cosmetic, semantics correct |
| 4 | TS1 CG: blast-radius / caller gap | Open (CG design limit) |

---

## Reproduce

```bash
./venv/bin/python evals/harness/typescript/agent_eval_ts.py \
  --graph    output/database/MTKruto-feature-caveman_installation/graph.db \
  --codebase /path/to/MTKruto \
  --model    claude-haiku-4-5-20251001 \
  --out      evals/harness/typescript/results/results_ts_v3.json
```
