import * as vscode from "vscode";
import { ChildProcess, spawn } from "child_process";
import * as path from "path";
import * as http from "http";
import * as fs from "fs";
import { SearchSidebarProvider } from "./views/SearchSidebar";
import { ReviewPanel, ReviewTarget } from "./views/ReviewPanel";
import { AnnotationProvider } from "./providers/AnnotationProvider";
import { SaveWatcher } from "./providers/SaveWatcher";
import {
  getPort,
  getAuthToken,
  initAuthToken,
  request as backendRequest,
  reviewCode,
  clearReviewCache,
  listOllamaModels,
} from "./api/BackendClient";
import { SecretStore, PROVIDER_LABELS, CloudProvider } from "./config/secrets";
import {
  buildChain,
  chooseCloudContext,
  revokeCloudConsent,
  selectModel,
} from "./config/settings";

let backend: ChildProcess | undefined;
let statusBarItem: vscode.StatusBarItem;
let annotations: AnnotationProvider;
let extensionPath: string;
let secrets: SecretStore;
let extContext: vscode.ExtensionContext;

export function activate(context: vscode.ExtensionContext) {
  extensionPath = context.extensionPath;
  extContext = context;
  initAuthToken();
  secrets = new SecretStore(context.secrets);
  annotations = new AnnotationProvider();
  context.subscriptions.push(annotations);

  const output = vscode.window.createOutputChannel("CodeLens AI");
  context.subscriptions.push(output);
  context.subscriptions.push(
    new SaveWatcher((message) => output.appendLine(message))
  );

  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBarItem.text = "$(loading~spin) CodeLens AI";
  statusBarItem.tooltip = "CodeLens AI — starting backend...";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // Register sidebar
  const sidebarProvider = new SearchSidebarProvider(context.extensionPath);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      SearchSidebarProvider.viewType,
      sidebarProvider
    )
  );

  startBackend(context);

  context.subscriptions.push(
    vscode.commands.registerCommand("codelens-ai.indexWorkspace", () => {
      indexWorkspace();
    }),
    vscode.commands.registerCommand("codelens-ai.search", () => {
      vscode.commands.executeCommand("codelens-ai.searchView.focus");
    }),
    vscode.commands.registerCommand("codelens-ai.reviewFile", () => {
      startReview(false);
    }),
    vscode.commands.registerCommand("codelens-ai.reviewSelection", () => {
      startReview(true);
    }),
    vscode.commands.registerCommand("codelens-ai.clearAnnotations", () => {
      annotations.clear();
    }),
    vscode.commands.registerCommand("codelens-ai.setApiKey", async () => {
      await secrets.promptAndStore();
    }),
    vscode.commands.registerCommand("codelens-ai.clearApiKeys", async () => {
      const confirm = await vscode.window.showWarningMessage(
        "Delete stored CodeLens AI API keys?",
        { modal: true, detail: "Removes the OpenAI and Anthropic keys from your OS keychain." },
        "Delete"
      );
      if (confirm !== "Delete") {
        return;
      }
      for (const p of ["openai", "anthropic"] as CloudProvider[]) {
        await secrets.delete(p);
      }
      await revokeCloudConsent(context);
      vscode.window.showInformationMessage("CodeLens AI: API keys deleted.");
    }),
    vscode.commands.registerCommand("codelens-ai.selectProvider", async () => {
      const picked = await vscode.window.showQuickPick(
        (["ollama", "openai", "anthropic"] as const).map((value) => ({
          label: PROVIDER_LABELS[value],
          description: value === "ollama" ? "local, private, free" : "cloud, needs an API key",
          value,
        })),
        { placeHolder: "Provider for code review" }
      );
      if (!picked) {
        return;
      }
      await vscode.workspace
        .getConfiguration("codelens-ai")
        .update("llmProvider", picked.value, vscode.ConfigurationTarget.Global);
      vscode.window.showInformationMessage(
        `CodeLens AI: review provider set to ${picked.label}.`
      );
    }),
    vscode.commands.registerCommand("codelens-ai.selectModel", async () => {
      await selectModel(listOllamaModels);
    }),
    vscode.commands.registerCommand("codelens-ai.chooseCloudContext", async () => {
      await chooseCloudContext(context);
    }),
    vscode.commands.registerCommand("codelens-ai.clearReviewCache", async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders?.length) {
        return;
      }
      try {
        const res = await clearReviewCache(folders[0].uri.fsPath);
        vscode.window.showInformationMessage(
          `CodeLens AI: cleared ${res.cleared} cached review(s).`
        );
      } catch (err: any) {
        vscode.window.showErrorMessage(`CodeLens AI: ${err.message}`);
      }
    })
  );
}

export function deactivate() {
  if (backend) {
    backend.kill();
    backend = undefined;
  }
}

/**
 * Locate the Python backend.
 *
 * Packaged as a .vsix the backend is bundled inside the extension; running from
 * a clone of the repo it sits beside it. Check both rather than assuming.
 */
