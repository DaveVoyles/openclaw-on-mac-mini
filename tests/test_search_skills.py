"""Tests for search_skills reliability collection behavior."""

from pathlib import Path

import pytest

import channel_profiles as retrieval_profiles
from skills import search_skills as mod


@pytest.fixture(autouse=True)
async def _close_search_session():
    """Ensure aiohttp session pool is closed between tests."""
    yield
    await mod.close_session()


@pytest.fixture
def _disable_script_providers(monkeypatch):
    """Disable subprocess-based providers for deterministic unit tests."""
    missing = Path("/definitely/missing/script.py")
    monkeypatch.setattr(mod, "_TAVILY_SCRIPT", missing)
    monkeypatch.setattr(mod, "_DDG_SCRIPT", missing)


@pytest.mark.asyncio
async def test_search_web_retries_provider_fallback_when_low_results(monkeypatch, _disable_script_providers):
    calls: list[str] = []

    monkeypatch.setattr(mod, "PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(mod, "FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(mod, "TAVILY_API_KEY", "")
    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda **_: None)

    async def fake_perplexity(query: str, num_results: int = 5, *, return_hits: bool = False):
        calls.append("perplexity")
        hits = [
            {
                "title": "Single synthetic recap",
                "url": "https://example.com/one",
                "snippet": "Only one item",
                "provider": "perplexity",
                "source": "Perplexity",
            }
        ]
        if return_hits:
            return "perplexity", hits
        return "perplexity"

    async def fake_firecrawl(query: str, num_results: int = 5, *, return_hits: bool = False):
        calls.append("firecrawl")
        hits = [
            {
                "title": "Game A",
                "url": "https://example.com/a",
                "snippet": "A",
                "provider": "firecrawl",
                "source": "Firecrawl",
            },
            {
                "title": "Game B",
                "url": "https://example.com/b",
                "snippet": "B",
                "provider": "firecrawl",
                "source": "Firecrawl",
            },
            {
                "title": "Game C",
                "url": "https://example.com/c",
                "snippet": "C",
                "provider": "firecrawl",
                "source": "Firecrawl",
            },
        ]
        if return_hits:
            return "firecrawl", hits
        return "firecrawl"

    monkeypatch.setattr(mod, "_perplexity_search", fake_perplexity)
    monkeypatch.setattr(mod, "_firecrawl_search", fake_firecrawl)

    output = await mod.search_web(
        "division 1 lacrosse this weekend",
        num_results=6,
        min_results=4,
        retry_on_low_results=True,
    )

    assert "Web Search Results" in output
    assert "Game A" in output
    assert "Game B" in output
    assert calls[:2] == ["perplexity", "firecrawl"]


@pytest.mark.asyncio
async def test_search_web_merges_and_dedupes_without_collapsing_distinct_games(monkeypatch, _disable_script_providers):
    monkeypatch.setattr(mod, "PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(mod, "FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(mod, "TAVILY_API_KEY", "")
    # Ensure deterministic budget regardless of any metrics collector state from other tests
    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda **_: None)

    async def fake_perplexity(query: str, num_results: int = 5, *, return_hits: bool = False):
        hits = [
            {
                "title": "Maryland vs Hopkins",
                "url": "https://example.com/game-1",
                "snippet": "Game 1",
                "provider": "perplexity",
                "source": "Perplexity",
            },
            {
                "title": "Virginia vs Duke",
                "url": "https://example.com/game-2",
                "snippet": "Game 2",
                "provider": "perplexity",
                "source": "Perplexity",
            },
        ]
        if return_hits:
            return "perplexity", hits
        return "perplexity"

    async def fake_firecrawl(query: str, num_results: int = 5, *, return_hits: bool = False):
        hits = [
            {
                "title": "Maryland vs Hopkins",
                "url": "https://example.com/game-1?utm_source=test",
                "snippet": "dup",
                "provider": "firecrawl",
                "source": "Firecrawl",
            },
            {
                "title": "Notre Dame vs Syracuse",
                "url": "https://example.com/game-3",
                "snippet": "Game 3",
                "provider": "firecrawl",
                "source": "Firecrawl",
            },
        ]
        if return_hits:
            return "firecrawl", hits
        return "firecrawl"

    monkeypatch.setattr(mod, "_perplexity_search", fake_perplexity)
    monkeypatch.setattr(mod, "_firecrawl_search", fake_firecrawl)

    output = await mod.search_web(
        "ncaa lacrosse games",
        num_results=10,
        min_results=3,
        retry_on_low_results=True,
    )

    assert "3 of 3 unique" in output
    assert "Maryland vs Hopkins" in output
    assert "Virginia vs Duke" in output
    assert "Notre Dame vs Syracuse" in output


