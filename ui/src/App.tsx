import { useState } from "react";
import { useRepo } from "./hooks/useRepo";
import { RepoSelector } from "./components/RepoSelector";
import { IndexPanel } from "./components/IndexPanel";
import { GraphViewer } from "./components/GraphViewer";
import { SqlEditor } from "./components/SqlEditor";
import { McpInspector } from "./components/McpInspector";

type Tab = "index" | "graph" | "sql" | "mcp";

const NAV: { id: Tab; label: string; title: string }[] = [
  { id: "index", label: "IDX", title: "Index" },
  { id: "graph", label: "GRF", title: "Graph" },
  { id: "sql",   label: "SQL", title: "SQL" },
  { id: "mcp",   label: "MCP", title: "MCP Tools" },
];

export default function App() {
  const repo = useRepo();
  const [tab, setTab] = useState<Tab>("index");

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo">J</div>
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
          {tab === "index" && <IndexPanel {...repo} />}
          {tab === "graph" && <GraphViewer {...repo} />}
          {tab === "sql"   && <SqlEditor {...repo} />}
          {tab === "mcp"   && <McpInspector {...repo} />}
        </div>
      </div>
    </div>
  );
}
