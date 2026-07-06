# JIDRA vs CodeGraph — Method-Level Retrieval Benchmark

**Repos:** MTKruto, Trezor Suite, PostyBirb, Shapeshift Web (all TypeScript)
**Date:** 2026-07-05 (V2)
**Methodology:** mirrors CodeGraph's `__tests__/evaluation/runner.ts` exactly
**Scoring:** Recall@20 + MRR for search, Recall for explore
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

## Results summary (V2)

| repo | methods | JIDRA passed | CG passed | JIDRA recall | CG recall | JIDRA search MRR | CG search MRR |
|---|---|---|---|---|---|---|---|
| **MTKruto** | ~2,300 | **12/12 (100%)** | 11/12 (92%) | **0.847** | 0.720 | 0.251 | 1.00† |
| **Trezor Suite** | ~12,600 | **10/12 (83%)** | 8/12 (67%) | **0.708** | 0.500 | 0.508 | 1.00† |
| **PostyBirb** | ~2,200 | **10/12 (83%)** | 9/12 (75%) | **0.708** | 0.625 | 0.067 | 1.00† |
| **Shapeshift** | ~6,200 | **12/12 (100%)** | 7/12 (58%) | **0.792** | 0.458 | 0.700 | 1.00† |
| **Aggregate** | — | **44/48 (92%)** | **35/48 (73%)** | **0.764** | 0.576 | 0.382 | 1.00† |

†CodeGraph MRR is 1.00 on cases it passes — it returns the symbol at rank 1 when it finds it.
JIDRA's lower MRR reflects finding the right symbol at rank 2–10 on ambiguous common names.
When CodeGraph misses, it scores 0.00; JIDRA's search recall is 1.00 across all repos.

---

## Per-repo detail

### MTKruto (TypeScript Telegram client, ~2,300 methods)

**Search:**

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr |
|---|---|---|---|---|
| sendMessage | 1.00 | 0.06 | 1.00 | 1.00 |
| invoke | 1.00 | 0.05 | 1.00 | 1.00 |
| serializeObject | 1.00 | 0.25 | **0.00** | **0.00** |
| getMe | 1.00 | 0.05 | 1.00 | 1.00 |
| signIn | 1.00 | 0.10 | 1.00 | 1.00 |
| forwardMessages | 1.00 | 1.00 | 1.00 | 1.00 |

JIDRA wins `serializeObject` — standalone TypeScript function CodeGraph doesn't index.
Low MRR on `sendMessage`/`invoke`/`getMe` — common names with many overloads, exact-name
pre-query pins the target but BM25 re-ranks it below other high-frequency matches.

**Explore:** JIDRA 6/6, CodeGraph 6/6. Both miss `serializeObject` in send-flow and
`send`/`getMe`/`forwardMessages` in respective explore cases.

---

### Trezor Suite (TypeScript monorepo, ~12,600 methods)

**Search:**

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr |
|---|---|---|---|---|
| signTransaction | 1.00 | 0.50 | 1.00 | 1.00 |
| getAddress | 1.00 | 0.05 | **0.00** | **0.00** |
| getFeatures | 1.00 | 0.50 | 1.00 | 1.00 |
| useSendForm | 1.00 | 0.50 | **0.00** | **0.00** |
| applySettings | 1.00 | 0.50 | 1.00 | 1.00 |
| estimateFee | 1.00 | 1.00 | 1.00 | 1.00 |

JIDRA 6/6, CodeGraph 4/6. CodeGraph misses `getAddress` and `useSendForm` —
both exist but aren't surfaced by codegraph's index.

**Explore:** JIDRA 4/6, CodeGraph 4/6. Same 2 failures (`explore-device`, `explore-fees`).
JIDRA explore latency ~200ms vs CodeGraph ~580ms on this large repo.

---

### PostyBirb (NestJS + Electron, ~2,200 methods)

