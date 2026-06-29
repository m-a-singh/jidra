"""
Graph visualization for jidra call graphs.
Generates interactive HTML with multiple export formats.
"""

from __future__ import annotations

import json

from ..models import Graph


def build_graph_data(
    graph: Graph,
    method_selector: str | None = None,
    depth: int = 4,
    package_filter: str | None = None,
    verbose: bool = False,
) -> dict:
    """
    Build graph data structure for visualization.

    Args:
        graph: Loaded Graph object
        method_selector: Optional method to focus on (uses ClassName#methodName format)
        depth: Traversal depth for focused view (BFS from method_selector)
        package_filter: Optional package prefix to filter
        verbose: Print progress updates

    Returns:
        Dict with nodes, edges, and metadata
    """
    if verbose:
        print(
            f"  • Building visualization data from {len(graph.methods)} methods and {len(graph.resolved_call_edges)} edges",
            flush=True,
        )

    # Build method/class lookups
    methods_by_id = {m.id: m for m in graph.methods}
    classes_by_id = {c.id: c for c in graph.classes}

    # Determine which methods to include
    filtered_method_ids = set()

    if method_selector:
        # BFS from focused method up to depth
        # Find the method by ClassName#methodName
        root_methods = [
            m
            for m in graph.methods
            if f"{m.class_full_name.split('.')[-1]}#{m.method_name}" == method_selector
            or f"{m.class_full_name}#{m.method_name}" == method_selector
        ]

        if root_methods:
            root_id = root_methods[0].id
            # BFS traversal
            visited = set()
            queue = [(root_id, 0)]

            while queue:
                method_id, current_depth = queue.pop(0)
                if method_id in visited or current_depth >= depth:
                    continue
                visited.add(method_id)
                filtered_method_ids.add(method_id)

                # Find edges from this method
                for edge in graph.resolved_call_edges:
                    if (
                        edge.caller_method_id == method_id
                        and edge.callee_method_id not in visited
                    ):
                        queue.append((edge.callee_method_id, current_depth + 1))
                    elif (
                        edge.callee_method_id == method_id
                        and edge.caller_method_id not in visited
                    ):
                        queue.append((edge.caller_method_id, current_depth + 1))
        else:
            # Method not found, include all
            filtered_method_ids = {m.id for m in graph.methods}
    elif package_filter:
        # Filter by package if specified
        filtered_methods = [
            m for m in graph.methods if m.class_full_name.startswith(package_filter)
        ]
        filtered_method_ids = {m.id for m in filtered_methods}
    else:
        filtered_method_ids = {m.id for m in graph.methods}

    # Build nodes
    nodes = []
    node_id_map = {}

    for method in graph.methods:
        if method.id not in filtered_method_ids:
            continue

        cls = classes_by_id.get(method.class_id)
        if not cls:
            continue

        # Determine if confirmed (would need access to actuator data - for now, all unconfirmed)
        confirmed = True  # Placeholder - would be set from validation report

        node_id_map[method.id] = len(nodes)
        nodes.append(
            {
                "id": method.id,
                "label": f"{cls.name}.{method.method_name}",
                "title": f"{method.class_full_name}.{method.method_name}\n{method.file_path}:{method.start_line}",
                "class_name": method.class_full_name,
                "file_path": method.file_path,
                "line": method.start_line,
                "signature": method.signature,
                "group": cls.stereotypes[0] if cls.stereotypes else "unknown",
                "confirmed": confirmed,
                "is_endpoint": method.is_endpoint,
                "http_method": method.http_method,
                "route": method.full_route,
            }
        )

    # Build edges
    edges = []
    for edge in graph.resolved_call_edges:
        if (
            edge.caller_method_id not in filtered_method_ids
            or edge.callee_method_id not in filtered_method_ids
        ):
            continue

        caller_method = methods_by_id.get(edge.caller_method_id)
        callee_method = methods_by_id.get(edge.callee_method_id)
        if not caller_method or not callee_method:
            continue

        edges.append(
            {
                "from": edge.caller_method_id,
                "to": edge.callee_method_id,
                "resolved": True,
                "weight": 1,
            }
        )

    result = {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "filter": {
                "method": method_selector,
                "package": package_filter,
                "depth": depth,
            },
        },
    }

    if verbose:
        print(
            f"  • Generated {len(nodes)} nodes and {len(edges)} edges for visualization",
            flush=True,
        )

    return result


