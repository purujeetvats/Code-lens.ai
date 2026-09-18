import * as vscode from "vscode";
import { CloudProvider, PROVIDER_LABELS, Provider, SecretStore } from "./secrets";

export interface ProviderSpec {
  provider: Provider;
  model: string | null;
  api_key: string | null;
}

const CONSENT_PREFIX = "codelens-ai.cloudConsent.";

function config() {
  return vscode.workspace.getConfiguration("codelens-ai");
}

export function getProvider(): Provider {
  return config().get<Provider>("llmProvider", "ollama");
}

export function getFallbackProvider(): Provider | "none" {
  return config().get<Provider | "none">("fallbackProvider", "none");
}

export function modelFor(provider: Provider): string | null {
  const key = {
    ollama: "ollamaModel",
    openai: "openaiModel",
    anthropic: "anthropicModel",
  }[provider];
  return config().get<string>(key, "") || null;
}

export function isCloud(provider: Provider): provider is CloudProvider {
  return provider === "openai" || provider === "anthropic";
}

/**
 * Whether RAG context — code chunks from *other* files in the workspace, plus
 * convention files — may be included in the prompt.
 *
 * Local Ollama always gets it: nothing leaves the machine. For a cloud provider
 * this defaults to off, because reviewing one file would otherwise upload
 * snippets of files the user never opened.
 */
export function shouldIncludeContext(provider: Provider): boolean {
  if (!isCloud(provider)) {
    return true;
  }
  return config().get<"none" | "workspace">("cloudContext", "none") === "workspace";
}

/**
 * CodeLens AI is local-first. Sending source code to a third-party API is a
 * meaningful change in where a user's code goes, so it is confirmed explicitly
 * once per provider rather than assumed from a settings dropdown.
 */
export type ContextScope = "workspace" | "code-only";

const FULL_CONTEXT = "Full context — better reviews";
const CODE_ONLY = "This file only — more private";
const STAY_LOCAL = "Use Ollama instead";

export async function ensureCloudConsent(
  context: vscode.ExtensionContext,
  provider: CloudProvider
): Promise<ContextScope | null> {
  const stateKey = CONSENT_PREFIX + provider;
  const granted = context.globalState.get<ContextScope>(stateKey);
  const current = shouldIncludeContext(provider) ? "workspace" : "code-only";

  // Consent to "this file only" is not consent to "this file plus whatever else
  // the retriever picks". Widening the scope re-asks; narrowing it does not.
  if (granted === "workspace" || granted === current) {
    return current;
  }

  const label = PROVIDER_LABELS[provider];
  const choice = await vscode.window.showWarningMessage(
    `Send code to ${label}?`,
    {
      modal: true,
      detail:
        `Reviewing with ${label} uploads code to their API over the internet, ` +
        `billed to your API key. Choose how much goes with it:\n\n` +
        `FULL CONTEXT — sends the code under review plus related snippets from ` +
        `other files in this workspace, and your README / config files. The ` +
        `reviewer knows how your codebase does things, so findings match your ` +
        `conventions and it catches problems that span files. Those other files ` +
        `are picked automatically and may include ones you have never opened.\n\n` +
        `THIS FILE ONLY — sends nothing but the code you are reviewing. Reviews ` +
        `are noticeably more generic: no awareness of your conventions, more ` +
        `style suggestions that do not fit your project.\n\n` +
        `Ollama runs locally and gets full context without anything leaving this ` +
        `machine. Either choice can be changed later with ` +
        `"CodeLens AI: Choose Cloud Context".`,
    },
    FULL_CONTEXT,
    CODE_ONLY,
    STAY_LOCAL
  );

  if (choice === FULL_CONTEXT || choice === CODE_ONLY) {
    const scope: ContextScope = choice === FULL_CONTEXT ? "workspace" : "code-only";
    await context.globalState.update(stateKey, scope);
    // Persist to settings too, so the choice is visible and editable afterwards.
    await config().update(
      "cloudContext",
      scope === "workspace" ? "workspace" : "none",
      vscode.ConfigurationTarget.Global
    );
    return scope;
  }

  if (choice === STAY_LOCAL) {
    await config().update(
      "llmProvider",
      "ollama",
      vscode.ConfigurationTarget.Global
    );
    vscode.window.showInformationMessage("CodeLens AI: switched to Ollama (local).");
  }
  return null;
}

