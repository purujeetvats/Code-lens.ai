"""Embedding service — encodes code chunks into vectors."""

from sentence_transformers import SentenceTransformer

# ponytail: singleton model, load once. Swap model name for upgrade path.
_model: SentenceTransformer | None = None
_model_name: str = ""

# ponytail: all-MiniLM-L6-v2 works reliably with sentence-transformers.
# Swap to code-specific model (jina-embeddings-v2-base-code) in Phase 6.
DEFAULT_MODEL = "all-MiniLM-L6-v2"


def get_model(model_name: str = DEFAULT_MODEL) -> SentenceTransformer:
    global _model, _model_name
    if _model is None or _model_name != model_name:
        _model = SentenceTransformer(model_name, truncate_dim=None)
        _model_name = model_name
    return _model


def embed_texts(texts: list[str], model_name: str = DEFAULT_MODEL) -> list[list[float]]:
    """Encode list of texts into embedding vectors."""
    model = get_model(model_name)
    # ponytail: batch encode, sentence-transformers handles batching internally
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return embeddings.tolist()


def get_embedding_dim(model_name: str = DEFAULT_MODEL) -> int:
    model = get_model(model_name)
    return model.get_embedding_dimension()


if __name__ == "__main__":
    vecs = embed_texts(["def hello(): print('hi')", "function greet() { console.log('hi') }"])
    print(f"Vectors: {len(vecs)}, dim: {len(vecs[0])}")
    print(f"First 5 values: {vecs[0][:5]}")
