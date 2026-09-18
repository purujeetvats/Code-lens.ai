import * as vscode from "vscode";

export type CloudProvider = "openai" | "anthropic";
export type Provider = "ollama" | CloudProvider;

const KEY_PREFIX = "codelens-ai.apiKey.";

export const PROVIDER_LABELS: Record<Provider, string> = {
  ollama: "Ollama (local)",
  openai: "OpenAI",
  anthropic: "Anthropic",
};

const KEY_HINTS: Record<CloudProvider, { prefix: string; url: string }> = {
  openai: { prefix: "sk-", url: "https://platform.openai.com/api-keys" },
  anthropic: { prefix: "sk-ant-", url: "https://console.anthropic.com/settings/keys" },
};

/**
 * API keys live in VS Code SecretStorage, which is backed by the OS keychain.
 * They are deliberately kept out of settings.json — people commit that file,
 * screenshot it, and sync it between machines.
 */
export class SecretStore {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  get(provider: Provider): Thenable<string | undefined> {
    if (provider === "ollama") {
      return Promise.resolve(undefined); // local, no auth
    }
    return this.secrets.get(KEY_PREFIX + provider);
  }

  async set(provider: CloudProvider, key: string): Promise<void> {
    await this.secrets.store(KEY_PREFIX + provider, key);
  }

  async delete(provider: CloudProvider): Promise<void> {
    await this.secrets.delete(KEY_PREFIX + provider);
  }

  async has(provider: Provider): Promise<boolean> {
    return !!(await this.get(provider));
  }

  /** Prompt for a key and store it. Returns the provider if one was saved. */
  async promptAndStore(provider?: CloudProvider): Promise<CloudProvider | undefined> {
    let target = provider;
    if (!target) {
      const picked = await vscode.window.showQuickPick(
        [
          { label: PROVIDER_LABELS.openai, value: "openai" as const },
          { label: PROVIDER_LABELS.anthropic, value: "anthropic" as const },
        ],
        { placeHolder: "Which provider is this API key for?" }
      );
      if (!picked) {
        return undefined;
      }
      target = picked.value;
    }

    const hint = KEY_HINTS[target];
    const key = await vscode.window.showInputBox({
      title: `${PROVIDER_LABELS[target]} API key`,
      prompt: `Stored in your OS keychain, never in settings.json. Get one at ${hint.url}`,
      password: true,
      ignoreFocusOut: true,
      placeHolder: `${hint.prefix}...`,
      validateInput: (value) => {
        const v = value.trim();
        if (!v) {
          return "Key cannot be empty";
        }
        if (!v.startsWith(hint.prefix)) {
          return `${PROVIDER_LABELS[target!]} keys start with "${hint.prefix}"`;
        }
        return undefined;
      },
    });

    if (!key) {
      return undefined;
    }

    await this.set(target, key.trim());
    vscode.window.showInformationMessage(
      `CodeLens AI: ${PROVIDER_LABELS[target]} API key saved to your OS keychain.`
    );
    return target;
  }
}
