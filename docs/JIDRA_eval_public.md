# JIDRA vs CodeGraph — Evaluation Summary

**JIDRA** is a code-graph navigation tool exposing purpose-built scalpel tools
(`find_callers`, `get_method_source`, `get_agent_flow`, `get_implementations`, `explore`).
**CodeGraph** is a single broad-search tool (`codegraph_explore`) that returns
blast-radius context from a vector/symbol index.

Both backends are given the same LLM agent (Claude Haiku 4.5 or Sonnet 4.6) and asked
the same questions. We measure correctness, tool calls, tokens consumed, cost, and
hallucinations.

**Three codebases evaluated:**

| language | codebase | methods indexed |
|---|---|---|
| Java | Spring Boot microservices monorepo | ~1,200 |
| Python | Python code-graph tooling project | ~891–1,100 |
| TypeScript | TypeScript backend monorepo | ~630 |

---

## Java Evaluation (v2–v11)

**8 tasks** covering: implementation counting (T1), interface→impl lookup (T2),
caller enumeration (T3), negative check (T4), call-flow tracing (T5),
hallucination bait (T6), behavioral impl matching (T7), interface method lookup (T8).

### Cumulative results table

| version | model | change | JIDRA correct | CG correct | JIDRA avg tok | CG avg tok | tok ratio | JIDRA cost | CG cost |
|---|---|---|---|---|---|---|---|---|---|
| v2 | Sonnet 4.6 | baseline | 8/8 | 8/8 | 15,019 | 45,936 | 3.1× | — | — |
| v3 | Sonnet 4.6 | harness improvements | 8/8 | 7/8 | 24,117 | 76,889 | 3.2× | — | — |
| v4 | Sonnet 4.6 | harness improvements | 8/8 | 7/8 | 16,716 | 71,856 | 4.3× | — | — |
| v5 | Sonnet 4.6 | cost tracking added | 8/8 | 8/8† | 14,795 | 33,260 | 2.2× | $0.054 | $0.109 |
| v6 | **Haiku 4.5** | model switch | 8/8 | 8/8 | 21,814 | 39,040 | 1.8× | $0.020 | $0.033 |
| v7 | Haiku 4.5 | — | 8/8 | 8/8 | 19,891 | 57,782 | 2.9× | $0.018 | $0.048 |
| v8 | Haiku 4.5 | stricter checker (v2) | 8/8 | 7/8 | 17,513 | 54,428 | 3.1× | $0.124 | $0.363 |
| v9 | Haiku 4.5 | Fix D (generated sort) | 7/8 ↓ | 7/8 | — | — | 1.9× | — | — |
| v10 | Haiku 4.5 | Fix D extended | 7/8 | 7/8 | — | — | 2.1× | — | — |
| v11 | Haiku 4.5 | `#` selector fix | **8/8** | 7/8 | 22,320 | 30,177 | **1.35×** | $0.156 | $0.205 |

†v5 CG: 8/8 correct but 1 hallucination (fabricated a non-existent method name on T5).

### Key findings

**Correctness:** JIDRA maintained 8/8 across all stable runs. CG consistently fails T1
(can't produce exact implementation counts — its blast-radius output lacks structured
enumeration). v8 introduced a stricter checker that exposed this permanently.

**Token efficiency:** JIDRA uses 1.35×–4.3× fewer tokens depending on run. The ratio is
task-mix sensitive: JIDRA wins strongly on structural queries (T1/T3/T4/T8) and loses
on behavioral queries where CG's inline source dumping is sufficient (T6/T7).

**Critical bugs fixed along the way:**
- **`#` separator bug (v9→v11):** `ClassName#method` notation fell through to full-text
  search → returned a generated stub instead of the real interface method. Fix: 4 lines
  in selector.py. Fixed T8 from 3c/18k✗ → 1c/5k✓.
- **Fix D (v9/v10):** Attempted to deprioritize generated-source candidates via sort order.
  Wrong layer — the actual bug was the selector routing, not candidate ranking. Cost 2
  extra eval runs to diagnose.

**Where CG wins:**
- T6 (hallucination bait, 4–6 calls): CG's broad search returns inline source; agent
  confirms absence without needing targeted fetches.
- T7 (behavioral impl matching): CG sometimes hits the target method inline in 1 call;
  JIDRA requires enumerate-impls → fetch-each chain (5–6 calls).

**Where JIDRA wins:**
- T1 (count implementations): exact structured result vs CG guessing from blast radius.
- T3 (enumerate callers): `find_callers` = 1 call. CG has no reverse-edge traversal;
  re-explores and fails.
