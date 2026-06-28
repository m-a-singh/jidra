import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

interface IndexEvent { ts: number; repo: string; languages: string; classes: number; methods: number; lines: number; elapsed_ms: number }
interface ReindexEvent { ts: number; repo: string; changed_file: string; language: string; change_type: string; methods_added: number; methods_deleted: number; elapsed_ms: number }
interface DocEvent { ts: number; source_path: string; source_type: string; chunks: number; status: string; elapsed_ms: number }

function fmtTime(ts: number): string {
  return new Date(ts).toLocaleString();
}

export function HistoryPanel({ repoPath }: RepoState) {
  const [indexEvents, setIndexEvents] = useState<IndexEvent[]>([]);
  const [reindexEvents, setReindexEvents] = useState<ReindexEvent[]>([]);
  const [docEvents, setDocEvents] = useState<DocEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterRepo, setFilterRepo] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const data = await api.history.list(filterRepo && repoPath ? repoPath : undefined, 50);
      setIndexEvents(data.index_events as unknown as IndexEvent[]);
      setReindexEvents(data.reindex_events as unknown as ReindexEvent[]);
      setDocEvents(data.doc_events as unknown as DocEvent[]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [filterRepo, repoPath]);

  return (
    <div className="panel-body">
      <div className="section-label">telemetry history</div>

      <div className="panel-row">
        <button className={`btn primary${loading ? " running" : ""}`} onClick={load} disabled={loading}>
          {loading ? "loading…" : "refresh"}
        </button>
        <label className="check-row">
          <input type="checkbox" checked={filterRepo} onChange={(e) => setFilterRepo(e.target.checked)} disabled={!repoPath} />
          filter to current repo
        </label>
      </div>

      <div className="section-label" style={{ marginTop: 24 }}>index events ({indexEvents.length})</div>
      {indexEvents.length === 0 ? (
        <div className="empty-state">No index events recorded yet.</div>
      ) : (
        <table className="result-table" style={{ marginBottom: 24 }}>
          <thead><tr><th>time</th><th>repo</th><th>languages</th><th>classes</th><th>methods</th><th>lines</th><th>elapsed</th></tr></thead>
          <tbody>
            {indexEvents.map((r, i) => (
              <tr key={i}>
                <td>{fmtTime(r.ts)}</td>
                <td title={r.repo}>{r.repo.split("/").pop()}</td>
                <td>{r.languages}</td>
                <td>{r.classes}</td>
                <td>{r.methods}</td>
                <td>{r.lines}</td>
                <td>{r.elapsed_ms}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="section-label">reindex events ({reindexEvents.length})</div>
      {reindexEvents.length === 0 ? (
        <div className="empty-state">No reindex events recorded yet.</div>
      ) : (
        <table className="result-table" style={{ marginBottom: 24 }}>
          <thead><tr><th>time</th><th>repo</th><th>file</th><th>lang</th><th>type</th><th>methods +/-</th><th>elapsed</th></tr></thead>
          <tbody>
            {reindexEvents.map((r, i) => (
              <tr key={i}>
                <td>{fmtTime(r.ts)}</td>
                <td title={r.repo}>{r.repo.split("/").pop()}</td>
                <td title={r.changed_file}>{r.changed_file.split("/").pop()}</td>
                <td>{r.language}</td>
                <td>{r.change_type}</td>
                <td>+{r.methods_added}/-{r.methods_deleted}</td>
                <td>{r.elapsed_ms}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="section-label">doc index events ({docEvents.length})</div>
      {docEvents.length === 0 ? (
        <div className="empty-state">No document indexing events recorded yet.</div>
      ) : (
        <table className="result-table">
          <thead><tr><th>time</th><th>source</th><th>type</th><th>chunks</th><th>status</th><th>elapsed</th></tr></thead>
          <tbody>
            {docEvents.map((r, i) => (
              <tr key={i}>
                <td>{fmtTime(r.ts)}</td>
                <td title={r.source_path}>{r.source_path.split("/").pop()}</td>
                <td>{r.source_type}</td>
                <td>{r.chunks}</td>
                <td style={{ color: r.status === "ok" ? "var(--success)" : "var(--error)" }}>{r.status}</td>
                <td>{r.elapsed_ms}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
