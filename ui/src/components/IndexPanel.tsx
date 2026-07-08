import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { OutputLog } from "./OutputLog";
import { FolderTreePicker } from "./FolderTreePicker";
import type { RepoState } from "../hooks/useRepo";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

interface LogLine { text: string; kind: "ok" | "err" | "warn" | "run" | "plain" }
type Status = { indexed: boolean; node_count?: number; class_count?: number };

export function IndexPanel({ repoPath, outputPath, onNavigate }: RepoState & { onNavigate?: (tab: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [actuatorUrl, setActuatorUrl] = useState("");
  const [buildDir, setBuildDir] = useState(".");
  const [port, setPort] = useState(8080);
  const [timeout, setTimeout_] = useState(60);
  const [skipBuild, setSkipBuild] = useState(false);
  const [useDocker, setUseDocker] = useState(false);
  const [writeMcp, setWriteMcp] = useState(true);
  const [indexDocs, setIndexDocs] = useState(true);
  const [skipFolders, setSkipFolders] = useState<string[]>([]);
  const [log, setLog] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [bgReindex, setBgReindex] = useState<{ running: boolean; pid: number | null }>({ running: false, pid: null });
  const [stopping, setStopping] = useState(false);
  const [logOpen, setLogOpen] = useState(false);

  useEffect(() => {
    if (!repoPath) { setStatus(null); return; }
    api.index.status(repoPath, outputPath || undefined).then(setStatus).catch(() => setStatus({ indexed: false }));
  }, [repoPath, outputPath]);

  useEffect(() => {
    if (!repoPath) return;
    function poll() {
      api.index.reindexStatus(repoPath, outputPath || undefined)
        .then((s) => setBgReindex({ running: s.running, pid: s.pid }))
        .catch(() => {});
    }
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [repoPath, outputPath]);

  const push = (text: string, kind: LogLine["kind"] = "plain") =>
    setLog((l) => [...l, { text, kind }]);

  function refreshStatus() {
    if (!repoPath) return;
    api.index.status(repoPath, outputPath || undefined).then(setStatus).catch(() => setStatus({ indexed: false }));
  }

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
        skip_folders: skipFolders.length ? skipFolders : undefined,
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
          if (d.phase === "complete") {
            setRunning(false);
            refreshStatus();
            setLogOpen(true);
            api.index.hooks({ repo_path: repoPath, output_path: outputPath || undefined, action: "install" })
              .then((res) => push(`Git hooks installed: ${res.hooks.join(", ") || "(none)"}`, "ok"))
              .catch(() => {});
          }
        }
      }
    );
  }

  return (
    <div className="flex-1 overflow-auto px-10 py-10">
      {status?.indexed && (
        <div className="flex items-center gap-4 rounded-lg border border-accent-dim bg-accent-subtle px-6 py-5 mb-10 max-w-[760px]">
          <span className="text-sm text-text">
            Existing index found — {status.node_count?.toLocaleString()} methods, {status.class_count?.toLocaleString()} classes.
          </span>
          <div className="flex-1" />
          <Button variant="primary" size="sm" onClick={() => onNavigate?.("graph")}>
            use existing index
          </Button>
          <Button variant="default" size="sm" disabled={running} onClick={run}>
            rebuild from scratch
          </Button>
        </div>
      )}

      <div className="text-xs text-text-dim tracking-widest uppercase mb-5 flex items-center gap-2 after:flex-1 after:h-px after:bg-gradient-to-r after:from-border-mid after:to-transparent">
        pipeline configuration
      </div>

      <div className="rounded-lg border border-border bg-surface-2 overflow-hidden mb-10 max-w-[760px]">
        <div className="grid grid-cols-[180px_1fr] gap-8 items-center px-6 py-6 border-b border-border transition-colors hover:bg-surface-3 focus-within:bg-surface-3">
          <div className="text-sm text-text-muted">actuator url</div>
          <div className="flex items-center gap-4 min-w-0">
            <Input
              placeholder="http://localhost:8080/actuator — leave blank for static analysis"
              value={actuatorUrl}
              onChange={(e) => setActuatorUrl(e.target.value)}
              className="border-b-border-strong bg-transparent"
            />
          </div>
        </div>

        <div className="grid grid-cols-[180px_1fr] gap-8 items-center px-6 py-6 border-b border-border transition-colors hover:bg-surface-3 focus-within:bg-surface-3">
          <div className="text-sm text-text-muted">build directory</div>
          <div className="flex items-center gap-4 min-w-0">
            <Input
              placeholder="relative to repo root  ·  default: ."
              value={buildDir}
              onChange={(e) => setBuildDir(e.target.value)}
              className="border-b-border-strong bg-transparent"
            />
          </div>
        </div>

        <div className="grid grid-cols-[180px_140px] gap-8 items-center px-6 py-6 border-b border-border transition-colors hover:bg-surface-3 focus-within:bg-surface-3">
          <div className="text-sm text-text-muted">actuator port</div>
          <div className="flex items-center gap-4 min-w-0">
            <Input type="number" value={port} onChange={(e) => setPort(+e.target.value)} className="border-b-border-strong bg-transparent" />
          </div>
        </div>

        <div className="grid grid-cols-[180px_140px] gap-8 items-center px-6 py-6 border-b border-border transition-colors hover:bg-surface-3 focus-within:bg-surface-3">
          <div className="text-sm text-text-muted">timeout (seconds)</div>
          <div className="flex items-center gap-4 min-w-0">
            <Input type="number" value={timeout} onChange={(e) => setTimeout_(+e.target.value)} className="border-b-border-strong bg-transparent" />
          </div>
        </div>

        <div className="flex gap-8 flex-wrap px-6 py-6">
          <label className="flex items-center gap-2.5 text-sm text-text-muted cursor-pointer select-none">
            <input type="checkbox" checked={skipBuild} onChange={(e) => setSkipBuild(e.target.checked)} className="accent-accent w-4 h-4" />
            skip build
          </label>
          <label className="flex items-center gap-2.5 text-sm text-text-muted cursor-pointer select-none">
            <input type="checkbox" checked={useDocker} onChange={(e) => setUseDocker(e.target.checked)} className="accent-accent w-4 h-4" />
            use docker
          </label>
          <label className="flex items-center gap-2.5 text-sm text-text-muted cursor-pointer select-none">
            <input type="checkbox" checked={writeMcp} onChange={(e) => setWriteMcp(e.target.checked)} className="accent-accent w-4 h-4" />
            write .mcp.json
          </label>
          <label className="flex items-center gap-2.5 text-sm text-text-muted cursor-pointer select-none">
            <input type="checkbox" checked={indexDocs} onChange={(e) => setIndexDocs(e.target.checked)} className="accent-accent w-4 h-4" />
            index docs (md · pdf · docx · txt)
          </label>
        </div>
      </div>

      <FolderTreePicker repoPath={repoPath} skipFolders={skipFolders} onChange={setSkipFolders} />

      <div className="flex gap-4 items-center mb-4">
        <Button variant="primary" size="lg" disabled={running || !repoPath} onClick={run} className="px-8 py-3">
          <span className="inline-flex items-center gap-2">
            <span className="inline-block text-sm origin-[50%_52%]" style={running ? { animation: "spin 1.1s linear infinite" } : undefined}>
              {running ? "◐" : "▶"}
            </span>
            <span>{running ? "running pipeline" : "run pipeline"}</span>
          </span>
        </Button>
        {running && (
          <span className="text-xs text-text-faint" style={{ animation: "fade-in 0.4s var(--ease-out-quart)" }}>
            indexing · validating · generating graph — may take several minutes
          </span>
        )}
      </div>

      {bgReindex.running && (
        <div className="flex items-center gap-3 mt-3 mb-2 px-3 py-2 rounded-md bg-accent-subtle border border-accent-dim text-sm">
          <span className="inline-block w-2 h-2 rounded-full bg-accent animate-pulse" />
          <span className="text-accent font-medium">background reindex running</span>
          {bgReindex.pid && <span className="text-text-faint text-xs">pid {bgReindex.pid}</span>}
          <Button
            variant="running"
            disabled={stopping}
            className="ml-auto px-3 py-1 text-xs h-auto"
            onClick={async () => {
              setStopping(true);
              await api.index.reindexStop({ repo_path: repoPath, output_path: outputPath || undefined }).catch(() => {});
              setBgReindex({ running: false, pid: null });
              setStopping(false);
            }}
          >
            {stopping ? "stopping…" : "■ stop"}
          </Button>
        </div>
      )}

      <button
        className="w-full text-left text-xs text-text-dim tracking-widest uppercase mt-6 mb-2 flex items-center gap-2 after:flex-1 after:h-px after:bg-gradient-to-r after:from-border-mid after:to-transparent"
        onClick={() => setLogOpen((o) => !o)}
      >
        <span>{logOpen ? "▾" : "▸"} output log</span>
        {log.length > 0 && <span className="text-text-faint normal-case tracking-normal">{log.length} lines</span>}
      </button>

      {logOpen && <OutputLog logs={log} />}
    </div>
  );
}
