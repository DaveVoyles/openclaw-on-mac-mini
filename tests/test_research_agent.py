"""Tests for research_agent module — ResearchAgent with mocked dependencies."""

import pytest
from unittest.mock import AsyncMock, patch

from research_agent import ResearchAgent


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
    # Should prioritize non-social-media URLs
    assert result[0] == "https://example.com/article"


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
