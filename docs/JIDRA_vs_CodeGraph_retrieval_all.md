# JIDRA vs CodeGraph — TypeScript Retrieval Benchmark

**Repos:** MTKruto, Trezor Suite, PostyBirb, Shapeshift Web (all TypeScript)  
**Methodology:** Mirrors CodeGraph's `__tests__/evaluation/runner.ts` — Recall@20 + MRR for search, Recall for explore  
**Pass threshold:** recall ≥ 0.5  
**Cases per repo:** 12 (6 search + 6 explore), method symbols only  
**Versions:** v1 (baseline) → v2 (exact-name pre-query fix, 2026-07-05) → v3 (stemming + BM25 score-tier fix, 2026-07-05)

---

## Executive Summary

JIDRA finds the right method 100% of the time on search (all repos, all versions). CodeGraph has persistent indexing gaps — standalone functions, React hooks, and route handlers in monorepo sub-packages are missed in every version. JIDRA's explore recall is higher on 3/4 repos.

**The single biggest V2→V3 change:** MRR jumped from 0.382 → 1.000. A BM25 score-tier bug caused callers of a method to outrank the method definition on common names (`sendMessage`, `invoke`, `login`). Fixed in V3 by a −1000 offset on name-scoped scores before merge.

**Aggregate progression:**

| metric | V1 | V2 | V3 | CodeGraph |
|---|---|---|---|---|
| Pass rate | — | 44/48 (92%) | **45/48 (94%)** | 35/48 (73%) |
| Mean recall | — | 0.764 | **0.791** | 0.578 |
| Search MRR | low | 0.382 | **1.000** | 1.00* |
| Explore recall | — | ~0.548 | **~0.632** | ~0.46–0.72 |

*CodeGraph MRR=1.00 on cases it finds — but misses 5 search targets entirely.

---

## Why Method Symbols Only

JIDRA is method-centric by design — it indexes methods as the primary unit with class metadata. Coding agents need to know *which method* to read. Returning `session.SessionEncrypted#__init__` gives an agent the exact file and entry point.

CodeGraph is node-type-agnostic — it indexes classes, interfaces, and methods equally. Class-level symbols tested against JIDRA is not a fair comparison. All cases here use method symbols only.

---

## Progression of Change

### V1 → V2

**Change:** Exact-name pre-query phase — before BM25 full-text search, query FTS5 with the method name directly. Pins known-name searches to rank 1 before running weighted BM25.

**Result:** MRR improved significantly on exact-name queries, but common names (`invoke`, `sendMessage`, `login`) still ranked callers above definitions because name-scoped BM25 scores (~−12) were being sorted raw against full-text scores (~−15). Bug existed, not yet discovered.

| metric | V1 | V2 | delta |
|---|---|---|---|
| Pass rate | — | 44/48 (92%) | — |
| Mean recall | — | 0.764 | — |
| Search MRR | — | 0.382 | — |

### V2 → V3

**Changes:**
1. **Morphological stemming (Snowball/English):** NL explore queries stem before FTS5 lookup. `"signing transactions"` → `"sign transact"` prefix query. Fixes cases where inflected English words didn't match the camelCase identifier.
2. **BM25 score-tier fix:** Name-scoped scores get −1000 offset before merge. Ensures definition always outranks callers. MRR: 0.382 → 1.000, zero regressions.

| metric | V2 | V3 | delta |
|---|---|---|---|
| Pass rate | 44/48 | 45/48 | +1 |
| Mean recall | 0.764 | 0.791 | +0.027 |
| Search MRR | 0.382 | **1.000** | **+0.618** |
| Explore recall | ~0.548 | ~0.632 | +0.084 |

**Cases fixed by stemming (V2→V3):**

| case | V2 | V3 | mechanism |
|---|---|---|---|
| shapeshift-explore-signing | FAIL 0.00 | **PASS 1.00** | "signing" → "sign" → signTransaction |
| trezor-explore-fees | FAIL 0.00 | **PASS 0.50** | "fees" → "fee" → estimateFee |
| shapeshift-explore-fees | PASS 0.50 | PASS 1.00 | recall gain |
| mtkruto-explore-auth | PASS 0.50 | PASS 1.00 | "authentication" → "authent" |
| mtkruto-explore-messaging | PASS 0.50 | PASS 1.00 | stemming |
| trezor-explore-send-form | PASS 0.50 | PASS 1.00 | stemming |
| trezor-explore-settings | PASS 0.50 | PASS 1.00 | stemming |
| postybirb-explore-submit | FAIL 0.00 | PASS 0.50 | "submit" → matches postSubmission |

**Search MRR fixes (notable V2→V3):**

