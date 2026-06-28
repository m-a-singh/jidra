import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

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
    <div className="topbar">
      <span className="topbar-label">repo</span>
      <button
        className="btn"
        onClick={handlePick}
        disabled={picking}
        style={{ padding: "3px 10px", fontSize: "var(--sz-xs)" }}
        title="Browse for repository folder"
      >
        {picking ? "…" : "browse"}
      </button>
      <input
        className="topbar-input"
        placeholder="/path/to/repository"
        value={repoPath}
        onChange={(e) => setRepoPath(e.target.value)}
        spellCheck={false}
      />
      <div className="topbar-sep" />
      <span className="topbar-label">out</span>
      <input
        className="topbar-input"
        style={{ width: 200 }}
        placeholder="output (optional)"
        value={outputPath}
        onChange={(e) => setOutputPath(e.target.value)}
        spellCheck={false}
      />
      <div style={{ flex: 1 }} />
      {status && (
        <div className="status-badge">
          <div className="status-dot" style={{ background: status.indexed ? "var(--success)" : "var(--text-faint)" }} />
          <span style={{ color: status.indexed ? "var(--success)" : "var(--text-faint)" }}>
            {status.indexed
              ? `${status.node_count?.toLocaleString()} methods · ${status.class_count?.toLocaleString()} classes${status.doc_count ? ` · ${status.doc_count} doc${status.doc_count === 1 ? "" : "s"}` : ""} · ${status.validated ? "validated" : "main"}`
              : "not indexed — index first"}
          </span>
        </div>
      )}
    </div>
  );
}
