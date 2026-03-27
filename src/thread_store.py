"""
OpenClaw Thread Store — Phase 13B
SQLite-backed persistent conversation threads.

Replaces the JSON-file based thread storage with a proper database that
supports search, metadata, and unlimited message history.

Tables:
  - threads:     Thread metadata (title, status, timestamps)
  - messages:    Individual messages within threads
  - thread_tags: Tag associations for threads
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("openclaw.thread_store")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(os.getenv("THREAD_DB_PATH", "/memory/openclaw.db"))
# Soft limit: how many recent messages to load into LLM context
CONTEXT_WINDOW_SIZE = int(os.getenv("THREAD_CONTEXT_WINDOW", "30"))

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

_db: Optional[sqlite3.Connection] = None
_lock = asyncio.Lock()


def _get_db() -> sqlite3.Connection:
    """Return the SQLite connection, creating tables on first call."""
    global _db
    if _db is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA foreign_keys=ON")
        _create_tables(_db)
        log.info("Thread store initialized at %s", DB_PATH)
    return _db


def _create_tables(db: sqlite3.Connection):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS threads (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            channel_id  INTEGER NOT NULL,
            name        TEXT,
            title       TEXT,
            status      TEXT DEFAULT 'active' CHECK(status IN ('active','archived','pinned')),
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            message_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id   INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            role        TEXT NOT NULL CHECK(role IN ('user','model','system')),
            content     TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            token_estimate INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS thread_tags (
            thread_id   INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            tag         TEXT NOT NULL,
            PRIMARY KEY (thread_id, tag)
        );

        CREATE INDEX IF NOT EXISTS idx_threads_user ON threads(user_id);
        CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
        CREATE INDEX IF NOT EXISTS idx_threads_updated ON threads(updated_at);
        CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Thread CRUD
# ---------------------------------------------------------------------------


async def create_thread(
    user_id: int, channel_id: int, name: Optional[str] = None
) -> int:
    """Create a new thread. Returns the thread ID."""
    now = time.time()

    async with _lock:
        def _insert():
            db = _get_db()
            cur = db.execute(
                "INSERT INTO threads (user_id, channel_id, name, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'active', ?, ?)",
                (user_id, channel_id, name, now, now),
            )
            db.commit()
            return cur.lastrowid

        loop = asyncio.get_running_loop()
        thread_id = await loop.run_in_executor(None, _insert)

    log.info("Created thread %d for user %d (name=%s)", thread_id, user_id, name)
    return thread_id


async def add_message(
    thread_id: int, role: str, content: str
) -> None:
    """Add a message to a thread."""
    now = time.time()
    token_est = len(content) // 4  # rough estimate

    async with _lock:
        def _insert():
            db = _get_db()
            db.execute(
                "INSERT INTO messages (thread_id, role, content, timestamp, token_estimate) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, role, content, now, token_est),
            )
            db.execute(
                "UPDATE threads SET updated_at = ?, message_count = message_count + 1 "
                "WHERE id = ?",
                (now, thread_id),
            )
            db.commit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _insert)


async def get_thread_messages(
    thread_id: int, limit: Optional[int] = None
) -> list[dict]:
    """Get messages from a thread, most recent last."""
    limit = limit or CONTEXT_WINDOW_SIZE

    async with _lock:
        def _query():
            db = _get_db()
            rows = db.execute(
                "SELECT role, content, timestamp FROM messages "
                "WHERE thread_id = ? ORDER BY timestamp DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
            # Reverse to chronological order
            return [dict(r) for r in reversed(rows)]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _query)


async def get_thread_history_for_llm(thread_id: int) -> list[dict]:
    """Get thread messages in Gemini-compatible format for LLM context.

    Uses a sliding window: loads CONTEXT_WINDOW_SIZE recent messages.
    """
    messages = await get_thread_messages(thread_id, limit=CONTEXT_WINDOW_SIZE)
    return [
        {"role": msg["role"], "parts": [msg["content"]]}
        for msg in messages
    ]


async def set_thread_title(thread_id: int, title: str) -> None:
    """Set a human-readable title for a thread."""
    async with _lock:
        def _update():
            db = _get_db()
            db.execute("UPDATE threads SET title = ? WHERE id = ?", (title, thread_id))
            db.commit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _update)


async def set_thread_name(thread_id: int, name: str) -> None:
    """Set the slug/name for a thread."""
    async with _lock:
        def _update():
            db = _get_db()
            db.execute("UPDATE threads SET name = ? WHERE id = ?", (name, thread_id))
            db.commit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _update)


async def set_thread_status(thread_id: int, status: str) -> None:
    """Change thread status (active/archived/pinned)."""
    if status not in ("active", "archived", "pinned"):
        raise ValueError(f"Invalid status: {status}")

    async with _lock:
        def _update():
            db = _get_db()
            db.execute("UPDATE threads SET status = ? WHERE id = ?", (status, thread_id))
            db.commit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _update)


async def delete_thread(thread_id: int) -> None:
    """Delete a thread and all its messages."""
    async with _lock:
        def _delete():
            db = _get_db()
            db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            db.commit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _delete)


# ---------------------------------------------------------------------------
# Thread queries
# ---------------------------------------------------------------------------


async def list_user_threads(
    user_id: int,
    status: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """List threads for a user, sorted by most recently active."""

    async with _lock:
        def _query():
            db = _get_db()
            sql = "SELECT * FROM threads WHERE user_id = ?"
            params: list = [user_id]
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in db.execute(sql, params).fetchall()]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _query)


async def find_thread_by_name(user_id: int, name: str) -> Optional[dict]:
    """Find a thread by user and name."""
    async with _lock:
        def _query():
            db = _get_db()
            row = db.execute(
                "SELECT * FROM threads WHERE user_id = ? AND name = ?",
                (user_id, name),
            ).fetchone()
            return dict(row) if row else None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _query)


async def search_threads(
    user_id: int, query: str, limit: int = 10
) -> list[dict]:
    """Search threads by title, name, or message content (keyword search)."""
    query_like = f"%{query}%"

    async with _lock:
        def _search():
            db = _get_db()
            # Search in thread title/name
            thread_hits = db.execute(
                "SELECT DISTINCT t.* FROM threads t "
                "WHERE t.user_id = ? AND (t.title LIKE ? OR t.name LIKE ?) "
                "ORDER BY t.updated_at DESC LIMIT ?",
                (user_id, query_like, query_like, limit),
            ).fetchall()

            # Search in message content
            msg_hits = db.execute(
                "SELECT DISTINCT t.* FROM threads t "
                "JOIN messages m ON m.thread_id = t.id "
                "WHERE t.user_id = ? AND m.content LIKE ? "
                "ORDER BY t.updated_at DESC LIMIT ?",
                (user_id, query_like, limit),
            ).fetchall()

            # Merge and deduplicate
            seen = set()
            results = []
            for row in list(thread_hits) + list(msg_hits):
                d = dict(row)
                if d["id"] not in seen:
                    seen.add(d["id"])
                    results.append(d)
            return results[:limit]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _search)


async def auto_archive_stale(days: int = 7) -> int:
    """Archive threads inactive for more than `days` days. Returns count."""
    cutoff = time.time() - (days * 86400)

    async with _lock:
        def _archive():
            db = _get_db()
            cur = db.execute(
                "UPDATE threads SET status = 'archived' "
                "WHERE status = 'active' AND updated_at < ?",
                (cutoff,),
            )
            db.commit()
            return cur.rowcount

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _archive)


# ---------------------------------------------------------------------------
# Migration: import existing JSON threads
# ---------------------------------------------------------------------------


async def migrate_json_threads(threads_dir: Path) -> int:
    """Import existing JSON thread files into SQLite. Safe to re-run (idempotent)."""
    if not threads_dir.exists():
        return 0

    count = 0
    for f in threads_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            name = data.get("name", f.stem)
            history = data.get("history", [])
            saved_at = data.get("saved_at", time.time())
            user_name = data.get("user_name", "User")

            # Extract user_id from filename (format: {user_id}_{name}.json)
            parts = f.stem.split("_", 1)
            try:
                user_id = int(parts[0])
            except (ValueError, IndexError):
                user_id = 0

            # Check if already migrated
            existing = await find_thread_by_name(user_id, name)
            if existing:
                continue

            thread_id = await create_thread(user_id, channel_id=0, name=name)

            for msg in history:
                role = msg.get("role", "user")
                parts_list = msg.get("parts", [])
                content = " ".join(str(p) for p in parts_list)
                if content.strip():
                    await add_message(thread_id, role, content)

            count += 1
            log.info("Migrated thread '%s' (%d messages)", name, len(history))

        except Exception as e:
            log.warning("Failed to migrate %s: %s", f.name, e)

    if count:
        log.info("✅ Migrated %d JSON threads to SQLite", count)
    return count


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def get_stats() -> dict:
    """Return thread store statistics."""
    async with _lock:
        def _stats():
            db = _get_db()
            total = db.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            active = db.execute("SELECT COUNT(*) FROM threads WHERE status='active'").fetchone()[0]
            archived = db.execute("SELECT COUNT(*) FROM threads WHERE status='archived'").fetchone()[0]
            messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            return {
                "total_threads": total,
                "active_threads": active,
                "archived_threads": archived,
                "total_messages": messages,
            }

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _stats)
