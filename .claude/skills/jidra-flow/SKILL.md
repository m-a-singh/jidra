---
name: jidra-flow
description: >
  Show the full downstream call tree from a method: what it calls, what those
  call, ranked by impact. Trigger: "call tree", "what does X call", "flow from X",
  "downstream calls", "trace calls from", "what flows from", "show call graph for".
---

Spawn a `jidra-investigator` subagent via the Agent tool with this prompt:

"Call tree for: $ARGUMENTS

1. Call `jidra_get_agent_flow` on the method/symbol.
2. The result gives ranked flow nodes with depth, tier, file:line, and rank_score.
3. If the symbol is ambiguous, call `jidra_search` with exact=True first to resolve it, then retry.
4. Return two sections:

**Entry method:** `file:line — signature`

**Downstream call tree** (top 15 by rank_score, grouped by depth):
| depth | file:line | symbol | tier | rank_score |
|---|---|---|---|---|

**High-impact nodes** (tier=business or rank_score > 0.7):
`file:line — symbol` — one-line reason why it matters

**Uncertain edges** (unresolved calls near entry):
`receiver.method` — likely external/framework

**Summary:** N nodes, max depth D, [business/infra/mixed] flow."
