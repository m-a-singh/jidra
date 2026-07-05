# JIDRA Init & Packaging

## Goal

Mirror codegraph's portable model: graph lives in the target project, single PATH binary, MCP guidance auto-delivered on connect, no absolute paths in committed config.

---

## `jidra init`

Run once per repo. Idempotent — re-running on an existing `.jidra/` does incremental reindex unless `--force`.

```bash
jidra init [--codebase PATH] [--force]
```

### What it does

1. Prompts for skip folders (comma-separated, optional)
2. Prompts for git hooks install (y/n)
3. For Java repos: prompts for actuator URL + Docker (removes phantom edges via live bean validation)
4. Builds graph → `<repo>/.jidra/graph.db`
5. Writes `<repo>/.mcp.json` with explicit `--graph` and `--codebase` paths
6. Installs agent + skills into `<repo>/.claude/`
7. Installs git hooks if confirmed

### Output layout

```
<repo>/
  .jidra/
    graph.db                      # code graph (165MB typical Java repo)
    .java_code_intel_cache.json   # Spring bean cache
    graph_visualization.html      # optional
    validation_report.json
  .mcp.json                       # MCP server config (explicit paths)
  .claude/
    agents/
      jidra-investigator.md
    skills/
      jidra-navigate/SKILL.md
      jidra-blast-radius/SKILL.md
```

---

## .mcp.json

Written with explicit `--graph` and `--codebase` paths. Replaced on every `jidra init`.

```json
{
  "mcpServers": {
    "jidra": {
      "type": "stdio",
      "command": "/path/to/venv/bin/python",
      "args": [
        "-m", "jidra.server.mcp_server",
        "--graph", "/repo/.jidra/graph.db",
        "--codebase", "/repo"
      ]
    }
  }
}
```

**Why explicit paths:** `jidra serve --mcp` without `--graph` uses cwd discovery, which fails when Claude Code launches MCP servers from a different working directory. Explicit paths are the only reliable approach.

**Why `.mcp.json` not `~/.claude.json`:** User-scope entries in `~/.claude.json` get overwritten by Claude Code app state writes. `.mcp.json` in the repo is stable.

---

## MCP server instructions

`FastMCP("JIDRA MCP", instructions=_INSTRUCTIONS)` delivers tool usage guidance in the MCP `initialize` response. Claude receives this on every connect — no CLAUDE.md needed.

---

## Graph discovery

`_discover_graph()` in `mcp_server.py` walks up from cwd to find `.jidra/graph.db`. Used when `--graph` is not passed (e.g. `jidra serve --mcp` without args). Falls back to `DEFAULT_MAIN_GRAPH` if nothing found.

---

## `jidra serve`

Alias for the existing `mcp` subcommand. Same behavior.

---

## CLAUDE.md injection — disabled

`_write_claude_md()` exists but is commented out in `_init()`. Injecting JIDRA instructions into CLAUDE.md causes the main session to call jidra tools directly, preventing skill-triggered agent delegation. Skills + agent are the preferred mechanism.

---

## Incremental reindex

If `.jidra/graph.db` exists and `--force` not passed, `jidra init` runs `incremental_reindex()` — fast diff-based update. Git hooks installed during init also trigger incremental reindex on commit.

---

## uvx compatibility

`pyproject.toml` uses standard `[project.scripts]` — `uvx jidra init` works without global install.

---

## What does NOT get committed (user's responsibility)

```
.jidra/          # add to .gitignore
.mcp.json        # add to .gitignore
.claude/         # add to .gitignore or commit selectively
```

`jidra init` does not touch `.gitignore` — user manages their own repo.
