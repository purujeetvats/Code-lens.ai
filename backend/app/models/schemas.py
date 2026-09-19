"""Pydantic request/response models."""

from pydantic import BaseModel


class IndexRequest(BaseModel):
    workspace_path: str
    force: bool = False
    # When set, index only these files (used by index-on-save) instead of
    # rescanning the whole workspace.
    files: list[str] | None = None


class IndexStatus(BaseModel):
    status: str  # "indexing" | "complete" | "error"
    total_files: int = 0
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    task_id: str = ""


class SearchRequest(BaseModel):
    query: str
    workspace_path: str
    limit: int = 10
    language: str | None = None
    # Drop hits scoring below this fraction of the best hit. 0 disables.
    min_score_ratio: float = 0.7


class SearchResult(BaseModel):
    file: str
    line_start: int
    line_end: int
    snippet: str
    score: float
    symbol: str
    language: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


class ProviderSpec(BaseModel):
    """One step of the LLM chain. `api_key` is held in memory only, never stored."""

    provider: str = "ollama"  # ollama | openai | anthropic
    model: str | None = None
    api_key: str | None = None


class ReviewRequest(BaseModel):
    code: str
    file_path: str
    workspace_path: str
    language: str = ""
    line_offset: int = 0  # 1-indexed line of code[0] in the real file
    primary: ProviderSpec = ProviderSpec()
    fallback: ProviderSpec | None = None
    use_cache: bool = True
    cache_ttl: int = 3600
    # When false, only the code under review is sent — no RAG chunks from other
    # files, no convention files. The extension turns this off for cloud providers.
    include_context: bool = True


class Finding(BaseModel):
    line: int
    severity: str  # critical | warning | info
    category: str  # bug | performance | security | style | complexity
    message: str
    suggestion: str = ""


class ReviewResponse(BaseModel):
    findings: list[Finding]
    summary: str = ""
    model: str = ""
    provider: str = ""
    context_used: int = 0  # number of RAG context chunks in the prompt
    context_files: list[str] = []  # which other files contributed context
    truncated: bool = False  # code was too long and only the head was reviewed
    lines_reviewed: int = 0
    cached: bool = False  # served from the local cache, no LLM call made
    fallback_used: bool = False  # primary provider failed, fallback answered
    warnings: list[str] = []  # e.g. why the primary provider was skipped
    injection_warnings: list[str] = []  # instruction-shaped text found in the code
