import { useEffect, useRef, useState, useCallback } from "react";
import { Network, DataSet } from "vis-network/standalone";
import { api } from "../lib/api";
import type { RepoState } from "../hooks/useRepo";

interface NodeMeta {
  id: string; label?: string; signature?: string; file_path?: string;
  line?: number; group?: string; class_name?: string;
  is_endpoint?: boolean; confirmed?: boolean; http_method?: string; route?: string;
  color?: { border?: string };
}

const LEGEND = [
  { color: "#2196f3", label: "Controller / Endpoint" },
  { color: "#34d399", label: "Service" },
  { color: "#f59e0b", label: "Repository" },
  { color: "#a78bfa", label: "Component" },
  { color: "#67e8f9", label: "Configuration" },
  { color: "#fb7185", label: "Entity" },
  { color: "#4d6173", label: "Other" },
];

export function GraphViewer({ repoPath, outputPath }: RepoState) {
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

  const [method, setMethod] = useState("");
  const [depth, setDepth] = useState(2);
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<NodeMeta[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const [selected, setSelected] = useState<NodeMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadMsg, setLoadMsg] = useState("loading…");
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState<{ nodes: number; edges: number; endpoints: number } | null>(null);
  const [physics, setPhysics] = useState(true);
  const [copied, setCopied] = useState(false);

  // callers/callees computed from raw edges
  function getCallers(id: string): NodeMeta[] {
    const byId = Object.fromEntries(rawNodesRef.current.map((n: NodeMeta) => [n.id, n]));
    return rawEdgesRef.current.filter((e: {from: string; to: string}) => e.to === id)
      .map((e: {from: string}) => byId[e.from]).filter(Boolean);
  }
  function getCallees(id: string): NodeMeta[] {
    const byId = Object.fromEntries(rawNodesRef.current.map((n: NodeMeta) => [n.id, n]));
    return rawEdgesRef.current.filter((e: {from: string; to: string}) => e.from === id)
      .map((e: {to: string}) => byId[e.to]).filter(Boolean);
  }

  function focusNode(id: string) {
    if (!networkRef.current) return;
    networkRef.current.focus(id, { scale: 1.6, animation: true });
    networkRef.current.selectNodes([id]);
    const node = rawNodesRef.current.find((n: NodeMeta) => n.id === id);
    if (node) setSelected(node._meta ?? node);
  }

  // Initialize network ONCE on mount — never destroy/recreate
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
          enabled: false,
          solver: "forceAtlas2Based",
          forceAtlas2Based: { gravitationalConstant: -40, centralGravity: 0.003, springLength: 100, springConstant: 0.05, damping: 0.7 },
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
      if (node) setSelected(node._meta ?? node);
    });

    networkRef.current = net;
  }, []);

  // Update DataSets in-place — no destroy/recreate
  const buildNetwork = useCallback((rawNodes: object[], rawEdges: object[]) => {
    if (!nodesRef.current || !edgesRef.current || !networkRef.current) return;

    rawNodesRef.current = rawNodes;
    rawEdgesRef.current = rawEdges;

    const largeGraph = rawNodes.length > 500;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const edgeData = (rawEdges as any[]).map((e: any, i: number) => ({
      id: e.id ?? `e${i}`, from: e.from, to: e.to,
      arrows: { to: { enabled: true, scaleFactor: 0.5 } },
      color: { color: "#1e3a5f", highlight: "#38bdf8", hover: "#38bdf8" },
      width: 1,
    }));

    nodesRef.current.clear();
    edgesRef.current.clear();
    nodesRef.current.add(rawNodes);
    edgesRef.current.add(edgeData);

    networkRef.current.setOptions({
      physics: { enabled: !largeGraph },
    });

    setTimeout(() => networkRef.current?.fit({ animation: !largeGraph }), 50);
  }, [physics]);

  async function load() {
    if (!repoPath) { setError("Repository not indexed. Go to IDX tab and run the pipeline first."); return; }
    setLoading(true);
    setLoadMsg("fetching graph data…");
    setError(null);
    setSelected(null);

    // progress message ticker while waiting
    const msgs = ["fetching graph data…", "building node set…", "computing edges…", "almost there…"];
    let tick = 0;
    const ticker = setInterval(() => { tick = (tick + 1) % msgs.length; setLoadMsg(msgs[tick]); }, 1800);

    try {
      const data = await api.graph.nodes({
        repo_path: repoPath,
        output_path: outputPath || undefined,
        method: method || undefined,
        depth,
        limit: method ? 300 : -1,
      });
      const nodes = data.nodes as object[];
      const edges = data.edges as object[];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const endpointCount = (nodes as any[]).filter((n: any) => n._meta?.is_endpoint || n.is_endpoint).length;
      clearInterval(ticker);
      setLoadMsg("initializing graph…");
      setCounts({ nodes: nodes.length, edges: edges.length, endpoints: endpointCount });
      if (!networkRef.current) initNetwork();
      buildNetwork(nodes, edges);
    } catch (e) {
      clearInterval(ticker);
      const msg = String(e).replace("Error: ", "");
      if (msg.includes("not found") || msg.includes("404") || msg.includes("No such file")) {
        setError("Repository not indexed. Go to IDX tab and run the pipeline first.");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }

  // ── Actions ──
  function fitAll() { networkRef.current?.fit({ animation: true }); }

  function togglePhysics() {
    const next = !physics;
    setPhysics(next);
    networkRef.current?.setOptions({ physics: { enabled: next } });
  }

  function focusEndpoints() {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ids = rawNodesRef.current.filter((n: any) => n._meta?.is_endpoint || n.is_endpoint).map((n: any) => n.id);
    if (ids.length && networkRef.current) { networkRef.current.fit({ nodes: ids, animation: true }); networkRef.current.selectNodes(ids); }
  }

  function showNeighbors() {
    if (!selected?.id || !networkRef.current) return;
    const id = selected.id;
    const neighbors = new Set<string>([id]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rawEdgesRef.current.forEach((e: any) => {
      if (e.from === id) neighbors.add(e.to);
      if (e.to === id) neighbors.add(e.from);
    });
    const ids = [...neighbors];
    networkRef.current.fit({ nodes: ids, animation: true });
    networkRef.current.selectNodes(ids);
  }

  function showCallers() {
    if (!selected?.id || !networkRef.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ids = [selected.id, ...rawEdgesRef.current.filter((e: any) => e.to === selected.id).map((e: any) => e.from)];
    networkRef.current.fit({ nodes: ids, animation: true });
    networkRef.current.selectNodes(ids);
  }

  function showCallees() {
    if (!selected?.id || !networkRef.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ids = [selected.id, ...rawEdgesRef.current.filter((e: any) => e.from === selected.id).map((e: any) => e.to)];
    networkRef.current.fit({ nodes: ids, animation: true });
    networkRef.current.selectNodes(ids);
  }

  function resetView() {
    networkRef.current?.unselectAll();
    networkRef.current?.fit({ animation: true });
    setSelected(null);
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify({ nodes: rawNodesRef.current, edges: rawEdgesRef.current }, null, 2)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "graph.json" });
    a.click();
  }

  function handleSearch(q: string) {
    setQuery(q);
    if (!q.trim()) { setSearchHits([]); setShowSearch(false); return; }
    const ql = q.toLowerCase();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const hits = rawNodesRef.current.filter((n: any) => {
      const m = n._meta ?? n;
      return String(m.label ?? "").toLowerCase().includes(ql) || String(m.class_name ?? "").toLowerCase().includes(ql);
    }).slice(0, 15).map((n: any) => ({ ...( n._meta ?? n), id: n.id, _border: n.color?.border }));
    setSearchHits(hits);
    setShowSearch(hits.length > 0);
  }

  // init network on mount, then load data
  useEffect(() => {
    initNetwork();
    if (repoPath) load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // resize observer
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(() => {
      if (!containerRef.current || !networkRef.current) return;
      const { offsetWidth: w, offsetHeight: h } = containerRef.current;
      if (w > 0 && h > 0) { networkRef.current.setSize(`${w}px`, `${h}px`); networkRef.current.redraw(); }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => () => { networkRef.current = null; }, []);

  const callers = selected?.id ? getCallers(selected.id) : [];
  const callees = selected?.id ? getCallees(selected.id) : [];
  const borderColor = (selected as any)?._border ?? "var(--cyan)";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", fontFamily: "var(--font)" }}>

      {/* ── Stats bar ── */}
      {counts && (
        <div style={{ display: "flex", gap: 24, padding: "8px 16px", background: "var(--surface)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <Stat value={counts.nodes} label="NODES" color="var(--cyan)" />
          <Stat value={counts.edges} label="CALL EDGES" color="var(--text-muted)" />
          <Stat value={counts.endpoints} label="ENDPOINTS" color="#f59e0b" />
          <Stat value={physics ? "ON" : "OFF"} label="PHYSICS" color={physics ? "var(--cyan)" : "var(--text-dim)"} />
        </div>
      )}

      {/* ── Toolbar ── */}
      <div className="graph-toolbar" style={{ flexWrap: "wrap", gap: 6 }}>
        {/* Search with dropdown */}
        <div style={{ position: "relative" }}>
          <input
            placeholder="Search method or class…"
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { setShowSearch(false); setQuery(""); } }}
            style={{ width: 220 }}
          />
          {showSearch && (
            <div style={{ position: "absolute", top: "100%", left: 0, width: 320, background: "var(--surface-2)", border: "1px solid var(--border-mid)", zIndex: 100, maxHeight: 280, overflow: "auto" }}>
              {searchHits.map((n) => (
                <div
                  key={n.id}
                  onClick={() => { focusNode(n.id); setShowSearch(false); setQuery(""); }}
                  style={{ padding: "6px 10px", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--cyan-subtle)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <div style={{ fontSize: "var(--sz-sm)", color: "var(--text)" }}>{n.label}</div>
                  <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-faint)" }}>{n.class_name}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ width: 1, height: 20, background: "var(--border-mid)", margin: "0 2px" }} />

        <button className="btn" onClick={fitAll} disabled={!counts}>Fit All</button>
        <button className={`btn${physics ? " primary" : ""}`} onClick={togglePhysics} disabled={!counts}>
          {physics ? "Physics On" : "Physics Off"}
        </button>
        <button className="btn" onClick={focusEndpoints} disabled={!counts}>Endpoints</button>
        <button className="btn" onClick={exportJson} disabled={!counts}>Export JSON</button>

        <div style={{ width: 1, height: 20, background: "var(--border-mid)", margin: "0 2px" }} />

        <input
          placeholder="ClassName#method"
          value={method}
          onChange={(e) => setMethod(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          style={{ width: 200 }}
        />
        <select value={depth} onChange={(e) => setDepth(+e.target.value)} style={{ width: 80 }}>
          {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>depth {d}</option>)}
        </select>
        <button className="btn primary" onClick={load} disabled={loading}>
          {loading ? loadMsg : "load"}
        </button>
        {error && <span className="inline-error">{error}</span>}
      </div>

      {/* ── Canvas + Inspector ── */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div ref={containerRef} style={{ flex: 1, background: "var(--bg)", position: "relative" }}>
          {loading && (
            <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", pointerEvents: "none", zIndex: 5 }}>
              <div style={{ width: 40, height: 40, border: "2px solid var(--border-mid)", borderTop: "2px solid var(--cyan)", borderRadius: "50%", animation: "spin 0.8s linear infinite", marginBottom: 14 }} />
              <span style={{ color: "var(--text-muted)", fontSize: "var(--sz-sm)" }}>{loadMsg}</span>
              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </div>
          )}
        </div>

        {/* ── Inspector ── */}
        <div className="node-detail" style={{ width: 280, minWidth: 280 }}>
          <div className="node-detail-header">INSPECTOR</div>
          <div style={{ flex: 1, overflow: "auto", padding: "10px 12px" }}>
            {selected ? (
              <>
                <div style={{ display: "inline-block", fontSize: "var(--sz-xs)", color: borderColor, border: `1px solid ${borderColor}`, padding: "1px 7px", marginBottom: 8, letterSpacing: "0.1em" }}>
                  {(selected.group ?? "unknown").toUpperCase()}
                </div>
                {selected.http_method && (
                  <span style={{ marginLeft: 6, fontSize: "var(--sz-xs)", background: borderColor + "22", color: borderColor, border: `1px solid ${borderColor}`, padding: "1px 5px" }}>
                    {selected.http_method}
                  </span>
                )}
                <div style={{ color: "var(--text)", fontSize: "var(--sz-base)", fontWeight: 500, margin: "6px 0 4px", wordBreak: "break-all" }}>
                  {selected.is_endpoint ? (selected.route ?? selected.label) : selected.label}
                </div>
                <div style={{ color: "var(--text-muted)", fontSize: "var(--sz-xs)", marginBottom: 10, wordBreak: "break-all" }}>
                  {selected.class_name}
                </div>
                {selected.file_path && (
                  <MetaRow label="file" value={selected.file_path.split("/").slice(-2).join("/") + ":" + (selected.line ?? "")} />
                )}
                {selected.signature && <MetaRow label="sig" value={selected.signature} />}

                <div style={{ marginTop: 10, display: "flex", gap: 5, flexWrap: "wrap" }}>
                  <button className="btn" style={{ fontSize: "var(--sz-xs)", padding: "3px 8px" }} onClick={showNeighbors}>Neighbors</button>
                  <button className="btn" style={{ fontSize: "var(--sz-xs)", padding: "3px 8px" }} onClick={showCallers}>Callers ({callers.length})</button>
                  <button className="btn" style={{ fontSize: "var(--sz-xs)", padding: "3px 8px" }} onClick={showCallees}>Callees ({callees.length})</button>
                  <button className="btn" style={{ fontSize: "var(--sz-xs)", padding: "3px 8px" }} onClick={resetView}>Reset</button>
                </div>

                {callers.length > 0 && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)", letterSpacing: "0.1em", marginBottom: 4 }}>CALLED BY ({callers.length})</div>
                    {callers.slice(0, 12).map((c) => (
                      <div key={c.id} onClick={() => focusNode(c.id)}
                        style={{ fontSize: "var(--sz-xs)", color: "var(--text-muted)", padding: "3px 0", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
                        onMouseEnter={(e) => (e.currentTarget.style.color = "var(--cyan)")}
                        onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
                      >{c.label}</div>
                    ))}
                  </div>
                )}

                {callees.length > 0 && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: "var(--sz-xs)", color: "var(--text-dim)", letterSpacing: "0.1em", marginBottom: 4 }}>CALLS ({callees.length})</div>
                    {callees.slice(0, 12).map((c) => (
                      <div key={c.id} onClick={() => focusNode(c.id)}
                        style={{ fontSize: "var(--sz-xs)", color: "var(--text-muted)", padding: "3px 0", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
                        onMouseEnter={(e) => (e.currentTarget.style.color = "var(--cyan)")}
                        onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
                      >{c.label}</div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div style={{ color: "var(--text-faint)", fontSize: "var(--sz-sm)" }}>Click a node to inspect it.</div>
            )}
          </div>

          {/* Legend */}
          <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: "5px 10px" }}>
            {LEGEND.map(({ color, label }) => (
              <span key={label} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--text-dim)" }}>
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }} />
                {label}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ value, label, color }: { value: string | number; label: string; color: string }) {
  return (
    <div>
      <div style={{ fontSize: "var(--sz-xl)", fontWeight: 600, color, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: "0.1em", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ marginBottom: 5 }}>
      <span style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: "0.08em", marginRight: 6 }}>{label.toUpperCase()}</span>
      <span style={{ fontSize: "var(--sz-xs)", color: "var(--text-muted)", wordBreak: "break-all" }}>{value}</span>
    </div>
  );
}
