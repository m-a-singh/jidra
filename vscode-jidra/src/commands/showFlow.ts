import * as vscode from 'vscode';
import { client, MethodInfo } from '../client';

export function makeShowFlow(ctx: vscode.ExtensionContext) {
  return async (methodArg?: MethodInfo): Promise<void> => {
    let method = methodArg;
    if (!method) {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      try {
        method = await client.methodAt(editor.document.uri.fsPath, editor.selection.active.line + 1) ?? undefined;
      } catch {}
    }
    if (!method) { vscode.window.showWarningMessage('JIDRA: No method found at cursor'); return; }
    const { openGraphPanel } = await import('../views/graphPanel');
    await openGraphPanel(ctx, method.id);
  };
}
