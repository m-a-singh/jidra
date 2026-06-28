import { useCallback, useEffect, useRef, useState } from "react";
import { Network, DataSet } from "vis-network/standalone";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

interface DocNodeMeta {
  id: string; label?: string; type?: string; full_path?: string; file_path?: string;
  source_type?: string; chunk_count?: number; linked_count?: number; full_name?: string;
  language?: string; tooltip?: string;
}

export function DocGraphViewer({ repoPath, outputPath }: RepoState) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nodesRef = useRef<any | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const edgesRef = useRef<any | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rawNodesRef = useRef<any[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rawEdgesRef = useRef<any[]>([]);
  const [stats, setStats] = useState<{ docs: number; chunks: number; classes: number; links: number } | null>(null);
  const [selected, setSelected] = useState<DocNodeMeta | null>(null);
  const [physics, setPhysics] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const initNetwork = useCallback(() => {
    if (!containerRef.current || networkRef.current) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const nodeDs = new (DataSet as any)([]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const edgeDs = new (DataSet as any)([]);
    nodesRef.current = nodeDs;
    edgesRef.current = edgeDs;

    const net = new Network(
      containerRef.current,
      { nodes: nodeDs, edges: edgeDs } as never,
      {
        layout: { improvedLayout: false },
        physics: {
          enabled: true,
          solver: "forceAtlas2Based",
          forceAtlas2Based: { gravitationalConstant: -60, centralGravity: 0.005, springLength: 110, springConstant: 0.04, damping: 0.7 },
          stabilization: { iterations: 150, updateInterval: 30 },
        },
        interaction: { hover: true, tooltipDelay: 300, multiselect: true, keyboard: true },
        nodes: { borderWidthSelected: 3 },
        edges: { smooth: { enabled: false } as never },
      }
    );

    net.on("stabilizationIterationsDone", () => {
      net.setOptions({ physics: { enabled: false } });
      setPhysics(false);
      net.fit({ animation: { duration: 400, easingFunction: "easeInOutQuad" } });
    });

    net.on("click", (params) => {
      if (!params.nodes.length) { setSelected(null); return; }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const node = (nodeDs as any).get(params.nodes[0]);
      if (node) setSelected(node._meta ?? null);
    });

    networkRef.current = net;
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const buildNetwork = useCallback((rawNodes: any[], rawEdges: any[]) => {
    if (!nodesRef.current || !edgesRef.current || !networkRef.current) return;

    rawNodesRef.current = rawNodes;
    rawEdgesRef.current = rawEdges;

    const largeGraph = rawNodes.length > 500;

    const nodeData = rawNodes.map((n) => ({
      id: n.id,
      label: n.label as string,
      shape: n.type === "doc" ? "diamond" : n.type === "class" ? "box" : "dot",
      size: n.size as number,
      title: n.tooltip as string,
      color: { background: n.color + "33", border: n.color, highlight: { background: n.color + "55", border: "#ffffff" } },
      font: { color: "#cdd9e5", size: 11, face: "JetBrains Mono, monospace" },
      _meta: n,
    }));
    const edgeData = rawEdges.map((e, i) => ({
      id: e.id ?? `e${i}`, from: e.from, to: e.to,
      color: e.color as string, width: e.width as number, dashes: e.dashes as boolean,
    }));

    nodesRef.current.clear();
    edgesRef.current.clear();
    nodesRef.current.add(nodeData);
    edgesRef.current.add(edgeData);

    networkRef.current.setOptions({ physics: { enabled: !largeGraph } });
    setPhysics(!largeGraph);

    setTimeout(() => networkRef.current?.fit({ animation: !largeGraph }), 50);
  }, []);

  async function load() {
    if (!repoPath) { setError("Repository not indexed. Go to IDX tab and run the pipeline first."); return; }
    setLoading(true);
    setError(null);
    setSelected(null);
    try {
      const data = await api.docs.graph(repoPath, outputPath || undefined);
      setStats(data.stats);
      if (!networkRef.current) initNetwork();
      buildNetwork(data.nodes, data.edges);
    } catch (e) {
      const msg = String(e).replace("Error: ", "");
      setError(msg.includes("not found") || msg.includes("404") ? "No documents indexed yet. Enable 'index docs' on the IDX tab and run the pipeline." : msg);
    } finally {
      setLoading(false);
    }
  }

  // class nodes a chunk links to, via "links_to" edges
  function getLinkedClasses(chunkId: string): DocNodeMeta[] {
    const byId = Object.fromEntries(rawNodesRef.current.map((n) => [n.id, n]));
    const classIds = rawEdgesRef.current
      .filter((e) => e.type === "links_to" && e.from === chunkId)
      .map((e) => e.to);
    return classIds.map((id) => byId[id]).filter(Boolean);
  }

  // union of linked classes across every chunk under a doc
  function getLinkedClassesForDoc(docId: string): DocNodeMeta[] {
    const chunkIds = rawEdgesRef.current
      .filter((e) => e.type === "contains" && e.from === docId)
      .map((e) => e.to);
    const seen = new Map<string, DocNodeMeta>();
    for (const cId of chunkIds) {
      for (const cls of getLinkedClasses(cId)) seen.set(cls.id, cls);
    }
    return [...seen.values()];
  }

  function focusNode(id: string) {
    if (!networkRef.current) return;
    networkRef.current.focus(id, { scale: 1.6, animation: true });
    networkRef.current.selectNodes([id]);
    const node = rawNodesRef.current.find((n) => n.id === id);
    if (node) setSelected(node);
  }

  function fitAll() { networkRef.current?.fit({ animation: true }); }

  function togglePhysics() {
    const next = !physics;
    setPhysics(next);
    networkRef.current?.setOptions({ physics: { enabled: next } });
  }

  useEffect(() => {
    return () => networkRef.current?.destroy();
  }, []);

  return (
    <div className="split" style={{ height: "100%" }}>
      <div className="split-right" style={{ position: "relative", minHeight: 0 }}>
        <div className="graph-toolbar">
          <button className={`btn primary${loading ? " running" : ""}`} onClick={load} disabled={loading || !repoPath}>
            {loading ? "loading…" : "load doc graph"}
          </button>
          <button className="btn" onClick={fitAll} disabled={!stats}>Fit All</button>
          <button className={`btn${physics ? " primary" : ""}`} onClick={togglePhysics} disabled={!stats}>
            {physics ? "Physics On" : "Physics Off"}
          </button>
          {stats && (
            <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)" }}>
              {stats.docs} docs · {stats.chunks} chunks · {stats.classes} linked classes · {stats.links} links
            </span>
          )}
          {error && <span className="inline-error">{error}</span>}
        </div>
        <div ref={containerRef} className="graph-canvas" style={{ flex: 1, minHeight: 0, minWidth: 0 }} />
        {!stats && !loading && !error && (
          <div className="empty-state" style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)" }}>
            {repoPath ? "Click 'load doc graph' to visualize doc-to-code linkage." : "Repository not indexed. Go to IDX tab and run the pipeline first."}
          </div>
        )}
      </div>

      <div className="node-detail">
        <div className="node-detail-header">INSPECTOR</div>
        <div style={{ padding: 16, overflow: "auto", flex: 1 }}>
          {selected ? (
            <>
              <div style={{ fontSize: "var(--sz-sm)", color: "var(--text)", marginBottom: 6, wordBreak: "break-word" }}>
                {selected.full_name || selected.label}
              </div>
              {selected.type === "doc" && (
                <>
                  <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)", marginBottom: 4 }}>{selected.source_type} · {selected.chunk_count} chunks</div>
                  <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)", wordBreak: "break-all", marginBottom: 12 }}>{selected.full_path}</div>
                  <LinkedClasses classes={getLinkedClassesForDoc(selected.id)} onFocus={focusNode} />
                </>
              )}
              {selected.type === "chunk" && (
                <LinkedClasses classes={getLinkedClasses(selected.id)} onFocus={focusNode} />
              )}
              {selected.type === "class" && (
                <>
                  <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)", marginBottom: 4 }}>{selected.language}</div>
                  <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)", wordBreak: "break-all" }}>{selected.file_path}</div>
                </>
              )}
            </>
          ) : (
            <div style={{ color: "var(--text-faint)", fontSize: "var(--sz-sm)" }}>Click a node to inspect it.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function LinkedClasses({ classes, onFocus }: { classes: DocNodeMeta[]; onFocus: (id: string) => void }) {
  if (classes.length === 0) {
    return <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)" }}>No linked classes.</div>;
  }
  return (
    <>
      <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)", marginBottom: 6 }}>
        {classes.length} linked class{classes.length === 1 ? "" : "es"}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {classes.map((c) => (
          <button
            key={c.id}
            className="btn"
            onClick={() => onFocus(c.id)}
            title={c.file_path}
            style={{ textAlign: "left", padding: "4px 8px", fontSize: "var(--sz-xs)", justifyContent: "flex-start" }}
          >
            {c.label}
          </button>
        ))}
      </div>
    </>
  );
}
