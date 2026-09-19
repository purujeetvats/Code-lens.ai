"""Search quality harness.

Measures retrieval against known-correct answers in this repo, so changes to the
embedding model, chunk text or ranking can be judged instead of eyeballed.

Usage:  python eval_search.py [workspace_path]
"""

import sys
from pathlib import Path

from app.services.embedder import embed_texts
from app.services.vector_store import COLLECTION, get_client, search

# (query, substring that must appear in the winning file path)
QUERIES: list[tuple[str, str]] = [
    ("where are the user's api keys stored securely", "secrets.ts"),
    ("detect prompt injection attempts in code", "injection.py"),
    ("split source code into chunks using the AST", "chunker.py"),
    ("search for similar vectors by cosine similarity", "vector_store.py"),
    ("cache responses in sqlite with a time to live", "cache.py"),
    ("try another LLM provider when the first one fails", "llm_router.py"),
    ("check that a path stays inside the workspace", "paths.py"),
    ("track file hashes so only changed files are reindexed", "file_tracker.py"),
    ("build the prompt sent to the model for review", "rag.py"),
    ("wait for the user to stop saving before reindexing", "SaveWatcher.ts"),
    ("show review findings as squiggles in the editor", "AnnotationProvider.ts"),
    ("generate a random nonce for the content security policy", "nonce.ts"),
    ("turn model output into validated findings", "review.py"),
    ("start the python backend process from the extension", "extension.ts"),
]


def evaluate(workspace: str, limit: int = 5, verbose: bool = True) -> dict:
    qdrant_path = str(Path(workspace) / ".codelens" / "qdrant")
    client = get_client(qdrant_path)
    names = [c.name for c in client.get_collections().collections]
    if COLLECTION not in names:
        raise SystemExit("No index found — run an index first.")

    top1 = top3 = top5 = 0
    reciprocal_ranks = []

    for query, expected in QUERIES:
        vec = embed_texts([query])[0]
        hits = search(client, vec, limit=limit)
        paths = [h.get("file_path", "") for h in hits]

        rank = next(
            (i + 1 for i, p in enumerate(paths) if expected.lower() in p.lower()), 0
        )
        if rank == 1:
            top1 += 1
        if 1 <= rank <= 3:
            top3 += 1
        if 1 <= rank <= 5:
            top5 += 1
        reciprocal_ranks.append(1 / rank if rank else 0.0)

        if verbose:
            mark = "OK  " if rank == 1 else (f"#{rank}  " if rank else "MISS")
            best = Path(paths[0]).name if paths else "-"
            score = hits[0].get("score", 0) if hits else 0
            print(f"  {mark} {query[:46]:46} -> {best:24} {score:.3f}")

    n = len(QUERIES)
    result = {
        "top1": top1 / n,
        "top3": top3 / n,
        "top5": top5 / n,
        "mrr": sum(reciprocal_ranks) / n,
        "n": n,
    }
    if verbose:
        print(
            f"\n  top-1 {result['top1']:.0%} | top-3 {result['top3']:.0%} | "
            f"top-5 {result['top5']:.0%} | MRR {result['mrr']:.3f}  (n={n})"
        )
    return result


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent)
    evaluate(ws)
