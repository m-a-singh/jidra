# JIDRA

JIDRA (Java Intelligent Diagnostic & Reasoning Agent) is a focused CLI for Java codebase graph indexing, call tracing, context extraction, prompt generation, and optional LLM-based diagnosis.

This project is intentionally minimal and graph-driven.

## Pitch (TL;DR)

- Index once → get a deterministic call graph you can trace and query offline.
- Trace from a method or HTTP route to see likely execution flow and unresolved edges.
- Generate prompt-ready context and prompts (`context`, `prompt`) for LLM workflows.
- Produce deterministic investigation docs from stack traces (`error-doc`) and flows (`flow-doc`).
- Optional LiteLLM-based diagnosis on top of graph-grounded context.

## What JIDRA Does

- Builds a graph from Java source (`index`)
- Traces method flow (`trace`)
- Traces by route entry (`trace-route`)
- Builds prompt-ready method context (`context`)
- Generates LLM prompt text (`prompt`)
- Runs LLM diagnosis with structured metrics (`diagnose`)

## What JIDRA Does Not Do

- No enrichment agents
- No multiprocessing/async pipelines
- No UI/debug dashboards
- No graph format mutation at runtime

## Project Layout

```text
jidra/
├── pyproject.toml
├── requirements.txt
├── README.md
└── jidra/
    ├── __init__.py
    ├── cli.py
    ├── config.yaml
    ├── llm_client.py
    ├── models.py
    ├── graph_io.py
    ├── selector.py
    ├── trace_engine.py
    ├── context_builder.py
    ├── extractor.py
    ├── exporter.py
    ├── filters.py
    └── cache.py
```

## Installation

This project is released under the MIT License (see `LICENSE`).

From project root:

```bash
pip install -e .
```

If you use the local venv:

```bash
.venv/bin/pip install -e .
```

## Quick Start

### Optional: configure your project package prefixes

Some features (like `error-doc` choosing the first "project" stack frame as an anchor) can use
package prefixes to distinguish your code from third-party libraries.

Set a comma-separated list:

```bash
export JIDRA_PROJECT_PREFIXES="com.myco.,org.example."
```

If unset, JIDRA treats any package as project code for anchoring.

### 1) Build graph

```bash
python -m jidra.cli index \
  --codebase /path/to/java/repo \
  --output /tmp/graph.jsonl
```

When output is a directory, JIDRA writes:
- `graph.jsonl` (main)
- `graph_test.jsonl` (test)

### 2) Trace method flow

```bash
python -m jidra.cli trace \
  --graph /tmp/graph.jsonl \
  --method com.example.Controller.search
```

### 3) Build method context

```bash
python -m jidra.cli context \
  --graph /tmp/graph.jsonl \
  --method com.example.Controller.search
```

### 4) Generate prompt text

```bash
python -m jidra.cli prompt \
  --graph /tmp/graph.jsonl \
  --method com.example.Controller.search \
  --target codex
```

### 5) Diagnose with LLM

```bash
python -m jidra.cli diagnose \
  --graph /tmp/graph.jsonl \
  --method com.example.Controller.search \
  --target codex \
  --llm-profile local
```

## Graph Selection Behavior

For `trace`, `context`, `trace-route`, `prompt`, `diagnose`:

- `--graph` provided: used directly
- `--graph` omitted: selected by `--graph-type` (`main` default)
  - `main` -> `jidra/output/graph.jsonl`
  - `test` -> `jidra/output/graph_test.jsonl`

## Method Selectors

Supported method selectors:

- method id
- full signature
- full class + method (`com.example.Class.method`)
- short class + method (`Class.method`)
- bare method name (if unique)

Ambiguous selector output includes candidate ids you can use directly.

## Command Reference

## `flow-doc`

Purpose: generate deterministic flow investigation markdown from indexed graph data (no LLM calls).

```bash
jidra flow-doc \
  [--graph <path>] \
  [--graph-type main|test] \
  --method <selector> \
  --output <markdown-path> \
  [--depth 4] \
  [--top-n 8] \
  [--max-subflows 8] \
  [--mind-map] \
  [--max-nodes 200] \
  [--include-details] \
  [--include-utility]
```

