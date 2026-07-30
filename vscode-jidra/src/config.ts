import * as vscode from 'vscode';
import { getDiscoveredOutputPath } from './daemon';

export function getConfig() {
  const cfg = vscode.workspace.getConfiguration('jidra');
  return {
    serverUrl: cfg.get<string>('serverUrl', 'http://127.0.0.1:7474'),
    graphPath: cfg.get<string>('graphPath', ''),
    codebasePath: cfg.get<string>('codebasePath', ''),
    autoReindex: cfg.get<boolean>('autoReindex', true),
    codeLensEnabled: cfg.get<boolean>('codeLensEnabled', true),
  };
}

export function getRepoAndOutput(): { repoPath: string; outputPath: string } {
  const cfg = getConfig();
  const repoPath = cfg.codebasePath || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '';
  const outputPath = cfg.graphPath || getDiscoveredOutputPath();
  return { repoPath, outputPath };
}
