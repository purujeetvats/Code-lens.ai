import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { Finding, ReviewResponse } from "../api/BackendClient";
import { AnnotationProvider } from "../providers/AnnotationProvider";
import { withNonce } from "./nonce";

export interface ReviewTarget {
  uri: vscode.Uri;
  code: string;
  language: string;
  lineOffset: number;
  label: string;
}

/** Singleton webview panel showing the findings of the last review. */
export class ReviewPanel {
  private static current: ReviewPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly annotations: AnnotationProvider;
  private readonly extensionPath: string;
  private readonly rerun: (target: ReviewTarget) => void;

  private target?: ReviewTarget;
  private findings: Finding[] = [];

  private constructor(
    extensionPath: string,
    annotations: AnnotationProvider,
    rerun: (target: ReviewTarget) => void
  ) {
    this.extensionPath = extensionPath;
    this.annotations = annotations;
    this.rerun = rerun;

    this.panel = vscode.window.createWebviewPanel(
      "codelens-ai.review",
      "CodeLens AI · Review",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.file(path.join(extensionPath, "webview"))],
      }
    );

    const htmlPath = path.join(extensionPath, "webview", "review.html");
    this.panel.webview.html = withNonce(fs.readFileSync(htmlPath, "utf-8"));

    this.panel.webview.onDidReceiveMessage((msg) => {
      if (msg.type === "openLine") {
        this.openLine(msg.line);
      } else if (msg.type === "dismiss") {
        this.dismiss(msg.index);
      } else if (msg.type === "reviewAgain" && this.target) {
        this.rerun(this.target);
      }
    });

    this.panel.onDidDispose(() => {
      if (this.target) {
        this.annotations.clear(this.target.uri);
      }
      ReviewPanel.current = undefined;
    });
  }

  static show(
    extensionPath: string,
    annotations: AnnotationProvider,
    rerun: (target: ReviewTarget) => void
  ): ReviewPanel {
    if (!ReviewPanel.current) {
      ReviewPanel.current = new ReviewPanel(extensionPath, annotations, rerun);
    } else {
      ReviewPanel.current.panel.reveal(vscode.ViewColumn.Beside, true);
    }
    return ReviewPanel.current;
  }

  setLoading(target: ReviewTarget) {
    this.target = target;
    this.findings = [];
    this.panel.title = `CodeLens AI · ${path.basename(target.uri.fsPath)}`;
    this.panel.webview.postMessage({ type: "loading", label: target.label });
  }

  setResults(result: ReviewResponse) {
    this.findings = result.findings;
    this.panel.webview.postMessage({
      type: "results",
      findings: result.findings,
      summary: result.summary,
      model: result.model,
      provider: result.provider,
      contextUsed: result.context_used,
      contextFiles: result.context_files ?? [],
      truncated: result.truncated,
      linesReviewed: result.lines_reviewed,
      cached: result.cached,
      fallbackUsed: result.fallback_used,
      warnings: result.warnings ?? [],
      injectionWarnings: result.injection_warnings ?? [],
      label: this.target?.label ?? "",
    });
  }

  setError(message: string) {
    this.panel.webview.postMessage({ type: "error", message });
  }

  private async openLine(line: number) {
    if (!this.target) {
      return;
    }
    const doc = await vscode.workspace.openTextDocument(this.target.uri);
    const editor = await vscode.window.showTextDocument(doc, {
      viewColumn: vscode.ViewColumn.One,
      preview: false,
    });
    const pos = new vscode.Position(Math.max(0, line - 1), 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  }

  private dismiss(index: number) {
    if (index < 0 || index >= this.findings.length || !this.target) {
      return;
    }
    this.findings.splice(index, 1);
    const doc = vscode.workspace.textDocuments.find(
      (d) => d.uri.toString() === this.target!.uri.toString()
    );
    this.annotations.show(this.target.uri, this.findings, doc);
    this.panel.webview.postMessage({ type: "findings", findings: this.findings });
  }
}
