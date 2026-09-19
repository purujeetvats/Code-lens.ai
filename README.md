# CodeLens AI

AI-powered semantic code search and code review for VS Code. Local-first: your code
stays on your machine, and everything works offline.

Two features:

- **Semantic search** — describe what you're looking for in plain English
  ("function that validates email") and jump straight to it.
- **AI code review** — get categorized findings with line numbers and concrete
  suggestions, grounded in the rest of your codebase.

## Supported languages

Python, JavaScript, JSX, TypeScript, TSX, Go, Rust, Java, C, C++, C#, Ruby, PHP
— 20 file extensions, parsed into function- and class-level chunks with
tree-sitter. Grammars load independently, so a missing one costs that language,
not the backend.

## Requirements

- **VS Code** 1.85 or newer
- **Python** 3.11+ on your `PATH` (the extension starts the backend itself)
- **[Ollama](https://ollama.com)** — only needed for code review, not for search

## Setup

```bash
# 1. Backend dependencies
cd backend
pip install -r requirements.txt

# 2. A model for code review
ollama pull qwen2.5-coder:7b

# 3. Extension
cd ../extension
npm install
npm run compile
```

Then press `F5` in VS Code to launch the Extension Development Host.

### Installing as a .vsix

```bash
cd extension
npm run package        # compiles, bundles the backend, builds the .vsix
code --install-extension codelens-ai-0.1.0.vsix
```

The packaged extension carries the Python backend inside it, but **not** the
Python dependencies — run `pip install -r backend/requirements.txt` once on the
machine you install it to. If `python` isn't on your PATH, the extension says so
and links the installer rather than failing silently.

The status bar shows `✓ CodeLens AI` once the backend is up. The first start is
slow — the embedding model downloads on first use.

## Usage

| Command | What it does |
|---|---|
| `CodeLens AI: Index Workspace` | Chunks and embeds the workspace. Run this first. |
| `CodeLens AI: Search` | Opens the search sidebar. |
| `CodeLens AI: Review File` | Reviews the active file. |
| `CodeLens AI: Review Selection` | Reviews the selected lines only. |
| `CodeLens AI: Clear Review Annotations` | Removes review squiggles from the editor. |
| `CodeLens AI: Select Review Provider` | Switch between Ollama, OpenAI, and Anthropic. |
| `CodeLens AI: Set API Key` | Store an OpenAI or Anthropic key in your OS keychain. |
| `CodeLens AI: Delete Stored API Keys` | Remove stored keys and reset cloud consent. |
| `CodeLens AI: Choose Cloud Context` | Pick how much workspace context cloud providers receive. |
| `CodeLens AI: Select Model` | Pick the review model for the active provider. |
| `CodeLens AI: Clear Review Cache` | Drop cached reviews for this workspace. |

Review and search are also on the editor right-click menu.

Findings appear in a side panel and as squiggles in the editor, so they also show
up in the Problems panel. Click a finding to jump to its line; dismiss the ones
you disagree with; hit **Review again** after fixing to re-check.

## Using your own API keys (optional)

Review runs on local Ollama by default and never leaves your machine. If you want
stronger reviews, you can bring your own OpenAI or Anthropic key:

1. Run `CodeLens AI: Set API Key` and pick a provider.
2. Paste the key. It goes into your **OS keychain** via VS Code SecretStorage —
   never into `settings.json`, never into this repo, never into a log.
3. Run `CodeLens AI: Select Review Provider` and choose that provider.

The first time a cloud provider is used, the extension asks for confirmation and
tells you exactly what gets uploaded. Your code is sent to that provider's API,
billed to your key. Answer "Use Ollama instead" to stay local. Revoke everything
at any time with `CodeLens AI: Delete Stored API Keys`.

### What a cloud review actually sends — you choose

The reviewer is RAG-based: it enriches the prompt with related code chunks from
elsewhere in your workspace and with your convention files. On a cloud provider
that means reviewing one file can upload snippets of files you never opened.

So the first cloud review asks you to pick, and explains the trade:

| Choice | What is uploaded | Review quality |
|---|---|---|
| **Full context** | The reviewed code, related snippets from other workspace files, and your README / config files. | Better. Knows your conventions, catches problems spanning files. |
| **This file only** | Only the code you are reviewing. Nothing else. | Noticeably more generic. No awareness of your conventions, more off-target style suggestions. |
| **Use Ollama instead** | Nothing — runs locally. | Full context, no upload. Weaker model than a frontier cloud one. |

Change it any time with `CodeLens AI: Choose Cloud Context`, or via
`codelens-ai.cloudContext`. Local Ollama always gets full context, since nothing
leaves the machine either way.

Narrowing the scope applies immediately; **widening it asks again**, because
agreeing to "this file only" was not agreement to send the rest. After every
review the panel names the files that contributed context, so what left your
machine is visible rather than implied.

One consequence worth knowing: configuring a **cloud fallback** also strips
context from your local reviews, since a single prompt is built for the whole
chain and the fallback might be the one to receive it.

Set `codelens-ai.fallbackProvider` to keep reviews working when your primary
provider is down or rate-limited. A rejected API key is reported to you rather
than silently falling back, so a broken key never hides behind a weaker model.

Identical reviews are served from a local SQLite cache instead of re-billing your
key. Keys are never part of the cache key and are never stored.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `codelens-ai.backendPort` | `52411` | Port for the local backend. |
| `codelens-ai.llmProvider` | `ollama` | Provider for review: `ollama`, `openai`, or `anthropic`. |
| `codelens-ai.fallbackProvider` | `none` | Provider to try if the primary is unreachable. |
| `codelens-ai.cloudContext` | `none` | Workspace context a cloud provider may receive: `none` or `workspace`. |
| `codelens-ai.ollamaModel` | `""` | Ollama model. Empty means auto-pick an installed one. |
| `codelens-ai.openaiModel` | `gpt-4o-mini` | OpenAI model for review. |
| `codelens-ai.anthropicModel` | `claude-opus-5` | Anthropic model for review. |
| `codelens-ai.reviewTimeoutSeconds` | `180` | How long to wait for a review. |
| `codelens-ai.cacheEnabled` | `true` | Reuse cached reviews for identical code. |
| `codelens-ai.cacheTtlSeconds` | `3600` | How long a cached review stays valid. |
| `codelens-ai.indexOnSave` | `true` | Re-index a file when you save it. |
| `codelens-ai.embeddingModel` | `unixcoder` | Embedding model for search. |

## How it works

```
VS Code extension (TypeScript)  ──HTTP──>  FastAPI backend (Python)
  search sidebar, review panel,              tree-sitter chunking,
  inline annotations                         embeddings, Qdrant, RAG, Ollama
```

**Search:** tree-sitter splits each file into function- and class-level chunks,
each chunk is embedded, and the vectors go into an embedded Qdrant instance under
`.codelens/`. A query is embedded the same way and matched by cosine similarity.
File hashes in SQLite keep re-indexing incremental.

**Review:** the code under review is embedded and used to retrieve related chunks
from elsewhere in the codebase, which are combined with the repo's convention
files into a prompt. Code lines are numbered in the prompt so the model cites real
line numbers; the backend then validates the response and clamps any line number
that falls outside the reviewed range.

With the default Ollama provider, nothing leaves your machine — everything runs on
`127.0.0.1`. Choosing a cloud provider sends the reviewed code to that provider,
and only after you confirm it; other workspace files are withheld unless you
explicitly widen `codelens-ai.cloudContext`.

## Security

The local backend is authenticated with a per-session token, so no web page you
visit can reach it. API keys live in your OS keychain and never touch disk.
Webviews run under a strict CSP. Full detail, including the limits, is in
[SECURITY.md](SECURITY.md).

**Costs:** there is no server behind this project and no telemetry. Cloud calls
go straight from your machine to the provider you configured, billed to your own
API key.

## Project layout

```
extension/          VS Code extension (TypeScript)
  src/api/          backend HTTP client
  src/views/        search sidebar, review panel
  src/providers/    inline diagnostics
  webview/          panel HTML/CSS/JS
backend/
  app/routers/      /index, /search, /review endpoints
  app/services/     chunker, embedder, vector store, RAG, LLM
docs/               design spec
```

## Conventions

- Prefer the standard library over a new dependency; prefer one line over fifty.
- Keep changes surgical — no speculative abstractions for cases that don't exist yet.
- Deliberate shortcuts are marked with a `ponytail:` comment explaining the trade-off.
- Python: type hints on function signatures, docstrings on modules and non-obvious
  functions. TypeScript: `strict` mode, no `any` in exported signatures.

## Status

All six build phases are done: skeleton, indexing, semantic search, the AI
reviewer, multi-provider LLM routing with fallback and caching, and polish —
index-on-save, 13 languages, progress reporting, and packaging. See
[the design spec](docs/design-spec.md).

Not yet done: a marketplace icon (`media/icon.png`), pinned Python dependency
versions, and a published release.

## Known limits

- Review is capped at ~12,000 characters of code per call. Longer files are
  reviewed in part, and the panel says so — select a range to review the rest.
- Review takes 1–2 minutes per file on CPU with a 7B model.
- Files re-index automatically on save; the first save after a restart pays a
one-off delay while the embedding model loads.
- Only one process can open the embedded Qdrant index at a time.
