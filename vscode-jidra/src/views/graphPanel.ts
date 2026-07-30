import * as vscode from 'vscode';
import * as path from 'path';
import { client } from '../client';

let currentPanel: vscode.WebviewPanel | undefined;

export async function openGraphPanel(
  ctx: vscode.ExtensionContext,
  focusMethodId?: string,
): Promise<void> {
  const visJsPath = path.join(ctx.extensionPath, 'media', 'vis-network.min.js');
  const visJsDir = vscode.Uri.file(path.dirname(visJsPath));

  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Beside);
  } else {
    currentPanel = vscode.window.createWebviewPanel(
      'jidraGraph',
      'JIDRA Graph',
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [visJsDir] },
    );
    currentPanel.onDidDispose(() => { currentPanel = undefined; });
    ctx.subscriptions.push(currentPanel);
  }

  currentPanel.webview.html = loadingHtml();

  const visUri = currentPanel.webview.asWebviewUri(vscode.Uri.file(visJsPath));

  try {
    if (focusMethodId) {
      const subgraph = await client.fetchSubgraph(focusMethodId, 2);
      currentPanel.webview.html = subgraphHtml(subgraph.nodes, subgraph.edges, focusMethodId, visUri.toString());
    } else {
      const html = await client.graphHtml();
      currentPanel.webview.html = html;
    }
  } catch (e: any) {
    currentPanel.webview.html = errorHtml(e?.message ?? String(e));
  }
}

function subgraphHtml(
  nodes: Array<{ id: string; label: string; color?: string; shape?: string }>,
  edges: Array<{ from: string; to: string }>,
  focusId: string,
  visUri: string,
): string {
  const nodesJson = JSON.stringify(nodes.map(n => ({
    id: n.id,
    label: n.label,
    color: n.id === focusId
      ? { background: '#f59e0b', border: '#d97706' }
      : { background: '#1e2d3d', border: '#38bdf8' },
    font: { color: '#e2e8f0', size: 13 },
    shape: n.shape ?? 'box',
    borderWidth: n.id === focusId ? 3 : 1,
  })));
  const edgesJson = JSON.stringify(edges.map(e => ({
    from: e.from, to: e.to,
    arrows: 'to',
    color: { color: '#475569' },
  })));

  return `<!DOCTYPE html><html>
<head>
<meta charset="utf-8"/>
<style>
  html,body,#graph{height:100%;margin:0;background:#0f1923;}
  #label{position:fixed;top:8px;left:8px;color:#94a3b8;font:12px monospace;pointer-events:none;}
</style>
<script src="${visUri}"></script>
</head>
<body>
<div id="label">JIDRA — call flow (depth 2)</div>
<div id="graph"></div>
<script>
  const nodes = new vis.DataSet(${nodesJson});
  const edges = new vis.DataSet(${edgesJson});
  const container = document.getElementById('graph');
  const network = new vis.Network(container, { nodes, edges }, {
    layout: { hierarchical: { direction: 'LR', sortMethod: 'directed', levelSeparation: 180 } },
    physics: false,
    interaction: { hover: true, tooltipDelay: 200 },
    edges: { smooth: { type: 'cubicBezier' } },
  });
  network.fit({ animation: true });
</script>
</body></html>`;
}

function loadingHtml(): string {
  return `<!DOCTYPE html><html><body style="background:#0f1923;color:#e2e8f0;font-family:monospace;padding:2rem;">
    <p>Loading...</p></body></html>`;
}

function errorHtml(msg: string): string {
  return `<!DOCTYPE html><html><body style="background:#0f1923;color:#f87171;font-family:monospace;padding:2rem;">
    <p>JIDRA graph unavailable: ${msg}</p>
    </body></html>`;
}
