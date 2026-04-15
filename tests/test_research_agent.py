"""Tests for research_agent module — ResearchAgent with mocked dependencies."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from research_agent import ResearchAgent, run_scheduled_research


@pytest.fixture(autouse=True)
def _disable_copilot_proxy():
    """Prevent xdist worker contamination from COPILOT_PROXY_ENABLED=True.

    Some test files in the same worker may enable the Copilot proxy. Patch it
    to False here so research_agent functions route through llm.chat_deep as
    the tests expect.
    """
    import llm.providers as _prov
    with patch.object(_prov, "COPILOT_PROXY_ENABLED", False):
        yield


@pytest.fixture
def agent():
    return ResearchAgent(max_searches=2, browse_top_n=1, timeout_seconds=10, max_concurrent=2)


@pytest.mark.asyncio
async def test_research_timeout():
    """Research agent returns timeout message when overall timeout hit."""
    agent = ResearchAgent(max_searches=2, browse_top_n=1, timeout_seconds=0.1, max_concurrent=2)
    import asyncio

    async def _hang(*args, **kwargs):
        await asyncio.sleep(999)

    with patch.object(agent, "_research", side_effect=_hang):
        result = await agent.run("test query")
        assert "timed out" in result.lower()


@pytest.mark.asyncio
async def test_research_error_handling(agent):
    """Research agent handles unexpected errors gracefully."""
    with patch.object(agent, "_research", new_callable=AsyncMock) as mock_research:
        mock_research.side_effect = RuntimeError("Boom")
        result = await agent.run("test query")
        assert "failed" in result.lower()


@pytest.mark.asyncio
async def test_plan_searches_fallback(agent):
    """When plan_searches fails, raw query is used as fallback."""
    with patch.object(agent, "_plan_searches", new_callable=AsyncMock) as mock_plan, \
         patch.object(agent, "_perform_searches", new_callable=AsyncMock) as mock_search, \
         patch.object(agent, "_fetch_pages", new_callable=AsyncMock) as mock_fetch, \
         patch.object(agent, "_synthesize", new_callable=AsyncMock) as mock_synth, \
         patch.object(agent, "_auto_save", new_callable=AsyncMock):
        mock_plan.return_value = []  # planning fails
        mock_search.return_value = [{"query": "test", "results": "result text", "urls": []}]
        mock_fetch.return_value = []
        mock_synth.return_value = "Synthesized report"

        result = await agent.run("test query")
        assert result == "Synthesized report"
        # Verify the raw query was used
        search_args = mock_search.call_args[0][0]
        assert "test query" in search_args


@pytest.mark.asyncio
async def test_url_prioritization(agent):
    """Social media URLs are deprioritized."""
    urls = [
        "https://twitter.com/post/123",
        "https://example.com/article",
        "https://reddit.com/r/test",
        "https://news.example.org/report",
    ]
    result = agent._prioritize_urls(urls)
    # Should rank by source quality (.org > generic > social media)
    assert result[0] == "https://news.example.org/report"


@pytest.mark.asyncio
async def test_url_deduplication():
    """Duplicate URLs are removed."""
    agent = ResearchAgent(max_searches=2, browse_top_n=5, timeout_seconds=10, max_concurrent=2)
    urls = [
        "https://example.com/page",
        "https://example.com/page",
        "https://other.com/page",
    ]
    result = agent._prioritize_urls(urls)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_generate_follow_ups(agent):
    """generate_follow_ups returns up to 3 follow-up questions."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = (
            "How does X compare to Y in cost?\n"
            "What are the long-term risks of Z?\n"
            "Which vendors offer the best support?\n",
            [],
        )
        result = await agent.generate_follow_ups("test query", "some report text")
        assert isinstance(result, list)
        assert len(result) == 3
        assert "cost" in result[0].lower()


