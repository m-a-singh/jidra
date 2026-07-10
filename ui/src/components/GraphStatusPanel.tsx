import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";

interface Props {
  repoPath: string;
  outputPath?: string;
}

interface DaemonStatus {
  running: boolean;
  pid: number | null;
  last_indexed_at: string | null;
}

interface DaemonEntry {
  sock: string;
  alive: boolean;
  pid?: number;
  graph_path?: string | null;
  codebase_path?: string | null;
  watcher_running?: boolean;
  active_connections?: number;
  uptime_s?: number;
}

interface StaleInfo {
  stale: boolean;
  changed_files_count?: number;
  deleted_files_count?: number;
  oldest_changed_file?: string | null;
  last_indexed_at?: string | null;
  hint: string;
  reason?: string;
}

interface LogEntry {
  ts: number;
  reloaded: boolean;
  summary?: Record<string, unknown>;
  error?: string;
}

function relativeTime(isoOrTs: string | number | null): string {
  if (!isoOrTs) return "never";
  const ms = typeof isoOrTs === "number" ? isoOrTs * 1000 : new Date(isoOrTs).getTime();
  const diff = Math.floor((Date.now() - ms) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatLogEntry(entry: LogEntry): string {
  const time = new Date(entry.ts * 1000).toLocaleTimeString();
  if (!entry.reloaded) return `${time}  error  ${entry.error ?? "unknown"}`;
  const s = entry.summary;
  if (!s) return `${time}  reloaded`;
  const ct = String(s.change_type ?? "");
  const added = s.added_methods != null ? `+${s.added_methods}` : "";
  const removed = s.removed_methods != null ? `-${s.removed_methods}` : "";
  const files = Array.isArray(s.changed_files) ? `${s.changed_files.length} files` : "";
  const ms = s.elapsed_ms != null ? `${Math.round(Number(s.elapsed_ms))}ms` : "";
  return [time, ct, files, added && removed ? `${added} ${removed} methods` : "", ms]
    .filter(Boolean)
    .join("  ");
}

export function GraphStatusPanel({ repoPath, outputPath }: Props) {
  const [daemon, setDaemon] = useState<DaemonStatus | null>(null);
  const [allDaemons, setAllDaemons] = useState<DaemonEntry[]>([]);
  const [stale, setStale] = useState<StaleInfo | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [startupLog, setStartupLog] = useState<string[]>([]);
  const [actionBusy, setActionBusy] = useState(false);
  const [reindexBusy, setReindexBusy] = useState(false);
  const [staleBusy, setStaleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Poll: cheap O(1) spot-check + daemon status + log only
  const refresh = useCallback(async () => {
    if (!repoPath) return;
    try {
      const [d, s, l, sl, all] = await Promise.all([
        api.daemon.status(repoPath, outputPath),
        api.daemon.stale(repoPath, outputPath, false),
        api.daemon.log(repoPath, outputPath, 50),
        api.daemon.startupLog(repoPath, outputPath, 30),
        api.daemon.list(),
      ]);
      setDaemon(d);
      setStale(s);
      setLog(l.entries);
      setStartupLog(sl.lines);
      setAllDaemons(all.daemons);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [repoPath, outputPath]);

  // Full check: rglob scan, only on explicit user click
  async function checkStale() {
    if (!repoPath) return;
    setStaleBusy(true);
    try {
      const s = await api.daemon.stale(repoPath, outputPath, true);
      setStale(s);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStaleBusy(false);
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  async function toggleDaemon() {
    if (!daemon) return;
    setActionBusy(true);
    try {
      if (daemon.running) {
        await api.daemon.stop({ repo_path: repoPath, output_path: outputPath });
      } else {
        await api.daemon.start({ repo_path: repoPath, output_path: outputPath });
      }
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActionBusy(false);
    }
  }

  async function triggerReindex() {
    setReindexBusy(true);
    try {
      await api.index.reindex({ repo_path: repoPath, output_path: outputPath });
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReindexBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4 overflow-y-auto h-full text-sm">
      {error && (
        <div className="text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error}
          <button className="ml-3 underline opacity-60" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}

      {/* All daemons — system-wide view */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="text-text font-medium mb-2">
          Running daemons ({allDaemons.length})
        </div>
        {allDaemons.length === 0 ? (
          <div className="text-text-dim text-xs">No daemons running. Start one below.</div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {allDaemons.map((d, i) => {
              const label = d.codebase_path
                ? d.codebase_path.split("/").slice(-2).join("/")
                : d.graph_path
                ? d.graph_path.split("/").slice(-3).join("/")
                : d.sock.split("/").pop();
              const uptime = d.uptime_s != null
                ? d.uptime_s < 60 ? `${d.uptime_s}s` : d.uptime_s < 3600 ? `${Math.floor(d.uptime_s / 60)}m` : `${Math.floor(d.uptime_s / 3600)}h`
                : "?";
              const isThis = d.graph_path != null && outputPath
                ? d.graph_path === outputPath
                : d.codebase_path != null && d.codebase_path === repoPath;
              return (
                <div key={i} className={`flex items-center gap-3 text-xs font-mono px-2 py-1 rounded ${isThis ? "bg-accent-subtle border border-accent-dim" : ""}`}>
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400 shrink-0" />
                  <span className="text-text truncate max-w-[280px]" title={d.codebase_path ?? d.graph_path ?? ""}>{label}</span>
                  <span className="text-text-dim">pid {d.pid}</span>
                  <span className={`${d.watcher_running ? "text-green-400" : "text-text-dim"}`}>{d.watcher_running ? "watching" : "idle"}</span>
                  {(d.active_connections ?? 0) > 0 && <span className="text-accent">{d.active_connections} conn</span>}
                  <span className="text-text-dim ml-auto">up {uptime}</span>
                  {isThis && <span className="text-accent">← this repo</span>}
                  <button
                    className="px-2 py-0.5 rounded border border-border text-text-dim hover:text-red-400 hover:border-red-800 transition-colors disabled:opacity-40"
                    disabled={actionBusy}
                    onClick={async () => {
                      setActionBusy(true);
                      try {
                        await api.daemon.stop({ repo_path: d.codebase_path ?? repoPath, output_path: d.graph_path ?? outputPath });
                        await refresh();
                      } catch (e: unknown) {
                        setError(e instanceof Error ? e.message : String(e));
                      } finally {
                        setActionBusy(false);
                      }
                    }}
                  >
                    stop
                  </button>
                </div>
              );
            })}
            {allDaemons.length > 1 && (
              <div className="text-text-dim text-xs mt-2">
                Each daemon holds one repo's graph in memory. Stop unused ones to free RAM.
              </div>
            )}
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="bg-surface border border-border rounded-lg p-4 flex flex-wrap gap-6 items-center">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${daemon?.running ? "bg-green-400" : "bg-zinc-500"}`} />
          <span className="text-text font-medium">Daemon</span>
          <span className="text-text-dim">{daemon?.running ? `running (pid ${daemon.pid})` : "stopped"}</span>
        </div>
        <div className="text-text-dim">
          Last indexed: <span className="text-text">{relativeTime(daemon?.last_indexed_at ?? null)}</span>
        </div>
        {stale && (
          <div className={`${stale.stale ? "text-yellow-400" : "text-green-400"}`}>
            {stale.stale
              ? `${stale.changed_files_count} changed, ${stale.deleted_files_count} deleted`
              : "graph current"}
          </div>
        )}
        <div className="ml-auto flex gap-2">
          <button
            className={`px-3 py-1 rounded border transition-colors disabled:opacity-40 ${
              daemon?.running
                ? "border-border text-text-dim hover:text-text hover:border-accent"
                : "border-accent text-accent hover:bg-accent-subtle"
            }`}
            onClick={toggleDaemon}
            disabled={actionBusy || !daemon}
          >
            {daemon?.running ? "Stop daemon" : "Start daemon"}
          </button>
          <button
            className="px-3 py-1 rounded border border-accent text-accent hover:bg-accent-subtle transition-colors disabled:opacity-40"
            onClick={triggerReindex}
            disabled={reindexBusy}
          >
            {reindexBusy ? "Reindexing…" : "Reindex now"}
          </button>
        </div>
      </div>

      {/* Stale files */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-text font-medium">Stale files</div>
          <button
            className="text-xs px-2 py-1 rounded border border-border text-text-dim hover:text-text transition-colors disabled:opacity-40"
            onClick={checkStale}
            disabled={staleBusy}
          >
            {staleBusy ? "Checking…" : "Full check"}
          </button>
        </div>
        {!stale ? (
          <div className="text-text-dim">Loading…</div>
        ) : !stale.stale ? (
          <div className="text-green-400">
            {stale.changed_files_count != null
              ? "No stale files — graph is current"
              : "Spot check passed — click Full check for details"}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="text-yellow-400 text-xs">
              {stale.changed_files_count != null
                ? `${stale.changed_files_count} changed · ${stale.deleted_files_count} deleted`
                : "Stale files detected — click Full check for details"}
              {stale.oldest_changed_file && (
                <span className="text-text-dim ml-2">oldest modified: {stale.oldest_changed_file.split("/").slice(-2).join("/")}</span>
              )}
            </div>
            <div className="text-text-dim text-xs">{stale.hint}</div>
          </div>
        )}
      </div>

      {/* Activity log */}
      <div className="bg-surface border border-border rounded-lg p-4 flex-1 min-h-0 flex flex-col">
        <div className="text-text font-medium mb-3">Activity log</div>
        {log.length === 0 && startupLog.length > 0 && !daemon?.running ? (
          <div className="flex flex-col gap-1">
            <div className="text-red-400 text-xs mb-1">Daemon crashed on startup — stderr output:</div>
            <div className="overflow-y-auto flex-1 font-mono text-xs flex flex-col gap-0.5">
              {startupLog.map((line, i) => (
                <div key={i} className="text-red-300 px-2 py-0.5">{line}</div>
              ))}
            </div>
          </div>
        ) : log.length === 0 ? (
          <div className="text-text-dim text-xs">No reindex events yet. Start the daemon to begin tracking.</div>
        ) : (
          <div className="overflow-y-auto flex-1 font-mono text-xs flex flex-col gap-1">
            {log.map((entry, i) => (
              <div
                key={i}
                className={`px-2 py-1 rounded ${!entry.reloaded ? "text-red-400 bg-red-950/20" : entry.summary?.change_type === "no_change" ? "text-text-dim" : "text-text"}`}
              >
                {formatLogEntry(entry)}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
