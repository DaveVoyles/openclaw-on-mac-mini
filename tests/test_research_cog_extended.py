"""Extended tests for ResearchCog — supplements test_research_cog.py."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import discord
import pytest

import cogs.research_cog as mod
from cooldowns import reset_cooldown

# ── Fixtures ─────────────────────────────────────────────────────────────────

class _FakeTree:
    def add_command(self, *a, **k): pass
    def remove_command(self, *a, **k): pass


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


def _make_interaction(user_id=1, done=False, is_thread=False):
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

    if is_thread:
        inter.channel = MagicMock(spec=discord.Thread)
        inter.channel.id = 200
        inter.channel.parent_id = 100
        inter.channel.send = AsyncMock()
    else:
        inter.channel = MagicMock()
        inter.channel.id = 100
        inter.channel.send = AsyncMock()

    inter.original_response = AsyncMock()
    fake_msg = AsyncMock()
    fake_thread = AsyncMock()
    fake_thread.id = 999
    fake_thread.send = AsyncMock()
    fake_msg.create_thread = AsyncMock(return_value=fake_thread)
    inter.original_response.return_value = fake_msg

    return inter


def _make_cog():
    return mod.ResearchCog(_FakeBot())


@pytest.fixture(autouse=True)
def _reset_research_cooldown():
    """Keep /research cooldown state from leaking across tests in this module."""
    reset_cooldown("research", 1)
    yield
    reset_cooldown("research", 1)


# ── cog_command_error ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cog_command_error_check_failure_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.CheckFailure("Not authorized")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    embed = inter.response.send_message.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


@pytest.mark.asyncio
async def test_cog_command_error_check_failure_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.CheckFailure("Blocked")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cog_command_error_generic():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.AppCommandError("boom")
    await cog.cog_command_error(inter, err)
    embed = inter.response.send_message.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── /websearch ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_websearch_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_search = AsyncMock(return_value="Here are the results: 1. Foo 2. Bar")
    mock_skills = MagicMock()
    mock_skills.search_web = mock_search

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"skills.advanced_skills": mock_skills}):
        await cog.websearch_cmd.callback(cog, inter, query="latest AI news", results=5)

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "latest AI news" in embed.title
    assert "results" in embed.description.lower() or "Foo" in embed.description


# ── /browse ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browse_cmd_invalid_url():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.browse_cmd.callback(cog, inter, url="not-a-url", question="")
    inter.response.send_message.assert_awaited_once()
    assert "must start with" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_browse_cmd_success_no_question():
    cog = _make_cog()
    inter = _make_interaction()

    mock_skills = MagicMock()
    mock_skills.browse_url = AsyncMock(return_value="Page content here.")

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"skills.advanced_skills": mock_skills}):
        await cog.browse_cmd.callback(cog, inter, url="https://example.com", question="")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "example.com" in embed.title
    assert "Page content" in embed.description


@pytest.mark.asyncio
async def test_browse_cmd_success_with_question():
    cog = _make_cog()
    inter = _make_interaction()

    mock_skills = MagicMock()
    mock_skills.browse_url = AsyncMock(return_value="The page talks about Python 3.12.")

    mock_llm = MagicMock()
    mock_llm.analyze_document = AsyncMock(return_value="Python 3.12 is the current stable version.")

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {
             "skills.advanced_skills": mock_skills,
             "llm": mock_llm,
         }):
        await cog.browse_cmd.callback(cog, inter, url="https://python.org", question="What version?")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "python.org" in embed.title


@pytest.mark.asyncio
async def test_browse_cmd_error_page():
    """When browse_url returns an error string, skip LLM analysis."""
    cog = _make_cog()
    inter = _make_interaction()

    mock_skills = MagicMock()
    mock_skills.browse_url = AsyncMock(return_value="❌ Connection refused")

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"skills.advanced_skills": mock_skills}):
        await cog.browse_cmd.callback(cog, inter, url="https://broken.example.com", question="What happened?")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "❌" in embed.description


# ── /research ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_cmd_emergency_stopped():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.research_cog.audit_log"), \
         patch("approvals.is_emergency_stopped", return_value=True):
        await cog.research_cmd.callback(cog, inter, query="test query", deep=False)

    inter.response.send_message.assert_awaited_once()
    assert "Emergency stop" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_research_cmd_llm_not_configured():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.research_cog.audit_log"), \
         patch("approvals.is_emergency_stopped", return_value=False), \
         patch("llm.is_configured", return_value=False):
        await cog.research_cmd.callback(cog, inter, query="test query", deep=False)

    inter.response.send_message.assert_awaited_once()
    assert "LLM not configured" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_research_cmd_success_with_thread():
    cog = _make_cog()
    inter = _make_interaction()

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value="# Research Report\n\nFindings here.")
    mock_agent.get_last_receipts = MagicMock(return_value={
        "vault": {"saved": True, "location": "vault/Research/q.md", "detail": "saved"},
        "vector": {"saved": True, "location": "research/r-1", "detail": "indexed"},
    })
    mock_agent.generate_follow_ups = AsyncMock(return_value=["What are the implications?", "Compare with X?"])

    mock_agent_cls = MagicMock(return_value=mock_agent)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=None)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("cogs.research_cog.audit_log"), \
         patch("approvals.is_emergency_stopped", return_value=False), \
         patch("llm.is_configured", return_value=True), \
         patch("research_agent.ResearchAgent", mock_agent_cls), \
         patch("runtime_state.request_context", return_value=mock_ctx):
        await cog.research_cmd.callback(cog, inter, query="AI in healthcare", deep=False)

    # Thread was created and report posted
    inter.original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_research_cmd_thread_creation_fails():
    """Research should continue even if thread creation fails."""
    cog = _make_cog()
    inter = _make_interaction()

    # Make create_thread fail
    fake_msg = AsyncMock()
    fake_msg.create_thread = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "Cannot create thread"))
    inter.original_response = AsyncMock(return_value=fake_msg)

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value="Research complete.")
    mock_agent.get_last_receipts = MagicMock(return_value={})
    mock_agent.generate_follow_ups = AsyncMock(return_value=[])

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=None)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("cogs.research_cog.audit_log"), \
         patch("approvals.is_emergency_stopped", return_value=False), \
         patch("llm.is_configured", return_value=True), \
         patch("research_agent.ResearchAgent", MagicMock(return_value=mock_agent)), \
         patch("runtime_state.request_context", return_value=mock_ctx):
        await cog.research_cmd.callback(cog, inter, query="test query", deep=False)

    # Should still send to followup
    inter.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_research_cmd_agent_failure():
    cog = _make_cog()
    inter = _make_interaction()

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=Exception("agent crashed"))
    mock_agent.get_last_receipts = MagicMock(return_value={})
    mock_agent.generate_follow_ups = AsyncMock(return_value=[])

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=None)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("cogs.research_cog.audit_log"), \
         patch("approvals.is_emergency_stopped", return_value=False), \
         patch("llm.is_configured", return_value=True), \
         patch("research_agent.ResearchAgent", MagicMock(return_value=mock_agent)), \
         patch("runtime_state.request_context", return_value=mock_ctx):
        await cog.research_cmd.callback(cog, inter, query="test", deep=False)

    # Should still complete and post error report
    inter.original_response.assert_awaited()


# ── /research-search ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_search_with_results():
    cog = _make_cog()
    inter = _make_interaction()

    results = [
        {
            "text": "AI research findings here",
            "similarity": 0.92,
            "metadata": {"query": "AI in healthcare"},
        }
    ]

    mock_vs = MagicMock()
    mock_vs.search = AsyncMock(return_value=results)
    mock_vs.RESEARCH_COLLECTION = "research"

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.research_search_cmd.callback(cog, inter, query="AI healthcare")

    inter.followup.send.assert_awaited_once()
    result = inter.followup.send.call_args[0][0]
    assert "AI in healthcare" in result
    assert "92%" in result


@pytest.mark.asyncio
async def test_research_search_no_results():
    cog = _make_cog()
    inter = _make_interaction()

    mock_vs = MagicMock()
    mock_vs.search = AsyncMock(return_value=[])
    mock_vs.RESEARCH_COLLECTION = "research"

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.research_search_cmd.callback(cog, inter, query="very obscure topic")

    result = inter.followup.send.call_args[0][0]
    assert "No matching research" in result


@pytest.mark.asyncio
async def test_research_search_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": None}):
        await cog.research_search_cmd.callback(cog, inter, query="test")

    result = inter.followup.send.call_args[0][0]
    assert "unavailable" in result


# ── /sources ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sources_with_results():
    cog = _make_cog()
    inter = _make_interaction()

    results = [
        {
            "text": "Python tutorial content",
            "similarity": 0.85,
            "metadata": {"url": "https://python.org/tutorial", "domain": "python.org", "type": "source"},
        }
    ]

    mock_vs = MagicMock()
    mock_vs.search = AsyncMock(return_value=results)
    mock_vs.RESEARCH_COLLECTION = "research"

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.sources_cmd.callback(cog, inter, query="Python")

    result = inter.followup.send.call_args[0][0]
    assert "python.org" in result


@pytest.mark.asyncio
async def test_sources_no_results():
    cog = _make_cog()
    inter = _make_interaction()

    mock_vs = MagicMock()
    mock_vs.search = AsyncMock(return_value=[])
    mock_vs.RESEARCH_COLLECTION = "research"

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": mock_vs}):
        await cog.sources_cmd.callback(cog, inter, query="nothing found")

    result = inter.followup.send.call_args[0][0]
    assert "No matching sources" in result


@pytest.mark.asyncio
async def test_sources_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"vector_store": None}):
        await cog.sources_cmd.callback(cog, inter, query="test")

    result = inter.followup.send.call_args[0][0]
    assert "unavailable" in result


# ── /compare ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compare_cmd_all_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_search_skills = MagicMock()
    mock_search_skills._perplexity_search = AsyncMock(return_value="Perplexity says: X")
    mock_search_skills._firecrawl_search = AsyncMock(return_value="Firecrawl says: Y")
    mock_search_skills.serper_search = AsyncMock(return_value="Serper says: Z")

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"skills.search_skills": mock_search_skills}):
        await cog.compare_cmd.callback(cog, inter, query="What is quantum computing?")

    # 3 providers → 3 followup sends
    assert inter.followup.send.await_count == 3


@pytest.mark.asyncio
async def test_compare_cmd_with_failures():
    cog = _make_cog()
    inter = _make_interaction()

    mock_search_skills = MagicMock()
    mock_search_skills._perplexity_search = AsyncMock(side_effect=Exception("API key missing"))
    mock_search_skills._firecrawl_search = AsyncMock(return_value="")  # Empty result
    mock_search_skills.serper_search = AsyncMock(return_value="Serper results here")

    with patch("cogs.research_cog.audit_log"), \
         patch.dict(sys.modules, {"skills.search_skills": mock_search_skills}):
        await cog.compare_cmd.callback(cog, inter, query="test comparison")

    assert inter.followup.send.await_count == 3
    # Check that failed provider shows error
    calls = inter.followup.send.call_args_list
    embeds = [c.kwargs.get("embed") for c in calls]
    descriptions = [e.description for e in embeds if e]
    assert any("Failed" in d or "API key" in d for d in descriptions)


# ── _ResearchView buttons ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_view_save_to_memory():
    view = mod._ResearchView(query="quantum computing", report="# Findings\n\nQuantum supremacy achieved.")
    inter = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.user = MagicMock()

    save_btn = next(btn for btn in view.children if "Save to Memory" in str(btn.label))

    with patch("qmd.remember_fact", AsyncMock(return_value="✅ Stored in memory")), \
         patch("cogs.research_cog.audit_log"):
        await save_btn.callback(inter)

    inter.response.send_message.assert_awaited_once()
    assert "Stored" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_research_view_save_to_vault_success():
    view = mod._ResearchView(query="AI safety", report="Report content about AI safety.")
    inter = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.followup.send = AsyncMock()
    inter.user = MagicMock()

    vault_btn = next(btn for btn in view.children if "Vault" in str(btn.label))

    with patch("obsidian_writer.save_to_vault", AsyncMock(return_value="Saved to vault/Research/AI safety.md")), \
         patch("cogs.research_cog.audit_log"):
        await vault_btn.callback(inter)

    inter.followup.send.assert_awaited_once()
    assert "Saved" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_research_view_save_to_vault_failure():
    view = mod._ResearchView(query="AI safety", report="Report content.")
    inter = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.followup.send = AsyncMock()
    inter.user = MagicMock()

    vault_btn = next(btn for btn in view.children if "Vault" in str(btn.label))

    with patch("obsidian_writer.save_to_vault", AsyncMock(side_effect=Exception("vault unavailable"))), \
         patch("cogs.research_cog.audit_log"):
        await vault_btn.callback(inter)

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title
