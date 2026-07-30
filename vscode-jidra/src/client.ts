import * as vscode from 'vscode';
import * as path from 'path';
import { getConfig, getRepoAndOutput } from './config';

// Extractors confirmed to store absolute paths in the graph DB
// Java: confirmed absolute. Others (Kotlin/Scala/C#/Go/Python/TS): relative.
const ABSOLUTE_PATH_EXTS = new Set(['.java']);

function resolveFilePath(filePath: string, repoPath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (ABSOLUTE_PATH_EXTS.has(ext)) return filePath;
  // JS/TS family stores repo-relative paths
  return repoPath && filePath.startsWith(repoPath + '/')
    ? filePath.slice(repoPath.length + 1)
    : filePath;
}

export const log = vscode.window.createOutputChannel('JIDRA Debug');

export interface MethodInfo {
  id: string;
  method_name: string;
  signature: string;
  class_full_name: string;
  start_line: number;
  end_line: number;
}

export class JidraClient {
  private get baseUrl(): string {
    return getConfig().serverUrl;
  }

  async methodsInFile(filePath: string): Promise<MethodInfo[]> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      file_path: resolveFilePath(filePath, repoPath),
      repo_path: repoPath,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const res = await fetch(`${this.baseUrl}/api/graph/methods-in-file?${params}`);
    if (!res.ok) return [];
    return res.json();
  }

  async methodAt(filePath: string, line: number): Promise<MethodInfo | null> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      file_path: resolveFilePath(filePath, repoPath),
      line: String(line),
      repo_path: repoPath,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const url = `${this.baseUrl}/api/graph/method-at?${params}`;
    const res = await fetch(url);
    const body = await res.text();
    log.appendLine(`[methodAt] ${res.status} file=${filePath.split('/').pop()} line=${line} body=${body.slice(0, 200)}`);
    if (!res.ok) return null;
    return body === 'null' ? null : JSON.parse(body);
  }

  async searchSymbols(q: string): Promise<{ results: Array<{ id: string; label: string; file_path: string; line: number }> }> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      repo_path: repoPath,
      q,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const res = await fetch(`${this.baseUrl}/api/graph/search?${params}`);
    if (!res.ok) return { results: [] };
    return res.json();
  }

  async findCallers(methodId: string, depth = 2): Promise<{ nodes: Array<{ id: string; label: string; file_path: string; line: number }> }> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      method_id: methodId,
      depth: String(depth),
      repo_path: repoPath,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const url = `${this.baseUrl}/api/graph/callers?${params}`;
    const res = await fetch(url);
    log.appendLine(`[findCallers] ${res.status} ${url}`);
    if (!res.ok) return { nodes: [] };
    return res.json();
  }

  async fetchSubgraph(methodId: string, depth = 2): Promise<{ nodes: any[]; edges: any[] }> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      method_id: methodId,
      depth: String(depth),
      repo_path: repoPath,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const res = await fetch(`${this.baseUrl}/api/graph/subgraph?${params}`);
    if (!res.ok) return { nodes: [], edges: [] };
    return res.json();
  }

  async daemonStatus(): Promise<{ status: string; method_count?: number; stale?: boolean }> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      repo_path: repoPath,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const res = await fetch(`${this.baseUrl}/api/index/daemon/status?${params}`);
    if (!res.ok) return { status: 'offline' };
    return res.json();
  }

  async reindex(changedFiles?: string[]): Promise<void> {
    const { repoPath, outputPath } = getRepoAndOutput();
    await fetch(`${this.baseUrl}/api/index/reindex`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_path: repoPath,
        ...(outputPath ? { output_path: outputPath } : {}),
        ...(changedFiles ? { changed_files: changedFiles } : {}),
      }),
    });
  }

  async graphHtml(): Promise<string> {
    const { repoPath, outputPath } = getRepoAndOutput();
    const params = new URLSearchParams({
      repo_path: repoPath,
      ...(outputPath ? { output_path: outputPath } : {}),
    });
    const res = await fetch(`${this.baseUrl}/api/graph/html?${params}`);
    if (!res.ok) throw new Error('Graph HTML not available');
    return res.text();
  }
}

export const client = new JidraClient();
