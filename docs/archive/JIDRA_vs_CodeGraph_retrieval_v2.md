# JIDRA vs CodeGraph — Retrieval Quality Benchmark

> **This doc covers MTKruto only (V1, 2026-07-03).**
> For the full 4-repo V2 benchmark see `JIDRA_vs_CodeGraph_retrieval_final.md`.

**Repo:** MTKruto (TypeScript Telegram client, ~2,300 methods)
**Cases:** 12 identical test cases run against both tools
**Methodology:** mirrors CodeGraph's own `__tests__/evaluation/runner.ts`
**Pass threshold:** recall ≥ 0.5
**Date:** 2026-07-03 (V1) — superseded by V2 results in `_final.md`

---

## Summary (V1)

| metric | JIDRA | CodeGraph |
|---|---|---|
| **passed** | **11/12 (92%)** | **11/12 (92%)** |
| **mean recall** | **0.681** | **0.760** |
| **mean MRR (search)** | **0.418** | **0.833** |
| search passed | 5/6 | 5/6 |
| explore passed | 6/6 | 6/6 |

V1 conclusion: tied on pass rate, CodeGraph wins MRR, JIDRA wins `serializeObject`.

**V2 update (2026-07-05):** exact-name pre-query phase added; tested across 4 repos.
Full V2 results show JIDRA 44/48 (92%) vs CodeGraph 35/48 (73%). See `_final.md`.

---

## Per-case results

### Search

| case | JIDRA recall | JIDRA mrr | CG recall | CG mrr | winner |
|---|---|---|---|---|---|
| search-class-client | 1.00 | 0.06 | 1.00 | 1.00 | CG (rank) |
| search-method-sendMessage | 1.00 | 1.00 | 1.00 | 1.00 | tie |
| search-method-serialize | 1.00 | 1.00 | 0.00 | 0.00 | **JIDRA** |
| search-class-transport | 1.00 | 0.20 | 1.00 | 1.00 | CG (rank) |
| search-class-session | 0.00 | 0.00 | 1.00 | 1.00 | **CG** |
| search-method-invoke | 1.00 | 0.25 | 1.00 | 1.00 | CG (rank) |

**JIDRA wins:** `serializeObject` — CodeGraph misses it (standalone function, not indexed).

**CodeGraph wins:** `SessionEncrypted` — JIDRA misses (class symbol; JIDRA is method-centric).
Note: `SessionEncrypted` was excluded from V2 test cases — class symbols are out of scope.

---

### Explore

| case | JIDRA recall | CG recall | JIDRA found | CG found |
|---|---|---|---|---|
| explore-send-flow | 0.67 | 0.67 | sendMessage, send | sendMessage, send |
| explore-session | 0.50 | 1.00 | send | SessionEncrypted, send |
| explore-client-invoke | 0.50 | 1.00 | invoke | invoke, Client |
| explore-connection | 0.50 | 0.50 | connect | connect |
| explore-storage | 0.50 | 0.50 | Storage | Storage |
| explore-error-handling | 0.50 | 0.50 | RPCError | RPCError |

CodeGraph achieves higher explore recall on `session` and `client-invoke` because it
surfaces class nodes (`SessionEncrypted`, `Client`) as first-class results. JIDRA's
explore is method-centric; class-level nodes appear only as metadata.

---

## Key findings (V1)

1. **`serializeObject`** — JIDRA's search win. Standalone function, CodeGraph doesn't index it.
2. **`SessionEncrypted`** — JIDRA's search loss. Class symbol, out of scope for V2.
3. **MRR gap** — JIDRA finds symbols but buries them on common names. Fixed in V2 with
   exact-name pre-query; gap narrowed but not closed on high-frequency names.
4. **Explore tied on pass rate** — 6/6 each. CodeGraph higher per-case recall due to class nodes.

---

## Reproduce

```bash
# JIDRA
python evals/analysis/codegraph/code_graph_retrieval_eval.py \
  --repo mtkruto \
  --db /path/to/MTKruto/graph.db \
  --out evals/results_mtkruto_v2.json

# CodeGraph
cd /path/to/codegraph
EVAL_CODEBASE=/path/to/MTKruto npx tsx __tests__/evaluation/runner.ts
```
