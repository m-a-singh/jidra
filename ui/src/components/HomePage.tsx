import { useState } from "react";
import type { RepoState } from "../hooks/useRepo";

interface ActionCard {
  id: string;
  label: string;
  icon: string;
  desc: string;
  color: "cyan" | "amber" | "green" | "purple";
}

const ACTIONS: ActionCard[] = [
  { id: "index", label: "Index", icon: "▲", desc: "Extract codebase graph and index", color: "cyan" },
  { id: "graph", label: "Graph", icon: "◆", desc: "Visualize code structure", color: "amber" },
  { id: "sql", label: "Database", icon: "◇", desc: "Query indexed data", color: "green" },
  { id: "mcp", label: "MCP Tools", icon: "⚙", desc: "Inspect available tools", color: "purple" },
  { id: "explore", label: "Explore", icon: "◈", desc: "Trace, context, flow, route", color: "cyan" },
  { id: "docs", label: "Doc graph", icon: "▣", desc: "Doc-to-code linkage", color: "amber" },
  { id: "history", label: "History", icon: "◷", desc: "Telemetry & reindex log", color: "purple" },
];

export function HomePage({
  repoPath,
  onSelectRepo,
  onSelectAction,
}: RepoState & { onSelectRepo: (path: string) => void; onSelectAction: (action: string) => void }) {
  const [input, setInput] = useState(repoPath || "");

  const handleSelect = (action: string) => {
    if (!input.trim()) return;
    onSelectRepo(input);
    onSelectAction(action);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: "40px 24px" }}>
        <div style={{ maxWidth: 680 }}>
          <div style={{ marginBottom: 40, textAlign: "center" }}>
            <div style={{ fontSize: 32, fontWeight: 600, color: "var(--cyan)", marginBottom: 8 }}>JIDRA</div>
            <div style={{ fontSize: 14, color: "var(--text-muted)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
              Code indexing · graph extraction · mcp tools
            </div>
          </div>

          <div style={{ marginBottom: 32 }}>
            <label style={{ fontSize: 12, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.1em", display: "block", marginBottom: 8 }}>
              Repository path
            </label>
            <input
              className="field-input"
              placeholder="/path/to/repository"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && input.trim()) handleSelect("index");
              }}
              style={{ marginBottom: 4 }}
            />
            <div style={{ fontSize: 11, color: "var(--text-dim)" }}>absolute or relative to current directory</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            {ACTIONS.map((action) => (
              <button
                key={action.id}
                onClick={() => handleSelect(action.id)}
                disabled={!input.trim()}
                style={{
                  padding: "16px 12px",
                  background: `var(--surface-2)`,
                  border: `0.5px solid var(--border)`,
                  borderRadius: 6,
                  cursor: input.trim() ? "pointer" : "default",
                  opacity: input.trim() ? 1 : 0.5,
                  transition: "all 0.2s ease",
                  textAlign: "center",
                }}
                onMouseEnter={(e) => {
                  if (input.trim()) {
                    (e.target as HTMLElement).style.borderColor = "var(--border-strong)";
                    (e.target as HTMLElement).style.background = "var(--surface-3)";
                  }
                }}
                onMouseLeave={(e) => {
                  (e.target as HTMLElement).style.borderColor = "var(--border)";
                  (e.target as HTMLElement).style.background = "var(--surface-2)";
                }}
              >
                <div style={{ fontSize: 18, marginBottom: 6 }}>{action.icon}</div>
                <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text)", marginBottom: 2 }}>{action.label}</div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{action.desc}</div>
              </button>
            ))}
          </div>

          <div style={{ marginTop: 40, padding: "16px", background: "var(--surface-2)", borderRadius: 6, border: "0.5px solid var(--border)" }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
              Jidra indexes your repository, builds a code structure graph, and exposes it via MCP tools for AI-assisted navigation and analysis.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
