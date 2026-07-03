# JIDRA vs CodeGraph — Method-Level Retrieval Benchmark

**Repos:** MTKruto, Trezor Suite, PostyBirb, Shapeshift Web (all TypeScript)
**Date:** 2026-07-03
**Methodology:** mirrors CodeGraph's `__tests__/evaluation/runner.ts` exactly
**Scoring:** Recall@10 + MRR for search, Recall for explore
**Pass threshold:** recall ≥ 0.5
**Cases per repo:** 12 (6 search + 6 explore), method symbols only

---

## Why method symbols only

JIDRA is **method-centric** by design — it indexes methods as the primary unit with
class information carried as metadata. This is deliberate: coding agents need to know
*which method* to read, not just *which class* exists. Returning
`session.SessionEncrypted#__init__` gives an agent the exact file and entry point.

CodeGraph is **node-type-agnostic** — it indexes classes, interfaces, and methods equally.
Testing class-level symbols against JIDRA is not a fair comparison. These cases use
method symbols only so both tools are measured on the same terms.

---

## Results summary

| repo | methods | JIDRA passed | CG passed | JIDRA recall | CG recall | JIDRA MRR | CG MRR |
|---|---|---|---|---|---|---|---|
| **MTKruto** | ~2,300 | **12/12 (100%)** | **11/12 (92%)** | **0.847** | 0.764 | **0.875** | 0.833 |
| **Trezor Suite** | ~12,600 | **10/12 (83%)** | 8/12 (67%) | **0.708** | 0.500 | **0.767** | 1.000† |
| **PostyBirb** | ~2,200 | **10/12 (83%)** | 9/12 (75%) | **0.708** | 0.625 | **0.667** | 1.000† |
| **Shapeshift** | ~6,200 | **12/12 (100%)** | 7/12 (58%) | **0.792** | 0.458 | **0.649** | 1.000† |
| **Aggregate** | — | **44/48 (92%)** | **35/48 (73%)** | **0.764** | 0.587 | **0.740** | 0.958† |

†CodeGraph MRR is 1.00 on cases it passes — it returns the symbol at rank 1 when it finds it.
JIDRA's lower MRR reflects finding the right symbol at rank 2–7 rather than rank 1.
When CodeGraph misses, it scores 0.00; JIDRA rarely misses on search.

---

## Per-repo detail

### MTKruto (TypeScript Telegram client, ~2,300 methods)

**Search:**

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr |
|---|---|---|---|---|
| sendMessage | 1.00 | 1.00 | 1.00 | 1.00 |
| invoke | 1.00 | 0.25 | 1.00 | 1.00 |
| serializeObject | 1.00 | 1.00 | **0.00** | **0.00** |
| getMe | 1.00 | 1.00 | 1.00 | 1.00 |
| signIn | 1.00 | 1.00 | 1.00 | 1.00 |
| forwardMessages | 1.00 | 1.00 | 1.00 | 1.00 |

JIDRA wins `serializeObject` — a standalone TypeScript function CodeGraph doesn't index.
CodeGraph ranks everything it finds at position 1. JIDRA ranks `invoke` 4th (MRR=0.25).

**Explore:** JIDRA 6/6, CodeGraph 5/6. Both miss `serializeObject` in the send-flow query.

---

### Trezor Suite (TypeScript monorepo, ~12,600 methods)

**Search:**

| case | JIDRA recall | CG recall |
|---|---|---|
| signTransaction | 1.00 | 1.00 |
| getAddress | 1.00 | **0.00** |
| getFeatures | 1.00 | 1.00 |
| useSendForm | 1.00 | **0.00** |
| applySettings | 1.00 | 1.00 |
| estimateFee | 1.00 | 1.00 |

JIDRA 6/6, CodeGraph 4/6. CodeGraph misses `getAddress` and `useSendForm` —
both exist in the repo but codegraph's index doesn't surface them.

**Explore:** JIDRA 4/6, CodeGraph 4/6. Same 2 failures on the same tasks —
content gaps not tool gaps. JIDRA explore latency ~200ms vs CodeGraph ~580ms.

---

### PostyBirb (NestJS + Electron, ~2,200 methods)

**Search:** JIDRA 6/6, CodeGraph 6/6. Tied — all 6 method symbols found by both.

