import { useEffect, useRef, useState } from "react";
import { Chart, registerables } from "chart.js";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

Chart.register(...registerables);

interface IndexEvent { ts: number; repo: string; languages: string; classes: number; methods: number; lines: number; elapsed_ms: number }
interface ReindexEvent { ts: number; repo: string; changed_file: string; language: string; change_type: string; methods_added: number; methods_deleted: number; lines_added: number; lines_deleted: number; elapsed_ms: number }
interface DocEvent { ts: number; source_path: string; source_type: string; chunks: number; linked_classes: number; file_size_bytes: number; elapsed_ms: number; status: string; error?: string }

const LANG_COLORS: Record<string, string> = {
  java: "#f59e0b", python: "#34d399", typescript: "#38bdf8", scala: "#fb7185", go: "#67e8f9",
};
const CHANGE_TYPE_COLORS: Record<string, string> = {
  no_change: "#64748b", metadata_only: "#38bdf8", callsite_change: "#f59e0b", structural: "#fb7185", full_rebuild: "#a78bfa",
};

function fmtTs(ts: number): string {
  return new Date(ts).toLocaleString();
}
function fmtElapsed(ms: number): string {
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}
function basename(p: string): string {
  return p.split("/").pop() || p;
}
function langBadge(lang: string) {
  const color = LANG_COLORS[lang] || "var(--text-muted)";
  return <span className="badge" style={{ background: "var(--surface-3)", color, border: `1px solid ${color}55` }}>{lang}</span>;
}
function changeTypeBadge(ct: string) {
  const color = CHANGE_TYPE_COLORS[ct] || "var(--text-muted)";
  return <span className="badge" style={{ background: "var(--surface-3)", color }}>{ct}</span>;
}

const GRID = "#1a2a3a";
const TICK = "#4d6173";
const baseOpts = {
  responsive: true,
  maintainAspectRatio: true,
  plugins: { legend: { labels: { color: TICK, boxWidth: 12, font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: TICK, maxTicksLimit: 6, font: { size: 10 } }, grid: { color: GRID } },
    y: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
  },
};
const rotatedOpts = {
  ...baseOpts,
  scales: { ...baseOpts.scales, x: { ...baseOpts.scales.x, ticks: { ...baseOpts.scales.x.ticks, maxRotation: 45 } } },
};
const noScales = { responsive: true, maintainAspectRatio: true, plugins: { legend: { labels: { color: TICK, font: { size: 11 } } } } };

