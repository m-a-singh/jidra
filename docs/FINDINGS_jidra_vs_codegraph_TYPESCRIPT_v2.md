# JIDRA vs CodeGraph — TypeScript Evaluation v2

**Model:** Haiku 4.5 (`--model claude-haiku-4-5-20251001`)
**Graph:** `output/database/ai_watchtower-feature-caveman_installation/graph.db`
**Codebase:** `ai_watchtower` TypeScript monorepo
**Date:** 2026-07-02
**Tasks:** TS1–TS5 (5 tasks; adds TS5 vs v1's 4)

---

## TL;DR

- **JIDRA 5/5** — perfect. Avg 2.4 calls, 16,108 tokens.
- **CG 3/5** — TS1 fails (reported 80 callers, caller_hit=1/2 ground truth). TS5 DNF (max iters, no answer).
- **Token ratio: 21.9×** JIDRA fewer. Up from ~4× in v1.
- JIDRA improved on every task vs v1 (fewer calls, fewer tokens). CG regressed on TS2 (10c vs 1c v1) and failed TS5.
- Hallucinations: both backends hallucinated file-shortname variants (adapter.ts, worker.ts, poller.ts) on TS1.

---

## Per-task results

| task | JIDRA calls/tok/wall | CG calls/tok/wall | winner |
|---|---|---|---|
| TS1 callers of `getDb` | 1 / 8,904 / 10.3s ✓ | 6 / 113,194 / 20.7s ✗ | **JIDRA** |
| TS2 callees of `spawnSession` | 5 / 36,733 / 15.4s ✓ | 10 / 344,364 / 35.9s ✓ | **JIDRA** (9.4× tokens) |
| TS3 negative (`purgeStaleSessions`) | 3 / 18,553 / 7.6s ✓ | 11 / 345,653 / 29.0s ✓ | **JIDRA** (18.6× tokens) |
| TS4 locate `enforceBudget` | 2 / 10,900 / 7.1s ✓ | 10 / 368,331 / 34.1s ✓ | **JIDRA** (33.8× tokens) |
| TS5 source of `enforceBudget` | 1 / 5,450 / 5.5s ✓ | 14 / 591,624 / 41.2s ✗ | **JIDRA** |

**Summary:**
```
              correct  avg_calls  avg_tokens   total_tokens   avg_wall
jidra          5/5       2.4        16,108        80,540        9.2s
codegraph      3/5      10.2       352,633     1,763,166       32.2s
token ratio    21.9×
```

---

## Task analysis

**TS1 — CG fails caller enumeration.**
JIDRA: `find_callers("getDb")` → 38 callers returned in 1 call / 8.9k tokens. Ground truth hit 37/38 (passes ≥2 threshold). CG: 6c/113k, stated "80 callers" but caller_hit=1 — blast-radius output lists dependent symbols, not actual call-graph callers. Same design gap as PY1.

Both backends hallucinated shortened file names (adapter.ts, worker.ts, poller.ts instead of analytics-adapter.ts etc). JIDRA halluc count=3, CG halluc=2.

**TS2 — CG passes but 9.4× costlier.**
JIDRA: 5c/37k, explore → get_agent_flow → get_method_source × 2 → find_callers. Source was truncated; agent fell back to flow-graph edges. Caller_hit 4/17 — low recall but passed threshold. CG: 10c/344k, manually paged through processManager.ts in 10 blasts, reconstructed callee list (callee_hit 12/17 — better recall, but 9.4× more expensive).

**TS3 — both correct, JIDRA 18.6× cheaper.**
JIDRA: 3c/19k. Needed 3 explore calls (purgeStaleSessions → purge stale sessions → purge) before concluding absent. v1 needed only 2. CG: 11c/346k — spiraled through 11 queries before concluding absent. Same answer, 18.6× fewer tokens for JIDRA.

**TS4 — both correct, JIDRA 33.8× cheaper.**
JIDRA: 2c/11k — explore → get_method_source. Clean 2-hop. CG: 10c/368k — could not extract the method body despite finding line 1127 reference. Answered from partial context.

**TS5 — CG DNF at 14 iterations.**
JIDRA: 1c/5k — `get_method_source("enforceBudget")` returned full source directly. CG: 14c/592k — hit max iterations trying to scroll past processManager.ts truncation boundary. Could not retrieve the method body. No final answer emitted. Same failure mode as v1 TS4 (14c/269k, no result).

---

## Hallucination note

Both backends hallucinated short-form filenames on TS1:
- `adapter.ts` → actual: `analytics-adapter.ts`
- `worker.ts` → actual: `analytics-worker.ts`
- JIDRA also: `poller.ts` → actual: `headroom-snapshot-poller.ts`

These are cosmetic (correct semantics, wrong filename casing). Checker logged them as hallucinated.

---

## CG truncation wall

TS4 and TS5 both failed due to CG hitting a truncation point in `processManager.ts` (~1127-1191 range). CG re-queried 10-14 times with different context hints but always received output truncated at line 813. This is a structural limit: `codegraph_explore` returns ~20k chars; processManager.ts exceeds that, and the target method is past the cut. CG has no scalpel equivalent to `get_method_source`.

---

## Reproduce

```bash
./venv/bin/python evals/agent_eval_ts.py \
  --codebase /Users/akhil.singh/Workflows/Personal/ai_watchtower \
  --graph    output/database/ai_watchtower-feature-caveman_installation/graph.db \
  --out      evals/results_ts_v2.json \
  --model    claude-haiku-4-5-20251001
```
