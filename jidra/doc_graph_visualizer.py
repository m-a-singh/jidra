"""
Build and render a standalone doc-to-code linkage graph.

Node types:
  - doc    : a document source (PDF, MD, etc.)
  - chunk  : a section/paragraph chunk within a doc
  - class  : a code class node (from graph.db)

Edge types:
  - doc→chunk   : containment
  - chunk→class : heuristic link (linked_classes field)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


# ── Data extraction ───────────────────────────────────────────────────────────

def build_doc_graph_data(conn: sqlite3.Connection, graph) -> dict:
    """
    Pull doc sources, chunks, and linked class nodes from the DB + graph object.
    Returns a dict with nodes/edges ready for rendering.
    """
    from . import doc_store
    doc_store.migrate(conn)

    sources = doc_store.list_sources(conn)
    if not sources:
        return {"nodes": [], "edges": [], "stats": {"docs": 0, "chunks": 0, "classes": 0, "links": 0}}

    # Build class lookup from graph
    class_by_name: dict[str, object] = {}
    for cls in graph.classes:
        full = getattr(cls, "full_name", "") or ""
        short = full.split(".")[-1] if "." in full else full
        if short:
            class_by_name[short] = cls
        if full:
            class_by_name[full] = cls

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_class_ids: set[str] = set()
    total_links = 0

    source_type_colors = {
        "pdf":      "#f59e0b",
        "docx":     "#38bdf8",
        "markdown": "#34d399",
        "pptx":     "#a78bfa",
        "file":     "#94a3b8",
    }

    for src in sources:
        src_path = src["source_path"]
        src_id = f"doc::{src_path}"
        src_color = source_type_colors.get(src["source_type"], "#94a3b8")
        src_label = Path(src_path).name

        nodes.append({
            "id": src_id,
            "label": src_label,
            "type": "doc",
            "source_type": src["source_type"],
            "full_path": src_path,
            "chunk_count": src["chunk_count"],
            "color": src_color,
            "size": 28,
            "tooltip": f"{src_label}\n{src['source_type'].upper()} · {src['chunk_count']} chunks",
        })

        # Load all chunks for this source
        chunks = conn.execute(
            "SELECT id, title, content, linked_classes, chunk_index FROM doc_chunks "
            "WHERE source_path = ? ORDER BY chunk_index",
            (src_path,),
        ).fetchall()

        for chunk_id, title, content, linked_classes_str, chunk_index in chunks:
            c_id = f"chunk::{chunk_id}"
            c_label = (title or f"§{chunk_index + 1}")[:40]
            preview = (content or "")[:120].replace("\n", " ")
            linked = [x for x in linked_classes_str.split(",") if x]

            nodes.append({
                "id": c_id,
                "label": c_label,
                "type": "chunk",
                "chunk_index": chunk_index,
                "linked_count": len(linked),
                "color": "#1e3a5f" if linked else "#1e293b",
                "border": src_color,
                "size": 14,
                "tooltip": f"{c_label}\n{preview}{'...' if len(content or '') > 120 else ''}",
            })

            # doc → chunk containment edge
            edges.append({
                "id": f"e::doc-chunk::{chunk_id}",
                "from": src_id,
                "to": c_id,
                "type": "contains",
                "color": "#1e293b",
                "width": 1,
                "dashes": False,
            })

            # chunk → class link edges
            for cls_name in linked:
                cls = class_by_name.get(cls_name)
                if not cls:
                    continue
                cls_id = f"class::{getattr(cls, 'id', cls_name)}"

                if cls_id not in seen_class_ids:
                    seen_class_ids.add(cls_id)
                    full_name = getattr(cls, "full_name", cls_name) or cls_name
                    file_path = getattr(cls, "file_path", "") or ""
                    lang = getattr(cls, "language", "unknown") or "unknown"
                    lang_colors = {
                        "java": "#f59e0b", "typescript": "#38bdf8",
                        "python": "#34d399", "scala": "#fb7185", "go": "#67e8f9",
                    }
                    nodes.append({
                        "id": cls_id,
                        "label": cls_name,
                        "type": "class",
                        "full_name": full_name,
                        "file_path": file_path,
                        "language": lang,
                        "color": lang_colors.get(lang, "#94a3b8"),
                        "size": 20,
                        "tooltip": f"{full_name}\n{Path(file_path).name if file_path else ''}\n[{lang}]",
                    })

                edges.append({
                    "id": f"e::link::{chunk_id}::{cls_name}",
                    "from": c_id,
                    "to": cls_id,
                    "type": "links_to",
                    "color": "#38bdf866",
                    "width": 2,
                    "dashes": True,
                })
                total_links += 1

    stats = {
        "docs": len(sources),
        "chunks": sum(s["chunk_count"] for s in sources),
        "classes": len(seen_class_ids),
        "links": total_links,
    }

    return {"nodes": nodes, "edges": edges, "stats": stats}


# ── HTML renderer ─────────────────────────────────────────────────────────────

def render_doc_graph_html(data: dict) -> str:
    import datetime
    nodes_json = json.dumps(data["nodes"], ensure_ascii=True)
    edges_json = json.dumps(data["edges"], ensure_ascii=True)
    stats = data["stats"]
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    legend_items = [
        ("#f59e0b", "PDF"),
        ("#38bdf8", "DOCX / TypeScript"),
        ("#34d399", "Markdown / Python"),
        ("#a78bfa", "PPTX"),
        ("#1e3a5f", "Chunk (linked)"),
        ("#1e293b", "Chunk (unlinked)"),
        ("#f59e0b", "Java class"),
        ("#94a3b8", "Class (other)"),
    ]
    legend_html = "".join(
        f"<span class='legend-item'><span class='dot' style='background:{c}'></span>{label}</span>"
        for c, label in legend_items
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>JIDRA Doc Graph</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9/dist/vis-network.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg: #080d14; --surface: #0f1923; --surface2: #162032;
    --border: #1e2d3d; --text: #cdd9e5; --muted: #4d6173;
    --accent: #38bdf8;
  }}
  body {{ font-family: 'Inter', -apple-system, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}

  .header {{
    background: linear-gradient(135deg, #0d1f35 0%, #080d14 60%);
    border-bottom: 1px solid var(--border);
    padding: 16px 28px;
    display: flex; align-items: center; gap: 20px; flex-shrink: 0;
  }}
  .header h1 {{ font-size: 1.2rem; font-weight: 700; color: var(--accent); letter-spacing: -0.02em; }}
  .header .sub {{ color: var(--muted); font-size: 0.78rem; margin-top: 2px; }}
  .header .generated {{ color: var(--muted); font-size: 0.72rem; margin-left: auto; }}

  .stats-bar {{
    display: flex; gap: 0; border-bottom: 1px solid var(--border); flex-shrink: 0;
  }}
  .stat {{
    padding: 10px 24px; border-right: 1px solid var(--border);
    font-size: 0.78rem;
  }}
  .stat .val {{ font-size: 1.1rem; font-weight: 700; display: block; }}
  .stat .lbl {{ color: var(--muted); font-size: 0.68rem; text-transform: uppercase; letter-spacing: .06em; }}

  .body {{ display: flex; flex: 1; overflow: hidden; }}

  #graph {{ flex: 1; background: var(--bg); }}

  .sidebar {{
    width: 300px; flex-shrink: 0; border-left: 1px solid var(--border);
    background: var(--surface); display: flex; flex-direction: column; overflow: hidden;
  }}
  .sidebar-header {{
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); font-weight: 600;
  }}
  .sidebar-body {{ flex: 1; overflow-y: auto; padding: 14px 16px; font-size: 0.82rem; line-height: 1.5; }}
  .sidebar-body .empty {{ color: var(--muted); font-size: 0.78rem; }}

  .node-type {{ display: inline-block; font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; padding: 2px 7px; border-radius: 20px; margin-bottom: 8px; }}
  .node-label {{ font-size: 1rem; font-weight: 600; margin-bottom: 4px; }}
  .node-meta {{ color: var(--muted); font-size: 0.75rem; margin-bottom: 10px; }}
  .node-content {{ background: var(--surface2); border-radius: 6px; padding: 10px; font-size: 0.76rem;
    color: #94a3b8; line-height: 1.5; max-height: 180px; overflow-y: auto; white-space: pre-wrap; }}
  .linked-list {{ margin-top: 10px; }}
  .linked-list .title {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: .07em;
    color: var(--muted); margin-bottom: 6px; }}
  .linked-pill {{ display: inline-block; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 4px; padding: 2px 7px; font-size: 0.72rem; margin: 2px; color: #94a3b8; cursor: pointer; }}
  .linked-pill:hover {{ border-color: var(--accent); color: var(--accent); }}

  .controls {{
    padding: 10px 16px; border-top: 1px solid var(--border); display: flex; gap: 8px; flex-wrap: wrap;
  }}
  .btn {{
    background: var(--surface2); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); font-size: 0.72rem; padding: 5px 12px; cursor: pointer; transition: border-color .15s;
  }}
  .btn:hover {{ border-color: var(--accent); color: var(--accent); }}
  .btn.active {{ border-color: var(--accent); color: var(--accent); background: #0d2035; }}

  .legend {{
    padding: 8px 16px; border-top: 1px solid var(--border);
    display: flex; flex-wrap: wrap; gap: 8px; font-size: 0.68rem; color: var(--muted);
  }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; }}
  .dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}

  .empty-state {{
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; color: var(--muted); gap: 12px; text-align: center;
  }}
  .empty-state .big {{ font-size: 3rem; opacity: .3; }}
  .empty-state p {{ font-size: 0.88rem; max-width: 300px; line-height: 1.6; }}
  code {{ background: var(--surface2); padding: 2px 6px; border-radius: 4px; font-size: 0.82rem; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>◈ JIDRA Doc Graph</h1>
    <div class="sub">Document-to-code linkage — specs and design docs mapped to source classes</div>
  </div>
  <div class="generated">Generated {generated_at}</div>
</div>

<div class="stats-bar">
  <div class="stat"><span class="val" style="color:#f59e0b">{stats['docs']}</span><span class="lbl">Documents</span></div>
  <div class="stat"><span class="val" style="color:#a78bfa">{stats['chunks']}</span><span class="lbl">Chunks</span></div>
  <div class="stat"><span class="val" style="color:#38bdf8">{stats['classes']}</span><span class="lbl">Classes Linked</span></div>
  <div class="stat"><span class="val" style="color:#34d399">{stats['links']}</span><span class="lbl">Total Links</span></div>
</div>

<div class="body">
  {'<div id="graph"></div>' if stats['docs'] > 0 else ''}
  {'<div class="empty-state"><div class="big">◈</div><p>No documents indexed yet.<br>Run <code>jidra index-docs --path ./specs/</code> to get started.</p></div>' if stats['docs'] == 0 else ''}

  <div class="sidebar">
    <div class="sidebar-header">Node Inspector</div>
    <div class="sidebar-body" id="inspector">
      <span class="empty">Click a node to inspect it.</span>
    </div>
    <div class="controls">
      <button class="btn" id="btnFit">Fit All</button>
      <button class="btn active" id="btnPhysics">Physics On</button>
      <button class="btn" id="btnDocs">Docs Only</button>
      <button class="btn" id="btnLinked">Linked Only</button>
      <button class="btn" id="btnAll">Show All</button>
    </div>
    <div class="legend">{legend_html}</div>
  </div>
</div>

<script>
const RAW_NODES = {nodes_json};
const RAW_EDGES = {edges_json};

if (RAW_NODES.length === 0) {{ document.querySelector('#graph') && document.querySelector('#graph').remove(); }}

const container = document.getElementById('graph');
if (!container) {{ throw new Error('no graph'); }}

// Build vis datasets
function makeDatasets(nodes, edges) {{
  return {{
    nodes: new vis.DataSet(nodes.map(n => ({{
      id: n.id,
      label: n.label,
      title: n.tooltip,
      color: {{
        background: n.color,
        border: n.border || n.color,
        highlight: {{ background: '#1e4a6b', border: '#38bdf8' }},
        hover: {{ background: n.color, border: '#38bdf8' }},
      }},
      size: n.size,
      shape: n.type === 'doc' ? 'diamond' : n.type === 'chunk' ? 'dot' : 'box',
      font: {{
        color: '#cdd9e5',
        size: n.type === 'doc' ? 14 : n.type === 'class' ? 12 : 10,
        face: 'Inter, sans-serif',
      }},
      borderWidth: n.type === 'doc' ? 3 : 1,
      _meta: n,
    }}))),
    edges: new vis.DataSet(edges.map(e => ({{
      id: e.id,
      from: e.from,
      to: e.to,
      color: {{ color: e.color, highlight: '#38bdf8', hover: '#38bdf8' }},
      width: e.width,
      dashes: e.dashes,
      arrows: e.type === 'links_to' ? {{ to: {{ enabled: true, scaleFactor: 0.5 }} }} : undefined,
    }}))),
  }};
}}

const opts = {{
  physics: {{
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {{ gravitationalConstant: -60, centralGravity: 0.005, springLength: 120, springConstant: 0.08, damping: 0.6 }},
    stabilization: {{ iterations: 200, updateInterval: 25 }},
  }},
  interaction: {{ hover: true, tooltipDelay: 200, navigationButtons: false, keyboard: true }},
  nodes: {{ borderWidthSelected: 3 }},
  edges: {{ smooth: {{ type: 'continuous', roundness: 0.3 }} }},
}};

let ds = makeDatasets(RAW_NODES, RAW_EDGES);
const network = new vis.Network(container, ds, opts);

// ── Inspector ──
const inspector = document.getElementById('inspector');
network.on('click', params => {{
  if (!params.nodes.length) {{ inspector.innerHTML = '<span class="empty">Click a node to inspect it.</span>'; return; }}
  const id = params.nodes[0];
  const meta = RAW_NODES.find(n => n.id === id)?._meta || RAW_NODES.find(n => n.id === id);
  const node = ds.nodes.get(id);
  const nodeMeta = node._meta;

  if (nodeMeta.type === 'doc') {{
    inspector.innerHTML = `
      <span class="node-type" style="background:#1e2d3d;color:#f59e0b">${{nodeMeta.source_type?.toUpperCase() || 'DOC'}}</span>
      <div class="node-label">${{nodeMeta.label}}</div>
      <div class="node-meta">${{nodeMeta.chunk_count}} chunks<br><span style="color:#4d6173;font-size:.72rem">${{nodeMeta.full_path}}</span></div>
    `;
  }} else if (nodeMeta.type === 'chunk') {{
    const linked = RAW_EDGES.filter(e => e.from === id && e.type === 'links_to')
      .map(e => RAW_NODES.find(n => n.id === e.to)?._meta?.label || e.to);
    inspector.innerHTML = `
      <span class="node-type" style="background:#1e293b;color:#a78bfa">CHUNK §${{nodeMeta.chunk_index + 1}}</span>
      <div class="node-label">${{nodeMeta.label}}</div>
      <div class="node-content">${{nodeMeta.tooltip?.split('\\n').slice(1).join('\\n') || ''}}</div>
      ${{linked.length ? `<div class="linked-list"><div class="title">Linked Classes (${{linked.length}})</div>${{linked.map(l => `<span class="linked-pill" onclick="focusClass('${{l}}')">${{l}}</span>`).join('')}}</div>` : ''}}
    `;
  }} else if (nodeMeta.type === 'class') {{
    const chunks = RAW_EDGES.filter(e => e.to === id && e.type === 'links_to')
      .map(e => RAW_NODES.find(n => n.id === e.from)?._meta?.label || e.from);
    inspector.innerHTML = `
      <span class="node-type" style="background:#162032;color:#38bdf8">CLASS · ${{nodeMeta.language?.toUpperCase() || ''}}</span>
      <div class="node-label">${{nodeMeta.label}}</div>
      <div class="node-meta">${{nodeMeta.full_name}}<br><span style="color:#4d6173;font-size:.72rem">${{nodeMeta.file_path ? nodeMeta.file_path.split('/').slice(-2).join('/') : ''}}</span></div>
      ${{chunks.length ? `<div class="linked-list"><div class="title">Referenced in ${{chunks.length}} chunk(s)</div>${{chunks.map(c => `<span class="linked-pill">${{c}}</span>`).join('')}}</div>` : ''}}
    `;
  }}
}});

function focusClass(label) {{
  const node = RAW_NODES.find(n => n._meta?.type === 'class' && n._meta?.label === label);
  if (node) {{ network.focus(node.id, {{ scale: 1.4, animation: true }}); network.selectNodes([node.id]); }}
}}

// ── Controls ──
let physicsOn = true;
document.getElementById('btnFit').onclick = () => network.fit({{ animation: true }});
document.getElementById('btnPhysics').onclick = function() {{
  physicsOn = !physicsOn;
  network.setOptions({{ physics: {{ enabled: physicsOn }} }});
  this.classList.toggle('active', physicsOn);
  this.textContent = physicsOn ? 'Physics On' : 'Physics Off';
}};
document.getElementById('btnDocs').onclick = () => {{
  const ids = RAW_NODES.filter(n => n.type === 'doc').map(n => n.id);
  network.fit({{ nodes: ids, animation: true }});
}};
document.getElementById('btnLinked').onclick = () => {{
  const linkedChunkIds = new Set(RAW_EDGES.filter(e => e.type === 'links_to').map(e => e.from));
  const show = RAW_NODES.filter(n => n.type === 'doc' || n.type === 'class' || linkedChunkIds.has(n.id)).map(n => n.id);
  network.fit({{ nodes: show, animation: true }});
}};
document.getElementById('btnAll').onclick = () => network.fit({{ animation: true }});

network.on('stabilizationIterationsDone', () => network.setOptions({{ physics: {{ enabled: false }} }}));
</script>
</body>
</html>"""
