import { useState } from "react";
import type { RepoState } from "../hooks/useRepo";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

interface ActionItem { id: string; label: string; icon: string; desc: string }

const ACTIONS: ActionItem[] = [
  { id: "graph", label: "Graph", icon: "◆", desc: "Visualize code structure" },
  { id: "sql", label: "Database", icon: "◇", desc: "Query indexed data" },
  { id: "mcp", label: "MCP tools", icon: "⚙", desc: "Inspect available tools" },
  { id: "explore", label: "Explore", icon: "◈", desc: "Trace, context, flow, route" },
  { id: "docs", label: "Doc graph", icon: "▣", desc: "Doc-to-code linkage" },
];

async function pickFolder(): Promise<string | null> {
  const res = await fetch("/api/util/pick-folder", { method: "POST" });
  if (!res.ok) return null;
  const data = await res.json();
  return data.cancelled ? null : data.path;
}

export function HomePage({
  repoPath,
  onSelectRepo,
  onSelectAction,
  onViewHistory,
}: RepoState & { onSelectRepo: (path: string) => void; onSelectAction: (action: string) => void; onViewHistory: () => void }) {
  const [input, setInput] = useState(repoPath || "");
  const [picking, setPicking] = useState(false);
  const ready = input.trim().length > 0;

  const handleSelect = (action: string) => {
    if (!ready) return;
    onSelectRepo(input);
    onSelectAction(action);
  };

  async function handlePick() {
    setPicking(true);
    try {
      const path = await pickFolder();
      if (path) setInput(path);
    } finally {
      setPicking(false);
    }
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-bg">
      <div className="flex justify-end px-6 pt-4">
        <Button variant="default" size="sm" onClick={onViewHistory}>
          ◷ telemetry — all repositories
        </Button>
      </div>
      <div className="flex-1 flex items-center justify-center px-8 py-16">
        <div className="max-w-[760px] w-full">
          <div className="mb-14 animate-[fade-up_0.45s_var(--ease-out-quart)_backwards]">
            <div className="text-6xl font-semibold text-accent tracking-tight mb-4">jidra</div>
            <div className="text-lg text-text-muted">
              Code indexing, graph extraction, and MCP tools for AI-assisted navigation.
            </div>
          </div>

          <div className="mb-10 animate-[fade-up_0.45s_var(--ease-out-quart)_0.06s_backwards]">
            <div className="flex items-center gap-4 mb-3">
              <Input
                placeholder="/path/to/repository"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && ready) handleSelect("index");
                }}
                className="text-lg py-3"
                autoFocus
              />
              <Button variant="default" size="default" onClick={handlePick} disabled={picking} title="Browse for repository folder">
                {picking ? "…" : "browse"}
              </Button>
            </div>
            <div className="text-sm text-text-faint">absolute or relative to current directory</div>
          </div>

          <Button
            onClick={() => handleSelect("index")}
            disabled={!ready}
            variant="primary"
            className="w-full text-lg mb-10 h-14 animate-[fade-up_0.45s_var(--ease-out-quart)_0.12s_backwards]"
          >
            ▲ index this repository
          </Button>

          <div className="border-t border-border pt-8">
            <div className="text-sm text-text-faint mb-3">
              or jump straight to a view (works once indexed)
            </div>
            {ACTIONS.map((action, i) => (
              <button
                key={action.id}
                onClick={() => handleSelect(action.id)}
                disabled={!ready}
                className="flex items-center gap-5 w-full py-4 px-3 bg-transparent border-0 border-b border-border cursor-pointer text-left transition-all duration-150 last:border-b-0 enabled:hover:bg-accent-subtle enabled:hover:translate-x-1 disabled:cursor-default disabled:opacity-45"
                style={{ animation: `fade-left 0.3s var(--ease-out-quart) ${0.16 + i * 0.025}s backwards` }}
              >
                <span className="text-xl text-text-muted w-6 shrink-0">{action.icon}</span>
                <span className="text-base text-text min-w-[110px]">{action.label}</span>
                <span className="text-sm text-text-faint">{action.desc}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
