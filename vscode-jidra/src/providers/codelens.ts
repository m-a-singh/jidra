import * as vscode from 'vscode';
import { client, MethodInfo } from '../client';
import { getConfig } from '../config';

const SUPPORTED = ['java', 'python', 'csharp', 'go', 'typescript', 'typescriptreact', 'javascript', 'javascriptreact'];

export class JidraCodeLensProvider implements vscode.CodeLensProvider {
  private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

  refresh(): void {
    this._onDidChangeCodeLenses.fire();
  }

  async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    if (!getConfig().codeLensEnabled) return [];
    if (!SUPPORTED.includes(document.languageId)) return [];

    let methods: MethodInfo[] = [];
    try {
      methods = await client.methodsInFile(document.uri.fsPath);
    } catch {
      return [];
    }

    const lenses: vscode.CodeLens[] = [];
    for (const method of methods) {
      const range = new vscode.Range(
        new vscode.Position(method.start_line - 1, 0),
        new vscode.Position(method.start_line - 1, 0),
      );
      lenses.push(new vscode.CodeLens(range, {
        title: '$(references) Find Callers',
        command: 'jidra.findCallers',
        arguments: [method],
      }));
      lenses.push(new vscode.CodeLens(range, {
        title: '$(type-hierarchy) Show Flow',
        command: 'jidra.showFlow',
        arguments: [method],
      }));
    }
    return lenses;
  }
}
