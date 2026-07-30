import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import { getConfig } from './config';

let daemonProcess: cp.ChildProcess | undefined;
let _starting = false;
let _discoveredOutputPath = '';

export function getDiscoveredOutputPath(): string {
  return _discoveredOutputPath;
}

export function isDaemonStarting(): boolean {
  return _starting;
}

export async function ensureDaemon(ctx: vscode.ExtensionContext): Promise<void> {
  const serverUrl = getConfig().serverUrl;

  // Always discover path first — synchronous, so available to CodeLens immediately
  const graphPath = findGraphDb();
  if (graphPath) {
    _discoveredOutputPath = path.dirname(graphPath);
  }

  // Already alive?
  try {
    const res = await fetch(`${serverUrl}/api/health`);
    if (res.ok) return;
  } catch {}

  if (!graphPath) {
    vscode.window.showWarningMessage(
      'JIDRA: No .jidra/graph.db found in workspace. Run `jidra index` first.',
    );
    return;
  }

  _starting = true;
  vscode.window.setStatusBarMessage('JIDRA ⟳ starting...', 10_000);
  daemonProcess = cp.spawn('jidra', ['serve', '--graph', graphPath], {
    detached: false,
    stdio: 'ignore',
    env: { ...process.env },
  });

  daemonProcess.on('error', err => {
    _starting = false;
    vscode.window.showErrorMessage(`JIDRA: Failed to start server — ${err.message}`);
  });

  daemonProcess.on('exit', code => {
    _starting = false;
    daemonProcess = undefined;
    if (code !== 0 && code !== null) {
      vscode.window.showWarningMessage(`JIDRA: Server exited with code ${code}`);
    }
  });

  ctx.subscriptions.push({ dispose: killDaemon });

  // Wait up to 8s for server to be ready
  await waitForServer(serverUrl, 8000);
  _starting = false;
}

export function killDaemon(): void {
  if (daemonProcess) {
    daemonProcess.kill();
    daemonProcess = undefined;
  }
}

function findGraphDb(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders?.length) return undefined;

  for (const folder of folders) {
    const candidate = path.join(folder.uri.fsPath, '.jidra', 'graph.db');
    if (fs.existsSync(candidate)) return candidate;
  }
  return undefined;
}

async function waitForServer(serverUrl: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${serverUrl}/api/health`);
      if (res.ok) return;
    } catch {}
    await new Promise(r => setTimeout(r, 300));
  }
}
