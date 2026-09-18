import * as vscode from "vscode";
import { Finding } from "../api/BackendClient";

/**
 * Inline review annotations. Uses a DiagnosticCollection so findings get
 * squiggles in the editor and rows in the Problems panel for free.
 */
export class AnnotationProvider {
  private readonly collection: vscode.DiagnosticCollection;

  constructor() {
    this.collection = vscode.languages.createDiagnosticCollection("codelens-ai");
  }

  dispose() {
    this.collection.dispose();
  }

  clear(uri?: vscode.Uri) {
    if (uri) {
      this.collection.delete(uri);
    } else {
      this.collection.clear();
    }
  }

  show(uri: vscode.Uri, findings: Finding[], doc?: vscode.TextDocument) {
    const diagnostics = findings.map((f) => {
      const lineIndex = Math.max(0, f.line - 1);
      const range = doc
        ? doc.lineAt(Math.min(lineIndex, doc.lineCount - 1)).range
        : new vscode.Range(lineIndex, 0, lineIndex, 200);

      const text = f.suggestion
        ? `${f.message}\n→ ${f.suggestion}`
        : f.message;

      const diag = new vscode.Diagnostic(range, text, toSeverity(f.severity));
      diag.source = `CodeLens AI · ${f.category}`;
      return diag;
    });

    this.collection.set(uri, diagnostics);
  }
}

function toSeverity(severity: string): vscode.DiagnosticSeverity {
  switch (severity) {
    case "critical":
      return vscode.DiagnosticSeverity.Error;
    case "warning":
      return vscode.DiagnosticSeverity.Warning;
    default:
      return vscode.DiagnosticSeverity.Information;
  }
}
