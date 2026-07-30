import * as vscode from 'vscode';
import { client } from '../client';

interface CallerNode {
  methodId: string;
  label: string;
  filePath: string;
  line: number;
  children?: CallerNode[];
}

export class JidraCallTreeProvider implements vscode.TreeDataProvider<CallerNode> {
  private _onDidChangeTreeData = new vscode.EventEmitter<CallerNode | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private rootNodes: CallerNode[] = [];

  async showCallers(methodId: string, methodName: string): Promise<void> {
    const result: any = await client.findCallers(methodId, 2);
    this.rootNodes = parseCallerResult(result, methodName);
    this._onDidChangeTreeData.fire();
    await vscode.commands.executeCommand('jidraCallers.focus');
  }

  getTreeItem(node: CallerNode): vscode.TreeItem {
    const item = new vscode.TreeItem(
      node.label,
      node.children?.length
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None,
    );
    if (node.filePath) {
      item.description = `${node.filePath.split('/').pop()}:${node.line}`;
      item.command = {
        command: 'jidra._navigateTo',
        title: 'Go to',
        arguments: [node.filePath, node.line],
      };
      item.tooltip = `${node.filePath}:${node.line}`;
    }
    item.iconPath = new vscode.ThemeIcon('references');
    return item;
  }

  getChildren(node?: CallerNode): CallerNode[] {
    if (!node) return this.rootNodes;
    return node.children ?? [];
  }
}

function parseCallerResult(result: any, fallbackLabel: string): CallerNode[] {
  const nodes: Array<{ id: string; label: string; file_path: string; line: number }> = result?.nodes ?? [];
  if (!nodes.length) {
    return [{ methodId: fallbackLabel, label: `${fallbackLabel} (no callers found)`, filePath: '', line: 0 }];
  }
  return nodes.map(n => ({
    methodId: n.id,
    label: n.label,
    filePath: n.file_path ?? '',
    line: n.line ?? 0,
  }));
}
