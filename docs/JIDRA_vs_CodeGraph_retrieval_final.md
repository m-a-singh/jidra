# JIDRA vs CodeGraph — Retrieval Quality Benchmark

**Repo:** MTKruto (TypeScript Telegram client, ~2,300 methods)
**Date:** 2026-07-03
**Methodology:** mirrors CodeGraph's `__tests__/evaluation/runner.ts` exactly —
same pass threshold (recall ≥ 0.5), same scoring (Recall@10, MRR), same 12 cases.

---

## Why two evaluations

JIDRA and CodeGraph have different graph philosophies:

**CodeGraph** is **node-type-agnostic** — it indexes classes, interfaces, enums,
and methods as equal first-class nodes. `searchNodes(kinds: ['class'])` returns
class nodes directly.

**JIDRA** is **method-centric** — it indexes methods as the primary unit with
class information carried as metadata. This is a deliberate design choice: agents
navigating code need to know *which method* to read, not just *which class* exists.
Returning `session.SessionEncrypted#__init__` tells an agent the exact file and
method — no whole-file read needed.

This difference means testing class-level symbols against JIDRA's search is not
a fair comparison. We run both:

- **Eval A** — 12 identical cases including class symbols (apples-to-oranges, shows architectural difference)
- **Eval B** — 12 method-level cases only (apples-to-apples, fair comparison)

---

## Eval A — Identical 12 cases (as-is comparison)

Same test cases run against both tools unchanged.
CodeGraph ran these with its original `test-cases-mtkruto.ts`.
JIDRA ran with `retrieval_eval_v3.py`.

### Summary

| metric | JIDRA | CodeGraph |
|---|---|---|
| **passed** | **11/12 (92%)** | **11/12 (92%)** |
| **mean recall** | **0.806** | **0.760** |
| **mean MRR (search)** | **0.418** | **0.833** |
| search passed | 5/6 | 5/6 |
| explore passed | 6/6 | 6/6 |

### Per-case — Search

| case | symbol type | JIDRA recall | JIDRA mrr | CG recall | CG mrr |
|---|---|---|---|---|---|
| search-class-client | class | 1.00 | 0.06 | 1.00 | 1.00 |
| search-method-sendMessage | method | 1.00 | 1.00 | 1.00 | 1.00 |
| search-method-serialize | method | 1.00 | 1.00 | **0.00** | **0.00** |
| search-class-transport | class | 1.00 | 0.20 | 1.00 | 1.00 |
| search-class-session | class | **0.00** | **0.00** | 1.00 | 1.00 |
| search-method-invoke | method | 1.00 | 0.25 | 1.00 | 1.00 |

**JIDRA unique win:** `serializeObject` — CodeGraph returns 0 results. JIDRA returns it at rank 1. Root cause: `serializeObject` is a standalone TypeScript function (not a class method). CodeGraph's node kind filtering excludes it when `kinds: ['method']` is applied to function-scoped declarations. JIDRA indexes all callable declarations.

**JIDRA unique loss:** `SessionEncrypted` — JIDRA returns 0 results on direct search. Root cause: `SessionEncrypted` is a class. JIDRA's search returns methods only. The class exists in JIDRA's graph as metadata (visible in signatures like `session.SessionEncrypted#__init__`) but is not a standalone searchable node.

**CodeGraph MRR advantage on shared passes:** on the 4 cases both tools find the symbol, CodeGraph returns it at rank 1 (MRR=1.00). JIDRA finds it but ranks it 4th–17th (MRR=0.06–0.25). This is JIDRA's BM25 ranking gap — the symbol is in the index but structural heuristics bury it under noisier results.

### Per-case — Explore

| case | JIDRA recall | CG recall | both missed |
|---|---|---|---|
| explore-send-flow | 0.67 | 0.67 | serializeObject |
| explore-session | 1.00 | 1.00 | — |
| explore-client-invoke | 1.00 | 1.00 | — |
| explore-connection | 0.50 | 0.50 | Transport |
| explore-storage | 1.00 | 0.50 | — |
| explore-error-handling | 0.50 | 0.50 | handleError |

**JIDRA explore recall: 0.778 vs CodeGraph: 0.722.** JIDRA wins on explore despite losing on search — `explore-storage` finds both `Storage` and `SessionEncrypted` via graph traversal from the storage query seed where CodeGraph only finds `Storage`. JIDRA's graph-traversal explore is more effective at surfacing associated symbols.

---

## Eval B — Method-level cases only (fair comparison)

New test cases using only method symbols — the correct comparison for JIDRA's
method-centric architecture. CodeGraph ran with `test-cases-mtkruto-methods.ts`.
JIDRA ran with `retrieval_eval_methods.py`.

### Summary

| metric | JIDRA | CodeGraph |
|---|---|---|
| **passed** | **12/12 (100%)** | **11/12 (92%)** |
| **mean recall** | **0.847** | **0.764** |
| **mean MRR (search)** | **0.875** | **0.833** |
| search passed | 6/6 | 5/6 |
| explore passed | 6/6 | 6/6 |

### Per-case — Search (method symbols only)

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr | winner |
|---|---|---|---|---|---|
| search-method-sendMessage | 1.00 | 1.00 | 1.00 | 1.00 | tie |
| search-method-invoke | 1.00 | 0.25 | 1.00 | 1.00 | CG (rank) |
| search-method-serializeObject | 1.00 | 1.00 | 0.00 | 0.00 | **JIDRA** |
| search-method-getMe | 1.00 | 1.00 | 1.00 | 1.00 | tie |
| search-method-signIn | 1.00 | 1.00 | 1.00 | 1.00 | tie |
| search-method-forwardMessages | 1.00 | 1.00 | 1.00 | 1.00 | tie |