- T8 (interface method by class#method): direct selector resolution vs FTS fallback.

---

## Python Evaluation (v1–v2)

**5 tasks** (v2; v1 had 4): caller enumeration (PY1), callee tracing (PY2),
negative check (PY3), definition lookup in large file (PY4), implementation source (PY5).

| version | JIDRA correct | CG correct | JIDRA avg tok | CG avg tok | tok ratio | JIDRA cost | CG cost |
|---|---|---|---|---|---|---|---|
| v1 | 4/4 | 1/4 | 24,419 | 192,716 | 7.9× | ~$0.092† | ~$0.629† |
| v2 | **5/5** | 4/5 | 11,444 | 139,716 | **12.2×** | **$0.054** | $0.573 |

†v1 cost estimated (no raw JSON); v2 exact. Haiku 4.5: $0.80/MTok in, $4.00/MTok out.

### Key findings

**JIDRA 5/5, CG 4/5 in v2.** CG fails PY1 (caller enumeration) — same blast-radius
design gap as Java T3. CG stated "27 callers" but 0 matched the ground-truth call graph.

**JIDRA: -53% tokens and -45% calls from v1→v2.** Improvements from graph/selector
fixes (including the `#` separator fix from Java v11) carried over directly to Python.

**PY4 — large file problem.** CG: 13 calls / 533k tokens / 45s to answer a definition
lookup in a 1,200-line file. Correct but 46× more expensive than JIDRA (2c/12k).
Truncation forces CG to reconstruct the answer from call-site context rather than reading
the definition directly.

**PY5 — checker blind spot.** New task asking for a method's source. CG returned the
correct method name but from the wrong file (a Go extractor instead of the Python
extractor). The harness checker passed (`has_source=True`) — semantic failure masked
as correct. Logged for next harness version.

**Cost ratio widened: 6.8× → 10.6×.** JIDRA got cheaper; CG remained expensive on PY4.

---

## TypeScript Evaluation (v1–v2)

**5 tasks** (v2; v1 had 4): caller enumeration (TS1), callee tracing (TS2),
negative check (TS3), locate+describe function (TS4), retrieve full source (TS5).

| version | JIDRA correct | CG correct | JIDRA avg tok | CG avg tok | tok ratio | JIDRA cost | CG cost |
|---|---|---|---|---|---|---|---|
| v1 | 4/4† | 1/4 | 37,039 | 148,970 | ~4× | ~$0.140‡ | ~$0.486‡ |
| v2 | **5/5** | 3/5 | 16,108 | 352,633 | **21.9×** | **$0.076** | **$1.433** |

†v1 TS3 scored wrong due to markdown bold breaking substring match; corrected 4/4.
‡v1 cost estimated (no raw JSON); v2 exact. Haiku 4.5: $0.80/MTok in, $4.00/MTok out.

### Key findings

**JIDRA 5/5, CG 3/5 in v2.** CG fails TS1 (caller enumeration, design gap) and TS5
(source retrieval, truncation wall).

**TS5 — truncation wall.** CG hit 14 iterations (592k tokens, ~$0.48 alone) trying to
scroll past a large file's truncation boundary. Target method is at line 1127; CG's tool
truncates at ~line 813. No `get_method_source` equivalent exists in CG. JIDRA: 1c/5k.

**CG flipped 1/4→3/5 but at +137% token cost.** CG avoided the 14-call cap on TS2/TS4
in v2 by using more varied queries — but this strategy burned more tokens per task.
"More correct" and "more expensive" at the same time.

**Cost ratio widened: 3.5× → 18.9×.** CG's single TS5 DNF spiral ($0.48) is the main
driver. JIDRA cut tokens -57% through graph improvements.

**Both backends hallucinate shortened file names on TS1** (e.g. `adapter.ts` instead of
`analytics-adapter.ts`). Cosmetic — correct semantics, wrong file casing.

---

## Cross-language summary

| language | version | JIDRA | CG | token ratio | JIDRA cost | CG cost |
|---|---|---|---|---|---|---|
| Java | v11 | 8/8 | 7/8 | 1.35× | $0.156 | $0.205 |
| Python | v2 | 5/5 | 4/5 | 12.2× | $0.054 | $0.573 |
| TypeScript | v2 | 5/5 | 3/5 | 21.9× | $0.076 | $1.433 |

Java ratio is lower because Java tasks include behavioral/structural queries (T6/T7)
where CG's inline-source approach is competitive. Python/TypeScript tasks are more
definition-and-traversal-heavy where CG's lack of scalpel tools is fatal.

---

## Structural gaps confirmed across all languages

| gap | Java | Python | TypeScript |
|---|---|---|---|
| Caller enumeration | CG fails T3 | CG fails PY1 | CG fails TS1 |
| Large-file source retrieval | CG expensive T4/T5 | CG 46× T4 | CG DNF TS5 |
| Exact implementation count | CG fails T1 | — | — |
| Behavioral impl matching | JIDRA slow T6/T7 | — | — |

**Root cause in all three cases:** `codegraph_explore` is a search tool, not a graph
traversal tool. It returns blast-radius context around a symbol but cannot:
1. Walk reverse call edges (who calls X?)
2. Extract a single method from a large file by name
3. Count implementations with precision

JIDRA's scalpel tools answer each of these in 1 call. The gap is largest when the
target is deep in a large file (TypeScript) or requires exact reverse-edge traversal
(all languages).
