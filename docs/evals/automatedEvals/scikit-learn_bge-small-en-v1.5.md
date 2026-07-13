# Retrieval Eval — scikit-learn/scikit-learn

**Date:** 2026-07-12  
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

| Pass rate | 0.5652 | 0.4783 | 0.3043 | 0.2174 |
| Mean recall | 0.5652 | 0.4783 | 0.3043 | 0.2174 |
| Mean MRR | 0.3713 | 0.3688 | 0.0987 | 0.0902 |
| Latency mean | 722ms | 381ms | 1ms | 0ms |
| Latency p50 | 428ms | 141ms | 1ms | 0ms |
| Latency p95 | 1195ms | 377ms | 2ms | 1ms |
| Pass count | 13/23 | 11/23 | 7/23 | 5/23 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.5652 | 0.3043 | +0.2609 |
| Mean recall | 0.5652 | 0.3043 | +0.2609 |
| Mean MRR | 0.3713 | 0.0987 | +0.2726 |

### Visual

```
Pass rate
  JIDRA search   █████████░░░░░░░  56.5%
  JIDRA explore  ████████░░░░░░░░  47.8%
  CG search      █████░░░░░░░░░░░  30.4%
  CG explore     ███░░░░░░░░░░░░░  21.7%

Mean recall
  JIDRA search   █████████░░░░░░░  56.5%
  JIDRA explore  ████████░░░░░░░░  47.8%
  CG search      █████░░░░░░░░░░░  30.4%
  CG explore     ███░░░░░░░░░░░░░  21.7%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  722ms
  JIDRA explore    ███████████░░░░░░░░░  381ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  1ms
  CG explore†      ░░░░░░░░░░░░░░░░░░░░  0ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 6 |
| JIDRA only | 7 |
| CG only | 2 |
| Both fail | 8 |
| **Total** | **23** |

**JIDRA net: +5 cases** (7 exclusive wins vs 2)

---

## JIDRA Exclusive Wins (7 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| scikit-learn__scikit-learn-10949 | 1.00 | 1.00 | 0.00 | 0.00 | `sklearn/utils/validation.py` |
| scikit-learn__scikit-learn-11281 | 1.00 | 1.00 | 0.00 | 0.00 | `sklearn/mixture/base.py` |
| scikit-learn__scikit-learn-13584 | 1.00 | 1.00 | 0.00 | 0.00 | `sklearn/utils/_pprint.py` |
| scikit-learn__scikit-learn-15535 | 1.00 | 1.00 | 0.00 | 0.00 | `sklearn/metrics/cluster/_supervised.py` |
| scikit-learn__scikit-learn-25500 | 1.00 | 0.00 | 0.00 | 0.00 | `sklearn/isotonic.py` |
| scikit-learn__scikit-learn-25638 | 1.00 | 1.00 | 0.00 | 0.00 | `sklearn/utils/multiclass.py` |
| scikit-learn__scikit-learn-25747 | 1.00 | 1.00 | 0.00 | 0.00 | `sklearn/utils/_set_output.py` |

---

## CG Exclusive Wins (2 cases)

CG (search or explore) passes; JIDRA (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File | Gap |
|------|-------------|--------------|-----------|------------|------|-----|
| scikit-learn__scikit-learn-13142 | 0.00 | 0.00 | 0.00 | 1.00 | `sklearn/mixture/base.py` | FTS term mismatch |
| scikit-learn__scikit-learn-14894 | 0.00 | 0.00 | 1.00 | 1.00 | `sklearn/svm/base.py` | FTS term mismatch |

---

## Both Fail (8 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| scikit-learn__scikit-learn-10297 | `sklearn/linear_model/ridge.py` |
| scikit-learn__scikit-learn-10508 | `sklearn/preprocessing/label.py` |
| scikit-learn__scikit-learn-13241 | `sklearn/decomposition/kernel_pca.py` |
| scikit-learn__scikit-learn-13496 | `sklearn/ensemble/iforest.py` |
| scikit-learn__scikit-learn-13497 | `sklearn/feature_selection/mutual_info_.py` |
| scikit-learn__scikit-learn-13779 | `sklearn/ensemble/voting.py` |
| scikit-learn__scikit-learn-14087 | `sklearn/linear_model/logistic.py` |
| scikit-learn__scikit-learn-14092 | `sklearn/neighbors/nca.py` |

---

## Key Insights

1. **Best JIDRA mode** (56.5%) vs **best CG mode** (30.4%) — JIDRA advantage: +0.2609.
2. **JIDRA explore** (47.8%) is the strongest single mode; **CG search** (30.4%) is CG's strongest.
3. **Latency** — JIDRA best: 381ms mean, CG best: 0ms mean (0% of JIDRA).
4. **CG explore** (21.7%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 8 cases** — config files, enums, migration files with sparse method content.
