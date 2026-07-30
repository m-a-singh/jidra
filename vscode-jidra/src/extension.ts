import * as vscode from 'vscode';
import { initStatusBar } from './statusBar';
import { searchSymbol } from './commands/searchSymbol';
import { reindex } from './commands/reindex';
import { makeFindCallers } from './commands/findCallers';
import { makeShowFlow } from './commands/showFlow';
import { makeBlastRadius } from './commands/blastRadius';
import { openGraphPanel } from './views/graphPanel';
import { JidraCallTreeProvider } from './views/callTree';
import { JidraCodeLensProvider } from './providers/codelens';
import { JidraHoverProvider } from './providers/hover';
import { client } from './client';
import { getConfig } from './config';
import { ensureDaemon } from './daemon';

const JIDRA_LANGUAGES = ['java', 'python', 'csharp', 'go', 'typescript', 'typescriptreact', 'javascript', 'javascriptreact'];
const LANG_SELECTOR = JIDRA_LANGUAGES.map(language => ({ language }));

export function activate(ctx: vscode.ExtensionContext): void {
  ensureDaemon(ctx);
  initStatusBar(ctx);

  // Call tree sidebar
  const callTree = new JidraCallTreeProvider();
  ctx.subscriptions.push(
    vscode.window.registerTreeDataProvider('jidraCallers', callTree),
  );

  // CodeLens
  const codeLens = new JidraCodeLensProvider();
  ctx.subscriptions.push(
    vscode.languages.registerCodeLensProvider(LANG_SELECTOR, codeLens),
  );

  // Hover
  ctx.subscriptions.push(
    vscode.languages.registerHoverProvider(LANG_SELECTOR, new JidraHoverProvider()),
  );

  // Commands
  ctx.subscriptions.push(
    vscode.commands.registerCommand('jidra.search', searchSymbol),

    vscode.commands.registerCommand('jidra.reindex', () => reindex()),

vscode.commands.registerCommand('jidra.findCallers', makeFindCallers(callTree)),

    vscode.commands.registerCommand('jidra.showFlow', makeShowFlow(ctx)),

    vscode.commands.registerCommand('jidra.blastRadius', makeBlastRadius(callTree)),

    vscode.commands.registerCommand('jidra.openGraph', () => openGraphPanel(ctx)),

    // Internal: navigate to file:line from tree click
    vscode.commands.registerCommand('jidra._navigateTo', async (filePath: string, line: number) => {
      const uri = vscode.Uri.file(filePath);
      const doc = await vscode.workspace.openTextDocument(uri);
      const editor = await vscode.window.showTextDocument(doc);
      const pos = new vscode.Position(Math.max(0, line - 1), 0);
      editor.selection = new vscode.Selection(pos, pos);
      editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
    }),
  );

  // Auto-reindex on save
  if (getConfig().autoReindex) {
    ctx.subscriptions.push(
      vscode.workspace.onDidSaveTextDocument(doc => {
        if (JIDRA_LANGUAGES.includes(doc.languageId)) {
          reindex([doc.uri.fsPath]);
          codeLens.refresh();
        }
      }),
    );
  }

  // Refresh CodeLens on config change
  ctx.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(e => {
      if (e.affectsConfiguration('jidra')) {
        codeLens.refresh();
      }
    }),
  );
}

export function deactivate(): void {}