@pytest.mark.asyncio
async def test_search_web_expands_query_for_sports_context_when_needed(monkeypatch, _disable_script_providers):
    calls: list[str] = []

    monkeypatch.setattr(mod, "PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(mod, "FIRECRAWL_API_KEY", "")
    monkeypatch.setattr(mod, "TAVILY_API_KEY", "")
    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda **_: None)

    async def fake_perplexity(query: str, num_results: int = 5, *, return_hits: bool = False):
        calls.append(query)
        if "weekend" in query:
            hits = [
                {
                    "title": "Weekend slate",
                    "url": "https://example.com/weekend",
                    "snippet": "weekend",
                    "provider": "perplexity",
                    "source": "Perplexity",
                },
                {
                    "title": "TV schedule",
                    "url": "https://example.com/tv",
                    "snippet": "tv",
                    "provider": "perplexity",
                    "source": "Perplexity",
                },
            ]
        else:
            hits = [
                {
                    "title": "Only one",
                    "url": "https://example.com/one",
                    "snippet": "one",
                    "provider": "perplexity",
                    "source": "Perplexity",
                },
            ]
        if return_hits:
            return "perplexity", hits
        return "perplexity"

    monkeypatch.setattr(mod, "_perplexity_search", fake_perplexity)

    output = await mod.search_web(
        "division 1 lacrosse games",
        num_results=8,
        min_results=2,
        retry_on_low_results=True,
        expand_query=True,
        expansion_context="sports_recap",
    )

    assert len(calls) >= 2
    assert any("weekend" in q for q in calls[1:])
    assert "Weekend slate" in output


@pytest.mark.asyncio
async def test_search_web_records_low_results_metric(monkeypatch, _disable_script_providers):
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(mod, "PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(mod, "FIRECRAWL_API_KEY", "")
    monkeypatch.setattr(mod, "TAVILY_API_KEY", "")
    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda **_: None)

    async def fake_perplexity(_query: str, num_results: int = 5, *, return_hits: bool = False):
        hits = [
            {
                "title": "Only one",
                "url": "https://example.com/one",
                "snippet": "one",
                "provider": "perplexity",
                "source": "Perplexity",
            }
        ]
        if return_hits:
            return "perplexity", hits
        return "perplexity"

    monkeypatch.setattr(mod, "_perplexity_search", fake_perplexity)
    monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="search": events.append((event, context)))

    output = await mod.search_web(
        "division 1 lacrosse games",
        num_results=5,
        min_results=3,
        retry_on_low_results=True,
        expand_query=False,
    )

    assert "⚠️ Only 1 unique results found (target: 3)." in output
    assert ("search_low_results_incident", "search") in events
    assert ("search_budget_metrics_missing", "search") in events


@pytest.mark.asyncio
async def test_e2e_incident_summary_search_surfaces_conflicts_with_quality_warning(
    monkeypatch, _disable_script_providers
):
    monkeypatch.setattr(mod, "PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(mod, "FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(mod, "TAVILY_API_KEY", "")
    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda **_: None)

    async def fake_perplexity(_query: str, num_results: int = 5, *, return_hits: bool = False):
        hits = [
            {
                "title": "Incident timeline - status page",
                "url": "https://status.example.com/incidents/123",
                "snippet": "Impact lasted 19 minutes before rollback completed.",
                "provider": "perplexity",
                "source": "Perplexity",
            },
            {
                "title": "Incident timeline - team chat",
                "url": "https://chat.example.com/thread/incident-123",
                "snippet": "Early update claimed impact lasted only 7 minutes.",
                "provider": "perplexity",
                "source": "Perplexity",
            },
        ]
        if return_hits:
            return "perplexity", hits
        return "perplexity"

    async def fake_firecrawl(_query: str, num_results: int = 5, *, return_hits: bool = False):
        hits = [
            {
                "title": "Postmortem action items",
                "url": "https://github.com/example/openclaw/issues/77",
                "snippet": "Follow-ups: alerting and rollout guardrails.",
                "provider": "firecrawl",
                "source": "Firecrawl",
            },
        ]
        if return_hits:
            return "firecrawl", hits
        return "firecrawl"

    monkeypatch.setattr(mod, "_perplexity_search", fake_perplexity)
    monkeypatch.setattr(mod, "_firecrawl_search", fake_firecrawl)

    result = await mod.search_web(
        "Need an incident summary with impact, timeline, mitigation, and follow-ups for Discord",
        num_results=6,
        min_results=4,
        retry_on_low_results=True,
        expand_query=False,
    )

    assert "**Web Search Results** (3 of 3 unique):" in result
    assert "Impact lasted 19 minutes" in result
    assert "impact lasted only 7 minutes" in result
    assert "*Providers queried:" in result
    assert "perplexity" in result
    assert "firecrawl" in result
    assert "⚠️ Only 3 unique results found (target: 4)." in result


