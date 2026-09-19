"""Search router — semantic code search via embeddings."""

import traceback
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.models.schemas import SearchRequest, SearchResponse, SearchResult
from app.services.paths import PathError, safe_workspace
from app.services.embedder import embed_texts
from app.services.vector_store import get_client, search, COLLECTION

router = APIRouter()


@router.post("/search")
def search_code(req: SearchRequest):
    try:
        workspace = safe_workspace(req.workspace_path)
        qdrant_path = str(workspace / ".codelens" / "qdrant")
        client = get_client(qdrant_path)

        # Check if collection exists
        collections = [c.name for c in client.get_collections().collections]
        if COLLECTION not in collections:
            return {"results": []}

        query_vec = embed_texts([req.query])[0]
        # Over-fetch, then drop the long tail: cosine scores on code are flat, so
        # a fixed limit pads the list with near-misses that read as noise. Keeping
        # only hits within 70% of the best score halved the list on the eval set
        # without losing a single correct answer.
        raw = search(client, query_vec, limit=req.limit * 2, language=req.language)
        if raw:
            cutoff = raw[0].get("score", 0) * req.min_score_ratio
            raw = [hit for hit in raw if hit.get("score", 0) >= cutoff][: req.limit]

        results = [
            {
                "file": hit.get("file_path", ""),
                "line_start": hit.get("line_start", 0),
                "line_end": hit.get("line_end", 0),
                "snippet": hit.get("text", ""),
                "score": round(hit.get("score", 0), 3),
                "symbol": hit.get("symbol_name", ""),
                "symbol_type": hit.get("symbol_type", "function"),
                "language": hit.get("language", ""),
            }
            for hit in raw
        ]
        return {"results": results}
    except PathError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception:
        # Log the traceback locally; don't hand internals back to the caller.
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "Search failed"})
