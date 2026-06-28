import { useState } from "react";
import { api } from "../lib/api";
import { JsonView } from "./JsonView";
import type { RepoState } from "../hooks/useRepo";

type Mode = "trace" | "context" | "flow" | "route" | "flow-doc" | "error-doc";

const MODES: { id: Mode; label: string }[] = [
  { id: "trace", label: "trace" },
  { id: "context", label: "context" },
  { id: "flow", label: "flow" },
  { id: "route", label: "route" },
  { id: "flow-doc", label: "flow-doc" },
  { id: "error-doc", label: "error-doc" },
];

export function ExplorePanel({ repoPath, outputPath }: RepoState) {
  const [mode, setMode] = useState<Mode>("trace");
  const [method, setMethod] = useState("");
  const [route, setRoute] = useState("");
  const [stackTrace, setStackTrace] = useState("");
  const [depth, setDepth] = useState(4);
  const [businessOnly, setBusinessOnly] = useState(true);
  const [result, setResult] = useState<unknown>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    if (!repoPath) { setError("Repository not indexed. Go to IDX tab and run the pipeline first."); return; }
    setError(null);
    setResult(null);
    setMarkdown(null);
    setRunning(true);
    try {
      if (mode === "trace") {
        setResult(await api.explore.trace({ repo_path: repoPath, output_path: outputPath || undefined, method, max_depth: depth, business_only: businessOnly }));
      } else if (mode === "context") {
        setResult(await api.explore.context({ repo_path: repoPath, output_path: outputPath || undefined, method, business_only: businessOnly }));
      } else if (mode === "flow") {
        setResult(await api.explore.flow({ repo_path: repoPath, output_path: outputPath || undefined, method, depth, business_only: businessOnly }));
      } else if (mode === "route") {
        setResult(await api.explore.traceRoute({ repo_path: repoPath, output_path: outputPath || undefined, route, max_depth: depth }));
      } else if (mode === "flow-doc") {
        const res = await api.explore.flowDoc({ repo_path: repoPath, output_path: outputPath || undefined, method, depth });
        setMarkdown(res.markdown);
      } else if (mode === "error-doc") {
        const res = await api.explore.errorDoc({ repo_path: repoPath, output_path: outputPath || undefined, stack_trace: stackTrace, depth });
        setMarkdown(res.markdown);
      }
    } catch (e) {
      setError(String(e).replace("Error: ", ""));
    } finally {
      setRunning(false);
    }
  }

  const needsRoute = mode === "route";
  const needsStack = mode === "error-doc";
  const needsMethod = !needsRoute && !needsStack;

  return (
    <div className="panel-body">
      <div className="section-label">explore</div>

      <div style={{ display: "flex", gap: 6, marginBottom: 20, flexWrap: "wrap" }}>
        {MODES.map((m) => (
          <button
            key={m.id}
            className={`btn${mode === m.id ? " primary" : ""}`}
            onClick={() => { setMode(m.id); setResult(null); setMarkdown(null); setError(null); }}
            style={{ padding: "5px 14px", fontSize: "var(--sz-sm)" }}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, marginBottom: 20 }}>
        {needsMethod && (
          <div className="config-card">
            <div className="config-card-label">method selector</div>
            <input className="field-input" placeholder="Class#method, signature, or method id" value={method} onChange={(e) => setMethod(e.target.value)} />
          </div>
        )}
        {needsRoute && (
          <div className="config-card">
            <div className="config-card-label">route path</div>
            <input className="field-input" placeholder="/api/v1/users" value={route} onChange={(e) => setRoute(e.target.value)} />
          </div>
        )}
        {mode !== "error-doc" && (
          <div className="config-card">
            <div className="config-card-label">depth</div>
            <input className="field-input" type="number" value={depth} onChange={(e) => setDepth(+e.target.value)} />
          </div>
        )}
        {(mode === "trace" || mode === "context" || mode === "flow") && (
          <div className="config-card" style={{ display: "flex", alignItems: "center" }}>
            <label className="check-row">
              <input type="checkbox" checked={businessOnly} onChange={(e) => setBusinessOnly(e.target.checked)} />
              business only (hide logging/metrics noise)
            </label>
          </div>
        )}
      </div>

      {needsStack && (
        <div style={{ marginBottom: 20 }}>
          <div className="config-card-label" style={{ marginBottom: 8 }}>stack trace</div>
          <textarea
            className="field-textarea"
            rows={8}
            placeholder="Paste a Java stack trace…"
            value={stackTrace}
            onChange={(e) => setStackTrace(e.target.value)}
          />
        </div>
      )}

      <div className="panel-row">
        <button className={`btn primary run-btn${running ? " running" : ""}`} onClick={run} disabled={running || !repoPath}>
          <span className="run-btn-content">
            <span className="run-btn-icon">{running ? "⚡" : "▶"}</span>
            <span className="run-btn-text">{running ? "running" : "run"}</span>
          </span>
          {running && <span className="run-btn-shimmer" />}
        </button>
        {error && <span className="inline-error">{error}</span>}
      </div>

      {markdown && (
        <div className="log-pane" style={{ padding: "12px 16px", whiteSpace: "pre-wrap", fontSize: "var(--sz-sm)", lineHeight: 1.7 }}>
          {markdown}
        </div>
      )}
      {result != null && !markdown && (
        <div className="log-pane" style={{ padding: 0 }}>
          <JsonView value={result} />
        </div>
      )}
      {result == null && !markdown && !error && (
        <div className="log-pane" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 160 }}>
          <span style={{ color: "var(--text-dim)" }}>configure above and run</span>
        </div>
      )}
    </div>
  );
}