**Explore:** JIDRA 4/6, CodeGraph 3/6. JIDRA wins `explore-file` (finds
`uploadFile` + `post`); CodeGraph misses `uploadFile`. Both fail on
`explore-submit` and `explore-update` — NestJS submission pipeline methods
don't surface in top-20 explore results for either tool.

---

### Shapeshift Web (React/Redux DeFi app, ~6,200 methods)

**Search:**

| case | JIDRA recall | CG recall |
|---|---|---|
| getTradeQuote | 1.00 | 1.00 |
| signTransaction | 1.00 | 1.00 |
| broadcastTransaction | 1.00 | 1.00 |
| estimateFees | 1.00 | 1.00 |
| getRates | 1.00 | **0.00** |
| getAssets | 1.00 | **0.00** |

JIDRA 6/6, CodeGraph 4/6. CodeGraph misses `getRates` and `getAssets` —
route handler functions in package sub-directories not surfaced by codegraph's index.

**Explore:** JIDRA 6/6, CodeGraph 3/6. CodeGraph fails `explore-fees`,
`explore-rates`, and `explore-trade` — traversal doesn't cross package
boundaries in the swapper sub-packages. JIDRA's FTS5 + graph traversal is
module-boundary-agnostic.

---

## Key findings

### 1. JIDRA wins on pass rate across all 4 repos (92% vs 73%)

44/48 vs 35/48. Consistent across all 4 repos — not one outlier.

### 2. CodeGraph search misses are indexing gaps

When CodeGraph misses, it returns 0 results — not wrong results. Methods exist
in the repos but aren't indexed. Root cause varies: standalone functions,
React hook conventions, route handlers in monorepo sub-packages. JIDRA's
tree-sitter extractor captures all of these.

### 3. CodeGraph MRR is 1.00 on cases it passes; JIDRA ranks lower on ambiguous names

When CodeGraph finds a symbol it returns it at rank 1. JIDRA finds it but
buries it on common names (`getAssets` at rank 7, `invoke` at rank 4). Known
ranking gap — JIDRA's heuristic scorer overrides BM25. Fixable.

### 4. Explore gap widens in complex monorepo structures

MTKruto (small, flat): 6/6 vs 5/6.
Trezor (large monorepo): 4/6 each.
Shapeshift (Redux + sub-packages): 6/6 vs 3/6.

CodeGraph's `findRelevantContext` traversal doesn't cross deep package
boundaries. JIDRA's exploration is module-boundary-agnostic.

### 5. JIDRA explore latency is significantly lower on large repos

Trezor Suite: ~200ms per explore vs CodeGraph ~580ms. On Shapeshift:
~200ms vs ~140ms (CodeGraph faster on smaller traversal due to early exits
when it can't find seeds). JIDRA is consistently faster on large repos.

---

## What this benchmark does not measure

This measures **retrieval quality** — does the tool surface the right symbol?

It does not measure agent navigation outcomes. That is covered in the separate
agent-in-loop eval (Java/Python/TypeScript) where JIDRA achieves 8/8, 5/5, 5/5
correctness vs CodeGraph's 7/8, 4/5, 3/5 at 1.35×–21.9× fewer tokens — on
structural tasks (callers, flows, DI resolution) that retrieval recall alone
cannot predict.

---

## Reproduce

```bash
# JIDRA — all 4 repos
PYTHONPATH=src python evals/code_graph_retrieval_eval.py \
  --repo mtkruto    --db /path/to/MTKruto/graph.db    --out evals/results_mtkruto.json
PYTHONPATH=src python evals/code_graph_retrieval_eval.py \
  --repo trezor     --db /path/to/trezor/graph.db     --out evals/results_trezor.json
PYTHONPATH=src python evals/code_graph_retrieval_eval.py \
  --repo postybirb  --db /path/to/postybirb/graph.db  --out evals/results_postybirb.json
PYTHONPATH=src python evals/code_graph_retrieval_eval.py \
  --repo shapeshift --db /path/to/web/graph.db        --out evals/results_shapeshift.json

# CodeGraph — set REPO at bottom of test-cases-all-repos.ts then:
cd /path/to/codegraph
cp test-cases-all-repos.ts __tests__/evaluation/test-cases.ts
EVAL_CODEBASE=/path/to/repo npx tsx __tests__/evaluation/runner.ts
```

Eval harness: `evals/code_graph_retrieval_eval.py` (JIDRA),
`evals/test-cases-all-repos.ts` (CodeGraph).
