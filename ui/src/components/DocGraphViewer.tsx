import { useEffect, useRef, useState } from "react";
import { Network, DataSet } from "vis-network/standalone";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

export function DocGraphViewer({ repoPath, outputPath }: RepoState) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const [stats, setStats] = useState<{ docs: number; chunks: number; classes: number; links: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!repoPath) { setError("Repository not indexed. Go to IDX tab and run the pipeline first."); return; }
    setLoading(true);
    setError(null);
    try {
      const data = await api.docs.graph(repoPath, outputPath || undefined);
      setStats(data.stats);
      const nodes = new (DataSet as any)(data.nodes.map((n) => ({
        id: n.id, label: n.label as string, color: n.color as string,
        shape: n.type === "doc" ? "diamond" : n.type === "class" ? "box" : "dot",
        size: n.size as number, title: n.tooltip as string,
        font: { color: "#cdd9e5", size: 11, face: "JetBrains Mono, monospace" },
      })));
      const edges = new (DataSet as any)(data.edges.map((e) => ({
        from: e.from, to: e.to, color: e.color as string,
        width: e.width as number, dashes: e.dashes as boolean,
      })));
      if (containerRef.current) {
        if (networkRef.current) networkRef.current.destroy();
        networkRef.current = new Network(containerRef.current, { nodes, edges }, {
          physics: { enabled: true, barnesHut: { gravitationalConstant: -3000, springLength: 120 } },
          interaction: { hover: true },
        });
      }
    } catch (e) {
      setError(String(e).replace("Error: ", ""));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    return () => networkRef.current?.destroy();
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="graph-toolbar">
        <button className={`btn primary${loading ? " running" : ""}`} onClick={load} disabled={loading || !repoPath}>
          {loading ? "loading…" : "load doc graph"}
        </button>
        {stats && (
          <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)" }}>
            {stats.docs} docs · {stats.chunks} chunks · {stats.classes} linked classes · {stats.links} links
          </span>
        )}
        {error && <span className="inline-error">{error}</span>}
      </div>
      <div ref={containerRef} className="graph-canvas" style={{ flex: 1 }} />
      {!stats && !loading && !error && (
        <div className="empty-state" style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)" }}>
          {repoPath ? "Click 'load doc graph' to visualize doc-to-code linkage." : "Repository not indexed. Go to IDX tab and run the pipeline first."}
        </div>
      )}
    </div>
  );
}
