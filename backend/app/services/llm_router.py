"""LLM routing — Ollama, OpenAI, Anthropic, with a fallback chain.

Cloud SDKs are imported lazily so an Ollama-only user never needs them installed.
API keys arrive per-request from the extension's SecretStorage and are never
written to disk or logged.
"""

import json
import urllib.error
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

PROVIDERS = ("ollama", "openai", "anthropic")
CLOUD_PROVIDERS = ("openai", "anthropic")

# Models we prefer when the configured Ollama model isn't installed, best first.
PREFERRED = ["qwen2.5-coder", "deepseek-coder", "codellama", "codegemma", "llama3", "mistral"]


class LLMError(Exception):
    """Raised with a message meant to be shown to the user.

    `retryable` marks failures worth trying the fallback provider for
    (rate limits, outages, timeouts) as opposed to ones that will fail
    identically every time (a bad API key, an unknown model).
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


def redact(text: str, api_key: str | None) -> str:
    """Strip an API key out of text before it reaches a log or the UI."""
    if not api_key or len(api_key) < 8:
        return text
    return text.replace(api_key, f"{api_key[:6]}...redacted")


def default_model(provider: str) -> str:
    return {
        "ollama": DEFAULT_OLLAMA_MODEL,
        "openai": DEFAULT_OPENAI_MODEL,
        "anthropic": DEFAULT_ANTHROPIC_MODEL,
    }.get(provider, DEFAULT_OLLAMA_MODEL)


def parse_json_response(raw: str) -> dict:
    """Parse a model's text output into an object, salvaging a wrapped JSON block."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"Model returned unparseable output: {raw[:300]}")


# ── Ollama ────────────────────────────────────────────────────────────────────

def list_models() -> list[str]:
    """Installed Ollama models. Raises LLMError if Ollama isn't reachable."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as e:
        # Retryable: an absent Ollama is precisely what a fallback provider is for.
        raise LLMError(
            f"Ollama not reachable at {OLLAMA_URL} ({e}). "
            "Install it from https://ollama.com and make sure it is running.",
            retryable=True,
        ) from e
    return [m.get("name", "") for m in body.get("models", [])]


def resolve_model(requested: str | None = None) -> str:
    """Pick an installed Ollama model: the requested one, else the best preferred."""
    installed = list_models()
    if not installed:
        raise LLMError(
            "Ollama has no models installed. "
            f"Run `ollama pull {DEFAULT_OLLAMA_MODEL}` and try again.",
            retryable=True,
        )

    wanted = requested or DEFAULT_OLLAMA_MODEL
    if wanted in installed:
        return wanted
    for name in installed:
        if name.split(":")[0] == wanted.split(":")[0]:
            return name
    for pref in PREFERRED:
        for name in installed:
            if name.split(":")[0] == pref:
                return name
    return installed[0]


def _call_ollama(prompt: str, model: str, timeout: int) -> str:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("response", "")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"Ollama returned {e.code}: {detail}", retryable=e.code >= 500) from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise LLMError(f"Ollama request failed: {e}", retryable=True) from e


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(prompt: str, model: str, api_key: str, timeout: int) -> str:
    try:
        import openai
    except ImportError as e:
        raise LLMError(
            "The `openai` package is not installed. Run `pip install openai` "
            "in the backend environment to use OpenAI."
        ) from e

    if not api_key:
        raise LLMError(
            "No OpenAI API key set. Run `CodeLens AI: Set API Key` and pick OpenAI."
        )

    client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=1)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except openai.AuthenticationError as e:
        raise LLMError("OpenAI rejected the API key. Check it and set it again.") from e
    except openai.RateLimitError as e:
        raise LLMError(
            "OpenAI rate limit or quota exceeded. Check your billing.", retryable=True
        ) from e
    except openai.APIStatusError as e:
        raise LLMError(
            redact(f"OpenAI returned {e.status_code}: {e.message}", api_key),
            retryable=e.status_code >= 500,
        ) from e
    except openai.APIConnectionError as e:
        raise LLMError(f"Could not reach OpenAI: {e}", retryable=True) from e

    return resp.choices[0].message.content or ""


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _call_anthropic(prompt: str, model: str, api_key: str, timeout: int) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise LLMError(
            "The `anthropic` package is not installed. Run `pip install anthropic` "
            "in the backend environment to use Claude."
        ) from e

    if not api_key:
        raise LLMError(
            "No Anthropic API key set. Run `CodeLens AI: Set API Key` and pick Anthropic."
        )

    client = anthropic.Anthropic(api_key=api_key, timeout=float(timeout), max_retries=1)
    try:
        # No `temperature` here: current Claude models reject sampling params.
        # No `thinking` either — adaptive thinking is on by default.
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic rejected the API key. Check it and set it again.") from e
    except anthropic.RateLimitError as e:
        raise LLMError(
            "Anthropic rate limit exceeded. Wait a moment or check your billing.",
            retryable=True,
        ) from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Anthropic has no model named '{model}'.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(
            redact(f"Anthropic returned {e.status_code}: {e.message}", api_key),
            retryable=e.status_code >= 500,
        ) from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Could not reach Anthropic: {e}", retryable=True) from e

    if resp.stop_reason == "refusal":
        raise LLMError("Claude declined to review this code.")

    # Responses can carry thinking blocks alongside text — take only the text.
    return "".join(block.text for block in resp.content if block.type == "text")


# ── Dispatch ──────────────────────────────────────────────────────────────────

def generate_json(
    prompt: str,
    provider: str = "ollama",
    model: str | None = None,
    api_key: str | None = None,
    timeout: int = 180,
) -> tuple[dict, str, str]:
    """Run one provider. Returns (parsed_object, provider, resolved_model)."""
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider '{provider}'. Expected one of {PROVIDERS}.")

    if provider == "ollama":
        resolved = resolve_model(model)
        raw = _call_ollama(prompt, resolved, timeout)
    elif provider == "openai":
        resolved = model or DEFAULT_OPENAI_MODEL
        raw = _call_openai(prompt, resolved, api_key or "", timeout)
    else:
        resolved = model or DEFAULT_ANTHROPIC_MODEL
        raw = _call_anthropic(prompt, resolved, api_key or "", timeout)

    return parse_json_response(raw), provider, resolved


def generate_with_fallback(
    prompt: str,
    chain: list[dict],
    timeout: int = 180,
) -> tuple[dict, str, str, list[str]]:
    """Try each provider in `chain` until one succeeds.

    Each entry: {"provider": ..., "model": ..., "api_key": ...}.
    Only retryable failures advance the chain — a bad key fails the same way
    on a retry, so it surfaces immediately instead of burning the fallback.

    Returns (parsed, provider, model, errors_from_failed_attempts).
    """
    if not chain:
        raise LLMError("No LLM provider configured.")

    errors: list[str] = []
    for i, step in enumerate(chain):
        provider = step.get("provider", "ollama")
        try:
            parsed, used_provider, used_model = generate_json(
                prompt,
                provider=provider,
                model=step.get("model"),
                api_key=step.get("api_key"),
                timeout=timeout,
            )
            return parsed, used_provider, used_model, errors
        except LLMError as e:
            errors.append(f"{provider}: {e.message}")
            is_last = i == len(chain) - 1
            if is_last or not e.retryable:
                if is_last:
                    raise LLMError(" | ".join(errors)) from e
                raise

    raise LLMError(" | ".join(errors))


if __name__ == "__main__":
    try:
        print(f"Installed Ollama models: {list_models()}")
        print(f"Resolved: {resolve_model()}")
    except LLMError as err:
        print(f"LLMError: {err}")
