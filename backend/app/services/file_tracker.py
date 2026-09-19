"""Track file hashes for incremental indexing."""

import hashlib
import sqlite3
import subprocess
from pathlib import Path

from app.services.chunker import SUPPORTED_EXTENSIONS

# ponytail: sqlite, stdlib, no ORM
SCHEMA = """
CREATE TABLE IF NOT EXISTS file_hashes (
    file_path TEXT PRIMARY KEY,
    hash TEXT NOT NULL
)
"""

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "out", ".codelens",
}


def get_db(db_path: str = ".codelens/file_tracker.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def file_hash(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def iter_candidate_files(workspace: str):
    """Yield indexable files in the workspace.

    In a git repo, ask git for the file list: it applies .gitignore for free, so
    build output, vendored copies and dependency folders stay out of the index.
    Indexing a gitignored duplicate of the source is worse than useless — it
    fills search results with the same symbol twice.
    """
    root = Path(workspace)
    listed_by_git = False

    if (root / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if out.returncode == 0:
                listed_by_git = True
                for line in out.stdout.splitlines():
                    p = root / line.strip()
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                        yield p
        except (OSError, subprocess.SubprocessError):
            listed_by_git = False

    if listed_by_git:
        return

    for path in root.rglob("*"):
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def get_changed_files(workspace: str, db: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Return (new_or_modified, deleted) file lists."""
    # Get all tracked hashes
    tracked = dict(db.execute("SELECT file_path, hash FROM file_hashes").fetchall())

    current_files: set[str] = set()
    changed: list[str] = []

    for path in iter_candidate_files(workspace):
        fpath = str(path)
        current_files.add(fpath)
        h = file_hash(fpath)

        if fpath not in tracked or tracked[fpath] != h:
            changed.append(fpath)

    deleted = [f for f in tracked if f not in current_files]
    return changed, deleted


def update_hash(db: sqlite3.Connection, file_path: str) -> None:
    h = file_hash(file_path)
    db.execute(
        "INSERT OR REPLACE INTO file_hashes (file_path, hash) VALUES (?, ?)",
        (file_path, h),
    )
    db.commit()


def remove_hash(db: sqlite3.Connection, file_path: str) -> None:
    db.execute("DELETE FROM file_hashes WHERE file_path = ?", (file_path,))
    db.commit()


if __name__ == "__main__":
    import tempfile, os
    tmp = tempfile.mkdtemp()
    db = get_db(os.path.join(tmp, "test.db"))
    # Should find current directory files
    changed, deleted = get_changed_files(".", db)
    print(f"Changed: {len(changed)}, Deleted: {len(deleted)}")
    if changed:
        print(f"First: {changed[0]}")
