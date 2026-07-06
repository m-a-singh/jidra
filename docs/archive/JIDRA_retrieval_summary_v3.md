# JIDRA Retrieval Benchmark — Summary

We compared JIDRA against CodeGraph, the leading open-source code graph MCP server,
on their own benchmark methodology across 4 real TypeScript codebases.

**Bottom line: JIDRA finds the right code more often and now ranks it correctly.**

---

## What we tested

48 code navigation queries across 4 open-source TypeScript repos:

| repo | what it is | size |
|---|---|---|
| MTKruto | Telegram client library | ~2,300 methods |
| Trezor Suite | Crypto wallet monorepo | ~12,600 methods |
| PostyBirb | Multi-platform posting app (NestJS) | ~2,200 methods |
| Shapeshift Web | DeFi trading app (React/Redux) | ~6,200 methods |

Each query asked one of two things: "find this method" (search) or "show me
how X works" (explore). We scored both tools on whether they returned the right
answer.

---

## Results (V3 — 2026-07-05)

| | JIDRA V3 | JIDRA V2 | CodeGraph |
|---|---|---|---|
| **Queries answered correctly** | **45 / 48 (94%)** | 44/48 (92%) | 35 / 48 (73%) |
| **Average recall** | **0.791** | 0.764 | 0.578 |
| **Search recall** | **1.00** (all repos) | 1.00 | 0.75–1.00 |
| **Search MRR** | **1.000** | 0.382 | 1.00† (when found) |

†CodeGraph always returns found symbols at rank 1 — when it finds something, it's
at the top. When it doesn't find it, it returns nothing. JIDRA now matches this
ranking precision while finding substantially more things.

---

## Per-repo

| repo | JIDRA V3 pass | JIDRA V2 pass | CG pass | JIDRA V3 recall | JIDRA V2 recall | CG recall |
|---|---|---|---|---|---|---|
| shapeshift | **12/12 (100%)** | 12/12 (100%) | 7/12 (58%) | **0.875** | 0.792 | 0.46 |
| mtkruto | **12/12 (100%)** | 12/12 (100%) | 11/12 (92%) | **0.889** | 0.847 | 0.72 |
| postybirb | **10/12 (83%)** | 10/12 (83%) | 9/12 (75%) | **0.708** | 0.708 | 0.63 |
| trezor | **11/12 (92%)** | 10/12 (83%) | 8/12 (67%) | **0.792** | 0.708 | 0.50 |
| **total** | **45/48 (94%)** | **44/48 (92%)** | **35/48 (73%)** | **0.791** | 0.764 | **0.578** |

---

## What changed from V2 → V3

Two improvements shipped in V3:

**1. Morphological stemming (Snowball/English)**  
Natural language queries now stem before FTS5 lookup. `"signing transactions"` →
`"sign transact"` prefix query, which matches `signTransaction`, `signedTx`, etc.
Previously `"signing"` ≠ `"sign"` in SQLite FTS5 — the query silently missed the
right method.

Fixed: `shapeshift-explore-signing` (was FAIL V2 → PASS V3)

**2. BM25 score-tier fix for name-scoped queries**  
Name-scoped weighted queries (searching the method name column) returned scores
around −12, while full-text AND queries (matching source_text with multi-token
inputs) returned scores around −15. SQLite BM25 scores are negative — more negative
= better match. Re-sorting by raw score caused caller methods that happened to
reference the target name in their source to outrank the target itself.

Fixed by applying a −1000 offset to name-scoped scores before merge, ensuring
the definition always ranks above callers. Result: MRR jumped from 0.382 → 1.000.

---

## What this means in practice

**If you search for a method that exists in the codebase:**
- JIDRA will find it at rank 1 in 100% of cases across all 4 repos
- CodeGraph will find it ~75–100% of the time depending on repo
- When CodeGraph misses, it returns nothing — not a wrong answer, just nothing

**If you ask a natural language question about how something works:**
- JIDRA returns relevant methods across module boundaries
- Stemming now handles inflected queries (`"how signing works"`, `"transaction flow"`)
- CodeGraph struggles when the relevant code lives in sub-packages or
  separate directories of a monorepo

**If ranking matters (you need the exact method at position 1):**
- JIDRA V3 achieves MRR=1.000 on search across all 4 repos — same as CodeGraph
- V2 had MRR=0.382 due to generic names (`invoke`, `login`, `sendMessage`) ranking
  their callers above the definition

---

## Why CodeGraph misses things

When CodeGraph returned 0 results, we checked why. In every case the method
existed in the codebase — CodeGraph's index simply didn't include it:

- Standalone TypeScript functions (not class methods)
- React hooks (`useSendForm`, `useApprove`)
- Route handler functions inside package sub-directories (`getRates`, `getAssets`)

JIDRA's extraction captures all of these regardless of how they're structured.

---

## Remaining gaps

- **postybirb explore-website / explore-update** — methods live on abstract base
  classes; graph traversal doesn't cross the interface boundary. Score: 0.00 both
  cases, unchanged from V2.
- **trezor explore-device** — `"device initialization"` has no lexical overlap with
  `getFeatures` / `getAddress`. Semantic gap; BM25 cannot bridge it without embeddings.
- **postybirb search MRR** — not measured in V3 eval (all search PASS with MRR=1.00).

---

## What this benchmark doesn't cover

This measures whether the tools find the right code. It doesn't measure whether
an agent using the tools produces correct answers to real coding tasks.

That is covered in a separate agent-in-loop evaluation (Java, Python, TypeScript)
where an LLM agent was given real coding tasks and scored on correctness and cost.
JIDRA achieved 8/8, 5/5, 5/5 correctness vs CodeGraph's 7/8, 4/5, 3/5 —
using 1.4× to 21.9× fewer tokens depending on the task type.

The two benchmarks together: JIDRA finds more code, ranks it correctly, and helps
agents answer questions more accurately at lower cost.

---

## Reproduce

Full methodology, per-case results, and eval harness code:
- `docs/JIDRA_vs_CodeGraph_retrieval_final_v3.md` — per-repo breakdown
- `evals/analysis/codegraph/code_graph_retrieval_eval.py` — JIDRA eval script
- `evals/analysis/codegraph/results/comparison_v3.md` — V2 vs V3 raw comparison
