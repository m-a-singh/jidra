import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";

type Status = { indexed: boolean; variant?: string; node_count?: number; class_count?: number; validated?: boolean; doc_count?: number };

async function pickFolder(): Promise<string | null> {
  const res = await fetch("/api/util/pick-folder", { method: "POST" });
  if (!res.ok) return null;
  const data = await res.json();
  return data.cancelled ? null : data.path;
}

export function RepoSelector({ repoPath, outputPath, setRepoPath, setOutputPath }: RepoState) {
  const [status, setStatus] = useState<Status | null>(null);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    if (!repoPath) return;
    api.index.status(repoPath, outputPath || undefined).then(setStatus).catch(() => setStatus({ indexed: false }));
  }, [repoPath, outputPath]);

  async function handlePick() {
    setPicking(true);
    try {
      const path = await pickFolder();
      if (path) setRepoPath(path);
    } finally {
      setPicking(false);
    }
  }

  return (
    <div className="h-11 min-h-11 bg-surface border-b border-border flex items-center px-3.5 gap-2.5">
      <span className="text-xs text-text-dim tracking-widest uppercase">repo</span>
      <Button variant="default" size="sm" onClick={handlePick} disabled={picking} title="Browse for repository folder">
        {picking ? "…" : "browse"}
      </Button>
      <input
        className="bg-transparent border-0 border-b border-border-mid text-text text-sm py-0.5 outline-none w-[340px] tracking-tight transition-colors focus:border-accent-dim placeholder:text-text-faint"
        placeholder="/path/to/repository"
        value={repoPath}
        onChange={(e) => setRepoPath(e.target.value)}
        spellCheck={false}
      />
      <div className="w-px h-3.5 bg-border-mid" />
      <span className="text-xs text-text-dim tracking-widest uppercase">out</span>
      <input
        className="bg-transparent border-0 border-b border-border-mid text-text text-sm py-0.5 outline-none w-[200px] tracking-tight transition-colors focus:border-accent-dim placeholder:text-text-faint"
        placeholder="output (optional)"
        value={outputPath}
        onChange={(e) => setOutputPath(e.target.value)}
        spellCheck={false}
      />
      <div className="flex-1" />
      {status && (
        <div className="text-xs tracking-wide flex items-center gap-1.5">
          <div className={cn("w-1.5 h-1.5 rounded-full", status.indexed ? "bg-success" : "bg-text-faint")} />
          <span className={status.indexed ? "text-success" : "text-text-faint"}>
            {status.indexed
              ? `${status.node_count?.toLocaleString()} methods · ${status.class_count?.toLocaleString()} classes${status.doc_count ? ` · ${status.doc_count} doc${status.doc_count === 1 ? "" : "s"}` : ""} · ${status.validated ? "validated" : "main"}`
              : "not indexed — index first"}
          </span>
        </div>
      )}
    </div>
  );
}
