import { useState } from "react";
import { cn } from "./lib/utils";
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
    <div className="flex h-screen w-screen overflow-hidden">
      <aside className="w-14 min-w-14 bg-surface border-r border-border flex flex-col items-center pt-2.5 gap-0.5">
        <div
          className="w-[30px] h-[30px] flex items-center justify-center text-accent text-sm font-semibold tracking-tight mb-3.5 border border-accent-dim bg-accent-subtle rounded-md cursor-pointer"
          onClick={() => { setTab(null); repo.setRepoPath(""); }}
        >
          J
        </div>
        {NAV.map((n) => (
          <div
            key={n.id}
            className={cn(
              "w-10 h-9 flex flex-col items-center justify-center cursor-pointer text-xs font-medium tracking-wide rounded-md transition-colors select-none",
              tab === n.id
                ? "text-accent bg-accent-subtle font-semibold"
                : "text-text-dim hover:text-text-muted hover:bg-accent-subtle"
            )}
            onClick={() => setTab(n.id)}
            title={n.title}
          >
            {n.label}
          </div>
        ))}
      </aside>
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <RepoSelector {...repo} />
        <div className="flex-1 overflow-hidden flex flex-col">
          {tab === "index"   && <IndexPanel {...repo} onNavigate={(t) => setTab(t as Tab)} />}
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
