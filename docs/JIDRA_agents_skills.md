# JIDRA Agents & Skills

Two Claude Code primitives ship with `jidra init`: a **subagent** (`jidra-investigator`) and two **skills** (`/jidra-navigate`, `/jidra-blast-radius`). Both are installed into the target repo's `.claude/` directory and require no manual setup.

---

## How it works

```
User query / slash command
        ↓
  Skill (SKILL.md) — matches query, spawns agent
        ↓
  jidra-investigator (agent) — haiku model, mcpServers: [jidra]
        ↓
  JIDRA MCP tools — queries .jidra/graph.db directly
        ↓
  file:line table + risk summary
```

**Key design decisions:**

- Skills are the trigger mechanism. Agent description alone is not reliable — main session (Sonnet/Opus) will handle queries itself if it has jidra tools. Skills force explicit delegation.
- `mcpServers: [jidra]` in agent frontmatter is required. Without it the agent has no jidra tools and falls back to grep.
- CLAUDE.md jidra injection is disabled — if CLAUDE.md instructs the main session to use jidra tools first, the skill is never triggered.
- Agent runs on **haiku** — pure read-only graph queries, no reasoning needed.

---

## jidra-investigator

**File:** `.claude/agents/jidra-investigator.md`

```yaml
model: haiku
mcpServers:
  - jidra
```

Read-only code locator. Never edits. Never proposes fixes. Returns file:line table.

Tool priority: `jidra_explore` → `jidra_get_method_source` → `jidra_find_callers` → `jidra_get_agent_flow` → `jidra_get_implementations` → Read/Grep only if JIDRA returns nothing.

---

## /jidra-navigate

**File:** `.claude/skills/jidra-navigate/SKILL.md`

Triggers on: "who calls", "what calls", "find callers", "trace flow", "what implements", "does X exist"

Spawns jidra-investigator with the user's full query. Returns file:line table with symbol and context column.

---

## /jidra-blast-radius

**File:** `.claude/skills/jidra-blast-radius/SKILL.md`

Triggers on: "blast radius", "impact of changing", "what breaks if", "who is affected by", "safe to change"

Spawns jidra-investigator with structured blast radius prompt:
1. `jidra_find_callers` depth=2
2. If caller_count > 10 at depth 1, drills top 3 callers deeper
3. Returns direct callers table + indirect callers table with chain path
4. Flags HTTP endpoints, scheduled jobs, public API in chain
5. Risk summary: LOW / MEDIUM / HIGH

---

## Example session — DeviceController.saveDevice blast radius


## Token & cost comparison — same query, 4 approaches

| Approach | Agent | in tokens | out tokens | cost |
|---|---|---|---|---|
| No JIDRA (caveman grep) | cavecrew-investigator | 188,836 | 514 | $0.161 |
| Explicit `/jidra-blast-radius` | jidra-investigator | 142,219 | 1,337 | $0.182 |
| Natural language (auto-triggered skill) | jidra-investigator | 189,736 | 1,133 | $0.217 |


**Key observations:**
- Grep approach cheapest but missed depth-2 chain entirely — no HTTP endpoint flag, no risk rating
- Explicit slash command was most efficient (142k in) — clean delegation path
- Natural language auto-trigger slightly more expensive (main session reasoning overhead) but better UX
- Both jidra approaches returned correct, actionable blast radius with HTTP endpoint and scheduled job flagged

---

## Installation

`jidra init` copies all three files into the target repo automatically:

```
<repo>/.claude/agents/jidra-investigator.md
<repo>/.claude/skills/jidra-navigate/SKILL.md
<repo>/.claude/skills/jidra-blast-radius/SKILL.md
<repo>/.claude/skills/jidra-error-investigate/SKILL.md
```

Source files live in the jidra repo at `.claude/agents/` and `.claude/skills/`.
