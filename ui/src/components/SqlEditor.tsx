import { useEffect, useRef, useState } from "react";
import { EditorView, basicSetup } from "codemirror";
import { sql } from "@codemirror/lang-sql";
import { oneDark } from "@codemirror/theme-one-dark";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

type SchemaEntry = { table: string; columns: { name: string; type: string }[] };

export function SqlEditor({ repoPath, outputPath }: RepoState) {
  const editorRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const [db, setDb] = useState<"graph" | "telemetry">("graph");
  const [schema, setSchema] = useState<SchemaEntry[]>([]);
  const [result, setResult] = useState<{ columns: string[]; rows: unknown[][]; truncated: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!editorRef.current) return;
    viewRef.current = new EditorView({
      doc: "SELECT * FROM methods LIMIT 50;",
      extensions: [basicSetup, sql(), oneDark],
      parent: editorRef.current,
    });
    return () => viewRef.current?.destroy();
  }, []);

  useEffect(() => {
    if (!repoPath) return;
    api.sql.schema(repoPath, db).then(setSchema).catch(() => setSchema([]));
  }, [repoPath, db]);

  async function run() {
    if (!repoPath || !viewRef.current) return;
    setError(null);
    setRunning(true);
    try {
      setResult(await api.sql.query({ repo_path: repoPath, sql: viewRef.current.state.doc.toString(), db }));
    } catch (e) {
      const msg = String(e).replace("Error: ", "");
      if (msg.includes("not found") || msg.includes("404") || msg.includes("No such file")) {
        setError("Repository not indexed. Go to IDX tab and run the pipeline first.");
      } else {
        setError(msg);
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="split">
      <div className="split-left schema-pane">
        <select
          className="db-select"
          value={db}
          onChange={(e) => setDb(e.target.value as "graph" | "telemetry")}
          style={{ width: "100%", marginBottom: 12 }}
        >
          <option value="graph">graph.db</option>
          <option value="telemetry">telemetry.db</option>
        </select>
        {schema.length === 0 && <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)" }}>{repoPath ? "loading schema…" : "not indexed — index first"}</div>}
        {schema.map((t) => (
          <div key={t.table}>
            <div className="schema-table-name">{t.table}</div>
            {t.columns.map((c) => (
              <div key={c.name} className="schema-col">
                {c.name}<span>{c.type}</span>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="split-right">
        <div className="sql-editor-pane">
          <div ref={editorRef} style={{ height: "100%" }} />
        </div>
        <div className="sql-toolbar">
          <button
            className={`btn primary${running ? " running" : ""}`}
            onClick={run}
            disabled={running || !repoPath}
          >
            {running ? "running…" : "run"}
          </button>
          {result && !running && (
            <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)" }}>
              {result.rows.length.toLocaleString()} rows
            </span>
          )}
          {result?.truncated && <span className="truncation-warn">truncated at 2 000 rows</span>}
          {error && <span className="inline-error">{error}</span>}
        </div>
        <div className="sql-result-pane">
          {result ? (
            <table className="result-table">
              <thead>
                <tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {result.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j} title={String(cell ?? "")}>{String(cell ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-state">{repoPath ? "Run a query to see results." : "Repository not indexed. Go to IDX tab and run the pipeline first."}</div>
          )}
        </div>
      </div>
    </div>
  );
}
