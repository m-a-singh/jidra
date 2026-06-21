"""
Graph visualization for jidra call graphs.
Generates interactive HTML with multiple export formats.
"""

from __future__ import annotations

import json
from pathlib import Path

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
        print(f"  • Building visualization data from {len(graph.methods)} methods and {len(graph.resolved_call_edges)} edges", flush=True)

    # Build method/class lookups
    methods_by_id = {m.id: m for m in graph.methods}
    classes_by_id = {c.id: c for c in graph.classes}
    classes_by_fullname = {c.full_name: c for c in graph.classes}

    # Determine which methods to include
    filtered_method_ids = set()

    if method_selector:
        # BFS from focused method up to depth
        # Find the method by ClassName#methodName
        root_methods = [
            m for m in graph.methods
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
                    if edge.caller_method_id == method_id and edge.callee_method_id not in visited:
                        queue.append((edge.callee_method_id, current_depth + 1))
                    elif edge.callee_method_id == method_id and edge.caller_method_id not in visited:
                        queue.append((edge.caller_method_id, current_depth + 1))
        else:
            # Method not found, include all
            filtered_method_ids = {m.id for m in graph.methods}
    elif package_filter:
        # Filter by package if specified
        filtered_methods = [m for m in graph.methods if m.class_full_name.startswith(package_filter)]
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
        if edge.caller_method_id not in filtered_method_ids or edge.callee_method_id not in filtered_method_ids:
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
        print(f"  • Generated {len(nodes)} nodes and {len(edges)} edges for visualization", flush=True)

    return result


def render_interactive_html(graph_data: dict) -> str:
    """
    Generate interactive HTML with Vis.js graph visualization.

    Returns:
        HTML string
    """
    nodes = graph_data["nodes"]
    edges = graph_data["edges"]

    # Convert to Vis.js format
    vis_nodes = [
        {
            "id": n["id"],
            "label": n["label"],
            "title": n["title"],
            "group": n["group"],
            "color": "green" if n["confirmed"] else "red",
        }
        for n in nodes
    ]

    vis_edges = [{"from": e["from"], "to": e["to"], "arrows": "to"} for e in edges]

    nodes_json = json.dumps(vis_nodes)
    edges_json = json.dumps(vis_edges)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>jidra Graph Visualization</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css" rel="stylesheet" type="text/css" />
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .container {{ display: flex; flex-direction: column; height: 100vh; }}
        .header {{ padding: 16px; background: #f5f5f5; border-bottom: 1px solid #ddd; }}
        .tabs {{ display: flex; border-bottom: 2px solid #ddd; background: #fafafa; }}
        .tab {{ padding: 12px 20px; cursor: pointer; border: none; background: none; font-size: 14px; }}
        .tab.active {{ border-bottom: 2px solid #0066cc; color: #0066cc; margin-bottom: -2px; }}
        .content {{ flex: 1; overflow: auto; }}
        .tab-content {{ display: none; height: 100%; }}
        .tab-content.active {{ display: block; }}
        #network {{ width: 100%; height: 100%; }}
        pre {{ padding: 16px; background: #f5f5f5; overflow: auto; }}
        button {{ padding: 8px 16px; margin: 8px; background: #0066cc; color: white; border: none; border-radius: 4px; cursor: pointer; }}
        button:hover {{ background: #0052a3; }}
        .stats {{ padding: 8px 16px; background: #f0f0f0; border-bottom: 1px solid #ddd; font-size: 13px; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>jidra Graph Visualization</h2>
            <div class="stats">Nodes: {graph_data['metadata']['total_nodes']} | Edges: {graph_data['metadata']['total_edges']}</div>
        </div>
        <div class="tabs">
            <button class="tab active" onclick="switchTab('interactive', this)">Interactive Graph</button>
            <button class="tab" onclick="switchTab('graphviz', this)">Graphviz DOT</button>
            <button class="tab" onclick="switchTab('json', this)">JSON Export</button>
        </div>
        <div class="content">
            <div id="interactive" class="tab-content active">
                <div id="network"></div>
            </div>
            <div id="graphviz" class="tab-content">
                <button onclick="copyToClipboard('dotContent')">Copy DOT</button>
                <pre id="dotContent">{_generate_graphviz_dot(graph_data)}</pre>
            </div>
            <div id="json" class="tab-content">
                <button onclick="downloadJSON()">Download JSON</button>
                <pre id="jsonContent">{json.dumps(graph_data, indent=2)}</pre>
            </div>
        </div>
    </div>

    <script type="text/javascript">
        const nodes = new vis.DataSet({nodes_json});
        const edges = new vis.DataSet({edges_json});
        const container = document.getElementById('network');
        const data = {{nodes: nodes, edges: edges}};
        const options = {{
            physics: {{enabled: true, barnesHut: {{gravitationalConstant: -26000}}}},
            nodes: {{
                shape: 'box',
                margin: 10,
                widthConstraint: {{max: 200}}
            }},
            edges: {{
                arrows: {{to: {{enabled: true}}}}
            }},
            groups: {{
                service: {{color: '#4CAF50'}},
                controller: {{color: '#2196F3'}},
                repository: {{color: '#FF9800'}},
                component: {{color: '#9C27B0'}},
                unknown: {{color: '#999'}}
            }}
        }};
        const network = new vis.Network(container, data, options);

        function switchTab(tab, btn) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
            document.getElementById(tab).classList.add('active');
            btn.classList.add('active');
            if (tab === 'interactive') {{ network.redraw(); }}
        }}

        function copyToClipboard(elementId) {{
            const text = document.getElementById(elementId).textContent;
            navigator.clipboard.writeText(text).then(() => {{
                alert('Copied to clipboard');
            }});
        }}

        function downloadJSON() {{
            const json = document.getElementById('jsonContent').textContent;
            const blob = new Blob([json], {{type: 'application/json'}});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'graph.json';
            a.click();
        }}
    </script>
</body>
</html>"""

    return html


def _generate_graphviz_dot(graph_data: dict) -> str:
    """Generate Graphviz DOT format."""
    nodes = graph_data["nodes"]
    edges = graph_data["edges"]

    lines = ["digraph {", '  rankdir=LR;', "  node [shape=box];"]

    # Add nodes with colors
    for node in nodes:
        color = "green" if node["confirmed"] else "red"
        lines.append(f'  "{node["id"]}" [label="{node["label"]}", fillcolor={color}, style=filled];')

    # Add edges
    for edge in edges:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}";')

    lines.append("}")
    return "\n".join(lines)


def render_json_export(graph_data: dict) -> str:
    """Render graph data as prettified JSON."""
    return json.dumps(graph_data, indent=2)
