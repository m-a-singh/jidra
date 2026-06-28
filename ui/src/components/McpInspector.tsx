import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

interface Tool {
  name: string;
  description: string;
  input_schema: {
    properties?: Record<string, ParamSchema>;
    required?: string[];
  };
}

interface ParamSchema {
  title?: string;
  type?: string;
  anyOf?: { type: string }[];
  default?: unknown;
  description?: string;
}

interface LogEntry {
  tool_name: string;
  method_id?: string;
  timestamp: string;
}

function resolveType(schema: ParamSchema): { base: string; nullable: boolean } {
  if (schema.anyOf) {
    const types = schema.anyOf.map((x) => x.type).filter((t) => t !== "null");
    return { base: types[0] ?? "string", nullable: schema.anyOf.some((x) => x.type === "null") };
  }
  return { base: schema.type ?? "string", nullable: false };
}

export function McpInspector({ repoPath, outputPath }: RepoState) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [selected, setSelected] = useState<Tool | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [nulled, setNulled] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [calling, setCalling] = useState(false);

  useEffect(() => {
    api.mcp.tools(repoPath || undefined).then(setTools).catch(() => {});
  }, [repoPath]);

  useEffect(() => {
    if (!repoPath) return;
    api.mcp.sessionLog(repoPath).then(setLog).catch(() => {});
  }, [repoPath]);

  function selectTool(t: Tool) {
    setSelected(t);
    setResult(null);
    setError(null);
    // initialise param state from schema defaults
    const vals: Record<string, string> = {};
    const nl: Record<string, boolean> = {};
    const props = t.input_schema?.properties ?? {};
    const required = new Set(t.input_schema?.required ?? []);
    for (const [k, schema] of Object.entries(props)) {
      const { nullable } = resolveType(schema);
      const hasDefault = "default" in schema;
      nl[k] = !required.has(k) && (nullable || hasDefault);
      vals[k] = hasDefault && schema.default !== null ? String(schema.default) : "";
    }
    setParamValues(vals);
    setNulled(nl);
  }

  async function call() {
    if (!selected) return;
    setError(null);
    setCalling(true);
    try {
      const params: Record<string, unknown> = {};
      const props = selected.input_schema?.properties ?? {};
      for (const k of Object.keys(props)) {
        if (nulled[k]) { params[k] = null; continue; }
        const { base } = resolveType(props[k]);
        const raw = paramValues[k] ?? "";
        if (base === "integer" || base === "number") params[k] = raw === "" ? null : Number(raw);
        else if (base === "boolean") params[k] = raw === "true";
        else params[k] = raw === "" ? null : raw;
      }
      const res = await api.mcp.call({
        tool: selected.name,
        params,
        repo_path: repoPath || undefined,
        output_path: outputPath || undefined,
      });
      setResult(JSON.stringify(res.result, null, 2));
      if (repoPath) api.mcp.sessionLog(repoPath).then(setLog).catch(() => {});
    } catch (e) {
      setError(String(e).replace("Error: ", ""));
    } finally {
      setCalling(false);
    }
  }

  const props = selected?.input_schema?.properties ?? {};
  const required = new Set(selected?.input_schema?.required ?? []);

  return (
    <div className="split" style={{ height: "100%" }}>
      {/* Tool list */}
      <div className="split-left tool-list">
        <div className="session-log-header" style={{ padding: "8px 12px 10px" }}>
          TOOLS ({tools.length})
        </div>
        {tools.map((t) => (
          <div
            key={t.name}
            className={`tool-item${selected?.name === t.name ? " active" : ""}`}
            onClick={() => selectTool(t)}
            title={t.description}
          >
            {t.name}
          </div>
        ))}
      </div>

      {/* Tool detail */}
      <div className="split-right tool-detail" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {selected ? (
          <>
            <div className="tool-header">
              <div className="tool-name">{selected.name}</div>
              <div className="tool-desc">{selected.description}</div>
            </div>

            {/* params — fixed height scrollable */}
            <div style={{ overflow: "auto", padding: "14px 20px", borderBottom: "1px solid var(--border)", maxHeight: 320, flexShrink: 0 }}>
              {Object.entries(props).map(([key, schema]) => {
                const { base, nullable } = resolveType(schema);
                const isRequired = required.has(key);
                const isNulled = nulled[key] ?? false;

                return (
                  <div key={key} style={{ marginBottom: 14 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                      <label style={{ fontSize: "var(--sz-sm)", color: isRequired ? "var(--cyan)" : "var(--text-muted)" }}>
                        {key}
                        {isRequired && <span style={{ color: "var(--error)", marginLeft: 3 }}>*</span>}
                        <span style={{ color: "var(--text-dim)", marginLeft: 6, fontSize: "var(--sz-xs)" }}>{base}</span>
                      </label>
                      {(nullable || !isRequired) && (
                        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: "var(--sz-xs)", color: "var(--text-dim)", cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={isNulled}
                            onChange={(e) => setNulled((n) => ({ ...n, [key]: e.target.checked }))}
                            style={{ accentColor: "var(--cyan-dim)" }}
                          />
                          null
                        </label>
                      )}
                    </div>
                    {base === "boolean" ? (
                      <select
                        disabled={isNulled}
                        value={paramValues[key] ?? ""}
                        onChange={(e) => setParamValues((v) => ({ ...v, [key]: e.target.value }))}
                        style={isNulled ? { ...inputStyle, opacity: 0.35 } : inputStyle}
                      >
                        <option value="">—</option>
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : (
                      <textarea
                        disabled={isNulled}
                        value={isNulled ? "null" : (paramValues[key] ?? "")}
                        onChange={(e) => setParamValues((v) => ({ ...v, [key]: e.target.value }))}
                        rows={key === "query" || key === "method" || key === "stack_trace" ? 3 : 1}
                        placeholder={isNulled ? "null" : schema.description ?? ""}
                        style={isNulled ? { ...textareaStyle, opacity: 0.35, resize: "none" } : textareaStyle}
                      />
                    )}
                  </div>
                );
              })}

              <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
                <button
                  className={`btn primary${calling ? " running" : ""}`}
                  onClick={call}
                  disabled={calling || !repoPath}
                >
                  {calling ? "calling…" : "call"}
                </button>
                {error && <span className="inline-error">{error}</span>}
              </div>
            </div>

            {/* result — fills remaining space */}
            {result && (
              <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", borderTop: "1px solid var(--border)" }}>
                <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)", letterSpacing: "0.1em", padding: "6px 20px 4px" }}>RESULT</div>
                <JsonResult value={result} />
              </div>
            )}
          </>
        ) : (
          <div className="empty-state">
            Select a tool from the list.<br />
            {!repoPath && <span style={{ color: "var(--error)" }}>Set a repo path first.</span>}
          </div>
        )}
      </div>

      {/* Session log */}
      <div className="session-log">
        <div className="session-log-header">SESSION LOG</div>
        <div style={{ flex: 1, overflow: "auto" }}>
          {log.length === 0
            ? <div className="empty-state">No tool calls yet.</div>
            : log.map((e, i) => (
              <div key={i} className="log-entry">
                <div className="log-entry-tool">{e.tool_name}</div>
                {e.method_id && <div className="log-entry-method">{e.method_id}</div>}
                <div className="log-entry-time">{new Date(e.timestamp).toLocaleTimeString()}</div>
              </div>
            ))
          }
        </div>
      </div>
    </div>
  );
}

function JsonResult({ value }: { value: string }) {
  const lines = value.split("\n");
  return (
    <pre style={{
      background: "var(--bg)", border: "none", borderTop: "none",
      padding: "10px 20px", fontSize: "var(--sz-sm)", lineHeight: 1.7,
      overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all",
      flex: 1, minHeight: 0, margin: 0,
    }}>
      {lines.map((line, i) => {
        // key highlight
        const keyMatch = line.match(/^(\s*)"([^"]+)"(\s*:)(.*)$/);
        if (keyMatch) {
          const [, indent, key, colon, rest] = keyMatch;
          const valColor = rest.trim().startsWith('"') ? "var(--text)"
            : /^[\d.-]+/.test(rest.trim()) ? "#6ab0f5"
            : rest.trim() === "true" || rest.trim() === "false" ? "#c49a2a"
            : rest.trim() === "null," || rest.trim() === "null" ? "var(--text-dim)"
            : "var(--text)";
          return (
            <span key={i}>
              {indent}
              <span style={{ color: "var(--cyan-dim)" }}>"{key}"</span>
              <span style={{ color: "var(--text-dim)" }}>{colon}</span>
              <span style={{ color: valColor }}>{rest}</span>
              {"\n"}
            </span>
          );
        }
        // string value lines / structural
        const isStructural = /^\s*[{}\[\],]*\s*$/.test(line);
        return (
          <span key={i} style={{ color: isStructural ? "var(--border-strong)" : "var(--text-muted)" }}>
            {line}{"\n"}
          </span>
        );
      })}
    </pre>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bg)",
  border: "1px solid var(--border-mid)",
  borderBottom: "1px solid var(--border-strong)",
  color: "var(--text)",
  fontFamily: "var(--font)",
  fontSize: "var(--sz-sm)",
  padding: "5px 8px",
  outline: "none",
};

const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  resize: "vertical",
  lineHeight: 1.5,
};