function useChart(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  build: () => { type: any; data: any; options: any } | null,
  deps: unknown[],
) {
  const chartRef = useRef<Chart | null>(null);
  useEffect(() => {
    chartRef.current?.destroy();
    chartRef.current = null;
    if (!canvasRef.current) return;
    const cfg = build();
    if (!cfg) return;
    chartRef.current = new Chart(canvasRef.current, cfg);
    return () => { chartRef.current?.destroy(); chartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function HistoryPanel({ repoPath }: RepoState) {
  const [indexEvents, setIndexEvents] = useState<IndexEvent[]>([]);
  const [reindexEvents, setReindexEvents] = useState<ReindexEvent[]>([]);
  const [docEvents, setDocEvents] = useState<DocEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterRepo, setFilterRepo] = useState(false);

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

  const totalClasses = indexEvents[0]?.classes ?? 0;
  const totalMethods = indexEvents[0]?.methods ?? 0;
  const totalLines = indexEvents[0]?.lines ?? 0;
  const avgIndexMs = indexEvents.length
    ? Math.round(indexEvents.reduce((s, r) => s + r.elapsed_ms, 0) / indexEvents.length)
    : 0;
  const totalReindex = reindexEvents.length;
  const okDocs = docEvents.filter((r) => r.status === "ok");
  const totalDocs = okDocs.length;
  const totalDocChunks = okDocs.reduce((s, r) => s + r.chunks, 0);

  const changeTypeCounts: Record<string, number> = {};
  for (const r of reindexEvents) changeTypeCounts[r.change_type] = (changeTypeCounts[r.change_type] || 0) + 1;

  const STATS = [
    { label: "Classes", value: totalClasses.toLocaleString(), color: "#38bdf8", icon: "⬡" },
    { label: "Methods", value: totalMethods.toLocaleString(), color: "#a78bfa", icon: "ƒ" },
    { label: "Lines of code", value: totalLines.toLocaleString(), color: "#34d399", icon: "≡" },
    { label: "Avg index time", value: fmtElapsed(avgIndexMs), color: "#f59e0b", icon: "⏱" },
    { label: "Reindex events", value: totalReindex.toLocaleString(), color: "#fb7185", icon: "↻" },
    { label: "Docs indexed", value: totalDocs.toLocaleString(), color: "#67e8f9", icon: "▤" },
    { label: "Doc chunks", value: totalDocChunks.toLocaleString(), color: "#a78bfa", icon: "⊞" },
  ];

  const growthCanvas = useRef<HTMLCanvasElement>(null);
  const doughnutCanvas = useRef<HTMLCanvasElement>(null);
  const elapsedCanvas = useRef<HTMLCanvasElement>(null);
  const reindexElapsedCanvas = useRef<HTMLCanvasElement>(null);
  const docChunksCanvas = useRef<HTMLCanvasElement>(null);
  const docElapsedCanvas = useRef<HTMLCanvasElement>(null);

  const idxRev = [...indexEvents].reverse();
  useChart(growthCanvas, () => ({
    type: "line",
    data: {
      labels: idxRev.map((r) => fmtTs(r.ts)),
      datasets: [
        { label: "Classes", data: idxRev.map((r) => r.classes), borderColor: "#38bdf8", backgroundColor: "#38bdf81a", fill: true, tension: 0.4, pointRadius: 4, pointHoverRadius: 6 },
        { label: "Methods", data: idxRev.map((r) => r.methods), borderColor: "#a78bfa", backgroundColor: "#a78bfa1a", fill: true, tension: 0.4, pointRadius: 4, pointHoverRadius: 6 },
      ],
    },
    options: baseOpts,
  }), [indexEvents]);

  useChart(elapsedCanvas, () => ({
    type: "bar",
    data: {
      labels: idxRev.map((r) => fmtTs(r.ts)),
      datasets: [{ label: "Elapsed (ms)", data: idxRev.map((r) => r.elapsed_ms), backgroundColor: "#f59e0b33", borderColor: "#f59e0b", borderWidth: 1, borderRadius: 4 }],
    },
    options: baseOpts,
  }), [indexEvents]);

  const ctLabels = Object.keys(changeTypeCounts);
  useChart(doughnutCanvas, () => ({
    type: "doughnut",
    data: {
      labels: ctLabels,
      datasets: [{ data: ctLabels.map((k) => changeTypeCounts[k]), backgroundColor: ctLabels.map((k) => CHANGE_TYPE_COLORS[k] || "#64748b"), borderWidth: 0, hoverOffset: 6 }],
    },
    options: { ...noScales, cutout: "65%" },
  }), [reindexEvents]);

  const reindexRev = [...reindexEvents].slice(0, 50).reverse();
  useChart(reindexElapsedCanvas, () => ({
    type: "bar",
    data: {
      labels: reindexRev.map((r) => `${basename(r.changed_file)} ${fmtTs(r.ts)}`),
      datasets: [{ label: "Elapsed (ms)", data: reindexRev.map((r) => r.elapsed_ms), backgroundColor: "#34d39933", borderColor: "#34d399", borderWidth: 1, borderRadius: 4 }],
    },
    options: rotatedOpts,
  }), [reindexEvents]);

  const docRev = [...docEvents].slice(0, 30).reverse();
  useChart(docChunksCanvas, () => ({
    type: "bar",
    data: {
      labels: docRev.map((r) => basename(r.source_path)),
      datasets: [
        { label: "Chunks", data: docRev.map((r) => r.chunks), backgroundColor: "#67e8f933", borderColor: "#67e8f9", borderWidth: 1, borderRadius: 4 },
        { label: "Linked classes", data: docRev.map((r) => r.linked_classes), backgroundColor: "#a78bfa33", borderColor: "#a78bfa", borderWidth: 1, borderRadius: 4 },
      ],
    },
    options: rotatedOpts,
  }), [docEvents]);

  useChart(docElapsedCanvas, () => ({
    type: "bar",
    data: {
      labels: docRev.map((r) => basename(r.source_path)),
      datasets: [{ label: "Elapsed (ms)", data: docRev.map((r) => r.elapsed_ms), backgroundColor: "#fb718533", borderColor: "#fb7185", borderWidth: 1, borderRadius: 4 }],
    },
    options: rotatedOpts,
  }), [docEvents]);

  return (
    <div className="panel-body">
      <div className="section-label">telemetry — all repositories</div>

      <div className="panel-row" style={{ marginBottom: 24 }}>
        <button className={`btn primary run-btn${loading ? " running" : ""}`} onClick={load} disabled={loading}>
          <span className="run-btn-content">
            <span className="run-btn-icon">{loading ? "◐" : "↻"}</span>
            <span>{loading ? "loading" : "refresh"}</span>
          </span>
        </button>
        <label className="check-row">
          <input type="checkbox" checked={filterRepo} onChange={(e) => setFilterRepo(e.target.checked)} disabled={!repoPath} />
          filter to current repo{repoPath ? "" : " (no repo selected)"}
        </label>
      </div>

      <div className="stats-grid">
        {STATS.map((s) => (
          <div key={s.label} className="stat-card">
            <div className="stat-icon" style={{ color: s.color }}>{s.icon}</div>
            <div className="stat-value" style={{ color: s.color }}>{s.value}</div>
            <div className="stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="charts-grid">
        <div className="chart-card">
          <h3>classes &amp; methods — index history</h3>
          <canvas ref={growthCanvas} />
        </div>
        <div className="chart-card">
          <h3>reindex change types</h3>
          <canvas ref={doughnutCanvas} />
        </div>
      </div>

      <div className="charts-row2">
        <div className="chart-card">
          <h3>full index elapsed time</h3>
          <canvas ref={elapsedCanvas} />
        </div>
        <div className="chart-card">
          <h3>reindex elapsed time (last 50)</h3>
          <canvas ref={reindexElapsedCanvas} />
        </div>
      </div>

      <div className="section-header">
        <h2>full index events</h2>
        <span className="count">{indexEvents.length}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>time</th><th>repo</th><th>languages</th><th>classes</th><th>methods</th><th>lines</th><th>elapsed</th></tr></thead>
          <tbody>
            {indexEvents.length === 0 ? (
              <tr><td colSpan={7} className="empty-row">No index events recorded yet.</td></tr>
            ) : indexEvents.map((r, i) => (
              <tr key={i}>
                <td className="ts">{fmtTs(r.ts)}</td>
                <td><span className="repo-name" title={r.repo}>{basename(r.repo)}</span></td>
                <td>{r.languages.split(",").filter(Boolean).map((l) => <span key={l} style={{ marginRight: 4 }}>{langBadge(l)}</span>)}</td>
                <td className="num">{r.classes.toLocaleString()}</td>
                <td className="num">{r.methods.toLocaleString()}</td>
                <td className="num">{r.lines.toLocaleString()}</td>
                <td className="num elapsed">{fmtElapsed(r.elapsed_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section-header">
        <h2>incremental reindex events</h2>
        <span className="count">{reindexEvents.length}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>time</th><th>repo</th><th>file</th><th>lang</th><th>change type</th><th>methods +/-</th><th>lines +/-</th><th>elapsed</th></tr></thead>
          <tbody>
            {reindexEvents.length === 0 ? (
              <tr><td colSpan={8} className="empty-row">No reindex events recorded yet.</td></tr>
            ) : reindexEvents.map((r, i) => (
              <tr key={i}>
                <td className="ts">{fmtTs(r.ts)}</td>
                <td><span className="repo-name" title={r.repo}>{basename(r.repo)}</span></td>
                <td><span className="file-name" title={r.changed_file}>{basename(r.changed_file)}</span></td>
                <td>{r.language ? langBadge(r.language) : null}</td>
                <td>{changeTypeBadge(r.change_type)}</td>
                <td className="num"><span className="delta-pos">+{r.methods_added}</span> / <span className="delta-neg">-{r.methods_deleted}</span></td>
                <td className="num"><span className="delta-pos">+{r.lines_added}</span> / <span className="delta-neg">-{r.lines_deleted}</span></td>
                <td className="num elapsed">{fmtElapsed(r.elapsed_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="charts-row2">
        <div className="chart-card">
          <h3>doc chunks &amp; linked classes per file</h3>
          <canvas ref={docChunksCanvas} />
        </div>
        <div className="chart-card">
          <h3>doc indexing elapsed time</h3>
          <canvas ref={docElapsedCanvas} />
        </div>
      </div>

      <div className="section-header">
        <h2>doc index events</h2>
        <span className="count">{docEvents.length}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>time</th><th>file</th><th>type</th><th>chunks</th><th>linked classes</th><th>size</th><th>elapsed</th><th>status</th></tr></thead>
          <tbody>
            {docEvents.length === 0 ? (
              <tr><td colSpan={8} className="empty-row">No documents indexed yet — enable "index docs" on the IDX tab.</td></tr>
            ) : docEvents.map((r, i) => {
              const sizeKb = r.file_size_bytes / 1024;
              const sizeStr = sizeKb >= 1 ? `${sizeKb.toFixed(1)} KB` : `${r.file_size_bytes} B`;
              return (
                <tr key={i}>
                  <td className="ts">{fmtTs(r.ts)}</td>
                  <td><span className="file-name" title={r.source_path}>{basename(r.source_path)}</span></td>
                  <td>{langBadge(r.source_type)}</td>
                  <td className="num">{r.chunks}</td>
                  <td className="num">{r.linked_classes}</td>
                  <td className="num">{sizeStr}</td>
                  <td className="num elapsed">{fmtElapsed(r.elapsed_ms)}</td>
                  <td>
                    <span
                      className="badge"
                      style={{ background: "var(--surface-3)", color: r.status === "ok" ? "var(--success)" : "var(--error)" }}
                      title={r.error || ""}
                    >
                      {r.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
