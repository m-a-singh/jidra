# JIDRA vs CodeGraph — TypeScript Agent-in-Loop Evaluation

**Language:** TypeScript  
**Model:** Haiku 4.5  
**Methodology:** Agent-in-loop — same LLM given one backend's MCP tools, scored on correct answer + tokens + calls  
**Test repos:** `agents_fleet` (v1, v3) / `ai_watchtower` (v2) — TypeScript monorepos  
**Versions:** v1 (2026-06-27, 4 tasks) → v2 (2026-07-02, 5 tasks) → v3 (2026-07-03, 5 tasks)

---

## Executive Summary

JIDRA achieves **100% correctness** across all TypeScript eval versions. CodeGraph is stuck at **3/5** — TS1 (caller enumeration) fails every version due to the same design gap as Python, and TS5 (large-file source retrieval) fails every version due to a truncation wall.

The token gap **widened massively** from v1 to v2 — JIDRA got 57% cheaper while CodeGraph got 137% more expensive. By v3, JIDRA uses **23.1× fewer tokens** and costs **~20× less** per eval run.

**Aggregate across v3:**

| metric | JIDRA | CodeGraph |
|---|---|---|
| Correctness | **5/5 (100%)** | 3/5 (60%) |
| Avg tokens (v3) | **13,085** | 302,202 |
| Token ratio (v3) | **23.1× fewer** | — |
| Avg calls (v3) | **2.0** | 8.8 |
| Est. cost/run (v3) | **$0.063** | $1.451 |
| Cost ratio (v3) | **~20×** cheaper | — |

---

## Progression of Change

### v1 → v2 (2026-06-27 → 2026-07-02)

| metric | v1 | v2 | delta |
|---|---|---|---|
| JIDRA correct | 4/4 (3/4 scored†) | 5/5 | perfect both |
| CG correct | 1/4 | 3/5 | +2 |
| JIDRA avg calls | 3.75 | 2.4 | **-36%** |
| JIDRA avg tokens | 37,039 | 16,108 | **-57%** |
| CG avg calls | 8.5 | 10.2 | +20% (worse) |
| CG avg tokens | 148,970 | 352,633 | **+137%** |
| Token ratio | ~4× | **21.9×** | gap widened 5.5× |
| JIDRA cost | ~$0.140 | $0.076 | -46% |
| CG cost | ~$0.486 | $1.433 | +195% |
| Cost ratio | 3.5× | **18.9×** | grew 5.4× |

†v1 TS3: JIDRA answer correct ("does not exist") but harness markdown bold broke substring match. Corrected score: 4/4.

**What changed:** `#` selector fix improves TypeScript resolution. TS5 added (source retrieval — exposes CG truncation wall). CG's "avoid 14-call cap" strategy: trades more tokens for correctness on TS2/TS4, but still fails TS1 (design gap) and TS5 (truncation wall).

### v2 → v3 (2026-07-02 → 2026-07-03)

| metric | v2 | v3 | delta |
|---|---|---|---|
| JIDRA correct | 5/5 | 5/5 | = |
| CG correct | 3/5 | 3/5 | = |
| JIDRA avg calls | 2.4 | 2.0 | -17% |
| JIDRA avg tokens | 16,108 | 13,085 | -19% |
| CG avg tokens | 352,633 | 302,202 | -14% |
| Token ratio | 21.9× | 23.1× | JIDRA gap grew slightly |
| JIDRA cost | $0.076 | $0.063 | -17% |
| CG cost | $1.433 | $1.451 | +1% |
| Cost ratio | 18.9× | ~20× | JIDRA savings grew |

**What changed:** `_resolve_one` `.get()` guards eliminate `KeyError` on sidecar collision-guard orphans — TS call edges resolve more cleanly. TS2 JIDRA improved 5c/37k → 3c/20k (richer call-edge graph). CG TS4 deepened 10c → 13c (cycled same truncated output 3 more times). Repo switched from `ai_watchtower` → `agents_fleet`.

---

## Per-Task Detail

### TS1 — Caller Enumeration (`getDb` callers)

**Goal:** Find all methods/functions that call `getDb`.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 1 / 7,815 ✓ | 1 / 5,340 ✗ | 34 callers found | 0 callers |
| v2 | 1 / 8,904 ✓ | 6 / 113,194 ✗ | 38 callers, caller_hit 37/38 | stated "80 callers", caller_hit=1/2 |
| v3 | 1 / 11,081 ✓ | 1 / 6,985 ✗ | 59 callers, caller_hit=37 | "80 callers", caller_hit=1 |

**Root cause of CG failure:** Same as Python PY1 — `codegraph_explore` blast-radius lists co-occurring dependents, not actual call-graph callers. CG has never passed this task across v1–v3.

