import * as vscode from 'vscode';
import { client } from './client';

let statusBarItem: vscode.StatusBarItem;
let pollTimer: NodeJS.Timeout | undefined;

export function initStatusBar(ctx: vscode.ExtensionContext): void {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  statusBarItem.command = 'jidra.reindex';
  statusBarItem.show();
  ctx.subscriptions.push(statusBarItem);
  updateStatus();
  pollTimer = setInterval(updateStatus, 30_000);
  ctx.subscriptions.push({ dispose: () => clearInterval(pollTimer) });
}

async function updateStatus(): Promise<void> {
  try {
    const s = await client.daemonStatus();
    if (s.status === 'offline' || s.status === 'error') {
      statusBarItem.text = 'JIDRA $(circle-slash) offline';
      statusBarItem.tooltip = 'JIDRA server offline — click to retry reindex';
      statusBarItem.backgroundColor = undefined;
    } else if (s.stale) {
      statusBarItem.text = 'JIDRA $(warning) stale';
      statusBarItem.tooltip = 'Graph is behind codebase — click to reindex';
      statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
      const count = s.method_count ? ` ${(s.method_count / 1000).toFixed(1)}k methods` : '';
      statusBarItem.text = `JIDRA $(check)${count}`;
      statusBarItem.tooltip = 'JIDRA graph up to date — click to reindex';
      statusBarItem.backgroundColor = undefined;
    }
  } catch {
    statusBarItem.text = 'JIDRA $(circle-slash) offline';
    statusBarItem.backgroundColor = undefined;
  }
}
