import * as vscode from 'vscode';
import { client, MethodInfo } from '../client';
import { JidraCallTreeProvider } from '../views/callTree';

export function makeFindCallers(tree: JidraCallTreeProvider) {
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
    await tree.showCallers(method.id, method.method_name);
  };
}
