"""
Comprehensive tests for src/thread_store.py — SQLite-backed thread store.
"""
import asyncio
import json
import time

import pytest

import thread_store as ts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Give every test its own in-memory/file DB by resetting the module globals."""
    db_file = tmp_path / "test_threads.db"
    monkeypatch.setattr(ts, "DB_PATH", db_file)
    monkeypatch.setattr(ts, "_db", None)
    yield db_file
    # Teardown: close and clear
    if ts._db is not None:
        try:
            ts._db.close()
        except Exception:
            pass
    ts._db = None


# ---------------------------------------------------------------------------
# _get_db / _create_tables
# ---------------------------------------------------------------------------

def test_get_db_creates_file(isolated_db):
    db = ts._get_db()
    assert db is not None
    assert isolated_db.exists()


def test_get_db_returns_same_connection():
    db1 = ts._get_db()
    db2 = ts._get_db()
    assert db1 is db2


def test_tables_created():
    db = ts._get_db()
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "threads" in tables
    assert "messages" in tables
    assert "thread_tags" in tables


# ---------------------------------------------------------------------------
# create_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_thread_returns_int():
    tid = await ts.create_thread(user_id=1, channel_id=10)
    assert isinstance(tid, int)
    assert tid > 0


@pytest.mark.asyncio
async def test_create_thread_persists():
    tid = await ts.create_thread(user_id=42, channel_id=99, name="my-thread")
    db = ts._get_db()
    row = db.execute("SELECT * FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row is not None
    assert row["user_id"] == 42
    assert row["channel_id"] == 99
    assert row["name"] == "my-thread"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_create_thread_without_name():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    db = ts._get_db()
    row = db.execute("SELECT name FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["name"] is None


@pytest.mark.asyncio
async def test_create_multiple_threads_unique_ids():
    ids = [await ts.create_thread(user_id=1, channel_id=1) for _ in range(5)]
    assert len(set(ids)) == 5


# ---------------------------------------------------------------------------
# add_message / get_thread_messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_message_stores_content():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "Hello, world!")
    msgs = await ts.get_thread_messages(tid)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello, world!"


@pytest.mark.asyncio
async def test_add_message_increments_count():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    for i in range(3):
        await ts.add_message(tid, "user", f"msg {i}")
    db = ts._get_db()
    row = db.execute("SELECT message_count FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["message_count"] == 3


@pytest.mark.asyncio
async def test_add_message_updates_updated_at():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    db = ts._get_db()
    before = db.execute("SELECT updated_at FROM threads WHERE id = ?", (tid,)).fetchone()["updated_at"]
    await asyncio.sleep(0.01)
    await ts.add_message(tid, "user", "hi")
    after = db.execute("SELECT updated_at FROM threads WHERE id = ?", (tid,)).fetchone()["updated_at"]
    assert after >= before


@pytest.mark.asyncio
async def test_get_thread_messages_chronological_order():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    for i in range(5):
        await ts.add_message(tid, "user", f"message {i}")
    msgs = await ts.get_thread_messages(tid)
    contents = [m["content"] for m in msgs]
    assert contents == [f"message {i}" for i in range(5)]


@pytest.mark.asyncio
async def test_get_thread_messages_respects_limit():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    for i in range(10):
        await ts.add_message(tid, "user", f"msg {i}")
    msgs = await ts.get_thread_messages(tid, limit=3)
    assert len(msgs) == 3


@pytest.mark.asyncio
async def test_get_thread_messages_empty_thread():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    msgs = await ts.get_thread_messages(tid)
    assert msgs == []


@pytest.mark.asyncio
async def test_add_message_all_roles():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "from user")
    await ts.add_message(tid, "model", "from model")
    await ts.add_message(tid, "system", "from system")
    msgs = await ts.get_thread_messages(tid)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "model", "system"]


# ---------------------------------------------------------------------------
# get_thread_history_for_llm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_thread_history_for_llm_format():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "ping")
    await ts.add_message(tid, "model", "pong")
    history = await ts.get_thread_history_for_llm(tid)
    assert len(history) == 2
    assert history[0] == {"role": "user", "parts": ["ping"]}
    assert history[1] == {"role": "model", "parts": ["pong"]}


@pytest.mark.asyncio
async def test_get_thread_history_for_llm_empty():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    history = await ts.get_thread_history_for_llm(tid)
    assert history == []


# ---------------------------------------------------------------------------
# set_thread_title / set_thread_name / set_thread_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_thread_title():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.set_thread_title(tid, "My Great Thread")
    db = ts._get_db()
    row = db.execute("SELECT title FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["title"] == "My Great Thread"


@pytest.mark.asyncio
async def test_set_thread_name():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.set_thread_name(tid, "slug-name")
    db = ts._get_db()
    row = db.execute("SELECT name FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["name"] == "slug-name"


@pytest.mark.asyncio
async def test_set_thread_status_valid_values():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    for status in ("active", "archived", "pinned"):
        await ts.set_thread_status(tid, status)
        db = ts._get_db()
        row = db.execute("SELECT status FROM threads WHERE id = ?", (tid,)).fetchone()
        assert row["status"] == status


@pytest.mark.asyncio
async def test_set_thread_status_invalid_raises():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    with pytest.raises(ValueError, match="Invalid status"):
        await ts.set_thread_status(tid, "invalid")


# ---------------------------------------------------------------------------
# delete_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_thread_removes_thread():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "hi")
    await ts.delete_thread(tid)
    db = ts._get_db()
    row = db.execute("SELECT id FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row is None


@pytest.mark.asyncio
async def test_delete_thread_cascades_messages():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "msg1")
    await ts.add_message(tid, "model", "msg2")
    await ts.delete_thread(tid)
    db = ts._get_db()
    rows = db.execute("SELECT id FROM messages WHERE thread_id = ?", (tid,)).fetchall()
    assert rows == []


# ---------------------------------------------------------------------------
# list_user_threads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_user_threads_returns_own_threads():
    await ts.create_thread(user_id=1, channel_id=1)
    await ts.create_thread(user_id=1, channel_id=1)
    await ts.create_thread(user_id=2, channel_id=1)  # other user
    threads = await ts.list_user_threads(user_id=1)
    assert len(threads) == 2
    assert all(t["user_id"] == 1 for t in threads)


@pytest.mark.asyncio
async def test_list_user_threads_filter_by_status():
    tid1 = await ts.create_thread(user_id=1, channel_id=1)
    tid2 = await ts.create_thread(user_id=1, channel_id=1)
    await ts.set_thread_status(tid2, "archived")
    active = await ts.list_user_threads(user_id=1, status="active")
    archived = await ts.list_user_threads(user_id=1, status="archived")
    assert len(active) == 1
    assert active[0]["id"] == tid1
    assert len(archived) == 1
    assert archived[0]["id"] == tid2


@pytest.mark.asyncio
async def test_list_user_threads_respects_limit():
    for _ in range(10):
        await ts.create_thread(user_id=5, channel_id=1)
    threads = await ts.list_user_threads(user_id=5, limit=3)
    assert len(threads) == 3


@pytest.mark.asyncio
async def test_list_user_threads_sorted_by_updated_at():
    tid1 = await ts.create_thread(user_id=1, channel_id=1)
    tid2 = await ts.create_thread(user_id=1, channel_id=1)
    # Add a message to tid1 to make it more recent
    await ts.add_message(tid1, "user", "bump")
    threads = await ts.list_user_threads(user_id=1)
    assert threads[0]["id"] == tid1


@pytest.mark.asyncio
async def test_list_user_threads_empty():
    threads = await ts.list_user_threads(user_id=999)
    assert threads == []


# ---------------------------------------------------------------------------
# find_thread_by_name
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_thread_by_name_found():
    await ts.create_thread(user_id=1, channel_id=1, name="alpha")
    result = await ts.find_thread_by_name(user_id=1, name="alpha")
    assert result is not None
    assert result["name"] == "alpha"


@pytest.mark.asyncio
async def test_find_thread_by_name_not_found():
    result = await ts.find_thread_by_name(user_id=1, name="nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_find_thread_by_name_user_scoped():
    await ts.create_thread(user_id=1, channel_id=1, name="shared-name")
    result = await ts.find_thread_by_name(user_id=2, name="shared-name")
    assert result is None


# ---------------------------------------------------------------------------
# search_threads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_threads_by_title():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.set_thread_title(tid, "Python Tips and Tricks")
    results = await ts.search_threads(user_id=1, query="Python")
    assert any(r["id"] == tid for r in results)


@pytest.mark.asyncio
async def test_search_threads_by_name():
    tid = await ts.create_thread(user_id=1, channel_id=1, name="python-tips")
    results = await ts.search_threads(user_id=1, query="python")
    assert any(r["id"] == tid for r in results)


@pytest.mark.asyncio
async def test_search_threads_by_message_content():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "How do I configure Radarr settings?")
    results = await ts.search_threads(user_id=1, query="Radarr")
    assert any(r["id"] == tid for r in results)


@pytest.mark.asyncio
async def test_search_threads_no_cross_user():
    tid = await ts.create_thread(user_id=1, channel_id=1, name="secret-thread")
    results = await ts.search_threads(user_id=2, query="secret")
    assert not any(r["id"] == tid for r in results)


@pytest.mark.asyncio
async def test_search_threads_no_match():
    await ts.create_thread(user_id=1, channel_id=1, name="alpha")
    results = await ts.search_threads(user_id=1, query="zzznomatch")
    assert results == []


@pytest.mark.asyncio
async def test_search_threads_deduplicates():
    # Thread matching both title and message content should appear once
    tid = await ts.create_thread(user_id=1, channel_id=1, name="radarr-thread")
    await ts.set_thread_title(tid, "Radarr Config")
    await ts.add_message(tid, "user", "Radarr settings help")
    results = await ts.search_threads(user_id=1, query="Radarr")
    ids = [r["id"] for r in results]
    assert ids.count(tid) == 1


@pytest.mark.asyncio
async def test_search_threads_respects_limit():
    for i in range(15):
        tid = await ts.create_thread(user_id=1, channel_id=1, name=f"thread-{i}")
        await ts.add_message(tid, "user", "searchable content xyz")
    results = await ts.search_threads(user_id=1, query="xyz", limit=5)
    assert len(results) <= 5


# ---------------------------------------------------------------------------
# auto_archive_stale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_archive_stale_archives_old_threads(monkeypatch):
    tid = await ts.create_thread(user_id=1, channel_id=1)
    # Manually backdate the thread
    old_ts = time.time() - (10 * 86400)
    ts._get_db().execute("UPDATE threads SET updated_at = ? WHERE id = ?", (old_ts, tid))
    ts._get_db().commit()
    count = await ts.auto_archive_stale(days=7)
    assert count == 1
    db = ts._get_db()
    row = db.execute("SELECT status FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "archived"


@pytest.mark.asyncio
async def test_auto_archive_stale_skips_recent():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    count = await ts.auto_archive_stale(days=7)
    assert count == 0
    db = ts._get_db()
    row = db.execute("SELECT status FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_auto_archive_stale_skips_already_archived():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    old_ts = time.time() - (10 * 86400)
    db = ts._get_db()
    db.execute("UPDATE threads SET updated_at = ?, status = 'archived' WHERE id = ?", (old_ts, tid))
    db.commit()
    count = await ts.auto_archive_stale(days=7)
    assert count == 0


@pytest.mark.asyncio
async def test_auto_archive_stale_skips_pinned():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    old_ts = time.time() - (10 * 86400)
    db = ts._get_db()
    db.execute("UPDATE threads SET updated_at = ?, status = 'pinned' WHERE id = ?", (old_ts, tid))
    db.commit()
    count = await ts.auto_archive_stale(days=7)
    assert count == 0


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_stats_empty():
    stats = await ts.get_stats()
    assert stats["total_threads"] == 0
    assert stats["active_threads"] == 0
    assert stats["archived_threads"] == 0
    assert stats["total_messages"] == 0


@pytest.mark.asyncio
async def test_get_stats_counts_correctly():
    tid1 = await ts.create_thread(user_id=1, channel_id=1)
    tid2 = await ts.create_thread(user_id=1, channel_id=1)
    await ts.set_thread_status(tid2, "archived")
    await ts.add_message(tid1, "user", "hi")
    await ts.add_message(tid1, "model", "hello")
    stats = await ts.get_stats()
    assert stats["total_threads"] == 2
    assert stats["active_threads"] == 1
    assert stats["archived_threads"] == 1
    assert stats["total_messages"] == 2


# ---------------------------------------------------------------------------
# migrate_json_threads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_json_threads_nonexistent_dir(tmp_path):
    count = await ts.migrate_json_threads(tmp_path / "no_such_dir")
    assert count == 0


@pytest.mark.asyncio
async def test_migrate_json_threads_imports_files(tmp_path):
    thread_dir = tmp_path / "threads"
    thread_dir.mkdir()
    thread_file = thread_dir / "42_my-thread.json"
    thread_file.write_text(json.dumps({
        "name": "my-thread",
        "history": [
            {"role": "user", "parts": ["Hello"]},
            {"role": "model", "parts": ["Hi there"]},
        ],
    }))
    count = await ts.migrate_json_threads(thread_dir)
    assert count == 1
    # Verify the thread and messages were created
    result = await ts.find_thread_by_name(42, "my-thread")
    assert result is not None
    msgs = await ts.get_thread_messages(result["id"])
    assert len(msgs) == 2


@pytest.mark.asyncio
async def test_migrate_json_threads_idempotent(tmp_path):
    thread_dir = tmp_path / "threads"
    thread_dir.mkdir()
    (thread_dir / "1_test.json").write_text(json.dumps({
        "name": "test",
        "history": [{"role": "user", "parts": ["hi"]}],
    }))
    count1 = await ts.migrate_json_threads(thread_dir)
    count2 = await ts.migrate_json_threads(thread_dir)
    assert count1 == 1
    assert count2 == 0  # Already migrated


@pytest.mark.asyncio
async def test_migrate_json_threads_skips_empty_messages(tmp_path):
    thread_dir = tmp_path / "threads"
    thread_dir.mkdir()
    (thread_dir / "1_test.json").write_text(json.dumps({
        "name": "test",
        "history": [
            {"role": "user", "parts": ["   "]},  # Whitespace only — should be skipped
            {"role": "model", "parts": ["real content"]},
        ],
    }))
    count = await ts.migrate_json_threads(thread_dir)
    assert count == 1
    result = await ts.find_thread_by_name(1, "test")
    msgs = await ts.get_thread_messages(result["id"])
    assert len(msgs) == 1
    assert msgs[0]["content"] == "real content"


@pytest.mark.asyncio
async def test_migrate_json_threads_invalid_user_id(tmp_path):
    thread_dir = tmp_path / "threads"
    thread_dir.mkdir()
    # Filename without integer prefix → user_id=0
    (thread_dir / "badname.json").write_text(json.dumps({
        "name": "badname",
        "history": [{"role": "user", "parts": ["hi"]}],
    }))
    count = await ts.migrate_json_threads(thread_dir)
    assert count == 1
    result = await ts.find_thread_by_name(0, "badname")
    assert result is not None


@pytest.mark.asyncio
async def test_migrate_json_threads_handles_bad_json(tmp_path):
    thread_dir = tmp_path / "threads"
    thread_dir.mkdir()
    (thread_dir / "1_bad.json").write_text("not json at all {{{")
    count = await ts.migrate_json_threads(thread_dir)
    assert count == 0


# ---------------------------------------------------------------------------
# auto_title_thread (with mocked LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_title_thread_returns_none_too_few_messages():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    await ts.add_message(tid, "user", "hi")
    result = await ts.auto_title_thread(tid)
    assert result is None


@pytest.mark.asyncio
async def test_auto_title_thread_sets_title(monkeypatch):
    tid = await ts.create_thread(user_id=1, channel_id=1)
    for i in range(4):
        role = "user" if i % 2 == 0 else "model"
        await ts.add_message(tid, role, f"message {i}")

    async def fake_chat_deep(prompt):
        return "Great Conversation Title", None

    import sys
    fake_llm = type(sys)("llm")
    fake_llm.chat_deep = fake_chat_deep
    monkeypatch.setitem(sys.modules, "llm", fake_llm)

    result = await ts.auto_title_thread(tid)
    assert result == "Great Conversation Title"
    db = ts._get_db()
    row = db.execute("SELECT title FROM threads WHERE id = ?", (tid,)).fetchone()
    assert row["title"] == "Great Conversation Title"


@pytest.mark.asyncio
async def test_auto_title_thread_handles_llm_failure():
    tid = await ts.create_thread(user_id=1, channel_id=1)
    for i in range(4):
        await ts.add_message(tid, "user" if i % 2 == 0 else "model", f"msg {i}")
    # llm module not importable → should return None gracefully
    result = await ts.auto_title_thread(tid)
    assert result is None