def test_resolve_retrieval_profile_settings_returns_default_profile():
    settings = retrieval_profiles.resolve_retrieval_profile_settings("any query")
    assert settings["min_results"] == 3
    assert settings["expand_query"] is False
    assert settings["topic_class"] == "general"


def test_resolve_retrieval_profile_settings_channel_name_ignored():
    settings = retrieval_profiles.resolve_retrieval_profile_settings("sports recap", channel_name="sports")
    assert settings["topic_class"] == "general"


@pytest.mark.asyncio
async def test_search_web_applies_default_profile_to_reliable_path(
    monkeypatch,
    _disable_script_providers,
):
    """Simplified profile always returns min_results=3 with no expansion_context."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda **_: None)

    async def fake_reliable(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(mod, "_search_web_reliable", fake_reliable)

    result = await mod.search_web("query with defaults only")

    assert result == "ok"
    assert captured["min_results"] == 3
    assert captured["expansion_context"] == ""
    assert captured["provider_attempt_cap"] >= 3


def test_rank_hits_for_evidence_prioritizes_trusted_and_fresh_sources():
    hits = [
        {
            "title": "Legacy fan forum recap",
            "url": "https://random-forum.example.com/post/old-thread",
            "snippet": "Archived discussion from 2014 historical results and opinions.",
            "provider": "duckduckgo",
            "source": "DuckDuckGo",
        },
        {
            "title": "NCAA Division I Scoreboard",
            "url": "https://www.ncaa.com/scoreboard/lacrosse-men/d1/2026/04/05",
            "snippet": "Updated today with final scores for this weekend games.",
            "provider": "serper",
            "source": "Serper",
        },
        {
            "title": "Independent blog predictions",
            "url": "https://exampleblog.net/lacrosse/picks",
            "snippet": "Preview for upcoming games this weekend.",
            "provider": "firecrawl",
            "source": "Firecrawl",
        },
    ]

    ranked = mod.rank_hits_for_evidence(hits)

    assert ranked[0]["title"] == "NCAA Division I Scoreboard"
    assert ranked[0]["evidence_score"] > ranked[-1]["evidence_score"]
    assert ranked[-1]["stale_signal"] is True


@pytest.mark.asyncio
async def test_search_web_applies_effective_budget_for_high_load(monkeypatch, _disable_script_providers):
    captured: dict[str, object] = {}
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        mod,
        "get_latency_load_snapshot",
        lambda command_hint="search": {"request_rate_rpm": 140.0, "p95_latency_ms": 2800.0, "error_rate": 0.04},
    )
    monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="search": events.append((event, context)))

    async def fake_reliable(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(mod, "_search_web_reliable", fake_reliable)

    result = await mod.search_web("division 1 lacrosse recap")

    assert result == "ok"
    assert captured["min_results"] == 2
    assert captured["max_query_variants"] == 1
    assert captured["provider_attempt_cap"] == 2
    assert ("search_budget_tightened_for_latency", "search") in events


@pytest.mark.asyncio
async def test_search_web_uses_failsafe_budget_when_metrics_missing(monkeypatch, _disable_script_providers):
    captured: dict[str, object] = {}
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda command_hint="search": None)
    monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="search": events.append((event, context)))

    async def fake_reliable(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(mod, "_search_web_reliable", fake_reliable)

    result = await mod.search_web("simple query")

    assert result == "ok"
    assert captured["min_results"] == 3
    assert captured["max_query_variants"] == 2
    assert captured["provider_attempt_cap"] == 4
    assert ("search_budget_metrics_missing", "search") in events