interface ModelChoice {
  id: string;
  label: string;
  detail: string;
}

/**
 * Curated model lists per provider. Pricing is input/output per million tokens,
 * shown because the user pays for it directly.
 */
const MODEL_CHOICES: Record<CloudProvider, ModelChoice[]> = {
  anthropic: [
    {
      id: "claude-opus-5",
      label: "Claude Opus 5",
      detail: "Most capable for code review · $5 / $25 per Mtok · 1M context",
    },
    {
      id: "claude-sonnet-5",
      label: "Claude Sonnet 5",
      detail: "Strong and cheaper · $2 / $10 per Mtok · 1M context",
    },
    {
      id: "claude-haiku-4-5",
      label: "Claude Haiku 4.5",
      detail: "Fastest and cheapest · $1 / $5 per Mtok · 200K context",
    },
    {
      id: "claude-fable-5-1",
      label: "Claude Fable 5.1",
      detail: "Deepest reasoning, premium price · $10 / $50 per Mtok · 1M context",
    },
  ],
  openai: [
    {
      id: "gpt-4o-mini",
      label: "GPT-4o mini",
      detail: "Cheap and fast, good default for review",
    },
    { id: "gpt-4o", label: "GPT-4o", detail: "Stronger, costs more per review" },
  ],
};