**Search:** JIDRA 6/6, CodeGraph 6/6. All 6 method symbols found by both.
JIDRA MRR low (0.04–0.14) — all targets found but not at rank 1.

**Explore:**

| case | JIDRA recall | CG recall | notes |
|---|---|---|---|
| explore-submit | **0.00** | 0.50 | JIDRA misses both; CG misses postSubmission only |
| explore-file | 1.00 | 0.50 | JIDRA wins — finds both uploadFile and post |
| explore-auth | 0.50 | **0.00** | JIDRA finds login; CG misses both |
| explore-website | 0.50 | **0.00** | JIDRA finds post; CG misses both |
| explore-update | **0.00** | **0.00** | both fail — NestJS abstract submission pipeline |
| explore-posting | 0.50 | 0.50 | both miss postSubmission |

JIDRA 4/6, CodeGraph 3/6. NestJS abstract class hierarchy is the common failure mode.

---

### Shapeshift Web (React/Redux DeFi app, ~6,200 methods)

**Search:**

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr |
|---|---|---|---|---|
| getTradeQuote | 1.00 | 1.00 | 1.00 | 1.00 |
| signTransaction | 1.00 | 1.00 | 1.00 | 1.00 |
| broadcastTransaction | 1.00 | 1.00 | 1.00 | 1.00 |
| estimateFees | 1.00 | 0.10 | 1.00 | 1.00 |
| getRates | 1.00 | 0.10 | **0.00** | **0.00** |
| getAssets | 1.00 | 1.00 | **0.00** | **0.00** |

JIDRA 6/6, CodeGraph 4/6. CodeGraph misses `getRates` and `getAssets` —
route handler functions in package sub-directories.

**Explore:** JIDRA 6/6, CodeGraph 3/6. CodeGraph fails `explore-fees`,
`explore-rates`, `explore-trade` — traversal doesn't cross package
boundaries in swapper sub-packages. JIDRA is module-boundary-agnostic.

---

## Key findings

### 1. JIDRA search recall is 1.00 across all repos; CodeGraph has indexing gaps

JIDRA finds every target in top-20. CodeGraph misses: standalone functions,
React hooks, route handlers in monorepo sub-packages.

### 2. CodeGraph MRR is 1.00 on cases it passes; JIDRA ranks lower on common names

When CodeGraph finds a symbol it returns it at rank 1. JIDRA finds it but
buries it on names like `invoke`, `sendMessage`, `login` (many overloads, many
files with that token in source). Exact-name pre-query phase (added V2) helps but
doesn't fully close the gap on high-frequency names.

### 3. Explore gap widens in complex monorepo structures

MTKruto (small, flat): 6/6 vs 6/6 (tied).
Trezor (large monorepo): 4/6 each (tied).
Shapeshift (Redux + sub-packages): 6/6 vs 3/6 (**JIDRA wins**).
PostyBirb (NestJS abstract classes): 4/6 vs 3/6 (**JIDRA wins**).

CodeGraph's `findRelevantContext` traversal doesn't cross deep package boundaries.

### 4. JIDRA explore latency faster on large repos

Trezor Suite: ~200ms per explore vs CodeGraph ~580ms.
Shapeshift: ~150–350ms vs ~140–180ms (roughly comparable).

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
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo mtkruto    --db /path/to/MTKruto/graph.db    --out evals/results_mtkruto_v2.json
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo trezor     --db /path/to/trezor/graph.db     --out evals/results_trezor_v2.json
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo postybirb  --db /path/to/postybirb/graph.db  --out evals/results_postybirb_v2.json
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo shapeshift --db /path/to/web/graph.db        --out evals/results_shapeshift_v2.json

# CodeGraph
cd /path/to/codegraph
EVAL_CODEBASE=/path/to/repo npx tsx __tests__/evaluation/runner.ts
```

Raw per-case data: `evals/analysis/codegraph/results/comparison_v2.md`
