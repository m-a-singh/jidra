# Retrieval Eval — sympy/sympy

**Date:** 2026-07-12  
**Cases:** 77  
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

| Pass rate | 0.8571 | 0.7662 | 0.1429 | 0.1039 |
| Mean recall | 0.8571 | 0.7662 | 0.1429 | 0.1039 |
| Mean MRR | 0.5087 | 0.5074 | 0.0365 | 0.0319 |
| Latency mean | 914ms | 299ms | 2ms | 1ms |
| Latency p50 | 807ms | 219ms | 1ms | 1ms |
| Latency p95 | 1618ms | 369ms | 11ms | 5ms |
| Pass count | 66/77 | 59/77 | 11/77 | 8/77 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.8571 | 0.1429 | +0.7142 |
| Mean recall | 0.8571 | 0.1429 | +0.7142 |
| Mean MRR | 0.5087 | 0.0365 | +0.4722 |

### Visual

```
Pass rate
  JIDRA search   ██████████████░░  85.7%
  JIDRA explore  ████████████░░░░  76.6%
  CG search      ██░░░░░░░░░░░░░░  14.3%
  CG explore     ██░░░░░░░░░░░░░░  10.4%

Mean recall
  JIDRA search   ██████████████░░  85.7%
  JIDRA explore  ████████████░░░░  76.6%
  CG search      ██░░░░░░░░░░░░░░  14.3%
  CG explore     ██░░░░░░░░░░░░░░  10.4%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  914ms
  JIDRA explore    ███████░░░░░░░░░░░░░  299ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  2ms
  CG explore†      ░░░░░░░░░░░░░░░░░░░░  1ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 12 |
| JIDRA only | 54 |
| CG only | 0 |
| Both fail | 11 |
| **Total** | **77** |

**JIDRA net: +54 cases** (54 exclusive wins vs 0)

---

## JIDRA Exclusive Wins (54 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| sympy__sympy-11897 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-12171 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/mathematica.py` |
| sympy__sympy-12454 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/matrices/matrices.py` |
| sympy__sympy-13031 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/matrices/sparse.py` |
| sympy__sympy-13043 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/integrals/intpoly.py` |
| sympy__sympy-13177 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/core/mod.py` |
| sympy__sympy-13437 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/functions/combinatorial/numbers.py` |
| sympy__sympy-13471 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/core/numbers.py` |
| sympy__sympy-13480 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/functions/elementary/hyperbolic.py` |
| sympy__sympy-13647 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/matrices/common.py` |
| sympy__sympy-13773 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/matrices/common.py` |
| sympy__sympy-13895 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/core/numbers.py` |
| sympy__sympy-13971 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-14024 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/core/numbers.py` |
| sympy__sympy-14317 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-14396 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/polys/polyoptions.py` |
| sympy__sympy-14774 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-14817 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/pretty/pretty.py` |
| sympy__sympy-15011 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/utilities/lambdify.py` |
| sympy__sympy-15308 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-15345 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/mathematica.py` |
| sympy__sympy-15609 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-15678 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/geometry/util.py` |
| sympy__sympy-16106 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/mathml.py` |
| sympy__sympy-16281 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/pretty/pretty.py` |
| sympy__sympy-16503 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/pretty/pretty.py` |
| sympy__sympy-16792 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/utilities/codegen.py` |
| sympy__sympy-17139 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/simplify/fu.py` |
| sympy__sympy-17655 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/geometry/point.py` |
| sympy__sympy-18189 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/solvers/diophantine.py` |
| sympy__sympy-18199 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/ntheory/residue_ntheory.py` |
| sympy__sympy-18532 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/core/basic.py` |
| sympy__sympy-18698 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/polys/polytools.py` |
| sympy__sympy-18835 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/utilities/iterables.py` |
| sympy__sympy-19007 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/matrices/expressions/blockmatrix.py` |
| sympy__sympy-19254 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/polys/factortools.py` |
| sympy__sympy-19487 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/functions/elementary/complexes.py` |
| sympy__sympy-20049 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/physics/vector/point.py` |
| sympy__sympy-20154 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/utilities/iterables.py` |
| sympy__sympy-20212 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/core/power.py` |
| sympy__sympy-20322 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/core/mul.py` |
| sympy__sympy-20442 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/physics/units/util.py` |
| sympy__sympy-20639 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/pretty/pretty.py` |
| sympy__sympy-21055 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/assumptions/refine.py` |
| sympy__sympy-21171 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/latex.py` |
| sympy__sympy-21379 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/core/mod.py` |
| sympy__sympy-21612 | 1.00 | 0.00 | 0.00 | 0.00 | `sympy/printing/str.py` |
| sympy__sympy-22005 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/solvers/polysys.py` |
| sympy__sympy-22840 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/simplify/cse_main.py` |
| sympy__sympy-23117 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/tensor/array/ndim_array.py` |
| sympy__sympy-23191 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/printing/pretty/pretty.py` |
| sympy__sympy-23262 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/utilities/lambdify.py` |
| sympy__sympy-24066 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/physics/units/unitsystem.py` |
| sympy__sympy-24213 | 1.00 | 1.00 | 0.00 | 0.00 | `sympy/physics/units/unitsystem.py` |

---

## Both Fail (11 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| sympy__sympy-11400 | `sympy/printing/ccode.py` |
| sympy__sympy-12236 | `sympy/polys/domains/polynomialring.py` |
| sympy__sympy-12419 | `sympy/matrices/expressions/matexpr.py` |
| sympy__sympy-13146 | `sympy/core/operations.py` |
| sympy__sympy-13915 | `sympy/core/mul.py` |
| sympy__sympy-15346 | `sympy/simplify/trigsimp.py` |
| sympy__sympy-17022 | `sympy/printing/pycode.py` |
| sympy__sympy-18087 | `sympy/core/exprtools.py` |
| sympy__sympy-20590 | `sympy/core/_print_helpers.py` |
| sympy__sympy-21627 | `sympy/functions/elementary/complexes.py` |
| sympy__sympy-22714 | `sympy/geometry/point.py` |

---

## Key Insights

1. **Best JIDRA mode** (85.7%) vs **best CG mode** (14.3%) — JIDRA advantage: +0.7142.
2. **JIDRA explore** (76.6%) is the strongest single mode; **CG search** (14.3%) is CG's strongest.
3. **Latency** — JIDRA best: 299ms mean, CG best: 1ms mean (0% of JIDRA).
4. **CG explore** (10.4%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 11 cases** — config files, enums, migration files with sparse method content.
