# Retrieval Eval — matplotlib/matplotlib

**Date:** 2026-07-07  
**Cases:** 23  
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

| Pass rate | 0.8261 | 0.8261 | 0.2609 | 0.0870 |
| Mean recall | 0.8261 | 0.8261 | 0.2609 | 0.0870 |
| Mean MRR | 0.5671 | 0.5994 | 0.0479 | 0.0326 |
| Latency mean | 350ms | 108ms | 1ms | 1ms |
| Latency p50 | 305ms | 89ms | 1ms | 0ms |
| Latency p95 | 628ms | 201ms | 2ms | 1ms |
| Pass count | 19/23 | 19/23 | 6/23 | 2/23 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.8261 | 0.2609 | +0.5652 |
| Mean recall | 0.8261 | 0.2609 | +0.5652 |
| Mean MRR | 0.5994 | 0.0479 | +0.5515 |

### Visual

```
Pass rate
  JIDRA search   █████████████░░░  82.6%
  JIDRA explore  █████████████░░░  82.6%
  CG search      ████░░░░░░░░░░░░  26.1%
  CG explore     █░░░░░░░░░░░░░░░  8.7%

Mean recall
  JIDRA search   █████████████░░░  82.6%
  JIDRA explore  █████████████░░░  82.6%
  CG search      ████░░░░░░░░░░░░  26.1%
  CG explore     █░░░░░░░░░░░░░░░  8.7%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  350ms
  JIDRA explore    ██████░░░░░░░░░░░░░░  108ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  1ms
  CG explore†      ░░░░░░░░░░░░░░░░░░░░  1ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 5 |
| JIDRA only | 14 |
| CG only | 1 |
| Both fail | 3 |
| **Total** | **23** |

**JIDRA net: +13 cases** (14 exclusive wins vs 1)

---

## JIDRA Exclusive Wins (14 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| matplotlib__matplotlib-22711 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/widgets.py` |
| matplotlib__matplotlib-22835 | 1.00 | 1.00 | 0.00 | 0.00 | `lib/matplotlib/artist.py` |
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

## CG Exclusive Wins (1 cases)

CG (search or explore) passes; JIDRA (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File | Gap |
|------|-------------|--------------|-----------|------------|------|-----|
| matplotlib__matplotlib-25079 | 0.00 | 0.00 | 1.00 | 1.00 | `lib/matplotlib/colors.py` | FTS term mismatch |

---

## Both Fail (3 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| matplotlib__matplotlib-23314 | `lib/mpl_toolkits/mplot3d/axes3d.py` |
| matplotlib__matplotlib-24265 | `lib/matplotlib/style/core.py` |
| matplotlib__matplotlib-25311 | `lib/matplotlib/offsetbox.py` |

---

## Key Insights

1. **Best JIDRA mode** (82.6%) vs **best CG mode** (26.1%) — JIDRA advantage: +0.5652.
2. **JIDRA explore** (82.6%) is the strongest single mode; **CG search** (26.1%) is CG's strongest.
3. **Latency** — JIDRA best: 108ms mean, CG best: 1ms mean (1% of JIDRA).
4. **CG explore** (8.7%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 3 cases** — config files, enums, migration files with sparse method content.