function resolveBackendDir(extensionPath: string): string | undefined {
  const candidates = [
    path.join(extensionPath, "backend"),
    path.join(extensionPath, "..", "backend"),
  ];
  return candidates.find((dir) => fs.existsSync(path.join(dir, "app", "main.py")));
}

function startBackend(context: vscode.ExtensionContext) {
  const port = getPort();
  const outputChannel = vscode.window.createOutputChannel("CodeLens AI");

  const backendDir = resolveBackendDir(context.extensionPath);
  if (!backendDir) {
    const msg =
      "CodeLens AI: could not find the Python backend. Reinstall the extension, " +
      "or run it from a clone of the repository.";
    outputChannel.appendLine(msg);
    setStatus("error", "Backend files missing");
    vscode.window.showErrorMessage(msg);
    return;
  }

  outputChannel.appendLine(`Backend dir: ${backendDir}`);
  outputChannel.appendLine(`Starting backend on port ${port}...`);

  backend = spawn(
    "python",
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: backendDir,
      stdio: "pipe",
      // Env, never argv — another local process can read a command line.
      env: { ...process.env, CODELENS_TOKEN: initAuthToken() },
    }
  );

  backend.stdout?.on("data", (data: Buffer) => {
    outputChannel.appendLine(data.toString().trim());
  });

  backend.stderr?.on("data", (data: Buffer) => {
    const msg = data.toString();
    outputChannel.appendLine(msg.trim());
    if (msg.includes("Application startup complete")) {
      pollHealth(port);
    }
  });

  backend.on("error", (err: NodeJS.ErrnoException) => {
    outputChannel.appendLine(`ERROR: ${err.message}`);
    if (err.code === "ENOENT") {
      setStatus("error", "Python not found");
      vscode.window
        .showErrorMessage(
          "CodeLens AI: `python` was not found on your PATH. Install Python 3.11+ to use this extension.",
          "Open install guide"
        )
        .then((choice) => {
          if (choice === "Open install guide") {
            vscode.env.openExternal(vscode.Uri.parse("https://www.python.org/downloads/"));
          }
        });
    } else {
      setStatus("error", `Failed to start backend: ${err.message}`);
    }
  });

  backend.on("exit", (code) => {
    outputChannel.appendLine(`Backend exited with code ${code}`);
    if (code !== null && code !== 0) {
      setStatus("error", `Backend exited with code ${code}`);
      vscode.window
        .showErrorMessage(
          `CodeLens AI: the backend exited unexpectedly (code ${code}). ` +
            "Dependencies may be missing.",
          "Show log"
        )
        .then((choice) => {
          if (choice === "Show log") {
            outputChannel.show();
          }
        });
    }
  });

  // Poll regardless — stderr detection is a fast path, this is the fallback
  setTimeout(() => pollHealth(port), 3000);
}

function pollHealth(port: number, retries = 10) {
  checkHealth(port)
    .then(() => {
      setStatus("connected", "Backend connected");
    })
    .catch(() => {
      if (retries > 0) {
        setTimeout(() => pollHealth(port, retries - 1), 1500);
      } else {
        setStatus("error", "Backend failed to start — check Python installation");
      }
    });
}

function checkHealth(port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const req = http.get(
      {
        hostname: "127.0.0.1",
        port,
        path: "/status",
        headers: { Authorization: `Bearer ${getAuthToken()}` },
      },
      (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else {
          reject(new Error(`Status ${res.statusCode}`));
        }
        res.resume();
      }
    );
    req.on("error", reject);
    req.setTimeout(2000, () => {
      req.destroy();
      reject(new Error("timeout"));
    });
  });
}

async function indexWorkspace() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showWarningMessage("CodeLens AI: No workspace folder open");
    return;
  }
  const workspacePath = folders[0].uri.fsPath;

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "CodeLens AI: indexing workspace",
      cancellable: true,
    },
    async (progress, token) => {
      let res: any;
      try {
        res = await backendRequest("POST", "/index", {
          workspace_path: workspacePath,
          force: false,
        });
      } catch (err: any) {
        setStatus("error", `Indexing failed: ${err.message}`);
        vscode.window.showErrorMessage(`CodeLens AI: ${err.message}`);
        return;
      }

      if (typeof res.status === "string" && res.status.startsWith("error")) {
        setStatus("error", res.status);
        vscode.window.showErrorMessage(`CodeLens AI: ${res.status}`);
        return;
      }

      const taskId = res.task_id;
      let lastIndexed = 0;

      await new Promise<void>((resolve) => {
        const poll = setInterval(async () => {
          if (token.isCancellationRequested) {
            clearInterval(poll);
            setStatus("connected", "Indexing cancelled");
            resolve();
            return;
          }

          let status: any;
          try {
            status = await backendRequest("GET", `/index/status/${taskId}`);
          } catch {
            clearInterval(poll);
            setStatus("error", "Lost connection during indexing");
            vscode.window.showErrorMessage(
              "CodeLens AI: lost connection to the backend during indexing."
            );
            resolve();
            return;
          }

          if (status.status === "complete") {
            clearInterval(poll);
            setStatus("connected", `Indexed ${status.indexed} files`);
            vscode.window.showInformationMessage(
              `CodeLens AI: indexed ${status.indexed} file(s)` +
                (status.errors ? `, ${status.errors} error(s)` : "")
            );
            resolve();
          } else if (String(status.status).startsWith("error")) {
            clearInterval(poll);
            setStatus("error", status.status);
            vscode.window.showErrorMessage(`CodeLens AI: ${status.status}`);
            resolve();
          } else {
            const total = status.total_files || 0;
            const done = status.indexed || 0;
            // withProgress increments are relative, so report the delta.
            if (total > 0) {
              progress.report({
                increment: ((done - lastIndexed) / total) * 100,
                message: `${done} of ${total} files`,
              });
            } else {
              progress.report({ message: "scanning for changes…" });
            }
            lastIndexed = done;
          }
        }, 1000);
      });
    }
  );
}