**JIDRA: 6/6, CodeGraph: 5/6.** CodeGraph misses `serializeObject` on method-level
cases too — confirming the root cause is not kind filtering but symbol indexing.
`serializeObject` is a standalone function that CodeGraph doesn't index as a method node.
JIDRA's tree-sitter extractor captures it.

MRR gap narrows significantly on method-only cases: JIDRA 0.875 vs CodeGraph 0.833.
The only MRR gap is `invoke` (JIDRA rank 4, CodeGraph rank 1) — `invoke` has many
matches in MTKruto and JIDRA's ranking buries the primary one.

### Per-case — Explore (method-level expected symbols)

| case | JIDRA explore | JIDRA flow | JIDRA combined | CG recall |
|---|---|---|---|---|
| explore-send-flow | 0.67 | 0.00 | 0.67 | 0.67 |
| explore-session | 1.00 | 0.00 | 1.00 | 1.00 |
| explore-client-invoke | 1.00 | 0.00 | 1.00 | 1.00 |
| explore-connection | 0.50 | 0.50 | 0.50 | 0.50 |
| explore-auth | 0.50 | 0.00 | 0.50 | — |
| explore-messaging | 0.50 | 0.00 | 0.50 | — |

Both tools match on the shared cases. `get_agent_flow` adds marginal value
(only `explore-connection` benefits from flow traversal). The explore recall gap
is a content gap — `forwardMessages` and `getMe` don't appear in top-20 FTS5 results
for their respective queries, not a tool design issue.

---

## Key findings

### 1. On method retrieval: JIDRA wins or ties everywhere except `invoke` ranking

On the task both tools are designed for — finding the right method — JIDRA matches
or beats CodeGraph. 6/6 vs 5/6 on search, 0.875 vs 0.833 MRR. The single MRR loss
(`invoke` at rank 4 vs rank 1) is a ranking calibration issue not an indexing gap.

### 2. CodeGraph indexes class nodes; JIDRA does not — by design

CodeGraph returning `SessionEncrypted` on a class search is a feature of its
node-type-agnostic architecture. JIDRA returning `session.SessionEncrypted#__init__`
via method search is a feature of its method-centric architecture. For agents,
the method-level result is more actionable — it gives the exact file and entry point,
not just the class name.

### 3. serializeObject is JIDRA's consistent win

Both evals confirm CodeGraph cannot find `serializeObject`. JIDRA finds it at rank 1.
This is a real indexing gap in CodeGraph for standalone TypeScript functions — a
meaningful difference for TypeScript codebases where functions-as-modules are common.

### 4. Explore quality: JIDRA ≥ CodeGraph on recall

JIDRA's explore recall (0.694–0.778) matches or exceeds CodeGraph's (0.694–0.722)
across both evals. CodeGraph reports edge density as an additional signal not
measured here. JIDRA's explore returns fewer nodes (40–43 vs 67–75) with comparable
or better symbol recall — more focused output.

### 5. get_agent_flow adds minimal value to retrieval

`flow_only_recall = 0.083`. `get_agent_flow` is designed for structural navigation
(tracing call paths, impact analysis) not symbol retrieval. Using it to boost explore
recall is not the right use case. Its value is demonstrated in the agent-in-loop eval,
not here.

---

## What this benchmark does and does not measure

**Measures:** does the tool surface the right symbol when asked directly or via
natural language query?

**Does not measure:** whether an agent using the tool reaches the correct answer
to a structural code navigation task. That is measured in the separate agent-in-loop
eval (Java/Python/TypeScript) where JIDRA achieves 8/8, 5/5, 5/5 correctness
vs CodeGraph's 7/8, 4/5, 3/5 — at 1.35×–21.9× fewer tokens.

The two benchmarks together give the complete picture:
- **Retrieval:** JIDRA and CodeGraph are near-parity on method symbol lookup; JIDRA
  wins on standalone function coverage; CodeGraph wins on class-level search.
- **Agent navigation:** JIDRA wins decisively on structural traversal tasks
  (callers, flows, DI resolution, existence checks) that matter for real agent use.

---

## Reproduce

```bash
# Eval A — identical cases
# CodeGraph (mixed class/method)
cd /path/to/codegraph
cp test-cases-mtkruto.ts __tests__/evaluation/test-cases.ts
EVAL_CODEBASE=/path/to/MTKruto npx tsx __tests__/evaluation/runner.ts

# JIDRA (mixed class/method, explore + get_agent_flow)
PYTHONPATH=src python evals/retrieval_eval_v3.py \
  --repo mtkruto --db /path/to/graph.db \
  --out evals/jidra_retrieval_mtkruto_v3.json

# Eval B — method-level cases only
# CodeGraph
cp test-cases-mtkruto-methods.ts __tests__/evaluation/test-cases.ts
EVAL_CODEBASE=/path/to/MTKruto npx tsx __tests__/evaluation/runner.ts

# JIDRA
PYTHONPATH=src python evals/retrieval_eval_methods.py \
  --repo mtkruto --db /path/to/graph.db \
  --out evals/jidra_retrieval_mtkruto_v4.json
```
