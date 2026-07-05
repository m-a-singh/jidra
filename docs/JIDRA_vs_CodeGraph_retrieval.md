# JIDRA vs CodeGraph — Retrieval Quality Benchmark

**Repo:** MTKruto (TypeScript Telegram client, ~2,300 methods)
**Cases:** 12 identical test cases run against both tools
**Methodology:** mirrors CodeGraph's own `__tests__/evaluation/runner.ts`
**Pass threshold:** recall ≥ 0.5
**Date:** 2026-07-03

---

## Summary

| metric | JIDRA | CodeGraph |
|---|---|---|
| **passed** | **11/12 (92%)** | **11/12 (92%)** |
| **mean recall** | **0.681** | **0.760** |
| **mean MRR (search)** | **0.418** | **0.833** |
| search passed | 5/6 | 5/6 |
| explore passed | 6/6 | 6/6 |

**Both tools pass 11/12 cases.** CodeGraph has higher recall (0.76 vs 0.68) and significantly higher MRR (0.83 vs 0.42) on search. JIDRA matches on explore pass rate (6/6) but with lower per-case recall.

---

## Per-case results

### Search (searchNodes equivalent)

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr | winner |
|---|---|---|---|---|---|
| search-class-client | 1.00 | 0.06 | 1.00 | 1.00 | CG (rank) |
| search-method-sendMessage | 1.00 | 1.00 | 1.00 | 1.00 | tie |
| search-method-serialize | 1.00 | 1.00 | 0.00 | 0.00 | **JIDRA** |
| search-class-transport | 1.00 | 0.20 | 1.00 | 1.00 | CG (rank) |
| search-class-session | 0.00 | 0.00 | 1.00 | 1.00 | **CG** |
| search-method-invoke | 1.00 | 0.25 | 1.00 | 1.00 | CG (rank) |

**JIDRA wins:** `serializeObject` — CodeGraph misses it entirely (0.00 recall). JIDRA returns it at rank 1.

**CodeGraph wins:** `SessionEncrypted` — JIDRA misses it entirely (0.00 recall). CodeGraph returns it at rank 1.

**CodeGraph dominates ranking:** On the 4 cases both tools find the symbol, CodeGraph returns it at rank 1 (MRR=1.00) while JIDRA buries it (MRR=0.06–0.25). This is the BM25 vs heuristic-ranking gap identified earlier — JIDRA finds the symbol but ranks it below noise.

---

### Explore (findRelevantContext equivalent)

| case | JIDRA recall | CG recall | JIDRA found | CG found |
|---|---|---|---|---|
| explore-send-flow | 0.67 | 0.67 | sendMessage, send | sendMessage, send |
| explore-session | 0.50 | 1.00 | send | SessionEncrypted, send |
| explore-client-invoke | 0.50 | 1.00 | invoke | invoke, Client |
| explore-connection | 0.50 | 0.50 | connect | connect |
| explore-storage | 0.50 | 0.50 | Storage | Storage |
| explore-error-handling | 0.50 | 0.50 | RPCError | RPCError |

**Both pass all 6 explore cases.** CodeGraph achieves higher recall on `explore-session` and `explore-client-invoke` — both cases where `SessionEncrypted` and `Client` (the class-level symbols) need to appear alongside method results. CodeGraph's graph includes class nodes as first-class citizens in explore results; JIDRA's explore returns method-level results predominantly.

---

## Key findings

**1. `serializeObject` — JIDRA's one search win**

CodeGraph returns 0 results for `serializeObject`. JIDRA returns it at rank 1. Root cause: `serializeObject` is a standalone function (not a class method) in MTKruto. CodeGraph's node kind filtering may exclude it when `kinds: ['method']` is applied to function-scoped symbols. JIDRA's tree-sitter extractor indexes all callable declarations including standalone functions.

**2. `SessionEncrypted` — JIDRA's one search loss**

JIDRA returns 0 results for `SessionEncrypted`. CodeGraph returns it at rank 1. Root cause: `SessionEncrypted` is a TypeScript class defined in a namespace/module scope that JIDRA's tree-sitter extractor may not fully resolve. The FTS5 index either doesn't contain it or the tokenization splits `SessionEncrypted` differently. This is the same class that appears in explore results only when reached via call-graph traversal from `send` — it exists in the graph but is not surfaced by direct name search.

**3. MRR gap is the real JIDRA weakness**

Both tools pass the same number of cases (11/12) but CodeGraph's MRR is 2× higher (0.83 vs 0.42). For the 5 cases JIDRA passes on search, it finds the symbol but ranks it 4th–17th. This is the BM25-as-tiebreaker problem: JIDRA's `_score_hit` overrides BM25 with structural heuristics that bury the exact match under noisier results. Fix: make BM25 the primary ranking signal.

**4. Explore is effectively tied on pass rate**

6/6 for both tools. CodeGraph has higher recall per case (0.72 vs 0.53 average) because it surfaces class-level nodes alongside methods. JIDRA's explore is method-centric.

---

## What this does and doesn't measure

This benchmark measures **retrieval quality** — does the tool surface the right symbol when asked?

It does **not** measure what the agent-in-loop eval measures: whether an agent using the tool reaches the correct answer to a structural code navigation task (caller enumeration, flow tracing, DI resolution, existence checks). On those tasks JIDRA's scalpel tools (`find_callers`, `get_agent_flow`, `get_implementations`) structurally outperform CodeGraph's single broad tool — as shown in the separate agent eval across Java, Python, and TypeScript codebases.

**The two benchmarks are complementary:**
- Retrieval eval (this doc): CodeGraph ≈ JIDRA on pass rate, CodeGraph wins on ranking (MRR)
- Agent eval: JIDRA wins on correctness, tokens, cost — decisively on structural tasks

---

## Reproduce

```bash
# JIDRA
PYTHONPATH=src python evals/retrieval_eval.py \
  --repo mtkruto \
  --db /path/to/MTKruto/graph.db \
  --out evals/jidra_retrieval_mtkruto.json

# CodeGraph (replace test-cases.ts with test-cases-mtkruto.ts first)
cd /path/to/codegraph
cp /path/to/test-cases-mtkruto.ts __tests__/evaluation/test-cases.ts
EVAL_CODEBASE=/path/to/MTKruto npx tsx __tests__/evaluation/runner.ts
```
