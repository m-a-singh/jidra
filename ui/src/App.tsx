import { useState } from "react";
import { useRepo } from "./hooks/useRepo";
import { HomePage } from "./components/HomePage";
import { RepoSelector } from "./components/RepoSelector";
import { IndexPanel } from "./components/IndexPanel";
import { GraphViewer } from "./components/GraphViewer";
import { SqlEditor } from "./components/SqlEditor";
import { McpInspector } from "./components/McpInspector";
import { ExplorePanel } from "./components/ExplorePanel";
import { DocGraphViewer } from "./components/DocGraphViewer";
import { HistoryPanel } from "./components/HistoryPanel";

type Tab = "index" | "graph" | "sql" | "mcp" | "explore" | "docs" | "history" | null;

const NAV: { id: Tab; label: string; title: string }[] = [
  { id: "index",   label: "IDX", title: "Index" },
  { id: "graph",   label: "GRF", title: "Graph" },
  { id: "sql",     label: "SQL", title: "SQL" },
  { id: "mcp",     label: "MCP", title: "MCP Tools" },
  { id: "explore", label: "TRC", title: "Trace / Context / Flow" },
  { id: "docs",    label: "DOC", title: "Doc Graph" },
  { id: "history", label: "HIST", title: "Telemetry History" },
];

export default function App() {
  const repo = useRepo();
  const [tab, setTab] = useState<Tab>(null);

  if (!repo.repoPath && tab !== "history") {
    return (
      <HomePage
        {...repo}
        onSelectRepo={(path) => repo.setRepoPath(path)}
        onSelectAction={(action) => setTab((action as Tab) || "index")}
        onViewHistory={() => setTab("history")}
      />
    );
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo" onClick={() => { setTab(null); repo.setRepoPath(""); }} style={{ cursor: "pointer" }}>J</div>
        {NAV.map((n) => (
          <div
            key={n.id}
            className={`nav-item${tab === n.id ? " active" : ""}`}
            onClick={() => setTab(n.id)}
            title={n.title}
          >
            {n.label}
          </div>
        ))}
      </aside>
      <div className="main">
        <RepoSelector {...repo} />
        <div className="panel">
          {tab === "index"   && <IndexPanel {...repo} />}
          {tab === "graph"   && <GraphViewer {...repo} />}
          {tab === "sql"     && <SqlEditor {...repo} />}
          {tab === "mcp"     && <McpInspector {...repo} />}
          {tab === "explore" && <ExplorePanel {...repo} />}
          {tab === "docs"    && <DocGraphViewer {...repo} />}
          {tab === "history" && <HistoryPanel {...repo} />}
        </div>
      </div>
    </div>
  );
}
