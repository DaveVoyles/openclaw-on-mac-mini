"""Tests for MemoryCog commands."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import pytest

import cogs.memory_cog as mod

# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeTree:
    def add_command(self, *a, **k): pass
    def remove_command(self, *a, **k): pass


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


def _make_interaction(user_id=1, done=False):
    inter = AsyncMock()
    inter.user.id = user_id
    inter.user.display_name = "TestUser"
    inter.user.__str__ = lambda self: "TestUser#0001"
    inter.channel_id = 100
    inter.guild_id = 999
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.is_done = MagicMock(return_value=done)
    inter.followup.send = AsyncMock()
    return inter


def _make_cog():
    return mod.MemoryCog(_FakeBot())


# ── cog_command_error ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cog_command_error_check_failure_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.CheckFailure("Not authorized")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "Not authorized" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_command_error_check_failure_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.CheckFailure("Blocked")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cog_command_error_generic_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.AppCommandError("exploded")
    await cog.cog_command_error(inter, err)
    msg = inter.response.send_message.call_args[0][0]
    assert "Command failed" in msg


# ── /remember ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.memory_cog.audit_log"), \
         patch("qmd.remember_fact", AsyncMock(return_value="✅ Fact stored")):
        await cog.remember_cmd.callback(cog, inter, content="Remember this", tags="work,ai")

    inter.response.send_message.assert_awaited_once()
    assert "Fact stored" in inter.response.send_message.call_args[0][0]


# ── /recall ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.memory_cog.audit_log"), \
         patch("qmd.recall_fact", AsyncMock(return_value="Here is what I found: XYZ")):
        await cog.recall_cmd.callback(cog, inter, query="machine learning")

    inter.response.send_message.assert_awaited_once()
    embed = inter.response.send_message.call_args.kwargs.get("embed")
    assert "machine learning" in embed.title
    assert "XYZ" in embed.description


# ── /goals ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goals_cmd_no_goals():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("goal_tracker.get_active_goals", return_value=[]):
        await cog.goals_cmd.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()
    assert "No active goals" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_goals_cmd_with_goals():
    cog = _make_cog()
    inter = _make_interaction()

    goals = [
        {"goal": "Learn Python", "mention_count": 3, "created_at": 1700000000},
        {"goal": "Ship MVP", "mention_count": 1, "created_at": 1700001000},
    ]

    with patch("goal_tracker.get_active_goals", return_value=goals):
        await cog.goals_cmd.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()
    embed = inter.response.send_message.call_args.kwargs.get("embed")
    assert "Active Goals" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "Learn Python" in field_names


# ── /memory-stats ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_stats_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_qmd = MagicMock()
    mock_qmd.qmd_store = MagicMock()
    mock_qmd.qmd_store._memory = {"key1": "val1", "key2": "val2"}

    mock_vector_store = MagicMock()
    mock_vector_store.get_stats = AsyncMock(return_value={
        "memories": {"count": 100},
        "research": {"count": 50},
    })

    mock_thread_stats = AsyncMock(return_value={
        "total_threads": 10,
        "active_threads": 5,
        "archived_threads": 5,
        "total_messages": 200,
    })

    with patch.dict(sys.modules, {
        "qmd": mock_qmd,
        "vector_store": mock_vector_store,
    }), patch("thread_store.get_stats", mock_thread_stats):
        await cog.memory_stats_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    result = inter.followup.send.call_args[0][0]
    assert "Memory Statistics" in result
    assert "QMD Facts" in result


@pytest.mark.asyncio
async def test_memory_stats_all_unavailable():
    cog = _make_cog()
    inter = _make_interaction()

    # Patch sys.modules to cause import errors
    broken_qmd = MagicMock()
    type(broken_qmd).qmd_store = property(MagicMock(side_effect=Exception("no qmd")))

    with patch.dict(sys.modules, {"qmd": None, "vector_store": None}), \
         patch("thread_store.get_stats", AsyncMock(side_effect=Exception("no threads"))):
        await cog.memory_stats_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    result = inter.followup.send.call_args[0][0]
    assert "unavailable" in result


# ── /memory-refresh ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_refresh_success():
    cog = _make_cog()
    inter = _make_interaction()

    results = [
        {"id": "abc", "text": "Machine learning fundamentals", "similarity": 0.95, "collection": "memories"},
        {"id": "def", "text": "Deep learning paper", "similarity": 0.88, "collection": "research"},
    ]

    mock_vs = MagicMock()
    mock_vs.search_all = AsyncMock(return_value=results)
    mock_vs.bump_access = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.memory_refresh_cmd.callback(cog, inter, query="machine learning")

    inter.followup.send.assert_awaited_once()
    result = inter.followup.send.call_args[0][0]
    assert "Reinforced" in result
    assert "Machine learning" in result


@pytest.mark.asyncio
async def test_memory_refresh_no_results():
    cog = _make_cog()
    inter = _make_interaction()

    mock_vs = MagicMock()
    mock_vs.search_all = AsyncMock(return_value=[])

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.memory_refresh_cmd.callback(cog, inter, query="obscure topic")

    assert "No matching memories" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_memory_refresh_exception():
    cog = _make_cog()
    inter = _make_interaction()

    mock_vs = MagicMock()
    mock_vs.search_all = AsyncMock(side_effect=Exception("vector DB down"))

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.memory_refresh_cmd.callback(cog, inter, query="test")

    assert "Refresh failed" in inter.followup.send.call_args[0][0]


# ── /rules ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rules_list_all():
    cog = _make_cog()
    inter = _make_interaction()

    rules = [{"rule": "Be concise", "id": "r1"}, {"rule": "No sarcasm", "id": "r2"}]

    mock_ui = MagicMock()
    mock_embed = MagicMock()
    mock_ui.paginate_items = MagicMock(return_value=[mock_embed])
    mock_ui.PaginationView = MagicMock()

    mock_rules = MagicMock()
    mock_rules.get_all_rules = AsyncMock(return_value=rules)
    mock_rules.get_relevant_rules = AsyncMock(return_value=[])
    mock_rules.delete_rule = AsyncMock(return_value=True)

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": mock_rules, "ui_components": mock_ui}):
        await cog.rules_cmd.callback(cog, inter, action="list", query="")

    inter.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_rules_list_empty():
    cog = _make_cog()
    inter = _make_interaction()

    mock_rules = MagicMock()
    mock_rules.get_all_rules = AsyncMock(return_value=[])

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": mock_rules}):
        await cog.rules_cmd.callback(cog, inter, action="list", query="")

    assert "No learned rules" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_rules_search_found():
    cog = _make_cog()
    inter = _make_interaction()

    mock_rules = MagicMock()
    mock_rules.get_relevant_rules = AsyncMock(return_value=["Be concise", "Use bullet points"])

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": mock_rules}):
        await cog.rules_cmd.callback(cog, inter, action="search", query="format")

    result = inter.followup.send.call_args[0][0]
    assert "Be concise" in result


@pytest.mark.asyncio
async def test_rules_search_not_found():
    cog = _make_cog()
    inter = _make_interaction()

    mock_rules = MagicMock()
    mock_rules.get_relevant_rules = AsyncMock(return_value=[])

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": mock_rules}):
        await cog.rules_cmd.callback(cog, inter, action="search", query="xyz")

    assert "No matching rules" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_rules_delete_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_rules = MagicMock()
    mock_rules.delete_rule = AsyncMock(return_value=True)

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": mock_rules}):
        await cog.rules_cmd.callback(cog, inter, action="delete", query="r1")

    assert "deleted" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_rules_delete_not_found():
    cog = _make_cog()
    inter = _make_interaction()

    mock_rules = MagicMock()
    mock_rules.delete_rule = AsyncMock(return_value=False)

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": mock_rules}):
        await cog.rules_cmd.callback(cog, inter, action="delete", query="missing-id")

    assert "not found" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_rules_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"rules_engine": None}):
        await cog.rules_cmd.callback(cog, inter, action="list", query="")

    assert "unavailable" in inter.followup.send.call_args[0][0]


# ── /profile ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_profile_cmd_full_profile():
    cog = _make_cog()
    inter = _make_interaction()

    profile = {
        "preferences": {"timezone": "US/Eastern", "verbosity": "concise"},
        "interests": ["AI", "basketball", "space"],
        "tools": ["Python", "VSCode"],
        "working_style": "async",
        "communication_style": "direct",
        "context_notes": ["User prefers bullet points", "User is in US Eastern timezone"],
    }

    mock_up = MagicMock()
    mock_up.load_profile = MagicMock(return_value=profile)

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_cmd.callback(cog, inter)

    result = inter.followup.send.call_args[0][0]
    assert "AI" in result
    assert "Python" in result
    assert "async" in result
    assert "direct" in result
    assert "bullet points" in result


@pytest.mark.asyncio
async def test_profile_cmd_empty_profile():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.load_profile = MagicMock(return_value={})

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_cmd.callback(cog, inter)

    result = inter.followup.send.call_args[0][0]
    assert "Empty" in result


@pytest.mark.asyncio
async def test_profile_cmd_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": None}):
        await cog.profile_cmd.callback(cog, inter)

    assert "unavailable" in inter.followup.send.call_args[0][0]


# ── /profile-edit ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_profile_edit_preference():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.update_preference = MagicMock()
    mock_up.sync_profile_to_vectors = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_edit_cmd.callback(cog, inter, field="preference", value="timezone=US/Eastern")

    result = inter.followup.send.call_args[0][0]
    assert "timezone" in result
    assert "US/Eastern" in result


@pytest.mark.asyncio
async def test_profile_edit_interest():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.add_interest = MagicMock()
    mock_up.sync_profile_to_vectors = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_edit_cmd.callback(cog, inter, field="interest", value="robotics")

    assert "Interest added" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_profile_edit_note():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.add_context_note = MagicMock()
    mock_up.sync_profile_to_vectors = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_edit_cmd.callback(cog, inter, field="note", value="I prefer morning updates")

    assert "Context note added" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_profile_edit_working_style():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.update_field = MagicMock()
    mock_up.sync_profile_to_vectors = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_edit_cmd.callback(cog, inter, field="working_style", value="deep work")

    assert "Working Style updated" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_profile_edit_communication_style():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.update_field = MagicMock()
    mock_up.sync_profile_to_vectors = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_edit_cmd.callback(cog, inter, field="communication_style", value="direct")

    assert "Communication Style updated" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_profile_edit_unknown_field():
    cog = _make_cog()
    inter = _make_interaction()

    mock_up = MagicMock()
    mock_up.sync_profile_to_vectors = AsyncMock()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": mock_up}):
        await cog.profile_edit_cmd.callback(cog, inter, field="zodiac", value="Aries")

    assert "Unknown field" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_profile_edit_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.memory_cog.audit_log"), \
         patch.dict(sys.modules, {"user_profile": None}):
        await cog.profile_edit_cmd.callback(cog, inter, field="interest", value="AI")

    assert "Update failed" in inter.followup.send.call_args[0][0]


# ── /export-conversations ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_conversations_success():
    cog = _make_cog()
    inter = _make_interaction()

    threads = [{"id": "t1", "title": "Chat 1"}, {"id": "t2", "title": "Chat 2"}]
    messages = [{"role": "user", "content": "hello"}]

    with patch("cogs.memory_cog.audit_log"), \
         patch("thread_store.list_user_threads", AsyncMock(return_value=threads)), \
         patch("thread_store.get_thread_messages", AsyncMock(return_value=messages)):
        await cog.export_conversations_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    kwargs = inter.followup.send.call_args.kwargs
    assert "Exported 2 conversation" in kwargs.get("content", "") or "Exported 2 conversation" in inter.followup.send.call_args[0][0]
    assert "file" in kwargs


@pytest.mark.asyncio
async def test_export_conversations_empty():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.memory_cog.audit_log"), \
         patch("thread_store.list_user_threads", AsyncMock(return_value=[])), \
         patch("thread_store.get_thread_messages", AsyncMock(return_value=[])):
        await cog.export_conversations_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
