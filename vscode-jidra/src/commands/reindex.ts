import * as vscode from 'vscode';
import { client } from '../client';

export async function reindex(changedFiles?: string[]): Promise<void> {
  try {
    await client.reindex(changedFiles);
    if (!changedFiles) {
      vscode.window.showInformationMessage('JIDRA: Reindex triggered');
    }
  } catch {
    vscode.window.showErrorMessage('JIDRA: Reindex failed — is the server running?');
  }
}