function startReview(selectionOnly: boolean) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("CodeLens AI: Open a file to review");
    return;
  }

  const doc = editor.document;
  let code: string;
  let lineOffset: number;
  let label: string;

  if (selectionOnly && !editor.selection.isEmpty) {
    const sel = editor.selection;
    code = doc.getText(sel);
    lineOffset = sel.start.line + 1;
    label = `${path.basename(doc.fileName)} : ${sel.start.line + 1}–${sel.end.line + 1}`;
  } else {
    if (selectionOnly) {
      vscode.window.showWarningMessage(
        "CodeLens AI: Nothing selected — reviewing the whole file"
      );
    }
    code = doc.getText();
    lineOffset = 1;
    label = path.basename(doc.fileName);
  }

  if (!code.trim()) {
    vscode.window.showWarningMessage("CodeLens AI: Nothing to review");
    return;
  }

  runReview({
    uri: doc.uri,
    code,
    language: doc.languageId,
    lineOffset,
    label,
  });
}

/**
 * Re-read the reviewed range from the open document so "Review again" sees the
 * user's fixes instead of the text captured by the first review.
 */
function refreshTarget(target: ReviewTarget): ReviewTarget {
  const doc = vscode.workspace.textDocuments.find(
    (d) => d.uri.toString() === target.uri.toString()
  );
  if (!doc) {
    return target;
  }

  const lineCount = target.code.split("\n").length;
  const isFullFile = target.lineOffset === 1 && lineCount >= doc.lineCount;
  if (isFullFile) {
    return { ...target, code: doc.getText() };
  }

  const startLine = Math.min(target.lineOffset - 1, doc.lineCount - 1);
  const endLine = Math.min(startLine + lineCount - 1, doc.lineCount - 1);
  const range = new vscode.Range(
    startLine,
    0,
    endLine,
    doc.lineAt(endLine).text.length
  );
  return { ...target, code: doc.getText(range) };
}

async function runReview(rawTarget: ReviewTarget) {
  const target = refreshTarget(rawTarget);
  const folders = vscode.workspace.workspaceFolders;
  const workspacePath = folders?.[0]?.uri.fsPath ?? path.dirname(target.uri.fsPath);

  // Resolve provider + keys first: consent prompts must not appear behind a
  // panel that already claims to be reviewing.
  const chain = await buildChain(extContext, secrets);
  if (!chain) {
    return;
  }

  const panel = ReviewPanel.show(extensionPath, annotations, runReview);
  panel.setLoading(target);
  annotations.clear(target.uri);

  const previousText = statusBarItem.text;
  statusBarItem.text = "$(loading~spin) CodeLens AI — Reviewing...";

  try {
    const result = await reviewCode({
      code: target.code,
      filePath: target.uri.fsPath,
      workspacePath,
      language: target.language,
      lineOffset: target.lineOffset,
      primary: chain.primary,
      fallback: chain.fallback,
      includeContext: chain.includeContext,
    });

    panel.setResults(result);

    const doc = vscode.workspace.textDocuments.find(
      (d) => d.uri.toString() === target.uri.toString()
    );
    annotations.show(target.uri, result.findings, doc);

    statusBarItem.text = previousText;
    statusBarItem.tooltip = `CodeLens AI — ${result.findings.length} finding(s) in ${target.label}`;
  } catch (err: any) {
    panel.setError(err.message);
    statusBarItem.text = previousText;
    vscode.window.showErrorMessage(`CodeLens AI: ${err.message}`);
  }
}

function setStatus(state: "connected" | "error", tooltip: string) {
  if (state === "connected") {
    statusBarItem.text = "$(check) CodeLens AI";
    statusBarItem.backgroundColor = undefined;
  } else {
    statusBarItem.text = "$(error) CodeLens AI";
    statusBarItem.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.errorBackground"
    );
  }
  statusBarItem.tooltip = `CodeLens AI — ${tooltip}`;
}
