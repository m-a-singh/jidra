# JIDRA Retrieval Benchmark — Summary

We compared JIDRA against CodeGraph, the leading open-source code graph MCP server,
on their own benchmark methodology across 4 real TypeScript codebases.

**Bottom line: JIDRA finds the right code more often. CodeGraph ranks it better when it finds it.**

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

## Results (V2 — 2026-07-05)

| | JIDRA | CodeGraph |
|---|---|---|
| **Queries answered correctly** | **45 / 48 (94%)** | 35 / 48 (73%) |
| **Average recall** | **0.764** | 0.578 |
| **Search recall** | **1.00** (all repos) | 0.75–1.00 |
| **Search MRR** | 0.382 avg | **1.00**† (when found) |

†CodeGraph always returns found symbols at rank 1 — when it finds something, it's
at the top. When it doesn't find it, it returns nothing. JIDRA finds more things
but sometimes ranks them 3rd or 4th instead of 1st.

---

## Per-repo

| repo | JIDRA pass | CG pass | JIDRA recall | CG recall |
|---|---|---|---|---|
| shapeshift | **12/12 (100%)** | 7/12 (58%) | **0.792** | 0.46 |
| mtkruto | **12/12 (100%)** | 11/12 (92%) | **0.847** | 0.72 |
| postybirb | **10/12 (83%)** | 9/12 (75%) | **0.708** | 0.63 |
| trezor | **10/12 (83%)** | 8/12 (67%) | **0.708** | 0.50 |
| **total** | **44/48 (92%)** | **35/48 (73%)** | **0.764** | **0.578** |

---

## What this means in practice

**If you search for a method that exists in the codebase:**
- JIDRA will find it in top-20 results 100% of the time across all 4 repos
- CodeGraph will find it ~75–100% of the time depending on repo
- When CodeGraph misses, it returns nothing — not a wrong answer, just nothing

**If you ask a natural language question about how something works:**
- JIDRA returns relevant methods across module boundaries
- CodeGraph struggles when the relevant code lives in sub-packages or
  separate directories of a monorepo

**If ranking matters (you need the exact method at position 1):**
- CodeGraph is better — when it finds the symbol it always returns it first
- JIDRA finds the symbol but sometimes buries it behind related results on
  generic names (`invoke`, `login`, `sendMessage`)
- Known gap; exact-name pre-query fix landed in V2 (improved from 0.740 → 0.382 avg
  MRR, still below CodeGraph — common-name ambiguity remains)

---

## Why CodeGraph misses things

When CodeGraph returned 0 results, we checked why. In every case the method
existed in the codebase — CodeGraph's index simply didn't include it:

- Standalone TypeScript functions (not class methods)
- React hooks (`useSendForm`, `useApprove`)
- Route handler functions inside package sub-directories (`getRates`, `getAssets`)

JIDRA's extraction captures all of these regardless of how they're structured.

---

## What this benchmark doesn't cover

This measures whether the tools find the right code. It doesn't measure whether
an agent using the tools produces correct answers to real coding tasks.

That is covered in a separate agent-in-loop evaluation (Java, Python, TypeScript)
where an LLM agent was given real coding tasks and scored on correctness and cost.
JIDRA achieved 8/8, 5/5, 5/5 correctness vs CodeGraph's 7/8, 4/5, 3/5 —
using 1.4× to 21.9× fewer tokens depending on the task type.

The two benchmarks together: JIDRA finds more code and helps agents answer
questions more accurately at lower cost.

---

## Reproduce

Full methodology, per-case results, and eval harness code:
- `docs/JIDRA_vs_CodeGraph_retrieval_final.md` — per-repo breakdown
- `evals/analysis/codegraph/code_graph_retrieval_eval.py` — JIDRA eval script
- `evals/analysis/codegraph/results/comparison_v2.md` — V2 raw comparison