/** Pick the review model for the active provider. */
export async function selectModel(
  ollamaModels: () => Promise<string[]>
): Promise<void> {
  const provider = getProvider();
  const settingKey = {
    ollama: "ollamaModel",
    openai: "openaiModel",
    anthropic: "anthropicModel",
  }[provider];
  const currentValue = config().get<string>(settingKey, "");

  type Item = vscode.QuickPickItem & { value: string | null };
  let items: Item[];

  if (provider === "ollama") {
    let installed: string[] = [];
    try {
      installed = await ollamaModels();
    } catch (err: any) {
      vscode.window.showErrorMessage(`CodeLens AI: ${err.message}`);
      return;
    }
    if (installed.length === 0) {
      vscode.window.showWarningMessage(
        "CodeLens AI: no Ollama models installed. Run `ollama pull qwen2.5-coder:7b`."
      );
      return;
    }
    items = installed.map((name) => ({
      label: name,
      detail: "Installed locally · free · nothing leaves your machine",
      value: name,
    }));
    items.unshift({
      label: "Auto",
      detail: "Pick the best installed model automatically",
      value: "",
    });
  } else {
    items = MODEL_CHOICES[provider].map((m) => ({
      label: m.label,
      description: m.id,
      detail: m.detail,
      value: m.id,
    }));
  }

  items.push({ label: "$(edit) Enter a model ID manually…", value: null });

  // Mark whichever one is in use now.
  for (const item of items) {
    if (item.value === currentValue) {
      item.description = `${item.description ?? ""} — current`.trim();
    }
  }

  const picked = await vscode.window.showQuickPick(items, {
    placeHolder: `Review model for ${PROVIDER_LABELS[provider]}`,
    matchOnDescription: true,
  });
  if (!picked) {
    return;
  }

  let value = picked.value;
  if (value === null) {
    const typed = await vscode.window.showInputBox({
      title: `${PROVIDER_LABELS[provider]} model ID`,
      prompt: "Exact model identifier, as the provider spells it",
      value: currentValue,
      ignoreFocusOut: true,
    });
    if (typed === undefined) {
      return;
    }
    value = typed.trim();
  }

  await config().update(settingKey, value, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(
    `CodeLens AI: review model set to ${value || "auto"}.`
  );
}

/** Re-ask the context question on demand, without waiting for a review. */
export async function chooseCloudContext(
  context: vscode.ExtensionContext
): Promise<void> {
  const picked = await vscode.window.showQuickPick(
    [
      {
        label: FULL_CONTEXT,
        detail:
          "Cloud reviews also receive related snippets from other files in the workspace.",
        value: "workspace" as const,
      },
      {
        label: CODE_ONLY,
        detail:
          "Cloud reviews receive only the code being reviewed. Expect more generic findings.",
        value: "none" as const,
      },
    ],
    { placeHolder: "How much workspace context may a cloud provider receive?" }
  );
  if (!picked) {
    return;
  }

  await config().update("cloudContext", picked.value, vscode.ConfigurationTarget.Global);

  // Re-consent at the new scope rather than inheriting the old agreement.
  const scope: ContextScope = picked.value === "workspace" ? "workspace" : "code-only";
  for (const p of ["openai", "anthropic"] as CloudProvider[]) {
    const granted = context.globalState.get<ContextScope>(CONSENT_PREFIX + p);
    if (granted && scope === "code-only") {
      await context.globalState.update(CONSENT_PREFIX + p, "code-only");
    }
  }

  vscode.window.showInformationMessage(`CodeLens AI: ${picked.label}.`);
}

export async function revokeCloudConsent(
  context: vscode.ExtensionContext
): Promise<void> {
  for (const p of ["openai", "anthropic"] as CloudProvider[]) {
    await context.globalState.update(CONSENT_PREFIX + p, undefined);
  }
}

/**
 * Build the primary + fallback chain for a review, resolving keys from the
 * keychain and getting consent for any cloud provider involved.
 * Returns null if the user declined.
 */
export async function buildChain(
  context: vscode.ExtensionContext,
  secrets: SecretStore
): Promise<{
  primary: ProviderSpec;
  fallback: ProviderSpec | null;
  includeContext: boolean;
} | null> {
  const primaryProvider = getProvider();
  let primaryScope: ContextScope = "workspace";

  if (isCloud(primaryProvider)) {
    const scope = await ensureCloudConsent(context, primaryProvider);
    if (scope === null) {
      return null;
    }
    primaryScope = scope;
    if (!(await secrets.has(primaryProvider))) {
      const saved = await promptForMissingKey(secrets, primaryProvider);
      if (!saved) {
        return null;
      }
    }
  }

  const primary: ProviderSpec = {
    provider: primaryProvider,
    model: modelFor(primaryProvider),
    api_key: (await secrets.get(primaryProvider)) ?? null,
  };

  let fallback: ProviderSpec | null = null;
  const fallbackProvider = getFallbackProvider();
  if (fallbackProvider !== "none" && fallbackProvider !== primaryProvider) {
    // A fallback is a silent path — only include a cloud one already consented
    // to, at a scope covering what this review would actually send.
    const granted = context.globalState.get<string>(CONSENT_PREFIX + fallbackProvider);
    const needed = shouldIncludeContext(fallbackProvider) ? "workspace" : "code-only";
    const consented =
      !isCloud(fallbackProvider) || granted === "workspace" || granted === needed;

    if (consented) {
      fallback = {
        provider: fallbackProvider,
        model: modelFor(fallbackProvider),
        api_key: (await secrets.get(fallbackProvider)) ?? null,
      };
    }
  }

  // One prompt is built for the whole chain, and the fallback may end up seeing
  // it. Take the most restrictive scope across every provider that could.
  const includeContext =
    primaryScope === "workspace" &&
    (fallback === null || shouldIncludeContext(fallback.provider));

  return { primary, fallback, includeContext };
}

async function promptForMissingKey(
  secrets: SecretStore,
  provider: CloudProvider
): Promise<boolean> {
  const label = PROVIDER_LABELS[provider];
  const choice = await vscode.window.showErrorMessage(
    `CodeLens AI: no ${label} API key configured.`,
    "Set API key",
    "Cancel"
  );
  if (choice !== "Set API key") {
    return false;
  }
  return (await secrets.promptAndStore(provider)) !== undefined;
}