@pytest.mark.asyncio
async def test_generate_follow_ups_error(agent):
    """generate_follow_ups returns empty list on failure."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = RuntimeError("LLM down")
        result = await agent.generate_follow_ups("test query", "report")
        assert result == []


@pytest.mark.asyncio
async def test_data_truncation(agent):
    """Combined research data is truncated to prevent context overflow."""
    with patch.object(agent, "_plan_searches", new_callable=AsyncMock) as mock_plan, \
         patch.object(agent, "_perform_searches", new_callable=AsyncMock) as mock_search, \
         patch.object(agent, "_fetch_pages", new_callable=AsyncMock) as mock_fetch, \
         patch.object(agent, "_synthesize", new_callable=AsyncMock) as mock_synth, \
         patch.object(agent, "_auto_save", new_callable=AsyncMock):
        mock_plan.return_value = ["q1"]
        # Return a very large result
        mock_search.return_value = [{"query": "q1", "results": "x" * 50000, "urls": []}]
        mock_fetch.return_value = []
        mock_synth.return_value = "Final report"

        await agent.run("test")
        # Check that the data passed to _synthesize was truncated
        synth_args = mock_synth.call_args
        data = synth_args[0][1]  # second positional arg (data)
        assert len(data) <= 41000  # 40K + "[...truncated...]" suffix


@pytest.mark.asyncio
async def test_research_records_persistence_receipts(agent):
    """Research run exposes explicit persistence receipts for UI surfaces."""
    fake_vs = SimpleNamespace(
        RESEARCH_COLLECTION="research",
        search=AsyncMock(return_value=[]),
        add_research_report=AsyncMock(return_value="research_123"),
    )

    with patch.object(agent, "_plan_searches", new_callable=AsyncMock) as mock_plan, \
         patch.object(agent, "_perform_searches", new_callable=AsyncMock) as mock_search, \
         patch.object(agent, "_fetch_pages", new_callable=AsyncMock) as mock_fetch, \
         patch.object(agent, "_synthesize", new_callable=AsyncMock) as mock_synth, \
         patch.object(agent, "_auto_save", new_callable=AsyncMock) as mock_auto_save:
        mock_plan.return_value = ["q1"]
        mock_search.return_value = [{"query": "q1", "results": "x", "urls": ["https://example.com"]}]
        mock_fetch.return_value = []
        mock_synth.return_value = "Final report"
        mock_auto_save.return_value = {
            "vault": {"saved": True, "location": "data/vault/Research/test.md", "detail": "ok"},
            "gdoc": {"saved": False, "location": "google-docs", "detail": "Skipped"},
        }

        with patch.dict(sys.modules, {"vector_store": fake_vs}):
            result = await agent.run("test query")

        assert result == "Final report"
        receipts = agent.get_last_receipts()
        assert receipts["vault"]["saved"] is True
        assert receipts["vault"]["location"] == "data/vault/Research/test.md"
        assert receipts["vector"]["saved"] is True
        assert receipts["vector"]["location"] == "research/research_123"


@pytest.mark.asyncio
async def test_research_receipts_capture_vector_index_failures(agent):
    """Vector indexing failures are represented in persistence receipts."""
    fake_vs = SimpleNamespace(
        RESEARCH_COLLECTION="research",
        search=AsyncMock(return_value=[]),
        add_research_report=AsyncMock(side_effect=RuntimeError("vector down")),
    )

    with patch.object(agent, "_plan_searches", new_callable=AsyncMock) as mock_plan, \
         patch.object(agent, "_perform_searches", new_callable=AsyncMock) as mock_search, \
         patch.object(agent, "_fetch_pages", new_callable=AsyncMock) as mock_fetch, \
         patch.object(agent, "_synthesize", new_callable=AsyncMock) as mock_synth, \
         patch.object(agent, "_auto_save", new_callable=AsyncMock) as mock_auto_save:
        mock_plan.return_value = ["q1"]
        mock_search.return_value = [{"query": "q1", "results": "x", "urls": []}]
        mock_fetch.return_value = []
        mock_synth.return_value = "Final report"
        mock_auto_save.return_value = {}

        with patch.dict(sys.modules, {"vector_store": fake_vs}):
            await agent.run("test query")

        receipts = agent.get_last_receipts()
        assert receipts["vector"]["saved"] is False
        assert "failed" in receipts["vector"]["detail"].lower()


@pytest.mark.asyncio
async def test_auto_save_receipts_include_vault_and_gdoc_locations(agent, monkeypatch):
    """_auto_save returns explicit save locations for vault and Google Docs."""
    monkeypatch.setenv("MATON_API_KEY", "test-key")
    post = AsyncMock()

    fake_obsidian = SimpleNamespace(
        save_to_vault=AsyncMock(return_value="✅ Saved to vault: `Research/sample.md`")
    )
    fake_nas = SimpleNamespace(
        nas_write_file=AsyncMock(
            return_value=(
                "✅ Saved `sample.md` to NAS at "
                "`/volume1/documents/research/sample.md` (10 bytes)"
            )
        )
    )
    fake_gateway = SimpleNamespace(
        create_google_doc=AsyncMock(
            return_value=(
                "✅ Google Doc created: **Research: sample**\n"
                "🔗 https://docs.google.com/document/d/doc123/edit"
            )
        )
    )

    with patch.dict(
        sys.modules,
        {
            "obsidian_writer": fake_obsidian,
            "nas": fake_nas,
            "gateway": fake_gateway,
        },
    ):
        receipts = await agent._auto_save("sample", "report body", post)

    assert receipts["vault"]["saved"] is True
    assert receipts["vault"]["location"] == "data/vault/Research/sample.md"
    assert receipts["gdoc"]["saved"] is True
    assert receipts["gdoc"]["location"] == "https://docs.google.com/document/d/doc123/edit"


# ---------------------------------------------------------------------------
# run_scheduled_research (schedulable wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scheduled_research_basic():
    """run_scheduled_research calls ResearchAgent.run and returns report."""
    mock_vs = AsyncMock()
    mock_vs.search = AsyncMock(return_value=[])
    mock_vs.RESEARCH_COLLECTION = "research"

    with patch.object(ResearchAgent, "run", new_callable=AsyncMock) as mock_run, \
         patch.dict("sys.modules", {"vector_store": mock_vs}):
        mock_run.return_value = "Test report"

        result = await run_scheduled_research("test query")
        assert result == "Test report"
        mock_run.assert_awaited_once_with("test query", on_progress=None, deep=False)


@pytest.mark.asyncio
async def test_run_scheduled_research_with_prior():
    """run_scheduled_research appends diff note when prior research exists."""
    mock_vs = AsyncMock()
    mock_vs.RESEARCH_COLLECTION = "research"
    mock_vs.search = AsyncMock(return_value=[
        {"text": "old report", "metadata": {"added_at": "2025-01-01"}}
    ])

    with patch.object(ResearchAgent, "run", new_callable=AsyncMock) as mock_run, \
         patch.dict("sys.modules", {"vector_store": mock_vs}):
        mock_run.return_value = "New report"

        result = await run_scheduled_research("test query")
        assert "New report" in result
        assert "recurring research update" in result
        assert "2025-01-01" in result


@pytest.mark.asyncio
async def test_run_scheduled_research_vector_store_error():
    """run_scheduled_research returns report even if vector store fails."""
    with patch.object(ResearchAgent, "run", new_callable=AsyncMock) as mock_run, \
         patch.dict("sys.modules", {"vector_store": None}):
        mock_run.return_value = "Fallback report"

        result = await run_scheduled_research("test query")
        assert result == "Fallback report"


@pytest.mark.asyncio
async def test_run_scheduled_research_channel_id_accepted():
    """run_scheduled_research accepts channel_id without error."""
    mock_vs = AsyncMock()
    mock_vs.search = AsyncMock(return_value=[])
    mock_vs.RESEARCH_COLLECTION = "research"

    with patch.object(ResearchAgent, "run", new_callable=AsyncMock) as mock_run, \
         patch.dict("sys.modules", {"vector_store": mock_vs}):
        mock_run.return_value = "Report"

        result = await run_scheduled_research("query", channel_id="123456")
        assert result == "Report"


# ---------------------------------------------------------------------------
# Additional tests for improved coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_progress_callback_error_is_swallowed(agent):
    """on_progress callback errors are logged but don't abort research."""
    call_count = 0

    async def bad_callback(msg: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Discord rate limited")

    with patch.object(agent, "_research", new_callable=AsyncMock) as mock_research:
        mock_research.return_value = "Report"
        result = await agent.run("test query", on_progress=bad_callback)
        assert result == "Report"


@pytest.mark.asyncio
async def test_plan_searches_returns_sub_queries(agent):
    """_plan_searches decomposes query using LLM and returns a list."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ('["query 1", "query 2", "query 3"]', [])
        result = await agent._plan_searches("test topic")
    assert result == ["query 1", "query 2", "query 3"]


@pytest.mark.asyncio
async def test_plan_searches_handles_markdown_fence(agent):
    """_plan_searches handles markdown code fences in LLM response."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ('```json\n["q1", "q2"]\n```', [])
        result = await agent._plan_searches("test topic")
    assert result == ["q1", "q2"]


@pytest.mark.asyncio
async def test_plan_searches_returns_empty_on_failure(agent):
    """_plan_searches returns empty list when LLM fails."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = RuntimeError("LLM error")
        result = await agent._plan_searches("test topic")
    assert result == []


@pytest.mark.asyncio
async def test_synthesize_returns_report(agent):
    """_synthesize calls LLM and returns generated report text."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ("Synthesized report content", [])
        result = await agent._synthesize("my query", "data text")
    assert result == "Synthesized report content"


@pytest.mark.asyncio
async def test_synthesize_returns_error_on_failure(agent):
    """_synthesize returns error string when LLM fails."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = RuntimeError("LLM down")
        result = await agent._synthesize("my query", "data text")
    assert "Synthesis failed" in result or "❌" in result


@pytest.mark.asyncio
async def test_identify_gaps_returns_follow_ups(agent):
    """_identify_gaps returns a list of follow-up queries."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ('["gap query 1", "gap query 2"]', [])
        result = await agent._identify_gaps("original query", "report text")
    assert result == ["gap query 1", "gap query 2"]


@pytest.mark.asyncio
async def test_identify_gaps_handles_markdown_fence(agent):
    """_identify_gaps handles markdown code fences."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ('```json\n["gap 1"]\n```', [])
        result = await agent._identify_gaps("query", "report")
    assert result == ["gap 1"]


@pytest.mark.asyncio
async def test_identify_gaps_returns_empty_on_failure(agent):
    """_identify_gaps returns empty list on failure."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = RuntimeError("LLM error")
        result = await agent._identify_gaps("query", "report")
    assert result == []


@pytest.mark.asyncio
async def test_merge_findings_merges_reports(agent):
    """_merge_findings calls LLM to merge reports."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ("Merged report text", [])
        result = await agent._merge_findings("query", "old report", "new data")
    assert result == "Merged report text"


@pytest.mark.asyncio
async def test_merge_findings_falls_back_on_failure(agent):
    """_merge_findings appends new data to existing report on failure."""
    with patch("llm.chat_deep", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = RuntimeError("LLM error")
        result = await agent._merge_findings("query", "old report", "new findings")
    assert "old report" in result
    assert "new findings" in result[:2100]


@pytest.mark.asyncio
async def test_perform_searches_returns_results(agent):
    """_perform_searches executes parallel search workers."""
    post = AsyncMock()
    with patch("skills.advanced_skills.search_web", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = "Search result text https://example.com/article"
        results = await agent._perform_searches(["q1", "q2"], post)
    assert len(results) == 2
    assert results[0]["query"] == "q1"
    assert "example.com" in results[0]["urls"][0]


@pytest.mark.asyncio
async def test_perform_searches_handles_failure(agent):
    """_perform_searches handles individual search failures."""
    post = AsyncMock()
    with patch("skills.advanced_skills.search_web", new_callable=AsyncMock) as mock_search:
        mock_search.side_effect = RuntimeError("Search API down")
        results = await agent._perform_searches(["q1"], post)
    assert len(results) == 1
    assert "Search failed" in results[0]["results"]


@pytest.mark.asyncio
async def test_fetch_pages_returns_content(agent):
    """_fetch_pages browses URLs and returns page content."""
    post = AsyncMock()
    with patch("skills.advanced_skills.browse_url", new_callable=AsyncMock) as mock_browse:
        mock_browse.return_value = "Page content here"
        pages = await agent._fetch_pages(["https://example.com"], post)
    assert len(pages) == 1
    assert pages[0]["url"] == "https://example.com"
    assert pages[0]["content"] == "Page content here"


@pytest.mark.asyncio
async def test_fetch_pages_skips_error_pages(agent):
    """_fetch_pages skips pages that start with error marker."""
    post = AsyncMock()
    with patch("skills.advanced_skills.browse_url", new_callable=AsyncMock) as mock_browse:
        mock_browse.return_value = "❌ Failed to browse page"
        pages = await agent._fetch_pages(["https://example.com"], post)
    assert len(pages) == 0


@pytest.mark.asyncio
async def test_fetch_pages_handles_timeout(agent):
    """_fetch_pages handles timeout gracefully."""
    import asyncio
    post = AsyncMock()

    async def slow_browse(url):
        await asyncio.sleep(999)

    with patch("skills.advanced_skills.browse_url", side_effect=slow_browse):
        pages = await agent._fetch_pages(["https://example.com"], post)
    assert pages == []


@pytest.mark.asyncio
async def test_fetch_pages_handles_browse_exception(agent):
    """_fetch_pages handles general browse errors gracefully."""
    post = AsyncMock()
    with patch("skills.advanced_skills.browse_url", new_callable=AsyncMock) as mock_browse:
        mock_browse.side_effect = RuntimeError("network error")
        pages = await agent._fetch_pages(["https://example.com"], post)
    assert pages == []


@pytest.mark.asyncio
async def test_auto_save_vault_failure_handled(agent):
    """_auto_save handles vault save failure gracefully."""
    import sys
    post = AsyncMock()
    with patch.dict(sys.modules, {"obsidian_writer": None, "nas": None, "gateway": None}):
        receipts = await agent._auto_save("test query", "report", post)
    assert "vault" in receipts
    assert receipts["vault"]["saved"] is False


@pytest.mark.asyncio
async def test_auto_save_nas_failure_skips_gdoc(agent):
    """_auto_save sets gdoc as skipped when NAS save fails."""
    import sys
    from types import SimpleNamespace
    post = AsyncMock()
    fake_obsidian = SimpleNamespace(
        save_to_vault=AsyncMock(return_value="✅ Saved to vault: `Research/test.md`")
    )
    with patch.dict(sys.modules, {"obsidian_writer": fake_obsidian, "nas": None, "gateway": None}):
        receipts = await agent._auto_save("test query", "report", post)
    assert receipts["gdoc"]["saved"] is False


@pytest.mark.asyncio
async def test_auto_save_vault_non_success_response(agent):
    """_auto_save handles non-success vault responses."""
    import sys
    from types import SimpleNamespace
    post = AsyncMock()
    fake_obsidian = SimpleNamespace(
        save_to_vault=AsyncMock(return_value="❌ Vault not configured")
    )
    with patch.dict(sys.modules, {"obsidian_writer": fake_obsidian, "nas": None, "gateway": None}):
        receipts = await agent._auto_save("test query", "report", post)
    assert receipts["vault"]["saved"] is False


@pytest.mark.asyncio
async def test_deep_research_stops_when_no_gaps(agent):
    """_deep_research_passes stops early when no gaps found."""
    post = AsyncMock()
    with patch.object(agent, "_identify_gaps", new_callable=AsyncMock) as mock_gaps:
        mock_gaps.return_value = []
        result = await agent._deep_research_passes(
            "query", "initial report", [], [], post
        )
    assert result == "initial report"


@pytest.mark.asyncio
async def test_deep_research_passes_runs_extra_passes(agent):
    """_deep_research_passes runs follow-up research passes when gaps found."""
    post = AsyncMock()
    with patch.object(agent, "_identify_gaps", new_callable=AsyncMock) as mock_gaps, \
         patch.object(agent, "_plan_searches", new_callable=AsyncMock) as mock_plan, \
         patch.object(agent, "_perform_searches", new_callable=AsyncMock) as mock_search, \
         patch.object(agent, "_fetch_pages", new_callable=AsyncMock) as mock_fetch, \
         patch.object(agent, "_merge_findings", new_callable=AsyncMock) as mock_merge:
        mock_gaps.return_value = ["follow-up 1"]
        mock_plan.return_value = ["sub-q1"]
        mock_search.return_value = [{"query": "sub-q1", "results": "results", "urls": []}]
        mock_fetch.return_value = []
        mock_merge.return_value = "Merged report"
        result = await agent._deep_research_passes(
            "query", "initial report", [], [], post
        )
    assert result == "Merged report"
    mock_merge.assert_called()


@pytest.mark.asyncio
async def test_deep_research_run(agent):
    """deep=True triggers deep research passes."""
    with patch.object(agent, "_plan_searches", new_callable=AsyncMock) as mock_plan, \
         patch.object(agent, "_perform_searches", new_callable=AsyncMock) as mock_search, \
         patch.object(agent, "_fetch_pages", new_callable=AsyncMock) as mock_fetch, \
         patch.object(agent, "_synthesize", new_callable=AsyncMock) as mock_synth, \
         patch.object(agent, "_auto_save", new_callable=AsyncMock) as mock_save, \
         patch.object(agent, "_deep_research_passes", new_callable=AsyncMock) as mock_deep:
        mock_plan.return_value = ["q1"]
        mock_search.return_value = [{"query": "q1", "results": "r", "urls": []}]
        mock_fetch.return_value = []
        mock_synth.return_value = "Initial report"
        mock_save.return_value = {}
        mock_deep.return_value = "Deep report"
        result = await agent.run("test query", deep=True)
    assert result == "Deep report"
    mock_deep.assert_called_once()
