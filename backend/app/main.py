import os
import secrets as pysecrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers import index, review, search

# The backend speaks only to the extension, over loopback. A shared token keeps
# it that way: without it, any web page the user visits could POST to
# 127.0.0.1:<port> and read source code out of their workspace.
AUTH_TOKEN = os.environ.get("CODELENS_TOKEN", "")
if not AUTH_TOKEN:
    AUTH_TOKEN = pysecrets.token_hex(32)
    print(
        "CODELENS_TOKEN not set — generated one for this process:\n"
        f"  {AUTH_TOKEN}\n"
        "Send it as `Authorization: Bearer <token>` on every request."
    )

# No /docs or /openapi.json: this is a private local API, not a public one.
app = FastAPI(title="CodeLens AI Backend", docs_url=None, redoc_url=None, openapi_url=None)

# Deliberately no CORSMiddleware. The extension is a Node HTTP client, not a
# browser, so it needs no CORS grant — and adding one would hand browsers the
# very cross-origin access this server must not give them.


@app.middleware("http")
async def require_token(request: Request, call_next):
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    # Constant-time compare so the token can't be recovered by timing.
    if not pysecrets.compare_digest(supplied, AUTH_TOKEN):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """Report validation failures without echoing the request body back.

    FastAPI's default handler includes the offending input, which for /review
    would mean repeating the caller's API key.
    """
    fields = [".".join(str(p) for p in err.get("loc", [])) for err in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"error": "Invalid request", "fields": fields},
    )


app.include_router(index.router)
app.include_router(search.router)
app.include_router(review.router)


@app.get("/status")
def status():
    # ponytail: don't touch Qdrant singleton here — it flips the client path
    # and interferes with search/index threads
    return {
        "healthy": True,
        "version": "0.1.0",
        "embedding_model": "all-MiniLM-L6-v2",
        "llm_provider": "ollama",
    }
