"""Index router — walks workspace, chunks, embeds, stores."""

import threading
import uuid
from pathlib import Path

from fastapi import APIRouter

from app.models.schemas import IndexRequest, IndexStatus
from app.services.paths import PathError, safe_workspace, within


def chunker_extensions() -> set[str]:
    from app.services.chunker import SUPPORTED_EXTENSIONS

    return SUPPORTED_EXTENSIONS
from app.services.chunker import chunk_file
from app.services.embedder import embed_texts, get_embedding_dim
from app.services.file_tracker import get_changed_files, get_db, remove_hash, update_hash
from app.services.vector_store import (
    delete_by_file,
    ensure_collection,
    get_client,
    upsert_chunks,
)

router = APIRouter()

# ponytail: in-memory task tracking, no Redis/Celery for a local tool
_tasks: dict[str, IndexStatus] = {}


def _index_worker(
    task_id: str, workspace: str, force: bool, only_files: list[str] | None = None
) -> None:
    """Background indexing — runs in a thread."""
    status = _tasks[task_id]
    codelens_dir = str(Path(workspace) / ".codelens")

    try:
        db = get_db(f"{codelens_dir}/file_tracker.db")
        client = get_client(f"{codelens_dir}/qdrant")
        dim = get_embedding_dim()
        ensure_collection(client, dim)

        if only_files is not None:
            # Targeted re-index: just the files handed to us that still exist
            # and sit inside the workspace.
            changed = []
            for f in only_files:
                try:
                    p = within(Path(workspace), f)
                except PathError:
                    continue
                if p.is_file() and p.suffix.lower() in chunker_extensions():
                    changed.append(str(p))
            deleted = []
        elif force:
            # Force re-index: treat all files as changed
            from app.services.file_tracker import SUPPORTED_EXTENSIONS, SKIP_DIRS
            changed = [
                str(p) for p in Path(workspace).rglob("*")
                if p.is_file()
                and p.suffix.lower() in SUPPORTED_EXTENSIONS
                and not any(skip in p.parts for skip in SKIP_DIRS)
            ]
            deleted: list[str] = []
        else:
            changed, deleted = get_changed_files(workspace, db)

        status.total_files = len(changed)

        # Remove deleted files from vector store
        for f in deleted:
            delete_by_file(client, f)
            remove_hash(db, f)

        # Process changed files in batches
        BATCH_SIZE = 20  # ponytail: fixed batch, configurable when needed
        for i in range(0, len(changed), BATCH_SIZE):
            batch_files = changed[i : i + BATCH_SIZE]
            all_chunks = []

            for fpath in batch_files:
                chunks = chunk_file(fpath)
                all_chunks.extend(chunks)

            if all_chunks:
                texts = [c.text for c in all_chunks]
                vectors = embed_texts(texts)
                payloads = [
                    {
                        "file_path": c.file_path,
                        "line_start": c.line_start,
                        "line_end": c.line_end,
                        "symbol_name": c.symbol_name,
                        "symbol_type": c.symbol_type,
                        "language": c.language,
                        "text": c.text[:500],  # store snippet for display
                    }
                    for c in all_chunks
                ]
                ids = [f"{c.file_path}:{c.line_start}" for c in all_chunks]
                # Delete old chunks for these files first
                seen_files: set[str] = set()
                for c in all_chunks:
                    if c.file_path not in seen_files:
                        delete_by_file(client, c.file_path)
                        seen_files.add(c.file_path)

                upsert_chunks(client, ids, vectors, payloads)

            # Update hashes
            for fpath in batch_files:
                try:
                    update_hash(db, fpath)
                except Exception:
                    status.errors += 1

            status.indexed += len(batch_files)

        status.skipped = 0
        status.status = "complete"

    except Exception as e:
        status.status = f"error: {e}"


@router.post("/index")
def start_index(req: IndexRequest) -> IndexStatus:
    try:
        workspace = str(safe_workspace(req.workspace_path))
    except PathError as e:
        return IndexStatus(status=f"error: {e}")

    task_id = str(uuid.uuid4())[:8]
    status = IndexStatus(status="indexing", task_id=task_id)
    _tasks[task_id] = status

    thread = threading.Thread(
        target=_index_worker,
        args=(task_id, workspace, req.force, req.files),
        daemon=True,
    )
    thread.start()
    return status


@router.get("/index/status/{task_id}")
def index_status(task_id: str) -> IndexStatus:
    if task_id not in _tasks:
        return IndexStatus(status="not_found", task_id=task_id)
    return _tasks[task_id]