Behavior:
- Normal mode (no `--mind-map`): prioritized flow slices using `top_n` and `max_subflows`.
- `--mind-map` mode: recursive resolved-edge traversal using `depth + max_nodes`; it does not use `top_n/max_subflows` for traversal.
- `--include-details`: in `--mind-map` mode, appends legacy detailed expanded sections that still use prioritized slicing (`top_n/max_subflows`).
- Output is deterministic for the same graph + method + flags.

Examples:

```bash
python -m jidra.cli flow-doc \
  --method SearchServiceController.search \
  --output flow_docs/verify_SearchServiceController_search.md \
  --depth 10 \
  --top-n 10 \
  --max-subflows 10 \
  --show-agents
```

```bash
python -m jidra.cli flow-doc \
  --method SearchServiceController.search \
  --output flow_docs/mindmap_SearchServiceController_search.md \
  --mind-map \
  --depth 6 \
  --max-nodes 120
```

## `error-doc`

Purpose: generate deterministic error investigation markdown from a Java stack trace text file and indexed graph.

```bash
jidra error-doc \
  --stack-trace <stack-trace.txt> \
  --output <markdown-path> \
  [--graph <path>] \
  [--graph-type main|test] \
  [--depth 6] \
  [--max-nodes 200] \
  [--mind-map]
```

Stack frame parsing:
- Parses lines in format: `at package.Class.method(File.java:123)`.

Frame-to-method matching:
- class full name
- method name
- file name
- line in method `[start_line, end_line]`

Match semantics:
- `matched`: exactly one graph method candidate.
- `ambiguous`: multiple candidates (reported as ambiguity).
- `unmatched`: no candidate.

Anchor + focused map:
- primary failure anchor: first matched/ambiguous project frame.
- focused flow map: generated via deterministic `flow-doc` mind-map traversal around anchor.
- upstream/downstream behavior:
  - downstream-focused when anchor has meaningful downstream callees.
  - upstream-focused fallback when downstream is weak.

Examples:

```bash
python -m jidra.cli error-doc \
  --stack-trace examples/error_1.txt \
  --output flow_docs/error_doc_verify_clean.md \
  --mind-map \
  --depth 6 \
  --max-nodes 80
```

## Determinism and Limits

- Static analysis only; runtime dispatch is not guaranteed.
- Unresolved calls may remain in outputs.
- External library frames/methods may be unmatched.
- Graph quality directly affects output quality.
- No runtime correctness claims; output is investigation guidance.

## Example Output Snippet

```markdown
## Suggested Debug Locations
| priority | location | reason |
|---:|---|---|
| 1 | `com.example.app.health.HealthIndicator#doHealthCheck(Health.Builder)` | failing project frame |
| 2 | `org.opensearch.client.opensearch.cluster.OpenSearchClusterClient#health:360` | caller frame above failure |
| 3 | `this.client.cluster().health` | unresolved external call near failure |
```

## `index`

```bash
jidra index --codebase <path> --output <path-or-dir>
```

Builds graph JSONL from Java source using tree-sitter parser pipeline.

## `trace`

```bash
jidra trace \
  [--graph <path>] \
  [--graph-type main|test] \
  --method <selector> \
  [--max-depth 5] \
  [--business-only] \
  [--output <file-or-dir>]
```

- `--business-only` filters support/metrics/logging from flow output
- root node is always preserved

## `context`

```bash
jidra context \
  [--graph <path>] \
  [--graph-type main|test] \
  --method <selector> \
  [--max-chars 12000] \
  [--max-tokens <int>] \
  [--business-only] \
  [--output <file-or-dir>]
```

Includes:
- method signature/source
- endpoint metadata
- resolved callee summary
- unresolved call summary

Context output is deduped/grouped for prompt readiness.

## `trace-route`

```bash
jidra trace-route \
  [--graph <path>] \
  [--graph-type main|test] \
  --route <path> \
  [--max-depth 5] \
  [--output <file-or-dir>]
```

## `prompt`

```bash
jidra prompt \
  [--graph <path>] \
  [--graph-type main|test] \
  --method <selector> \
  [--max-chars 12000] \
  [--max-tokens <int>] \
  [--business-only|--no-business-only] \
  [--target claude|codex|generic] \
  [--output <file-or-dir>]
