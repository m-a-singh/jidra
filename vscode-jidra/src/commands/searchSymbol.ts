import * as vscode from 'vscode';
import { client } from '../client';

export async function searchSymbol(): Promise<void> {
  const query = await vscode.window.showInputBox({ prompt: 'JIDRA: Search symbol', placeHolder: 'method or class name' });
  if (!query) return;

  const data = await client.searchSymbols(query);
  const results = data.results ?? [];
  if (!results.length) {
    vscode.window.showInformationMessage('JIDRA: No results found');
    return;
  }

  const picks = results.map(r => ({
    label: r.label,
    description: r.file_path ? `${r.file_path}:${r.line}` : '',
    filePath: r.file_path,
    line: r.line,
  }));

  const pick = await vscode.window.showQuickPick(picks, { placeHolder: 'Select symbol to navigate' });
  if (!pick?.filePath) return;

  const uri = vscode.Uri.file(pick.filePath);
  const doc = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(doc);
  const pos = new vscode.Position(Math.max(0, (pick.line ?? 1) - 1), 0);
  editor.selection = new vscode.Selection(pos, pos);
  editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
}