| case | V2 MRR | V3 MRR |
|---|---|---|
| mtkruto-search-sendMessage | 0.06 | 1.00 |
| mtkruto-search-invoke | 0.05 | 1.00 |
| postybirb-search-login | 0.04 | 1.00 |
| postybirb-search-postSubmission | 0.04 | 1.00 |
| trezor-search-getAddress | 0.05 | 1.00 |
| shapeshift-search-estimateFees | 0.10 | 1.00 |

---

## Per-Repo Results (V3)

### MTKruto (~2,300 methods — TypeScript Telegram client)

**JIDRA V3: 12/12 | Recall=0.889 | Search MRR=1.000**  
**CodeGraph: 11/12 | Recall=0.720**

**Search:**

| case | JIDRA V3 recall | JIDRA V3 MRR | CG recall | CG MRR | V2 MRR |
|---|---|---|---|---|---|
| sendMessage | 1.00 | **1.00** | 1.00 | 1.00 | 0.06 |
| invoke | 1.00 | **1.00** | 1.00 | 1.00 | 0.05 |
| serializeObject | 1.00 | 1.00 | **0.00** | **0.00** | 0.25 |
| getMe | 1.00 | **1.00** | 1.00 | 1.00 | 0.05 |
| signIn | 1.00 | **1.00** | 1.00 | 1.00 | 0.10 |
| forwardMessages | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

CG misses `serializeObject` — standalone TypeScript function, not a class method.

**Explore:**

| case | JIDRA V3 | CG | V2 recall | V3 recall |
|---|---|---|---|---|
| explore-send-flow | PASS 0.67 | PASS 0.67 | 0.67 | 0.67 |
| explore-session | PASS 1.00 | PASS 0.50 | 0.50 | **1.00** |
| explore-invoke | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |
| explore-connection | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |
| explore-auth | PASS 1.00 | PASS 1.00 | 0.50 | **1.00** |
| explore-messaging | PASS 1.00 | PASS 0.50 | 0.50 | **1.00** |

---

### Trezor Suite (~12,600 methods — TypeScript monorepo)

**JIDRA V3: 11/12 | Recall=0.792 | Search MRR=1.000**  
**CodeGraph: 8/12 | Recall=0.500**

**Search:**

| case | JIDRA V3 recall | JIDRA V3 MRR | CG recall | CG MRR | V2 MRR |
|---|---|---|---|---|---|
| signTransaction | 1.00 | **1.00** | 1.00 | 1.00 | 0.50 |
| getAddress | 1.00 | **1.00** | **0.00** | **0.00** | 0.05 |
| getFeatures | 1.00 | **1.00** | 1.00 | 1.00 | 0.50 |
| useSendForm | 1.00 | **1.00** | **0.00** | **0.00** | 0.50 |
| applySettings | 1.00 | **1.00** | 1.00 | 1.00 | 0.50 |
| estimateFee | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

CG misses `getAddress` and `useSendForm` — React hook and method in sub-package.

**Explore:**

| case | JIDRA V3 | CG | V2 | V3 |
|---|---|---|---|---|
| explore-tx-flow | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |
| explore-device | **FAIL 0.00** | **FAIL 0.00** | 0.00 | 0.00 |
| explore-send-form | PASS 1.00 | PASS 0.50 | 0.50 | **1.00** |
| explore-fees | PASS 0.50 | **FAIL 0.00** | 0.00 | **0.50** (stemming) |
| explore-settings | PASS 1.00 | PASS 0.50 | 0.50 | **1.00** |
| explore-account | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |

`explore-device` fails both tools — `"device initialization"` has no lexical overlap with `getFeatures`/`getAddress`. Needs embeddings.

---

### PostyBirb (~2,200 methods — NestJS + Electron)

**JIDRA V3: 10/12 | Recall=0.708 | Search MRR=1.000**  
**CodeGraph: 9/12 | Recall=0.625**

**Search (V3):** All 6 PASS, MRR=1.000 for all.

| case | JIDRA V3 MRR | V2 MRR |
|---|---|---|
| validateSubmission | 1.00 | 0.14 |
| postSubmission | 1.00 | 0.04 |
| uploadFile | 1.00 | 0.05 |
| login | 1.00 | 0.04 |
| updateSubmission | 1.00 | 0.05 |
| post | 1.00 | 0.08 |

**Explore:**

| case | JIDRA V3 | CG | V2 | V3 |
|---|---|---|---|---|
| explore-submit | PASS 0.50 | PASS 0.50 | FAIL 0.00 | **PASS 0.50** (stemming) |
| explore-file | PASS 1.00 | PASS 0.50 | 1.00 | 1.00 |
| explore-auth | PASS 0.50 | **FAIL 0.00** | 0.50 | 0.50 |
| explore-website | **FAIL 0.00** | **FAIL 0.00** | 0.00 | 0.00 |
| explore-update | **FAIL 0.00** | **FAIL 0.00** | 0.00 | 0.00 |
| explore-posting | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |

`explore-website` and `explore-update` fail both tools — `validateSubmission` / `updateSubmission` / `login` are defined on abstract base classes; graph traversal stops at the interface boundary.