**Hallucination note:** Both backends hallucinate short-form filenames on TS1 (`adapter.ts` → `analytics-adapter.ts`, `worker.ts` → `analytics-worker.ts`). Cosmetic — semantics correct.

---

### TS2 — Callee Tracing (`spawnSession` downstream calls)

**Goal:** Trace what `spawnSession` calls directly.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 10 / 116,417 ✓ | 14 / 274,633 ✗ | 10/15 known callees | 0/15 (max-iter spiral) |
| v2 | 5 / 36,733 ✓ | 10 / 344,364 ✓ | correct | correct, 9.4× costlier |
| v3 | 3 / 19,608 ✓ | 8 / 240,385 ✓ | callee_hit 7/17 | callee_hit 12/17, 12.3× costlier |

CG passes v2+ by manually paging through source (better recall on v3: 12/17 vs JIDRA 7/17) but at 12× the token cost. JIDRA v1 was expensive (10c/116k) due to source truncation; v2–v3 richer graph resolution cut this to 3c/20k.

---

### TS3 — Negative Existence Check (`purgeStaleSessions`)

**Goal:** Confirm the function does not exist.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 2 / 11,326 ✓ | 5 / 46,999 ✓ | absent | absent |
| v2 | 3 / 18,553 ✓ | 11 / 345,653 ✓ | absent | absent, 18.6× costlier |
| v3 | 3 / 18,243 ✓ | 8 / 178,710 ✓ | absent | absent, 9.8× costlier |

Both correct. JIDRA needs 2–3 explore calls to conclude absent. CG v2 spiraled to 11 calls before concluding — same correct answer, 18.6× more tokens.

**v1 TS3 scoring artifact:** JIDRA response `"does **not exist**"` had markdown bold breaking substring match `"does not exist"`. Fixed to markdown-tolerant in v2.

---

### TS4 — Definition + Description (`enforceBudget`)

**Goal:** Find where `enforceBudget` is defined and describe what it does.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v1 | 2 / 12,598 ✓ | 14 / 269,106 ✗ | `processManager.ts`, purpose described | failed to locate |
| v2 | 2 / 10,900 ✓ | 10 / 368,331 ✓ | same | answered from partial context, 33.8× costlier |
| v3 | 2 / 11,027 ✓ | 13 / 580,187 ✓ | same | same, 52.6× costlier, 3 more calls than v2 |

CG v1 hit truncation wall in `processManager.ts` (target at line 1127, tool truncates at ~line 813). v2+ CG escapes by reconstructing from call-site context, but cycles 10–13 calls and costs $0.37–$0.56 per query. JIDRA: 2 calls / $0.011.

---

### TS5 — Source Retrieval (`enforceBudget` full source)

**Goal:** Return the full source of `enforceBudget`.

| version | JIDRA calls/tok | CG calls/tok | JIDRA result | CG result |
|---|---|---|---|---|
| v2 | 1 / 5,450 ✓ | 14 / 591,624 ✗ | full source returned | DNF — max iters, no answer |
| v3 | 1 / 5,468 ✓ | 14 / 504,745 ✗ | full source returned | DNF — max iters |

JIDRA: `get_method_source("enforceBudget")` → direct hit, 1 call. CG: no scalpel equivalent — `codegraph_explore` returns ~20k chars; `processManager.ts` exceeds that, target at line 1127 unreachable. Same DNF in both v2 and v3. Design limit — not fixable by retry.

---

## Persistent CodeGraph Gaps

| gap | status | root cause |
|---|---|---|
| TS1 caller enumeration | Open (all versions) | blast-radius ≠ call-graph callers |
| TS4/TS5 truncation wall | TS4 partial workaround, TS5 always DNF | `codegraph_explore` truncates large files, no `get_method_source` equivalent |
| TS2 callee cost | 9–12× more expensive | manual source-paging instead of `get_flow` |
| TS1 short-form filename hallucinations | Open (cosmetic) | both backends; semantics correct |

---

## Reproduce

```bash
# v3 (agents_fleet)
./venv/bin/python evals/harness/typescript/agent_eval_ts.py \
  --graph    output/database/agents_fleet-feature-caveman_installation/graph.db \
  --codebase /path/to/agents_fleet \
  --model    claude-haiku-4-5-20251001 \
  --out      evals/harness/typescript/results/results_ts_v3.json

# v2 (ai_watchtower)
./venv/bin/python evals/agent_eval_ts.py \
  --codebase /path/to/ai_watchtower \
  --graph    output/database/ai_watchtower-feature-caveman_installation/graph.db \
  --out      evals/results_ts_v2.json \
  --model    claude-haiku-4-5-20251001
```
