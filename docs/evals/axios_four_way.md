# Retrieval Eval — axios/axios

**Date:** 2026-07-05  
**Cases:** 6  
**Inputs:**
- JIDRA search: `evals/dataset/results/results_jidra_search.json`
- JIDRA explore: `evals/dataset/results/results_jidra_explore.json`
- CG search: `evals/dataset/results/results_cg_search.json`
- CG explore: `evals/dataset/results/results_cg_explore.json`

---

## Summary

| Metric | JIDRA search | JIDRA explore | CG search | CG explore |
|--------|-------------|--------------|-----------|------------|
| Pass rate | 1.0000 | 0.8333 | 0.5000 | 0.1667 |
| Mean recall | 0.8333 | 0.8125 | 0.4167 | 0.1875 |
| Mean MRR | 0.5403 | 0.6461 | 0.1071 | 0.2222 |
| Latency mean | 29ms | 10ms | 0ms | 0ms |
| Latency p50 | 30ms | 10ms | 0ms | 0ms |
| Latency p95 | 43ms | 16ms | 1ms | 1ms |
| Pass count | 6/6 | 5/6 | 3/6 | 1/6 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 1.0000 | 0.5000 | +0.5000 |
| Mean recall | 0.8333 | 0.4167 | +0.4166 |
| Mean MRR | 0.6461 | 0.2222 | +0.4239 |

### Visual

```
Pass rate
  JIDRA search   ████████████████  100.0%
  JIDRA explore  █████████████░░░  83.3%
  CG search      ████████░░░░░░░░  50.0%
  CG explore     ███░░░░░░░░░░░░░  16.7%

Mean recall
  JIDRA search   █████████████░░░  83.3%
  JIDRA explore  █████████████░░░  81.2%
  CG search      ███████░░░░░░░░░  41.7%
  CG explore     ███░░░░░░░░░░░░░  18.8%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  29ms
  JIDRA explore    ███████░░░░░░░░░░░░░  10ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  0ms
  CG explore       ░░░░░░░░░░░░░░░░░░░░  0ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 3 |
| JIDRA only | 3 |
| CG only | 0 |
| Both fail | 0 |
| **Total** | **6** |

**JIDRA net: +3 cases** (3 exclusive wins vs 0)

---

## JIDRA Exclusive Wins (3 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| axios__axios-4731 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/adapters/http.js` |
| axios__axios-4738 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/adapters/http.js` |
| axios__axios-6539 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/helpers/isAbsoluteURL.js` |

---

## Key Insights

1. **Best JIDRA mode** (100.0%) vs **best CG mode** (50.0%) — JIDRA advantage: +0.5000.
2. **JIDRA explore** (83.3%) is the strongest single mode; **CG search** (50.0%) is CG's strongest.
3. **Latency** — JIDRA best: 10ms mean, CG best: 0ms mean (2% of JIDRA).
4. **CG explore** (16.7%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 0 cases** — config files, enums, migration files with sparse method content.