---

### Shapeshift Web (~6,200 methods — React/Redux DeFi app)

**JIDRA V3: 12/12 | Recall=0.875 | Search MRR=1.000**  
**CodeGraph: 7/12 | Recall=0.458**

**Search:**

| case | JIDRA V3 recall | JIDRA V3 MRR | CG recall | CG MRR | V2 MRR |
|---|---|---|---|---|---|
| getTradeQuote | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| signTransaction | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| broadcastTransaction | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| estimateFees | 1.00 | **1.00** | 1.00 | 1.00 | 0.10 |
| getRates | 1.00 | **1.00** | **0.00** | **0.00** | 0.10 |
| getAssets | 1.00 | 1.00 | **0.00** | **0.00** | 1.00 |

CG misses `getRates` and `getAssets` — route handler functions in package sub-directories.

**Explore:**

| case | JIDRA V3 | CG | V2 | V3 |
|---|---|---|---|---|
| explore-swap | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |
| explore-assets | PASS 0.50 | PASS 0.50 | 0.50 | 0.50 |
| explore-fees | PASS 1.00 | **FAIL 0.00** | 0.50 | **1.00** |
| explore-signing | PASS 1.00 | — | FAIL 0.00 | **PASS 1.00** (stemming) |
| explore-broadcast | PASS 1.00 | PASS 0.50 | 1.00 | 1.00 |
| explore-rates | PASS 0.50 | **FAIL 0.00** | 0.50 | 0.50 |

---

## Summary Table (V3 Final)

| repo | JIDRA V3 pass | CG pass | JIDRA recall | CG recall | JIDRA MRR | CG MRR |
|---|---|---|---|---|---|---|
| shapeshift | 12/12 (100%) | 7/12 (58%) | 0.875 | 0.458 | **1.000** | 1.00* |
| mtkruto | 12/12 (100%) | 11/12 (92%) | 0.889 | 0.720 | **1.000** | 1.00* |
| postybirb | 10/12 (83%) | 9/12 (75%) | 0.708 | 0.625 | **1.000** | 1.00* |
| trezor | 11/12 (92%) | 8/12 (67%) | 0.792 | 0.500 | **1.000** | 1.00* |
| **total** | **45/48 (94%)** | **35/48 (73%)** | **0.791** | **0.576** | **1.000** | 1.00* |

*CG MRR=1.00 on cases it passes — but misses 5 search targets entirely (getRates, getAssets, serializeObject, getAddress, useSendForm).

---

## Remaining Gaps (Unchanged Across All Versions)

| case | recall | root cause | fixable with |
|---|---|---|---|
| postybirb-explore-website | 0.00 | abstract base class boundary | interface traversal |
| postybirb-explore-update | 0.00 | same — `updateSubmission`/`validateSubmission` on abstract class | interface traversal |
| trezor-explore-device | 0.00 | semantic gap — "device initialization" → getFeatures/getAddress | embeddings |

---

## Why CodeGraph Misses Search Targets

In every case the method existed — CodeGraph's index simply didn't include it:

- **Standalone TypeScript functions** (not class methods) — `serializeObject`
- **React hooks** — `useSendForm`, `useApprove`
- **Route handlers in sub-packages** — `getRates`, `getAssets` (swapper sub-packages)
- **Methods in deeply nested monorepo paths** — `getAddress` (Trezor Suite)

JIDRA's extraction captures all of these regardless of structure.

---

## What This Benchmark Doesn't Cover

This measures retrieval quality — does the tool surface the right symbol? It doesn't measure agent navigation outcomes.

Agent-in-loop evaluation (Java/Python/TypeScript) shows JIDRA achieves 8/8, 5/5, 5/5 correctness vs CodeGraph's 7/8, 4/5, 3/5 — at 1.4×–23.1× fewer tokens depending on task type. See [FINDINGS_PYTHON.md](FINDINGS_PYTHON.md) and [FINDINGS_TYPESCRIPT.md](FINDINGS_TYPESCRIPT.md).

---

## Reproduce

```bash
# JIDRA V3 — all 4 repos
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo mtkruto    --db /path/to/MTKruto/.jidra/graph.db    --out evals/results_mtkruto_v3.json
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo trezor     --db /path/to/trezor/.jidra/graph.db     --out evals/results_trezor_v3.json
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo postybirb  --db /path/to/postybirb/.jidra/graph.db  --out evals/results_postybirb_v3.json
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo shapeshift --db /path/to/web/.jidra/graph.db        --out evals/results_shapeshift_v3.json

# CodeGraph (unchanged from V2)
cd /path/to/codegraph
EVAL_CODEBASE=/path/to/repo npx tsx __tests__/evaluation/runner.ts
```

Raw per-version deltas: `evals/analysis/codegraph/results/comparison_v3.md`
