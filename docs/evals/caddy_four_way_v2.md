# Retrieval Eval — caddyserver/caddy

**Date:** 2026-07-07  
**Cases:** 14  
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

| Pass rate | 0.7857 | 0.7143 | 0.0714 | 0.2143 |
| Mean recall | 0.7857 | 0.6905 | 0.0357 | 0.1786 |
| Mean MRR | 0.2074 | 0.2230 | 0.0119 | 0.0176 |
| Latency mean | 103ms | 34ms | 2ms | 1ms |
| Latency p50 | 105ms | 31ms | 0ms | 1ms |
| Latency p95 | 179ms | 62ms | 23ms | 4ms |
| Pass count | 11/14 | 10/14 | 1/14 | 3/14 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.7857 | 0.2143 | +0.5714 |
| Mean recall | 0.7857 | 0.1786 | +0.6071 |
| Mean MRR | 0.2230 | 0.0176 | +0.2054 |

### Visual

```
Pass rate
  JIDRA search   █████████████░░░  78.6%
  JIDRA explore  ███████████░░░░░  71.4%
  CG search      █░░░░░░░░░░░░░░░  7.1%
  CG explore     ███░░░░░░░░░░░░░  21.4%

Mean recall
  JIDRA search   █████████████░░░  78.6%
  JIDRA explore  ███████████░░░░░  69.0%
  CG search      █░░░░░░░░░░░░░░░  3.6%
  CG explore     ███░░░░░░░░░░░░░  17.9%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  103ms
  JIDRA explore    ███████░░░░░░░░░░░░░  34ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  2ms
  CG explore†      ░░░░░░░░░░░░░░░░░░░░  1ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 3 |
| JIDRA only | 8 |
| CG only | 0 |
| Both fail | 3 |
| **Total** | **14** |

**JIDRA net: +8 cases** (8 exclusive wins vs 0)

---

## JIDRA Exclusive Wins (8 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| caddyserver__caddy-5404 | 1.00 | 1.00 | 0.00 | 0.00 | `caddyconfig/caddyfile/lexer.go` |
| caddyserver__caddy-5626 | 1.00 | 0.67 | 0.00 | 0.00 | `caddyconfig/caddyfile/parse.go, caddyconfig/caddyfile/lexer.go, caddyconfig/caddyfile/dispenser.go` |
| caddyserver__caddy-5761 | 1.00 | 1.00 | 0.00 | 0.00 | `caddyconfig/caddyfile/lexer.go` |
| caddyserver__caddy-5870 | 1.00 | 1.00 | 0.00 | 0.00 | `admin.go` |
| caddyserver__caddy-6051 | 1.00 | 1.00 | 0.00 | 0.00 | `caddyconfig/caddyfile/lexer.go` |
| caddyserver__caddy-6115 | 1.00 | 1.00 | 0.00 | 0.00 | `modules/caddyhttp/reverseproxy/selectionpolicies.go` |
| caddyserver__caddy-6288 | 1.00 | 1.00 | 0.00 | 0.00 | `caddyconfig/httpcaddyfile/httptype.go, caddyconfig/caddyfile/lexer.go` |
| caddyserver__caddy-6411 | 1.00 | 1.00 | 0.00 | 0.00 | `replacer.go` |

---

## Both Fail (3 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| caddyserver__caddy-4943 | `modules/logging/filters.go` |
| caddyserver__caddy-6345 | `modules/caddypki/acmeserver/caddyfile.go` |
| caddyserver__caddy-6350 | `modules/caddyhttp/ip_matchers.go` |

---

## Key Insights

1. **Best JIDRA mode** (78.6%) vs **best CG mode** (21.4%) — JIDRA advantage: +0.5714.
2. **JIDRA explore** (71.4%) is the strongest single mode; **CG search** (7.1%) is CG's strongest.
3. **Latency** — JIDRA best: 34ms mean, CG best: 1ms mean (3% of JIDRA).
4. **CG explore** (21.4%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 3 cases** — config files, enums, migration files with sparse method content.
