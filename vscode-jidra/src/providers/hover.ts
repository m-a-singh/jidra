import * as vscode from 'vscode';
import { client } from '../client';

const SUPPORTED = ['java', 'python', 'csharp', 'go', 'typescript', 'typescriptreact', 'javascript', 'javascriptreact'];

export class JidraHoverProvider implements vscode.HoverProvider {
  async provideHover(
    document: vscode.TextDocument,
    position: vscode.Position,
  ): Promise<vscode.Hover | null> {
    if (!SUPPORTED.includes(document.languageId)) return null;

    let method = null;
    try {
      method = await client.methodAt(document.uri.fsPath, position.line + 1);
    } catch {
      return null;
    }
    if (!method) return null;

    const md = new vscode.MarkdownString();
    md.isTrusted = true;
    md.appendMarkdown(`**JIDRA** — \`${method.class_full_name}\`\n\n`);
    md.appendCodeblock(method.signature, 'java');
    const args = encodeURIComponent(JSON.stringify([method]));
    md.appendMarkdown(`\n[Find Callers](command:jidra.findCallers?${args}) · [Show Flow](command:jidra.showFlow?${args}) · [Blast Radius](command:jidra.blastRadius?${args})`);

    const range = new vscode.Range(
      new vscode.Position(method.start_line - 1, 0),
      new vscode.Position(method.end_line - 1, 0),
    );
    return new vscode.Hover(md, range);
  }
}
