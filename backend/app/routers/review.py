"""Review router — RAG context + LLM call (multi-provider) + structured findings."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.models.enums import CATEGORY_VALUES, SEVERITY_VALUES
from app.models.schemas import Finding, ReviewRequest, ReviewResponse
from app.services import cache
from app.services.llm_router import (
    LLMError,
    generate_with_fallback,
    list_models,
)
from app.services.injection import scan as scan_injection
from app.services.paths import PathError, safe_workspace
from app.services.rag import build_prompt, clip_code, read_conventions, retrieve_similar

router = APIRouter()


def _parse_findings(raw: object, line_min: int, line_max: int) -> list[Finding]:
    """Coerce model output into validated findings, dropping anything malformed."""
    if isinstance(raw, dict):
        raw = raw.get("findings", [])
    if not isinstance(raw, list):
        return []

    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message", "")).strip()
        if not message:
            continue

        try:
            line = int(item.get("line", line_min))
        except (TypeError, ValueError):
            line = line_min
        # Clamp hallucinated line numbers into the reviewed range
        line = max(line_min, min(line, line_max))

        severity = str(item.get("severity", "")).lower().strip()
        if severity not in SEVERITY_VALUES:
            severity = "info"

        category = str(item.get("category", "")).lower().strip()
        if category not in CATEGORY_VALUES:
            category = "bug"

        findings.append(
            Finding(
                line=line,
                severity=severity,
                category=category,
                message=message,
                suggestion=str(item.get("suggestion", "")).strip(),
            )
        )

    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f.severity], f.line))
    return findings


def _relative_context_files(similar: list[dict], workspace: str) -> list[str]:
    """Workspace-relative paths of the files that contributed RAG context.

    Reported back so the user can see exactly which files left their machine.
    """
    seen: list[str] = []
    for hit in similar:
        raw = hit.get("file_path", "")
        if not raw:
            continue
        try:
            name = str(Path(raw).relative_to(workspace))
        except ValueError:
            name = Path(raw).name
        if name not in seen:
            seen.append(name)
    return seen


def _build_chain(req: ReviewRequest) -> tuple[list[dict], list[str]]:
    """Primary provider plus fallback, dropping cloud steps that have no key."""
    warnings: list[str] = []
    chain: list[dict] = []

    for spec, label in ((req.primary, "primary"), (req.fallback, "fallback")):
        if spec is None:
            continue
        if spec.provider in ("openai", "anthropic") and not spec.api_key:
            warnings.append(
                f"Skipped {label} provider '{spec.provider}' — no API key configured."
            )
            continue
        chain.append(
            {"provider": spec.provider, "model": spec.model, "api_key": spec.api_key}
        )

    return chain, warnings


@router.post("/review")
def review_code(req: ReviewRequest):
    if not req.code.strip():
        return ReviewResponse(findings=[], summary="Nothing to review — code is empty.")

    try:
        workspace = safe_workspace(req.workspace_path)
    except PathError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    chain, warnings = _build_chain(req)
    if not chain:
        return JSONResponse(
            status_code=400,
            content={
                "error": "No usable LLM provider. "
                + (" ".join(warnings) if warnings else "Configure one in settings.")
            },
        )

    offset = max(1, req.line_offset or 1)
    code, truncated = clip_code(req.code)
    # Clamp against the code the model actually saw, not the full submission
    lines_reviewed = len(code.splitlines())
    line_max = offset + lines_reviewed - 1

    # Context means other people's files leave the machine. Callers opt in.
    if req.include_context:
        similar = retrieve_similar(str(workspace), code, req.file_path)
        conventions = read_conventions(str(workspace))
    else:
        similar, conventions = [], ""

    context_files = _relative_context_files(similar, str(workspace))
    # Flag manipulation attempts in the reviewed code, so a suspiciously clean
    # review on a suspicious file isn't silently trusted.
    injection_warnings = scan_injection(code, offset)
    prompt = build_prompt(
        code, req.file_path, req.language, offset, similar, conventions, truncated
    )

    # Cache lookup keyed on the prompt + the provider we're about to ask
    db_path = str(workspace / ".codelens" / "cache.db")
    head = chain[0]
    cache_key = ""
    conn = None
    if req.use_cache:
        try:
            conn = cache.get_db(db_path)
            cache_key = cache.make_key(
                prompt, head["provider"], head["model"] or head["provider"]
            )
            hit = cache.get(conn, cache_key, ttl=req.cache_ttl)
            if hit is not None:
                return ReviewResponse(
                    findings=_parse_findings(hit, offset, line_max),
                    summary=str(hit.get("summary", "")).strip() or "No issues found.",
                    model=str(hit.get("_model", "")),
                    provider=str(hit.get("_provider", "")),
                    context_used=len(similar),
                    context_files=context_files,
                    truncated=truncated,
                    lines_reviewed=lines_reviewed,
                    cached=True,
                    warnings=warnings,
                    injection_warnings=injection_warnings,
                )
        except Exception:
            # ponytail: a broken cache must never block a review
            conn = None

    try:
        body, provider, model, attempt_errors = generate_with_fallback(prompt, chain)
    except LLMError as e:
        return JSONResponse(status_code=503, content={"error": e.message})

    findings = _parse_findings(body, offset, line_max)
    summary = str(body.get("summary", "")).strip() if isinstance(body, dict) else ""
    if not summary:
        summary = f"{len(findings)} finding(s)." if findings else "No issues found."

    if conn is not None and cache_key:
        try:
            cache.put(
                conn,
                cache_key,
                {**body, "_provider": provider, "_model": model},
                provider,
                model,
            )
        except Exception:
            pass

    return ReviewResponse(
        findings=findings,
        summary=summary,
        model=model,
        provider=provider,
        context_used=len(similar),
        context_files=context_files,
        truncated=truncated,
        lines_reviewed=lines_reviewed,
        cached=False,
        fallback_used=bool(attempt_errors),
        warnings=warnings + attempt_errors,
        injection_warnings=injection_warnings,
    )


@router.get("/review/models")
def review_models():
    """Installed Ollama models — used by the extension to show what's available."""
    try:
        return {"models": list_models()}
    except LLMError as e:
        return JSONResponse(status_code=503, content={"error": e.message})


@router.post("/review/cache/clear")
def clear_cache(workspace_path: str):
    """Drop cached reviews for a workspace."""
    try:
        root = safe_workspace(workspace_path)
        conn = cache.get_db(str(root / ".codelens" / "cache.db"))
        return {"cleared": cache.clear(conn)}
    except PathError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
