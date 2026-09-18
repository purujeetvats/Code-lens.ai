"""Qdrant vector store — embedded mode, no server needed."""

import hashlib
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, Filter, FieldCondition, MatchValue

COLLECTION = "codelens_chunks"

# ponytail: singleton client, one Qdrant instance per process
_client: QdrantClient | None = None
_storage_path: str = ""


def get_client(storage_dir: str = ".codelens") -> QdrantClient:
    """Get or create Qdrant client in embedded mode (local files, no server).

    Embedded Qdrant allows exactly one client per storage folder, so the
    singleton key must be the *resolved* path. Comparing raw strings meant
    `...\\.codelens/qdrant` and `...\\.codelens\\qdrant` looked like different
    folders, and opening the second one threw "already accessed by another
    instance".
    """
    global _client, _storage_path
    resolved = str(Path(storage_dir).resolve())

    if _client is None or _storage_path != resolved:
        if _client is not None:
            # Genuinely switching workspaces: release the old lock first.
            try:
                _client.close()
            except Exception:
                pass
        Path(resolved).mkdir(parents=True, exist_ok=True)
        _client = QdrantClient(path=resolved)
        _storage_path = resolved
    return _client


def ensure_collection(client: QdrantClient, dim: int) -> None:
    """Create collection if it doesn't exist."""
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )


def _chunk_uuid(chunk_key: str) -> str:
    """Deterministic UUID from chunk key (file:line)."""
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_key))


def upsert_chunks(
    client: QdrantClient,
    ids: list[str],
    vectors: list[list[float]],
    payloads: list[dict],
) -> None:
    """Store chunks with vectors and metadata."""
    points = [
        PointStruct(id=_chunk_uuid(chunk_id), vector=vec, payload=payload)
        for chunk_id, vec, payload in zip(ids, vectors, payloads)
    ]
    client.upsert(collection_name=COLLECTION, points=points)


def search(
    client: QdrantClient,
    query_vector: list[float],
    limit: int = 10,
    language: str | None = None,
) -> list[dict]:
    """Search for similar code chunks."""
    query_filter = None
    if language:
        query_filter = Filter(
            must=[FieldCondition(key="language", match=MatchValue(value=language))]
        )

    results = client.query_points(
        collection_name=COLLECTION,
        query=query_vector,
        limit=limit,
        query_filter=query_filter,
    )
    return [
        {**hit.payload, "score": hit.score}
        for hit in results.points
    ]


def delete_by_file(client: QdrantClient, file_path: str) -> None:
    """Delete all chunks for a given file (for re-indexing)."""
    client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
        ),
    )


def get_stats(client: QdrantClient) -> dict:
    """Get collection stats."""
    try:
        info = client.get_collection(COLLECTION)
        return {"vector_count": info.points_count, "status": str(info.status)}
    except Exception:
        return {"vector_count": 0, "status": "not_created"}


if __name__ == "__main__":
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        c = get_client(tmp)
        ensure_collection(c, 4)
        upsert_chunks(c, ["a"], [[0.1, 0.2, 0.3, 0.4]], [{"file_path": "test.py", "symbol": "foo"}])
        results = search(c, [0.1, 0.2, 0.3, 0.4], limit=1)
        print(f"Search result: {results}")
        print(f"Stats: {get_stats(c)}")
        c.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