```

Default: `--business-only` is enabled.

## `diagnose`

```bash
jidra diagnose \
  [--graph <path>] \
  [--graph-type main|test] \
  --method <selector> \
  [--target claude|codex|generic] \
  [--model <model>] \
  [--max-chars 12000] \
  [--max-tokens <int>] \
  [--business-only|--no-business-only] \
  [--llm-profile local|enterprise] \
  [--config <path-to-config.yaml>] \
  [--show-prompt] \
  [--quiet] \
  [--output <file-or-dir>]
```

Behavior:
- No `--output` + interactive TTY + not `--quiet`: ANSI-readable report
- No `--output` + non-TTY or `--quiet`: JSON printed
- With `--output`: JSON written to file
- `--show-prompt`: includes prompt text in result JSON
- `--max-chars`: controls method context/source size sent into prompt construction
- `--max-tokens`: overrides model output token limit for this run (when omitted, config profile default is used)

## Output Naming

When `--output` is a directory:

- trace: `trace_<graph_type>_<method>.json`
- trace + business-only: `trace_business_<graph_type>_<method>.json`
- context: `context_<graph_type>_<method>.json`
- context + business-only: `context_business_<graph_type>_<method>.json`
- trace-route: `trace_route_<graph_type>_<route_or_entry>.json`
- prompt: `prompt_<target>_<graph_type>_<method>.txt`
- diagnose: `diagnose_<target>_<graph_type>_<method>.json`

Names are normalized to lowercase snake-style safe parts.

## LLM Configuration

JIDRA uses `jidra/config.yaml`.

Example:

```yaml
llm:
  provider: litellm
  profile: local

  profiles:
    local:
      api_base: "http://localhost:4000"
      api_key_env: "LITELLM_PROXY_API_KEY"
      default_model: "ollama/gemma4:e4b"
      timeout_seconds: 120
      temperature: 0.2
      max_tokens: 1200

    enterprise:
      api_base: "https://your-enterprise-litellm.example.com"
      api_key_env: "ENTERPRISE_LITELLM_API_KEY"
      default_model: "gpt-4o-mini"
      timeout_seconds: 120
      temperature: 0.2
      max_tokens: 2000
```

Rules:
- Default profile comes from `llm.profile`
- CLI override: `--llm-profile`
- If `api_key_env` is set, env var is read
- Missing config falls back to safe local defaults

## Diagnose Output Shape

`diagnose` returns JSON with:

```json
{
  "method": "...",
  "analysis": "...",
  "llm": {
    "provider": "litellm",
    "profile": "local",
    "model": "...",
    "usage": {
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "reasoning_tokens": 0
    },
    "latency_seconds": 0.0,
    "limits": {
      "max_chars": 12000,
      "max_tokens": null
    }
  },
  "context_summary": {
    "business_flow_count": 0,
    "unresolved_count": 0
  }
}
```

If provider usage is unavailable, token counts are estimated and:

```json
"estimated": true
```

is added under `llm.usage`.

## Context/Token Limits

- `--max-chars` (context, prompt, diagnose):
  - default `12000`
  - passed directly to context building to constrain context payload size
- `--max-tokens` (context, prompt, diagnose):
  - optional CLI override
  - primarily used by `diagnose` to cap LLM output tokens
  - if omitted, profile default from `jidra/config.yaml` is used

## Troubleshooting

## `jidra --help` works but diagnose fails

Likely LLM connectivity issue:
- verify LiteLLM endpoint in config
- verify API key/env key
- verify network access to endpoint

## `No methods matched selector`

Use a stronger selector:
- class+method or exact method id from ambiguity output

## `no_flow_root:/route`

No endpoint matched that route in graph. Validate route annotations and graph source set.

## `pip install -e .` fails

Check Python/venv and package index/network availability.

## Experiments (Optional / Unshipped)

This repo includes an `jidra/experiments/` package with exploratory agent-style components:

- `enrichment_agent.py`, `enrichment_judge.py`, `enrichment_orchestrator.py`, `enrichment_ui.py`
- `method_prompt.py`, `token_count.py`

These modules are **optional** and not required for the core deterministic CLI workflow.
They are currently used only when you enable agent visibility in `flow-doc` via `--show-agents`.

If you are vendoring JIDRA or aiming for a minimal footprint, you can ignore this folder.

## Development Notes

- `cli.py` handles command orchestration only.
- `llm_client.py` owns provider/config/use-metrics behavior.
- graph extraction and graph format are intentionally unchanged.
