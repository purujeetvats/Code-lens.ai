import * as vscode from "vscode";
import * as path from "path";
import { request } from "../api/BackendClient";

// Kept in sync with the grammars the backend registers.
const INDEXABLE = new Set([
  ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
  ".go", ".rs", ".java",
  ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".c",
  ".cs", ".rb", ".php",
]);

const DEBOUNCE_MS = 1500;

/**
 * Re-index files as they are saved, so search results don't go stale.
 *
 * Saves are batched: editing three files in quick succession, or a
 * format-on-save that fires twice, costs one indexing pass rather than several.
 */
export class SaveWatcher {
  private readonly pending = new Set<string>();
  private timer: NodeJS.Timeout | undefined;
  private readonly disposable: vscode.Disposable;

  constructor(private readonly onError: (message: string) => void) {
    this.disposable = vscode.workspace.onDidSaveTextDocument((doc) =>
      this.queue(doc)
    );
  }

  dispose() {
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.disposable.dispose();
  }

  private queue(doc: vscode.TextDocument) {
    const enabled = vscode.workspace
      .getConfiguration("codelens-ai")
      .get<boolean>("indexOnSave", true);
    if (!enabled || doc.uri.scheme !== "file") {
      return;
    }
    if (!INDEXABLE.has(path.extname(doc.fileName).toLowerCase())) {
      return;
    }
    // Only files inside the indexed workspace.
    if (!vscode.workspace.getWorkspaceFolder(doc.uri)) {
      return;
    }

    this.pending.add(doc.fileName);
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => this.flush(), DEBOUNCE_MS);
  }

  private async flush() {
    const files = [...this.pending];
    this.pending.clear();
    this.timer = undefined;
    if (files.length === 0) {
      return;
    }

    const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(files[0]));
    if (!folder) {
      return;
    }

    try {
      await request(
        "POST",
        "/index",
        { workspace_path: folder.uri.fsPath, files },
        15000
      );
    } catch (err: any) {
      // A failed background re-index is not worth a modal; the next full index
      // will catch up. Surface it quietly.
      this.onError(`Index on save failed: ${err.message}`);
    }
  }
}
