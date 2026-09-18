import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { request } from "../api/BackendClient";
import { withNonce } from "./nonce";

export class SearchSidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "codelens-ai.searchView";
  private _view?: vscode.WebviewView;

  constructor(private readonly _extensionPath: string) {}

  resolveWebviewView(webviewView: vscode.WebviewView) {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.file(path.join(this._extensionPath, "webview")),
      ],
    };

    const htmlPath = path.join(this._extensionPath, "webview", "search.html");
    webviewView.webview.html = withNonce(fs.readFileSync(htmlPath, "utf-8"));

    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg.type === "search") {
        this._handleSearch(msg.query, msg.language);
      } else if (msg.type === "openFile") {
        this._openFile(msg.file, msg.line);
      }
    });
  }

  private _handleSearch(query: string, language: string | null) {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      this._view?.webview.postMessage({
        type: "error",
        message: "No workspace folder open",
      });
      return;
    }

    request(
      "POST",
      "/search",
      {
        query,
        workspace_path: folders[0].uri.fsPath,
        limit: 15,
        language,
      },
      30000
    )
      .then((parsed) => {
        this._view?.webview.postMessage({
          type: "searchResults",
          results: parsed.results || [],
        });
      })
      .catch((err: Error) => {
        this._view?.webview.postMessage({
          type: "error",
          message: err.message,
        });
      });
  }

  private async _openFile(filePath: string, line: number) {
    try {
      const uri = vscode.Uri.file(filePath);
      const doc = await vscode.workspace.openTextDocument(uri);
      const editor = await vscode.window.showTextDocument(doc, {
        preview: true,
        preserveFocus: false,
      });
      const pos = new vscode.Position(Math.max(0, line - 1), 0);
      editor.selection = new vscode.Selection(pos, pos);
      editor.revealRange(
        new vscode.Range(pos, pos),
        vscode.TextEditorRevealType.InCenter
      );
    } catch {
      vscode.window.showErrorMessage(`Could not open file: ${filePath}`);
    }
  }
}
