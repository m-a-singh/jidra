---
name: jidra-blast-radius
description: >
  Show blast radius before changing X: all callers 2 levels up, flagging
  high-impact paths. Trigger: "blast radius", "impact of changing", "what breaks if",
  "who is affected by", "safe to change".
---

Spawn a `jidra-investigator` subagent via the Agent tool with this prompt:

"Blast radius for: $ARGUMENTS

1. Call `jidra_find_callers` on the symbol (depth=2).
2. If caller_count > 10 at depth 1, go deeper on the top 3 callers only.
3. Return two tables:
   - Direct callers (depth 1): file:line, symbol, one-line context
   - Indirect callers (depth 2): file:line, symbol, chain path
4. Flag any HTTP endpoints, scheduled jobs, or public API methods in the chain — these are highest impact.
5. Final line: total unique call sites, risk summary (low/medium/high)."
