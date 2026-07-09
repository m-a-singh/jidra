# Retrieval Eval — preactjs/preact

**Date:** 2026-07-05  
**Cases:** 17  
**Inputs:**
- JIDRA search: `evals/dataset/results/results_jidra_search.json`
- JIDRA explore: `evals/dataset/results/results_jidra_explore.json`
- CG search: `evals/dataset/results/results_cg_search.json`
- CG explore: `evals/dataset/results/results_cg_explore.json`

---

## Summary

| Metric | JIDRA search | JIDRA explore | CG search | CG explore |
|--------|-------------|--------------|-----------|------------|
| Pass rate | 0.9412 | 0.9412 | 0.4706 | 0.2941 |
| Mean recall | 0.8725 | 0.8725 | 0.4314 | 0.2549 |
| Mean MRR | 0.4371 | 0.4982 | 0.1415 | 0.1245 |
| Latency mean | 26ms | 9ms | 0ms | 0ms |
| Latency p50 | 22ms | 8ms | 0ms | 0ms |
| Latency p95 | 90ms | 32ms | 1ms | 1ms |
| Pass count | 16/17 | 16/17 | 8/17 | 5/17 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.9412 | 0.4706 | +0.4706 |
| Mean recall | 0.8725 | 0.4314 | +0.4411 |
| Mean MRR | 0.4982 | 0.1415 | +0.3567 |

### Visual

```
Pass rate
  JIDRA search   ███████████████░  94.1%
  JIDRA explore  ███████████████░  94.1%
  CG search      ████████░░░░░░░░  47.1%
  CG explore     █████░░░░░░░░░░░  29.4%

Mean recall
  JIDRA search   ██████████████░░  87.2%
  JIDRA explore  ██████████████░░  87.2%
  CG search      ███████░░░░░░░░░  43.1%
  CG explore     ████░░░░░░░░░░░░  25.5%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  26ms
  JIDRA explore    ███████░░░░░░░░░░░░░  9ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  0ms
  CG explore       ░░░░░░░░░░░░░░░░░░░░  0ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 8 |
| JIDRA only | 8 |
| CG only | 0 |
| Both fail | 1 |
| **Total** | **17** |

**JIDRA net: +8 cases** (8 exclusive wins vs 0)

---

## JIDRA Exclusive Wins (8 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| preactjs__preact-2896 | 1.00 | 1.00 | 0.00 | 0.00 | `src/diff/children.js` |
| preactjs__preact-2927 | 1.00 | 1.00 | 0.00 | 0.00 | `src/diff/props.js` |
| preactjs__preact-3010 | 0.50 | 0.50 | 0.00 | 0.00 | `src/diff/children.js` |
| preactjs__preact-3345 | 1.00 | 1.00 | 0.00 | 0.00 | `hooks/src/index.js` |
| preactjs__preact-3454 | 1.00 | 1.00 | 0.00 | 0.00 | `src/diff/props.js` |
| preactjs__preact-3739 | 1.00 | 1.00 | 0.00 | 0.00 | `hooks/src/index.js` |
| preactjs__preact-4245 | 1.00 | 1.00 | 0.00 | 0.00 | `hooks/src/index.js` |
| preactjs__preact-4316 | 1.00 | 1.00 | 0.00 | 0.00 | `src/diff/props.js` |

---

## Both Fail (1 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| preactjs__preact-3689 | `hooks/src/index.d.ts, hooks/src/internal.d.ts` |

---

## Key Insights

1. **Best JIDRA mode** (94.1%) vs **best CG mode** (47.1%) — JIDRA advantage: +0.4706.
2. **JIDRA explore** (94.1%) is the strongest single mode; **CG search** (47.1%) is CG's strongest.
3. **Latency** — JIDRA best: 9ms mean, CG best: 0ms mean (3% of JIDRA).
4. **CG explore** (29.4%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 1 cases** — config files, enums, migration files with sparse method content.
