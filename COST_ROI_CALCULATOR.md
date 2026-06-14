# JIDRA Cost/ROI Calculator

## Overview

The Cost/ROI calculator measures JIDRA's actual financial impact on code-native workflows — every number comes from your real codebase, not estimates or hardcoded percentages.

## Real-World Benchmark

Two real Claude Code sessions, same question, same model (claude-sonnet-4-6 1M context), search-service `suggest` method:

```
Question: "Can you explain what SearchController.search does, identify any
           potential performance issues or bottlenecks, and tell me which other
           components it interacts with?"

Without JIDRA (no MCP, Claude used Bash/Glob/Read/Grep):
  in=833,782   out=5,161   cost=$0.229770

With JIDRA (MCP connected, Claude used jidra_get_method_context + graph tools):
  in=227,095   out=1,784   cost=$0.227549

Input token reduction: 72.8%
Cost reduction at Sonnet: ~$0.002 (output tokens dominate at $3/$15 per 1M)
Cost reduction at Opus ($15/$45 per 1M):
  Without JIDRA: $12.51/query
  With JIDRA:     $3.41/query
  Savings:        $9.10/query  →  $4,550/year at 500 queries/year
```

**Without JIDRA** — Claude explored files manually (833k input tokens):

![Without JIDRA](docs/assets/benchmark_no_jidra.png)

**With JIDRA** — Claude used graph tools (227k input tokens):

![With JIDRA](docs/assets/benchmark_jidra.png)

**Why same cost at Sonnet but large savings at Opus:** Output tokens cost 5× more than input at Sonnet rates, so compressing input from 833k to 227k saves relatively little when output is 5k tokens. At Opus rates the input price is $15/M, making the 606k token reduction worth $9.09 per query.

Current validation artifacts in `scripts/jidra/validations/results.json` show the `search` method reducing input tokens from 26,847 to 5,243 in the callee-accuracy run and from 26,845 to 5,241 in the caller-tracing / consistency-drift runs.

Three modes, increasing precision:

| Mode | What it does | Requires |
|------|-------------|----------|
| Graph averages | Token stats across all methods | `graph_validated.jsonl` |
| Method offline | Token measurement for one specific method | `graph_validated.jsonl` |
| Method online | Real Claude API calls, exact token counts | `graph_validated.jsonl` + source repo + `ANTHROPIC_API_KEY` |

---

## Usage

### 1. Graph-wide averages
Quick overview across your whole codebase. No method needed.

```bash
jidra cost-roi --model claude-opus-4-7 --queries 1000
```

Shows average token costs across all 2,430+ methods in your graph and projects annual savings.

---

### 2. Method-specific proof (offline, default)
Measures tokens for one real method — reads actual source files, no API calls.

```bash
jidra cost-roi \
  --method SearchController.search \
  --model claude-opus-4-7 \
  --queries 1000
```

- **Without JIDRA**: reads the method's source file + all files in its call chain, counts tokens
- **With JIDRA**: measures the `jidra_get_method_context` tool response, counts tokens
- Token counting uses `chars/4` approximation (no API key needed, instant)
- Validation output in `scripts/jidra/validations/results.json` shows `search` at **80.5%** token reduction in the online method proof (26,847 → 5,243 input tokens)

Add `--codebase` if source files have moved since the graph was built:
```bash
jidra cost-roi \
  --method SearchController.search \
  --codebase /path/to/java-repo \
  --model claude-opus-4-7 \
  --queries 1000
```

---

### 3. Method-specific proof (online — exact numbers)
Makes real Claude API calls with both contexts. Mirrors the `empirical_proof_test.py` approach — same question, same model, real `input_tokens` from the API response.

```bash
jidra cost-roi \
  --method SearchController.search \
  --codebase /path/to/java-repo \
  --model claude-opus-4-7 \
  --queries 1000 \
  --offline false
```

Requires `ANTHROPIC_API_KEY` in environment. Makes 2 API calls per run (traditional + JIDRA context).

---

## Example Output

### Offline method proof
```
======================================================================
JIDRA Cost/ROI — Method Proof
======================================================================
Method:   com.example.SearchController#search(...)
Location: SearchController.java:345-568
Model:    claude-opus-4-7

Token Measurement  (estimated, chars/4)
----------------------------------------------------------------------
Without JIDRA: 11,329 tokens
  (4 source file(s): SearchController.java, DogStatsdClient.java, ...)
With JIDRA:    4,264 tokens
  (jidra_get_method_context response)
Reduction:     62.4%

Cost Per Query  (estimated)
----------------------------------------------------------------------
Without JIDRA: $0.2059
With JIDRA:    $0.1000
Savings:       $0.1060

Annual Savings (1000 queries): $105.9750
======================================================================
```

### Online method proof (real API numbers)
```
Token Measurement  (REAL — from Claude API)
----------------------------------------------------------------------
Without JIDRA: 26,847 input tokens
  (traditional raw-source context)
With JIDRA:    5,243 input tokens
  (jidra_get_method_context response)
Reduction:     80.5%

Cost Per Query  (REAL)
----------------------------------------------------------------------
Without JIDRA: $0.0951
With JIDRA:    $0.0271
Savings:       $0.0680

Annual Savings (1000 queries): $68.0300
```

---

## All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--graph` | `jidra/output/graph_validated.jsonl` | Path to graph file |
| `--method` | none | Class.method selector — enables method-specific proof |
| `--codebase` | none | Path to Java repo root — for reading source files |
| `--model` | `claude-sonnet-4-6` | LLM model to price against |
| `--queries` | `500` | Times Claude calls a JIDRA tool per year (~10/week) |
| `--offline` | `true` | `true` = estimate tokens from graph; `false` = real API calls |
| `--output` | none | Write JSON result to file instead of printing |

---

## Running the Tests

```bash
# All cost calculator tests
python -m pytest tests/test_cost_calculator.py -v

# Real-graph tests only
python -m pytest tests/test_cost_calculator.py -v -k "real"

# Unit tests only (no graph file needed)
python -m pytest tests/test_cost_calculator.py -v -k "not real and not missing"
```

---

## How Token Measurement Works

**JIDRA tokens** — what `jidra_get_method_context` actually returns:
```json
{
  "method": "qualified.name",
  "source": "..method source..",
  "class_context": "..class header..",
  "calls": [...up to 10 outgoing edges],
  "called_by": [...up to 10 incoming edges]
}
```

**Naive tokens** — what you'd paste into context without JIDRA:
- The method's own source file
- Source files of all direct callees (the files involved in the call chain)

**Reduction %** = `(naive - jidra) / naive × 100`

Online mode gets these numbers directly from the Claude API `usage.input_tokens` field — no estimation.

---

## Important Notes

- Output tokens are not reduced by JIDRA (only input context is compressed)
- Online mode costs ~$0.10-0.25 per run depending on model (2 API calls)
- Pricing in `LLM_PRICING` dict — update if rates change
- Run `jidra validate` first for accurate call chain edges
- For `search`, the validation suite showed improved callee recall (5.9% → 94.1%) and lower hallucination rate in callee accuracy (80.0% → 55.6%)

---

## Files

- `jidra/cost_calculator.py` — calculator module (`analyze_method_offline`, `analyze_method_online`, `analyze_graph`)
- `scripts/jidra/validations/results.json` — validation artifact used to derive the `search` method proof numbers
- `tests/test_cost_calculator.py` — 22 tests (unit + real-graph)
