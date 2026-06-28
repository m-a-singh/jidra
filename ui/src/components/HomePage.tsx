import { useState } from "react";
import type { RepoState } from "../hooks/useRepo";

interface ActionItem { id: string; label: string; icon: string; desc: string }

const ACTIONS: ActionItem[] = [
  { id: "graph", label: "Graph", icon: "◆", desc: "Visualize code structure" },
  { id: "sql", label: "Database", icon: "◇", desc: "Query indexed data" },
  { id: "mcp", label: "MCP tools", icon: "⚙", desc: "Inspect available tools" },
  { id: "explore", label: "Explore", icon: "◈", desc: "Trace, context, flow, route" },
  { id: "docs", label: "Doc graph", icon: "▣", desc: "Doc-to-code linkage" },
];

export function HomePage({
  repoPath,
  onSelectRepo,
  onSelectAction,
  onViewHistory,
}: RepoState & { onSelectRepo: (path: string) => void; onSelectAction: (action: string) => void; onViewHistory: () => void }) {
  const [input, setInput] = useState(repoPath || "");
  const ready = input.trim().length > 0;

  const handleSelect = (action: string) => {
    if (!ready) return;
    onSelectRepo(input);
    onSelectAction(action);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <div style={{ display: "flex", justifyContent: "flex-end", padding: "16px 24px 0" }}>
        <button
          onClick={onViewHistory}
          className="btn"
          style={{ fontSize: "var(--sz-xs)", padding: "5px 12px" }}
        >
          ◷ telemetry — all repositories
        </button>
      </div>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: "40px 24px" }}>
        <div style={{ maxWidth: 560, width: "100%" }}>
          <div style={{ marginBottom: 36, animation: "fade-up 0.45s var(--ease-out-quart) backwards" }}>
            <div style={{ fontSize: 30, fontWeight: 600, color: "var(--cyan)", letterSpacing: "-0.01em", marginBottom: 6 }}>jidra</div>
            <div style={{ fontSize: 14, color: "var(--text-muted)" }}>
              Code indexing, graph extraction, and MCP tools for AI-assisted navigation.
            </div>
          </div>

          <div style={{ marginBottom: 28, animation: "fade-up 0.45s var(--ease-out-quart) 0.06s backwards" }}>
            <input
              className="field-input"
              placeholder="/path/to/repository"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && ready) handleSelect("index");
              }}
              style={{ fontSize: 16, marginBottom: 6 }}
              autoFocus
            />
            <div style={{ fontSize: 12, color: "var(--text-faint)" }}>absolute or relative to current directory</div>
          </div>

          <button
            onClick={() => handleSelect("index")}
            disabled={!ready}
            className="btn primary"
            style={{
              width: "100%",
              padding: "12px 18px",
              fontSize: 15,
              marginBottom: 28,
              opacity: ready ? 1 : 0.4,
              animation: "fade-up 0.45s var(--ease-out-quart) 0.12s backwards",
            }}
          >
            ▲ index this repository
          </button>

          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 18 }}>
            <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 4 }}>
              or jump straight to a view (works once indexed)
            </div>
            {ACTIONS.map((action, i) => (
              <button
                key={action.id}
                onClick={() => handleSelect(action.id)}
                disabled={!ready}
                className="home-action-row"
                style={{ animation: `fade-left 0.3s var(--ease-out-quart) ${0.16 + i * 0.025}s backwards` }}
              >
                <span style={{ fontSize: 15, color: "var(--text-muted)", width: 20, flexShrink: 0 }}>{action.icon}</span>
                <span style={{ fontSize: 14, color: "var(--text)", minWidth: 92 }}>{action.label}</span>
                <span style={{ fontSize: 12, color: "var(--text-faint)" }}>{action.desc}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
