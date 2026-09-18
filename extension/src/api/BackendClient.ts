import * as vscode from "vscode";
import * as http from "http";
import { randomBytes } from "crypto";
import { ProviderSpec } from "../config/settings";

export interface Finding {
  line: number;
  severity: "critical" | "warning" | "info";
  category: string;
  message: string;
  suggestion: string;
}

export interface ReviewResponse {
  findings: Finding[];
  summary: string;
  model: string;
  provider: string;
  context_used: number;
  context_files: string[];
  truncated: boolean;
  lines_reviewed: number;
  cached: boolean;
  fallback_used: boolean;
  warnings: string[];
  injection_warnings: string[];
}

export function getPort(): number {
  return vscode.workspace
    .getConfiguration("codelens-ai")
    .get<number>("backendPort", 52411);
}

/**
 * Shared secret for the loopback API, generated per session.
 *
 * The backend listens on 127.0.0.1, which any web page the user visits can also
 * reach. Without a token, a malicious page could POST to /search or /index and
 * read the user's source code. Held in memory only — never written to settings,
 * disk, or the process command line.
 */
let authToken = "";

export function initAuthToken(): string {
  if (!authToken) {
    authToken = randomBytes(32).toString("hex");
  }
  return authToken;
}

export function getAuthToken(): string {
  return authToken;
}

/**
 * HTTP call to the local backend. Reviews run a local LLM, so the timeout is
 * per-call rather than a single global value.
 */
export function request(
  method: "GET" | "POST",
  path: string,
  body?: object,
  timeoutMs = 5000
): Promise<any> {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : undefined;
    const headers: http.OutgoingHttpHeaders = {
      Authorization: `Bearer ${authToken}`,
    };
    if (data) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(data);
    }

    const options: http.RequestOptions = {
      hostname: "127.0.0.1",
      port: getPort(),
      path,
      method,
      headers,
    };

    const req = http.request(options, (res) => {
      let chunks = "";
      res.on("data", (d) => (chunks += d));
      res.on("end", () => {
        let parsed: any;
        try {
          parsed = JSON.parse(chunks);
        } catch {
          reject(new Error(chunks.slice(0, 300) || `Empty response (${res.statusCode})`));
          return;
        }
        if (res.statusCode && res.statusCode >= 400) {
          reject(new Error(parsed?.error || `Request failed (${res.statusCode})`));
          return;
        }
        resolve(parsed);
      });
    });

    req.on("error", () =>
      reject(new Error("Backend not running — reload the window to restart it."))
    );
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      reject(new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`));
    });

    if (data) {
      req.write(data);
    }
    req.end();
  });
}

export function reviewCode(params: {
  code: string;
  filePath: string;
  workspacePath: string;
  language: string;
  lineOffset: number;
  primary: ProviderSpec;
  fallback: ProviderSpec | null;
  includeContext: boolean;
}): Promise<ReviewResponse> {
  const cfg = vscode.workspace.getConfiguration("codelens-ai");
  const timeoutMs = cfg.get<number>("reviewTimeoutSeconds", 180) * 1000;

  return request(
    "POST",
    "/review",
    {
      code: params.code,
      file_path: params.filePath,
      workspace_path: params.workspacePath,
      language: params.language,
      line_offset: params.lineOffset,
      primary: params.primary,
      fallback: params.fallback,
      include_context: params.includeContext,
      use_cache: cfg.get<boolean>("cacheEnabled", true),
      cache_ttl: cfg.get<number>("cacheTtlSeconds", 3600),
    },
    timeoutMs
  );
}

export async function listOllamaModels(): Promise<string[]> {
  const res = await request("GET", "/review/models", undefined, 10000);
  return res.models ?? [];
}

export function clearReviewCache(workspacePath: string): Promise<any> {
  return request(
    "POST",
    `/review/cache/clear?workspace_path=${encodeURIComponent(workspacePath)}`,
    undefined,
    10000
  );
}
