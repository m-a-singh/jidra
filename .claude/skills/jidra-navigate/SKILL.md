---
name: jidra-navigate
description: >
  Use for ANY code navigation question: who calls X, what does X call,
  how does X flow, what implements X, does X exist.
  Trigger: "who calls", "what calls", "find callers", "trace flow",
  "what implements", "does X exist".
---

Spawn a `jidra-investigator` subagent via the Agent tool. Pass the user's full query as the prompt. Do not call JIDRA tools yourself.

Prompt to pass: "$ARGUMENTS — use JIDRA tools to answer. Return file:line table: symbol, path, line, one-line context. Read-only, no fixes."
