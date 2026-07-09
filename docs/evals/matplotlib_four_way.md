# Retrieval Eval — matplotlib/matplotlib

**Date:** 2026-07-05  
**Cases:** 23  
**Inputs:**
- JIDRA search: `evals/dataset/results/results_jidra_search.json`
- JIDRA explore: `evals/dataset/results/results_jidra_explore.json`
- CG search: `evals/dataset/results/results_cg_search.json`
- CG explore: `evals/dataset/results/results_cg_explore.json`

---

## Summary

| Metric | JIDRA search | JIDRA explore | CG search | CG explore |
|--------|-------------|--------------|-----------|------------|
| Pass rate | 0.9130 | 0.8261 | 0.2609 | 0.0870 |
| Mean recall | 0.9130 | 0.8261 | 0.2609 | 0.0870 |
| Mean MRR | 0.5788 | 0.6028 | 0.0479 | 0.0326 |
| Latency mean | 379ms | 88ms | 1ms | 1ms |
| Latency p50 | 334ms | 70ms | 1ms | 0ms |
| Latency p95 | 761ms | 165ms | 2ms | 2ms |
| Pass count | 21/23 | 19/23 | 6/23 | 2/23 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.9130 | 0.2609 | +0.6521 |
| Mean recall | 0.9130 | 0.2609 | +0.6521 |
| Mean MRR | 0.6028 | 0.0479 | +0.5549 |

### Visual

```
Pass rate
  JIDRA search   ███████████████░  91.3%
  JIDRA explore  █████████████░░░  82.6%
  CG search      ████░░░░░░░░░░░░  26.1%
  CG explore     █░░░░░░░░░░░░░░░  8.7%

Mean recall
  JIDRA search   ███████████████░  91.3%
  JIDRA explore  █████████████░░░  82.6%
  CG search      ████░░░░░░░░░░░░  26.1%
  CG explore     █░░░░░░░░░░░░░░░  8.7%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  379ms
  JIDRA explore    █████░░░░░░░░░░░░░░░  88ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  1ms
  CG explore       ░░░░░░░░░░░░░░░░░░░░  1ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 6 |
| JIDRA only | 15 |
| CG only | 0 |
| Both fail | 2 |
| **Total** | **23** |

**JIDRA net: +15 cases** (15 exclusive wins vs 0)

---

## JIDRA Exclusive Wins (15 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| matplotlib__matplotlib-22711 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/widgets.py` |
| matplotlib__matplotlib-22835 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/artist.py` |
| matplotlib__matplotlib-23314 | 1.00 | 0.00 | 0.00 | 0.00 | `lib/mpl_toolkits/mplot3d/axes3d.py` |
| matplotlib__matplotlib-23476 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/figure.py` |
| matplotlib__matplotlib-23562 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/mpl_toolkits/mplot3d/art3d.py` |
| matplotlib__matplotlib-23563 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/mpl_toolkits/mplot3d/art3d.py` |
| matplotlib__matplotlib-23964 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/backends/backend_ps.py` |
| matplotlib__matplotlib-23987 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/figure.py` |
| matplotlib__matplotlib-24149 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/axes/_axes.py` |
| matplotlib__matplotlib-24334 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/axis.py` |
| matplotlib__matplotlib-24970 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/colors.py` |
| matplotlib__matplotlib-25332 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/cbook.py` |
| matplotlib__matplotlib-25433 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/figure.py` |
| matplotlib__matplotlib-25442 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/offsetbox.py` |
| matplotlib__matplotlib-26020 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/mpl_toolkits/axes_grid1/axes_grid.py` |

---

## Both Fail (2 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| matplotlib__matplotlib-24265 | `lib/matplotlib/style/core.py` |
| matplotlib__matplotlib-25311 | `lib/matplotlib/offsetbox.py` |

---

## Key Insights

1. **Best JIDRA mode** (91.3%) vs **best CG mode** (26.1%) — JIDRA advantage: +0.6521.
2. **JIDRA explore** (82.6%) is the strongest single mode; **CG search** (26.1%) is CG's strongest.
3. **Latency** — JIDRA best: 88ms mean, CG best: 1ms mean (1% of JIDRA).
4. **CG explore** (8.7%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 2 cases** — config files, enums, migration files with sparse method content.
