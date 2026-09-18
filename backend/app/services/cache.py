"""SQLite response cache — skips the LLM call for an identical review.

The cache key is derived from the prompt, provider and model only. API keys are
never part of the key and never stored.
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

DEFAULT_TTL_SECONDS = 3600  # reviews go stale as code changes

_connections: dict[str, sqlite3.Connection] = {}


def get_db(db_path: str) -> sqlite3.Connection:
    """Open (and remember) the cache DB for a workspace."""
    if db_path not in _connections:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS review_cache (
                key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        conn.commit()
        _connections[db_path] = conn
    return _connections[db_path]


def make_key(prompt: str, provider: str, model: str, temperature: float = 0.1) -> str:
    raw = f"{provider}\x00{model}\x00{temperature}\x00{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(conn: sqlite3.Connection, key: str, ttl: int = DEFAULT_TTL_SECONDS) -> dict | None:
    """Return the cached response, or None if missing or expired."""
    row = conn.execute(
        "SELECT response, created_at FROM review_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None

    response, created_at = row
    if time.time() - created_at > ttl:
        conn.execute("DELETE FROM review_cache WHERE key = ?", (key,))
        conn.commit()
        return None

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return None


def put(
    conn: sqlite3.Connection, key: str, response: dict, provider: str, model: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO review_cache VALUES (?, ?, ?, ?, ?)",
        (key, json.dumps(response), provider, model, time.time()),
    )
    conn.commit()


def clear(conn: sqlite3.Connection) -> int:
    """Drop every cached response. Returns how many were removed."""
    count = conn.execute("SELECT COUNT(*) FROM review_cache").fetchone()[0]
    conn.execute("DELETE FROM review_cache")
    conn.commit()
    return count


if __name__ == "__main__":
    import tempfile

    db = get_db(str(Path(tempfile.mkdtemp()) / "cache.db"))
    k = make_key("prompt", "ollama", "m")
    print("miss:", get(db, k))
    put(db, k, {"findings": []}, "ollama", "m")
    print("hit:", get(db, k))
    print("expired:", get(db, k, ttl=0))
