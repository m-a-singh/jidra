---
name: jidra-error-investigate
description: >
  Investigate an exception or stack trace: find the failure anchor in the call
  graph, map upstream callers and downstream calls, surface likely root cause.
  Trigger: "investigate error", "debug this exception", "stack trace", "why did X fail",
  "what caused this crash", "error investigation".
---

Spawn a `jidra-investigator` subagent via the Agent tool with this prompt:

"Investigate this error/stack trace: $ARGUMENTS

1. Call `jidra_analyze_stack_trace` with the full stack trace text.
2. The result gives you: matched frames, primary anchor (failure point), flow map around it, unresolved calls nearby.
3. If anchor found — call `jidra_find_callers` on the anchor (depth=1) to surface all entry points that reach it.
4. If anchor found — call `jidra_get_method_source` on the anchor to show the exact failure site.
5. Return:

**Failure anchor:** `file:line — symbol` (the first project frame in the trace)
**Caller frame above:** `file:line — symbol` (what called the failing method)

**Likely root cause locations** (ranked):
| priority | file:line | symbol | reason |
|---|---|---|---|

**Entry points that reach the anchor** (from jidra_find_callers):
`file:line — symbol`

**Unresolved calls near failure** (suspicious external deps):
`receiver.method` — note if this is the likely culprit

**Verdict:** one sentence — what broke and where to look first."