def render_interactive_html(graph_data: dict) -> str:
    """Generate interactive dark-theme HTML with vis-network@9."""
    import datetime

    nodes = graph_data["nodes"]
    edges = graph_data["edges"]
    meta = graph_data.get("metadata", {})
    total_nodes = meta.get("total_nodes", len(nodes))
    total_edges = meta.get("total_edges", len(edges))
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Node colors by stereotype / group ──────────────────────────────────────
    _STEREOTYPE_COLORS = {
        "controller": "#2196f3",
        "service": "#34d399",
        "repository": "#f59e0b",
        "component": "#a78bfa",
        "configuration": "#67e8f9",
        "entity": "#fb7185",
        "http_handler": "#2196f3",
        "flask_route": "#34d399",
        "fastapi_route": "#34d399",
        "django_handler": "#34d399",
        "unknown": "#4d6173",
    }
    _HTTP_COLORS = {
        "GET": "#34d399",
        "POST": "#f59e0b",
        "PUT": "#38bdf8",
        "DELETE": "#fb7185",
        "PATCH": "#a78bfa",
    }

    def _node_color(n: dict) -> str:
        if n.get("is_endpoint") and n.get("http_method"):
            return _HTTP_COLORS.get(n["http_method"].upper(), "#38bdf8")
        return _STEREOTYPE_COLORS.get((n.get("group") or "unknown").lower(), "#4d6173")

    def _node_shape(n: dict) -> str:
        if n.get("is_endpoint"):
            return "diamond"
        grp = (n.get("group") or "").lower()
        if grp in (
            "controller",
            "http_handler",
            "flask_route",
            "fastapi_route",
            "django_handler",
        ):
            return "diamond"
        if grp in ("service",):
            return "ellipse"
        if grp in ("repository",):
            return "database"
        return "box"

    vis_nodes = []
    for n in nodes:
        color = _node_color(n)
        tooltip_parts = [n.get("signature") or n["label"]]
        if n.get("file_path"):
            fname = n["file_path"].split("/")[-1]
            tooltip_parts.append(f"{fname}:{n.get('line', '')}")
        if n.get("route"):
            tooltip_parts.append(f"{n.get('http_method', '')} {n['route']}")
        vis_nodes.append(
            {
                "id": n["id"],
                "label": n["label"],
                "title": "\n".join(tooltip_parts),
                "shape": _node_shape(n),
                "color": {
                    "background": color + "33",
                    "border": color,
                    "highlight": {"background": color + "55", "border": "#fff"},
                    "hover": {"background": color + "44", "border": color},
                },
                "font": {"color": "#cdd9e5", "size": 11, "face": "Inter, monospace"},
                "borderWidth": 2,
                "_meta": n,
            }
        )

    vis_edges = [
        {
            "from": e["from"],
            "to": e["to"],
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},
            "color": {"color": "#1e3a5f", "highlight": "#38bdf8", "hover": "#38bdf8"},
            "width": 1,
            "smooth": {"type": "continuous", "roundness": 0.2},
        }
        for e in edges
    ]

    nodes_json = json.dumps(vis_nodes, ensure_ascii=True)
    edges_json = json.dumps(vis_edges, ensure_ascii=True)
    dot_escaped = _generate_graphviz_dot(graph_data).replace("`", "\\`")

    # Physics off by default for large graphs to avoid browser freeze
    physics_default = "false" if total_nodes > 800 else "true"

    # Stereotype legend items
    legend_items = [
        ("#2196f3", "Controller / Endpoint"),
        ("#34d399", "Service"),
        ("#f59e0b", "Repository"),
        ("#a78bfa", "Component"),
        ("#67e8f9", "Configuration"),
        ("#fb7185", "Entity"),
        ("#4d6173", "Other"),
        ("#fb7185", "DELETE"),
        ("#f59e0b", "POST"),
        ("#34d399", "GET"),
    ]
    legend_html = "".join(
        f"<span class='legend-item'><span class='dot' style='background:{c}'></span>{lbl}</span>"
        for c, lbl in legend_items
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JIDRA Code Graph</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9/dist/vis-network.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg: #080d14; --surface: #0f1923; --surface2: #162032;
    --border: #1e2d3d; --text: #cdd9e5; --muted: #4d6173; --accent: #38bdf8;
  }}
  body {{ font-family: 'Inter', -apple-system, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}

  .header {{
    background: linear-gradient(135deg, #0d1f35 0%, #080d14 60%);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px; display: flex; align-items: center; gap: 16px; flex-shrink: 0;
  }}
  .header h1 {{ font-size: 1.15rem; font-weight: 700; color: var(--accent); letter-spacing: -0.02em; }}
  .header .sub {{ color: var(--muted); font-size: 0.75rem; margin-top: 2px; }}
  .header .generated {{ color: var(--muted); font-size: 0.7rem; margin-left: auto; text-align: right; }}

  .stats-bar {{ display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }}
  .stat {{ padding: 9px 20px; border-right: 1px solid var(--border); }}
  .stat .val {{ font-size: 1rem; font-weight: 700; display: block; }}
  .stat .lbl {{ color: var(--muted); font-size: 0.65rem; text-transform: uppercase; letter-spacing: .06em; }}

  .toolbar {{
    display: flex; align-items: center; gap: 8px; padding: 8px 16px;
    border-bottom: 1px solid var(--border); flex-shrink: 0; background: var(--surface);
  }}
  .search-wrap {{ position: relative; flex: 1; max-width: 320px; }}
  .search-wrap input {{
    width: 100%; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px 6px 30px; color: var(--text);
    font-size: 0.8rem; outline: none;
  }}
  .search-wrap input:focus {{ border-color: var(--accent); }}
  .search-wrap .icon {{ position: absolute; left: 9px; top: 50%; transform: translateY(-50%); color: var(--muted); font-size: 0.75rem; }}
  .search-results {{
    position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 100;
    background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
    max-height: 200px; overflow-y: auto; display: none;
  }}
  .search-result {{
    padding: 7px 12px; font-size: 0.78rem; cursor: pointer; border-bottom: 1px solid var(--border);
  }}
  .search-result:hover {{ background: var(--surface2); }}
  .search-result .sr-sub {{ color: var(--muted); font-size: 0.68rem; }}

  .body {{ display: flex; flex: 1; overflow: hidden; }}
  #graph {{ flex: 1; background: var(--bg); }}

  .sidebar {{
    width: 290px; flex-shrink: 0; border-left: 1px solid var(--border);
    background: var(--surface); display: flex; flex-direction: column; overflow: hidden;
  }}
  .sidebar-header {{
    padding: 10px 14px; border-bottom: 1px solid var(--border);
    font-size: 0.68rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 600;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .sidebar-body {{ flex: 1; overflow-y: auto; padding: 12px 14px; font-size: 0.8rem; line-height: 1.5; }}
  .empty-hint {{ color: var(--muted); font-size: 0.75rem; }}

  .node-badge {{ display: inline-block; font-size: 0.62rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .07em; padding: 2px 7px; border-radius: 20px; margin-bottom: 8px; }}
  .node-name {{ font-size: 0.95rem; font-weight: 600; margin-bottom: 3px; word-break: break-all; }}
  .node-class {{ color: var(--muted); font-size: 0.73rem; margin-bottom: 10px; word-break: break-all; }}
  .meta-row {{ display: flex; gap: 8px; margin-bottom: 4px; font-size: 0.73rem; }}
  .meta-key {{ color: var(--muted); min-width: 60px; }}
  .meta-val {{ color: var(--text); word-break: break-all; }}
  .section-title {{ font-size: 0.65rem; text-transform: uppercase; letter-spacing: .07em;
    color: var(--muted); margin: 12px 0 5px; font-weight: 600; }}
  .caller-item {{ background: var(--surface2); border-radius: 4px; padding: 4px 8px;
    font-size: 0.72rem; margin-bottom: 3px; cursor: pointer; color: #94a3b8; }}
  .caller-item:hover {{ border-left: 2px solid var(--accent); color: var(--text); padding-left: 6px; }}

  .controls {{
    padding: 8px 12px; border-top: 1px solid var(--border);
    display: flex; gap: 6px; flex-wrap: wrap;
  }}
  .btn {{
    background: var(--surface2); border: 1px solid var(--border); border-radius: 5px;
    color: var(--text); font-size: 0.68rem; padding: 4px 10px; cursor: pointer; transition: border-color .15s, color .15s;
    white-space: nowrap;
  }}
  .btn:hover {{ border-color: var(--accent); color: var(--accent); }}
  .btn.active {{ border-color: var(--accent); color: var(--accent); background: #0d2035; }}
  .btn.danger:hover {{ border-color: #fb7185; color: #fb7185; }}

  .legend {{
    padding: 7px 12px; border-top: 1px solid var(--border);
    display: flex; flex-wrap: wrap; gap: 6px; font-size: 0.65rem; color: var(--muted);
  }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}

  .http-badge {{ display: inline-block; font-size: 0.6rem; font-weight: 700;
    padding: 1px 5px; border-radius: 3px; margin-right: 4px; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>◈ JIDRA Code Graph</h1>
    <div class="sub">Interactive call graph — methods, classes, edges</div>
  </div>
  <div class="generated">Generated {generated_at}</div>
</div>

<div class="stats-bar">
  <div class="stat"><span class="val" style="color:#38bdf8">{total_nodes:,}</span><span class="lbl">Methods</span></div>
  <div class="stat"><span class="val" style="color:#34d399">{total_edges:,}</span><span class="lbl">Call Edges</span></div>
  <div class="stat"><span class="val" style="color:#f59e0b">{sum(1 for n in nodes if n.get("is_endpoint")):,}</span><span class="lbl">Endpoints</span></div>
  <div class="stat"><span class="val" style="color:#a78bfa">{"on" if physics_default == "true" else "off"}</span><span class="lbl">Physics</span></div>
</div>

<div class="toolbar">
  <div class="search-wrap">
    <span class="icon">🔍</span>
    <input id="searchInput" type="text" placeholder="Search method or class…" autocomplete="off">
    <div class="search-results" id="searchResults"></div>
  </div>
  <button class="btn" id="btnFit">Fit All</button>
  <button class="btn {"active" if physics_default == "true" else ""}" id="btnPhysics">Physics {"On" if physics_default == "true" else "Off"}</button>
  <button class="btn" id="btnEndpoints">Endpoints</button>
  <button class="btn" id="btnCopyDot" title="Copy Graphviz DOT to clipboard">Copy DOT</button>
  <button class="btn" id="btnExportJson" title="Download graph JSON">Export JSON</button>
</div>

<div class="body">
  <div id="graph"></div>
  <div class="sidebar">
    <div class="sidebar-header">
      <span>Inspector</span>
      <span id="sidebarCount" style="color:var(--muted);font-size:.68rem"></span>
    </div>
    <div class="sidebar-body" id="inspector">
      <span class="empty-hint">Click a node to inspect it.</span>
    </div>
    <div class="controls">
      <button class="btn" id="btnNeighbors">Show Neighbors</button>
      <button class="btn" id="btnCallers">Callers</button>
      <button class="btn" id="btnCallees">Callees</button>
      <button class="btn danger" id="btnReset">Reset View</button>
    </div>
    <div class="legend">{legend_html}</div>
  </div>
</div>

<script>
const RAW_NODES = {nodes_json};
const RAW_EDGES = {edges_json};
const DOT_CONTENT = `{dot_escaped}`;
const GRAPH_JSON = {json.dumps(graph_data, ensure_ascii=True)};

// ── Build vis datasets ──
const nodeDs = new vis.DataSet(RAW_NODES);
const edgeDs = new vis.DataSet(RAW_EDGES);
const network = new vis.Network(
  document.getElementById('graph'),
  {{ nodes: nodeDs, edges: edgeDs }},
  {{
    physics: {{
      enabled: {physics_default},
      solver: 'forceAtlas2Based',
      forceAtlas2Based: {{ gravitationalConstant: -40, centralGravity: 0.003, springLength: 100, springConstant: 0.05, damping: 0.7 }},
      stabilization: {{ iterations: 150, updateInterval: 30 }},
    }},
    interaction: {{ hover: true, tooltipDelay: 300, multiselect: true, keyboard: true }},
    nodes: {{ borderWidthSelected: 3 }},
    edges: {{ smooth: {{ type: 'continuous', roundness: 0.2 }} }},
  }}
);
network.on('stabilizationIterationsDone', () => {{
  network.setOptions({{ physics: {{ enabled: false }} }});
  document.getElementById('btnPhysics').textContent = 'Physics Off';
  document.getElementById('btnPhysics').classList.remove('active');
}});

// ── Inspector ──
let selectedId = null;
const inspector = document.getElementById('inspector');

function _metaRow(key, val) {{
  return `<div class="meta-row"><span class="meta-key">${{key}}</span><span class="meta-val">${{val}}</span></div>`;
}}

function showNode(id) {{
  selectedId = id;
  const node = RAW_NODES.find(n => n.id === id);
  if (!node) return;
  const m = node._meta;
  const color = node.color?.border || '#38bdf8';
  const grp = (m.group || 'unknown');
  const callers = RAW_EDGES.filter(e => e.to === id).map(e => RAW_NODES.find(n => n.id === e.from)).filter(Boolean);
  const callees = RAW_EDGES.filter(e => e.from === id).map(e => RAW_NODES.find(n => n.id === e.to)).filter(Boolean);
  const httpBadge = m.http_method
    ? `<span class="http-badge" style="background:${{node.color?.border}}22;color:${{node.color?.border}};border:1px solid ${{node.color?.border}}">${{m.http_method}}</span>`
    : '';

  inspector.innerHTML = `
    <span class="node-badge" style="background:${{color}}22;color:${{color}}">${{grp.toUpperCase()}}</span>
    <div class="node-name">${{httpBadge}}${{m.is_endpoint ? `<span style="color:#38bdf8">${{m.route || m.label}}</span>` : m.label}}</div>
    <div class="node-class">${{m.class_name || ''}}</div>
    ${{_metaRow('File', m.file_path ? m.file_path.split('/').slice(-2).join('/') + ':' + (m.line||'') : '—')}}
    ${{m.signature ? _metaRow('Sig', `<span style="font-size:.68rem;color:#94a3b8">${{m.signature}}</span>`) : ''}}
    ${{callers.length ? `<div class="section-title">Called by (${{callers.length}})</div>${{callers.slice(0,12).map(c=>`<div class="caller-item" onclick="focusNode('${{c.id}}')">${{c.label}}</div>`).join('')}}` : ''}}
    ${{callees.length ? `<div class="section-title">Calls (${{callees.length}})</div>${{callees.slice(0,12).map(c=>`<div class="caller-item" onclick="focusNode('${{c.id}}')">${{c.label}}</div>`).join('')}}` : ''}}
  `;
  document.getElementById('sidebarCount').textContent = `${{callers.length}}↑ ${{callees.length}}↓`;
}}

network.on('click', p => {{
  if (p.nodes.length) showNode(p.nodes[0]);
  else {{ inspector.innerHTML = '<span class="empty-hint">Click a node to inspect it.</span>'; selectedId = null; }}
}});

function focusNode(id) {{
  network.focus(id, {{ scale: 1.6, animation: true }});
  network.selectNodes([id]);
  showNode(id);
}}

// ── Search ──
const searchInput = document.getElementById('searchInput');
const searchResults = document.getElementById('searchResults');

searchInput.addEventListener('input', () => {{
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {{ searchResults.style.display = 'none'; return; }}
  const hits = RAW_NODES.filter(n =>
    n.label.toLowerCase().includes(q) || (n._meta?.class_name||'').toLowerCase().includes(q)
  ).slice(0, 15);
  if (!hits.length) {{ searchResults.style.display = 'none'; return; }}
  searchResults.innerHTML = hits.map(n =>
    `<div class="search-result" onclick="focusNode('${{n.id}}');searchResults.style.display='none';searchInput.value=''">
       ${{n.label}}<div class="sr-sub">${{n._meta?.class_name||''}}</div>
     </div>`
  ).join('');
  searchResults.style.display = 'block';
}});
document.addEventListener('click', e => {{ if (!e.target.closest('.search-wrap')) searchResults.style.display = 'none'; }});

// ── Controls ──
let physicsOn = {physics_default} === true || {physics_default} === 'true';
document.getElementById('btnFit').onclick = () => network.fit({{ animation: true }});

document.getElementById('btnPhysics').onclick = function() {{
  physicsOn = !physicsOn;
  network.setOptions({{ physics: {{ enabled: physicsOn }} }});
  this.classList.toggle('active', physicsOn);
  this.textContent = physicsOn ? 'Physics On' : 'Physics Off';
}};

document.getElementById('btnEndpoints').onclick = () => {{
  const ids = RAW_NODES.filter(n => n._meta?.is_endpoint).map(n => n.id);
  if (ids.length) network.fit({{ nodes: ids, animation: true }});
}};

document.getElementById('btnCopyDot').onclick = () => {{
  navigator.clipboard.writeText(DOT_CONTENT).then(() => {{
    const btn = document.getElementById('btnCopyDot');
    btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy DOT', 1500);
  }});
}};

document.getElementById('btnExportJson').onclick = () => {{
  const blob = new Blob([JSON.stringify(GRAPH_JSON, null, 2)], {{type: 'application/json'}});
  const a = Object.assign(document.createElement('a'), {{ href: URL.createObjectURL(blob), download: 'graph.json' }});
  a.click();
}};

document.getElementById('btnNeighbors').onclick = () => {{
  if (!selectedId) return;
  const neighbors = new Set([selectedId]);
  RAW_EDGES.forEach(e => {{
    if (e.from === selectedId) neighbors.add(e.to);
    if (e.to === selectedId) neighbors.add(e.from);
  }});
  network.fit({{ nodes: [...neighbors], animation: true }});
  network.selectNodes([...neighbors]);
}};

document.getElementById('btnCallers').onclick = () => {{
  if (!selectedId) return;
  const ids = [selectedId, ...RAW_EDGES.filter(e => e.to === selectedId).map(e => e.from)];
  network.fit({{ nodes: ids, animation: true }});
  network.selectNodes(ids);
}};

document.getElementById('btnCallees').onclick = () => {{
  if (!selectedId) return;
  const ids = [selectedId, ...RAW_EDGES.filter(e => e.from === selectedId).map(e => e.to)];
  network.fit({{ nodes: ids, animation: true }});
  network.selectNodes(ids);
}};

document.getElementById('btnReset').onclick = () => {{
  network.unselectAll();
  network.fit({{ animation: true }});
  inspector.innerHTML = '<span class="empty-hint">Click a node to inspect it.</span>';
  selectedId = null;
  document.getElementById('sidebarCount').textContent = '';
}};

// Keyboard shortcut: / to focus search
document.addEventListener('keydown', e => {{
  if (e.key === '/' && document.activeElement !== searchInput) {{
    e.preventDefault(); searchInput.focus();
  }}
  if (e.key === 'Escape') {{ searchResults.style.display = 'none'; searchInput.blur(); }}
}});
</script>
</body>
</html>"""


def _generate_graphviz_dot(graph_data: dict) -> str:
    """Generate Graphviz DOT format."""
    nodes = graph_data["nodes"]
    edges = graph_data["edges"]

    lines = ["digraph {", "  rankdir=LR;", "  node [shape=box];"]

    # Add nodes with colors
    for node in nodes:
        color = "green" if node["confirmed"] else "red"
        lines.append(
            f'  "{node["id"]}" [label="{node["label"]}", fillcolor={color}, style=filled];'
        )

    # Add edges
    for edge in edges:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}";')

    lines.append("}")
    return "\n".join(lines)


def render_json_export(graph_data: dict) -> str:
    """Render graph data as prettified JSON."""
    return json.dumps(graph_data, indent=2)
