import { useState } from "react";
import { api } from "../lib/api";
import { OutputLog } from "./OutputLog";
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
  const [indexDocs, setIndexDocs] = useState(true);
  const [log, setLog] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [hooking, setHooking] = useState(false);

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
        index_docs: indexDocs,
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

  async function reindex() {
    if (!repoPath) { push("set a repo path first", "err"); return; }
    setReindexing(true);
    push("Incremental reindex started…", "run");
    try {
      const res = await api.index.reindex({ repo_path: repoPath, output_path: outputPath || undefined });
      push(`Reindex done: ${JSON.stringify(res.summary)}`, "ok");
    } catch (e) {
      push(String(e).replace("Error: ", ""), "err");
    } finally {
      setReindexing(false);
    }
  }

  async function installHooks() {
    if (!repoPath) { push("set a repo path first", "err"); return; }
    setHooking(true);
    try {
      const res = await api.index.hooks({ repo_path: repoPath, output_path: outputPath || undefined, action: "install" });
      push(`Git hooks installed: ${res.hooks.join(", ") || "(none)"}`, "ok");
    } catch (e) {
      push(String(e).replace("Error: ", ""), "err");
    } finally {
      setHooking(false);
    }
  }

  return (
    <div className="panel-body">
      <div className="section-label">pipeline configuration</div>

      <div className="field-group" style={{ marginBottom: 20, maxWidth: 720 }}>
        <div className="field-row">
          <div className="field-row-label">actuator url</div>
          <div className="field-row-control">
            <input
              className="field-input"
              placeholder="http://localhost:8080/actuator — leave blank for static analysis"
              value={actuatorUrl}
              onChange={(e) => setActuatorUrl(e.target.value)}
            />
          </div>
        </div>

        <div className="field-row">
          <div className="field-row-label">build directory</div>
          <div className="field-row-control">
            <input
              className="field-input"
              placeholder="relative to repo root  ·  default: ."
              value={buildDir}
              onChange={(e) => setBuildDir(e.target.value)}
            />
          </div>
        </div>

        <div className="field-row compact">
          <div className="field-row-label">actuator port</div>
          <div className="field-row-control">
            <input className="field-input" type="number" value={port} onChange={(e) => setPort(+e.target.value)} />
          </div>
        </div>

        <div className="field-row compact">
          <div className="field-row-label">timeout (seconds)</div>
          <div className="field-row-control">
            <input className="field-input" type="number" value={timeout} onChange={(e) => setTimeout_(+e.target.value)} />
          </div>
        </div>

        <div className="field-row-toggles">
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
          <label className="check-row">
            <input type="checkbox" checked={indexDocs} onChange={(e) => setIndexDocs(e.target.checked)} />
            index docs (md · pdf · docx · txt)
          </label>
        </div>
      </div>

      <div className="panel-row">
        <button
          className={`btn primary run-btn${running ? " running" : ""}`}
          disabled={running || !repoPath}
          onClick={run}
          style={{ padding: "8px 24px" }}
        >
          <span className="run-btn-content">
            <span className="run-btn-icon">{running ? "◐" : "▶"}</span>
            <span>{running ? "running pipeline" : "run pipeline"}</span>
          </span>
        </button>
        {running && <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)", animation: "fade-in 0.4s var(--ease-out-quart)" }}>
          indexing · validating · generating graph — may take several minutes
        </span>}
      </div>

      <div className="panel-row" style={{ marginTop: 4, marginBottom: 24 }}>
        <button className="btn" disabled={reindexing || !repoPath} onClick={reindex} style={{ padding: "5px 16px", fontSize: "var(--sz-sm)" }}>
          {reindexing ? "reindexing…" : "↻ incremental reindex"}
        </button>
        <button className="btn" disabled={hooking || !repoPath} onClick={installHooks} style={{ padding: "5px 16px", fontSize: "var(--sz-sm)" }}>
          {hooking ? "installing…" : "⚓ install git hooks"}
        </button>
        <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)" }}>
          reindex updates graph for changed files only · hooks auto-reindex on commit
        </span>
      </div>

      <div className="section-label">output log</div>

      <OutputLog logs={log} />
    </div>
  );
}
