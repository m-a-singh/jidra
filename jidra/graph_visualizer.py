"""
Graph visualization for jidra call graphs.
Generates interactive HTML with multiple export formats.
"""

from __future__ import annotations

import json

from .models import Graph


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


# Covers the full STEREOTYPE_VALUES taxonomy in models.py so no group silently
# falls back to "unknown" grey.
_GROUP_COLORS = {
    "controller": "#38bdf8",
    "rest_controller": "#38bdf8",
    "service": "#34d399",
    "transactional_service": "#22d3ee",
    "repository": "#f59e0b",
    "component": "#a78bfa",
    "configuration": "#67e8f9",
    "entity": "#fb7185",
    "event_listener": "#fbbf24",
    "react_component": "#f472b6",
    "react_hook": "#fda4af",
    "react_context": "#fb7185",
    "vue_component": "#4ade80",
    "vue_composable": "#86efac",
    "vue_store": "#22c55e",
    "angular_component": "#dc2626",
    "angular_service": "#ef4444",
    "angular_module": "#b91c1c",
    "angular_guard": "#f87171",
    "django_view": "#84cc16",
    "django_model": "#65a30d",
    "flask_route": "#34d399",
    "fastapi_route": "#2dd4bf",
    "unknown": "#94a3b8",
}

_HTTP_COLORS = {
    "GET": "#34d399",
    "POST": "#f59e0b",
    "PUT": "#38bdf8",
    "DELETE": "#fb7185",
    "PATCH": "#a78bfa",
}

_SHAPE_BY_GROUP = {
    "controller": "diamond",
    "rest_controller": "diamond",
    "flask_route": "diamond",
    "fastapi_route": "diamond",
    "django_view": "diamond",
    "service": "ellipse",
    "transactional_service": "ellipse",
    "repository": "database",
}


def _node_shape(group: str, is_endpoint: bool) -> str:
    if is_endpoint:
        return "diamond"
    return _SHAPE_BY_GROUP.get(group, "dot")


def _node_color(group: str, confirmed: bool, http_method: str | None = None) -> dict:
    base = _GROUP_COLORS.get(group, _GROUP_COLORS["unknown"])
    if http_method:
        base = _HTTP_COLORS.get(http_method.upper(), base)
    if not confirmed:
        return {
            "background": "#1e293b",
            "border": base,
            "highlight": {"background": "#1e4a6b", "border": "#38bdf8"},
            "hover": {"background": "#1e293b", "border": "#38bdf8"},
        }
    return {
        "background": base,
        "border": base,
        "highlight": {"background": "#1e4a6b", "border": "#38bdf8"},
        "hover": {"background": base, "border": "#38bdf8"},
    }


