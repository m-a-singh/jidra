import { useState } from "react";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

interface LogLine { text: string; kind: "ok" | "err" | "warn" | "run" | "plain" }

export function IndexPanel({ repoPath, outputPath }: RepoState) {
  const [actuatorUrl, setActuatorUrl] = useState("");
  const [buildDir, setBuildDir] = useState(".");
  const [port, setPort] = useState(8080);
  const [timeout, setTimeout_] = useState(60);
  const [skipBuild, setSkipBuild] = useState(false);
  const [useDocker, setUseDocker] = useState(false);
  const [writeMcp, setWriteMcp] = useState(true);
  const [log, setLog] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);

  const push = (text: string, kind: LogLine["kind"] = "plain") =>
    setLog((l) => [...l, { text, kind }]);

  function run() {
    if (!repoPath) { push("set a repo path first", "err"); return; }
    setLog([]);
    setRunning(true);
    api.index.run(
      {
        repo_path: repoPath,
        output_path: outputPath || undefined,
        actuator_url: actuatorUrl || undefined,
        port, timeout,
        skip_build: skipBuild,
        build_dir: buildDir !== "." ? buildDir : undefined,
        use_docker: useDocker,
        write_mcp_config: writeMcp,
      },
      (event, data) => {
        const d = data as { msg?: string; phase?: string };
        if (event === "error")  { push(d.msg ?? "error", "err"); setRunning(false); }
        else if (event === "warn") { push(d.msg ?? "", "warn"); }
        else if (event === "status") {
          const kind =
            d.phase === "complete" ? "ok" :
            d.phase === "start"    ? "run" : "plain";
          push(d.msg ?? "", kind);
          if (d.phase === "complete") setRunning(false);
        }
      }
    );
  }

  return (
    <div className="panel-body">
      <div className="section-label">pipeline configuration</div>

      <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: "18px 24px", alignItems: "start", maxWidth: 680, marginBottom: 24 }}>

        <div style={labelStyle}>actuator url</div>
        <div>
          <input
            className="field-input"
            placeholder="http://localhost:8080/actuator — leave blank for static analysis"
            value={actuatorUrl}
            onChange={(e) => setActuatorUrl(e.target.value)}
          />
        </div>

        <div style={labelStyle}>build directory</div>
        <div>
          <input
            className="field-input"
            placeholder="relative to repo root  ·  default: ."
            value={buildDir}
            onChange={(e) => setBuildDir(e.target.value)}
          />
        </div>

        <div style={labelStyle}>actuator port</div>
        <div>
          <input
            className="field-input"
            style={{ width: 120 }}
            type="number"
            value={port}
            onChange={(e) => setPort(+e.target.value)}
          />
        </div>

        <div style={labelStyle}>timeout (s)</div>
        <div>
          <input
            className="field-input"
            style={{ width: 120 }}
            type="number"
            value={timeout}
            onChange={(e) => setTimeout_(+e.target.value)}
          />
        </div>

        <div style={labelStyle}>options</div>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", paddingTop: 4 }}>
          <label className="check-row">
            <input type="checkbox" checked={skipBuild} onChange={(e) => setSkipBuild(e.target.checked)} />
            skip build
          </label>
          <label className="check-row">
            <input type="checkbox" checked={useDocker} onChange={(e) => setUseDocker(e.target.checked)} />
            use docker
          </label>
          <label className="check-row">
            <input type="checkbox" checked={writeMcp} onChange={(e) => setWriteMcp(e.target.checked)} />
            write .mcp.json
          </label>
        </div>
      </div>

      <div className="panel-row">
        <button
          className={`btn primary${running ? " running" : ""}`}
          disabled={running || !repoPath}
          onClick={run}
          style={{ padding: "5px 20px" }}
        >
          {running ? "running pipeline…" : "run pipeline"}
        </button>
        {running && <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)" }}>
          indexing · validating · generating graph — may take several minutes
        </span>}
      </div>

      <div className="log-pane">
        {log.length === 0
          ? <span style={{ color: "var(--text-dim)" }}>configure above and run the pipeline</span>
          : log.map((l, i) => <div key={i} className={`log-${l.kind}`}>{l.text}</div>)
        }
      </div>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  fontSize: "var(--sz-sm)",
  color: "var(--text-muted)",
  textAlign: "right",
  paddingTop: 6,
};
