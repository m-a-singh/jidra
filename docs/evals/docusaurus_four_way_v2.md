# Retrieval Eval — facebook/docusaurus

**Date:** 2026-07-07  
**Cases:** 5  
**Inputs:**
- JIDRA search: `evals/dataset/results/results_jidra_search.json`
- JIDRA explore: `evals/dataset/results/results_jidra_explore.json`
- CG search: `evals/dataset/results/results_cg_search.json`
- CG explore: `evals/dataset/results/results_cg_explore.json`

---

## Summary

| Metric | JIDRA search | JIDRA explore | CG search | CG explore† |
|--------|-------------|--------------|-----------|-------------|

> † CG explore = 1-hop edge expansion approximation — not CodeGraph's native semantic explore. Underestimates real CG explore performance.

| Pass rate | 0.6000 | 0.4000 | 0.2000 | 0.0000 |
| Mean recall | 0.6667 | 0.4000 | 0.2000 | 0.0000 |
| Mean MRR | 0.0395 | 0.0722 | 0.0100 | 0.0000 |
| Latency mean | 129ms | 44ms | 10ms | 2ms |
| Latency p50 | 124ms | 42ms | 0ms | 1ms |
| Latency p95 | 157ms | 54ms | 38ms | 6ms |
| Pass count | 3/5 | 2/5 | 1/5 | 0/5 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.6000 | 0.2000 | +0.4000 |
| Mean recall | 0.6667 | 0.2000 | +0.4667 |
| Mean MRR | 0.0722 | 0.0100 | +0.0622 |

### Visual

```
Pass rate
  JIDRA search   ██████████░░░░░░  60.0%
  JIDRA explore  ██████░░░░░░░░░░  40.0%
  CG search      ███░░░░░░░░░░░░░  20.0%
  CG explore     ░░░░░░░░░░░░░░░░  0.0%

Mean recall
  JIDRA search   ███████████░░░░░  66.7%
  JIDRA explore  ██████░░░░░░░░░░  40.0%
  CG search      ███░░░░░░░░░░░░░  20.0%
  CG explore     ░░░░░░░░░░░░░░░░  0.0%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  129ms
  JIDRA explore    ███████░░░░░░░░░░░░░  44ms
  CG search        █░░░░░░░░░░░░░░░░░░░  10ms
  CG explore†      ░░░░░░░░░░░░░░░░░░░░  2ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 1 |
| JIDRA only | 2 |
| CG only | 0 |
| Both fail | 2 |
| **Total** | **5** |

**JIDRA net: +2 cases** (2 exclusive wins vs 0)

---

## JIDRA Exclusive Wins (2 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| facebook__docusaurus-10309 | 1.00 | 0.00 | 0.00 | 0.00 | `packages/docusaurus-plugin-content-docs/src/client/docsClientUtils.ts` |
| facebook__docusaurus-8927 | 1.00 | 1.00 | 0.00 | 0.00 | `packages/docusaurus-utils/src/markdownLinks.ts` |

---

## Both Fail (2 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| facebook__docusaurus-9183 | `packages/docusaurus-theme-classic/src/theme/CodeBlock/Content/String.tsx, packages/docusaurus-theme-classic/src/options.ts` |
| facebook__docusaurus-9897 | `packages/docusaurus-utils/src/markdownUtils.ts` |

---

## Key Insights

1. **Best JIDRA mode** (60.0%) vs **best CG mode** (20.0%) — JIDRA advantage: +0.4000.
2. **JIDRA explore** (40.0%) is the strongest single mode; **CG search** (20.0%) is CG's strongest.
3. **Latency** — JIDRA best: 44ms mean, CG best: 2ms mean (6% of JIDRA).
4. **CG explore** (0.0%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 2 cases** — config files, enums, migration files with sparse method content.