def render_interactive_html(graph_data: dict) -> str:
    """
    Generate interactive HTML with a vis-network graph: dark theme, node
    inspector sidebar, physics/filter controls, and a legend — matching the
    look of the doc-graph visualizer (see doc_graph_visualizer.py).

    Returns:
        HTML string
    """
    import datetime

    nodes = graph_data["nodes"]
    edges = graph_data["edges"]
    meta = graph_data["metadata"]

    groups_present = sorted({n["group"] for n in nodes}) or ["unknown"]
    # Disable physics by default on large graphs to avoid browser freeze.
    physics_default = len(nodes) <= 800

    vis_nodes = [
        {
            "id": n["id"],
            "label": n["label"],
            "title": n["title"],
            "group": n["group"],
            "class_name": n["class_name"],
            "file_path": n["file_path"],
            "line": n["line"],
            "signature": n["signature"],
            "confirmed": n["confirmed"],
            "is_endpoint": n["is_endpoint"],
            "http_method": n["http_method"],
            "route": n["route"],
            "color": _node_color(n["group"], n["confirmed"], n["http_method"]),
            "shape": _node_shape(n["group"], n["is_endpoint"]),
            "size": 16 if n["is_endpoint"] else 12,
            "font": {"color": "#cdd9e5", "size": 11, "face": "Inter, sans-serif"},
            "borderWidth": 3 if n["is_endpoint"] else 1,
        }
        for n in nodes
    ]
    vis_edges = [
        {
            "id": f"e::{i}",
            "from": e["from"],
            "to": e["to"],
            "color": {
                "color": "#38bdf855",
                "highlight": "#38bdf8",
                "hover": "#38bdf8",
            },
            "width": 1,
            "smooth": {"type": "continuous", "roundness": 0.3},
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},
        }
        for i, e in enumerate(edges)
    ]

    nodes_json = json.dumps(vis_nodes, ensure_ascii=True)
    edges_json = json.dumps(vis_edges, ensure_ascii=True)
    dot_json = json.dumps(_generate_graphviz_dot(graph_data))
    export_json = json.dumps(graph_data, indent=2)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    legend_html = "".join(
        f"<span class='legend-item'><span class='dot' style='background:{_GROUP_COLORS.get(g, _GROUP_COLORS['unknown'])}'></span>{g}</span>"
        for g in groups_present
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JIDRA Graph Visualization</title>
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
    width: 320px; flex-shrink: 0; border-left: 1px solid var(--border);
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
  .node-label {{ font-size: 1rem; font-weight: 600; margin-bottom: 4px; word-break: break-all; }}
  .node-meta {{ color: var(--muted); font-size: 0.75rem; margin-bottom: 10px; word-break: break-all; }}
  .node-content {{ background: var(--surface2); border-radius: 6px; padding: 10px; font-size: 0.76rem;
    color: #94a3b8; line-height: 1.5; max-height: 180px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }}

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
  pre {{ padding: 14px; background: var(--surface2); border-radius: 6px; overflow: auto; font-size: 0.76rem; color: #94a3b8; max-height: 100%; }}
  .export-overlay {{
    position: absolute; inset: 0; background: var(--bg); z-index: 5; display: none;
    flex-direction: column; overflow: hidden;
  }}
  .export-overlay.active {{ display: flex; }}
  .export-overlay .export-bar {{ padding: 8px 16px; border-bottom: 1px solid var(--border); display: flex; gap: 8px; flex-shrink: 0; }}
  .export-overlay .export-body {{ flex: 1; overflow: auto; padding: 16px; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>◈ JIDRA Graph Visualization</h1>
    <div class="sub">Interactive call graph — methods, classes, and resolved call edges</div>
  </div>
  <div class="generated">Generated {generated_at}</div>
</div>

<div class="stats-bar">
  <div class="stat"><span class="val" style="color:#38bdf8">{meta["total_nodes"]}</span><span class="lbl">Nodes</span></div>
  <div class="stat"><span class="val" style="color:#34d399">{meta["total_edges"]}</span><span class="lbl">Edges</span></div>
  <div class="stat"><span class="val" style="color:#f59e0b">{sum(1 for n in nodes if n["is_endpoint"])}</span><span class="lbl">Endpoints</span></div>
</div>

<div class="toolbar">
  <div class="search-wrap">
    <span class="icon">&#128269;</span>
    <input id="searchInput" type="text" placeholder="Search method or class&hellip;" autocomplete="off">
    <div class="search-results" id="searchResults"></div>
  </div>
</div>

<div class="body" style="position: relative;">
  {'<div id="graph"></div>' if nodes else ""}
  {'<div class="empty-state"><div class="big">◈</div><p>No nodes to display.<br>Try a different package filter or method focus.</p></div>' if not nodes else ""}

  <div id="dotOverlay" class="export-overlay">
    <div class="export-bar">
      <button class="btn" onclick="copyOverlay('dotContent')">Copy DOT</button>
      <button class="btn" onclick="closeOverlay('dotOverlay')">Close</button>
    </div>
    <div class="export-body"><pre id="dotContent"></pre></div>
  </div>
  <div id="jsonOverlay" class="export-overlay">
    <div class="export-bar">
      <button class="btn" onclick="downloadJSON()">Download JSON</button>
      <button class="btn" onclick="closeOverlay('jsonOverlay')">Close</button>
    </div>
    <div class="export-body"><pre id="jsonContent"></pre></div>
  </div>

  <div class="sidebar">
    <div class="sidebar-header">Node Inspector</div>
    <div class="sidebar-body" id="inspector">
      <span class="empty">Click a node to inspect it.</span>
    </div>
    <div class="controls">
      <button class="btn" id="btnFit">Fit All</button>
      <button class="btn {"active" if physics_default else ""}" id="btnPhysics">Physics {"On" if physics_default else "Off"}</button>
      <button class="btn" id="btnEndpoints">Endpoints Only</button>
      <button class="btn" id="btnAll">Show All</button>
      <button class="btn" id="btnNeighbors">Show Neighbors</button>
      <button class="btn" id="btnCallers">Callers</button>
      <button class="btn" id="btnCallees">Callees</button>
      <button class="btn" id="btnDot">View DOT</button>
      <button class="btn" id="btnJson">View JSON</button>
    </div>
    <div class="legend">{legend_html}</div>
  </div>
</div>

<script>
const RAW_NODES = {nodes_json};
const RAW_EDGES = {edges_json};
const DOT_TEXT = {dot_json};
const EXPORT_JSON = {export_json};

if (RAW_NODES.length === 0) {{ const g = document.querySelector('#graph'); if (g) g.remove(); }}

const container = document.getElementById('graph');

function makeDatasets(nodes, edges) {{
  return {{
    nodes: new vis.DataSet(nodes.map(n => ({{ ...n, _meta: n }}))),
    edges: new vis.DataSet(edges),
  }};
}}

const opts = {{
  physics: {{
    enabled: {"true" if physics_default else "false"},
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {{ gravitationalConstant: -60, centralGravity: 0.005, springLength: 120, springConstant: 0.08, damping: 0.6 }},
    stabilization: {{ iterations: 200, updateInterval: 25 }},
  }},
  interaction: {{ hover: true, tooltipDelay: 200, navigationButtons: false, keyboard: true }},
  nodes: {{ borderWidthSelected: 3 }},
  edges: {{ smooth: {{ type: 'continuous', roundness: 0.3 }} }},
}};

let network = null;
let ds = null;
if (container) {{
  ds = makeDatasets(RAW_NODES, RAW_EDGES);
  network = new vis.Network(container, ds, opts);

  const inspector = document.getElementById('inspector');
  window._showInspectorNode = id => {{
    const node = ds.nodes.get(id);
    if (!node) return;
    selectedId = id;
    const m = node._meta;
    const routeLine = m.is_endpoint ? `<div class="node-meta">${{m.http_method || ''}} ${{m.route || ''}}</div>` : '';
    inspector.innerHTML = `
      <span class="node-type" style="background:#1e2d3d;color:#38bdf8">${{(m.group || 'unknown').toUpperCase()}}${{m.confirmed ? '' : ' · UNCONFIRMED'}}</span>
      <div class="node-label">${{m.label}}</div>
      <div class="node-meta">${{m.class_name}}<br><span style="color:#4d6173;font-size:.72rem">${{m.file_path}}:${{m.line}}</span></div>
      ${{routeLine}}
      <div class="node-content">${{m.signature || ''}}</div>
    `;
  }};
  network.on('click', params => {{
    if (!params.nodes.length) {{ inspector.innerHTML = '<span class="empty">Click a node to inspect it.</span>'; selectedId = null; return; }}
    window._showInspectorNode(params.nodes[0]);
  }});

  network.on('stabilizationIterationsDone', () => {{
    network.setOptions({{ physics: {{ enabled: false }} }});
    physicsOn = false;
    const btn = document.getElementById('btnPhysics');
    btn.textContent = 'Physics Off';
    btn.classList.remove('active');
  }});
}}

let selectedId = null;
document.getElementById('btnFit').onclick = () => network && network.fit({{ animation: true }});
let physicsOn = {"true" if physics_default else "false"};
document.getElementById('btnPhysics').onclick = function() {{
  if (!network) return;
  physicsOn = !physicsOn;
  network.setOptions({{ physics: {{ enabled: physicsOn }} }});
  this.classList.toggle('active', physicsOn);
  this.textContent = physicsOn ? 'Physics On' : 'Physics Off';
}};
document.getElementById('btnEndpoints').onclick = () => {{
  if (!network) return;
  const ids = RAW_NODES.filter(n => n.is_endpoint).map(n => n.id);
  if (ids.length) network.fit({{ nodes: ids, animation: true }});
}};
document.getElementById('btnAll').onclick = () => network && network.fit({{ animation: true }});

document.getElementById('btnNeighbors').onclick = () => {{
  if (!network || !selectedId) return;
  const neighbors = new Set([selectedId]);
  RAW_EDGES.forEach(e => {{
    if (e.from === selectedId) neighbors.add(e.to);
    if (e.to === selectedId) neighbors.add(e.from);
  }});
  const ids = [...neighbors];
  network.fit({{ nodes: ids, animation: true }});
  network.selectNodes(ids);
}};
document.getElementById('btnCallers').onclick = () => {{
  if (!network || !selectedId) return;
  const ids = [selectedId, ...RAW_EDGES.filter(e => e.to === selectedId).map(e => e.from)];
  network.fit({{ nodes: ids, animation: true }});
  network.selectNodes(ids);
}};
document.getElementById('btnCallees').onclick = () => {{
  if (!network || !selectedId) return;
  const ids = [selectedId, ...RAW_EDGES.filter(e => e.from === selectedId).map(e => e.to)];
  network.fit({{ nodes: ids, animation: true }});
  network.selectNodes(ids);
}};

// ── Search ──
const searchInput = document.getElementById('searchInput');
const searchResults = document.getElementById('searchResults');
searchInput.addEventListener('input', () => {{
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {{ searchResults.style.display = 'none'; return; }}
  const hits = RAW_NODES.filter(n =>
    n.label.toLowerCase().includes(q) || (n.class_name || '').toLowerCase().includes(q)
  ).slice(0, 15);
  if (!hits.length) {{ searchResults.style.display = 'none'; return; }}
  searchResults.innerHTML = hits.map(n =>
    `<div class="search-result" data-id="${{n.id}}">${{n.label}}<div class="sr-sub">${{n.class_name || ''}}</div></div>`
  ).join('');
  searchResults.querySelectorAll('.search-result').forEach(el => {{
    el.addEventListener('click', () => {{
      const id = el.getAttribute('data-id');
      if (network) {{ network.focus(id, {{ scale: 1.5, animation: true }}); network.selectNodes([id]); }}
      if (window._showInspectorNode) window._showInspectorNode(id);
      searchResults.style.display = 'none';
      searchInput.value = '';
    }});
  }});
  searchResults.style.display = 'block';
}});
document.addEventListener('click', e => {{ if (!e.target.closest('.search-wrap')) searchResults.style.display = 'none'; }});
document.addEventListener('keydown', e => {{
  if (e.key === '/' && document.activeElement !== searchInput) {{ e.preventDefault(); searchInput.focus(); }}
  if (e.key === 'Escape') {{ searchResults.style.display = 'none'; searchInput.blur(); }}
}});

document.getElementById('btnDot').onclick = () => {{
  document.getElementById('dotContent').textContent = DOT_TEXT;
  document.getElementById('dotOverlay').classList.add('active');
}};
document.getElementById('btnJson').onclick = () => {{
  document.getElementById('jsonContent').textContent = JSON.stringify(EXPORT_JSON, null, 2);
  document.getElementById('jsonOverlay').classList.add('active');
}};

function closeOverlay(id) {{ document.getElementById(id).classList.remove('active'); }}
function copyOverlay(id) {{
  navigator.clipboard.writeText(document.getElementById(id).textContent).then(() => alert('Copied to clipboard'));
}}
function downloadJSON() {{
  const blob = new Blob([JSON.stringify(EXPORT_JSON, null, 2)], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'graph.json';
  a.click();
}}
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
