"""Tests for reporting_skills.py."""

import datetime as dt
from types import SimpleNamespace

import pytest

import llm
from skills import reporting_skills as mod


def _msg(
    author_name: str,
    content: str,
    *,
    bot: bool = False,
    attachments: list[str] | None = None,
):
    author = SimpleNamespace(display_name=author_name, name=author_name, bot=bot)
    attachment_objs = [SimpleNamespace(filename=name) for name in (attachments or [])]
    return SimpleNamespace(
        author=author,
        content=content,
        clean_content=content,
        attachments=attachment_objs,
        created_at=dt.datetime(2026, 4, 1, 12, 30, tzinfo=dt.timezone.utc),
    )


def test_build_sports_watch_query_prefers_explicit_query():
    query = mod.build_sports_watch_query(
        query="men's division 1 college lacrosse this week",
        sport="lacrosse",
        league="NCAA",
        team="Maryland",
        days=7,
    )
    assert query == "men's division 1 college lacrosse this week"


def test_build_sports_watch_query_assembles_structured_inputs():
    query = mod.build_sports_watch_query(
        sport="college lacrosse",
        league="NCAA Division 1",
        team="Maryland",
        days=5,
    )
    assert "Maryland NCAA Division 1 college lacrosse" in query
    assert "next 5 days" in query


def test_infer_sports_request_extracts_slots_from_plain_english():
    slots = mod.infer_sports_request(
        "What games does Maryland have in the next 5 days in men's division 1 lacrosse?"
    )
    assert slots["team"] == "Maryland"
    assert slots["sport"] == "lacrosse"
    assert slots["league"] == "NCAA Division 1"
    assert slots["days"] == 5


def test_infer_sports_request_treats_today_as_same_day_window():
    slots = mod.infer_sports_request(
        "Show me the schedule for the men's division 1 college lacrosse games today"
    )
    assert slots["sport"] == "lacrosse"
    assert slots["league"] == "NCAA Division 1"
    assert slots["days"] == 1


def test_infer_report_request_extracts_format_and_window_hints():
    slots = mod.infer_report_request(
        "Give me the box office financials and new releases for the last week in table form with emojis."
    )
    assert slots["topic"] == "box office"
    assert slots["days"] == 7
    assert slots["output_style"] == "discord-table-detailed"
    assert slots["emoji_level"] in {"light", "rich"}


def test_append_report_guardrails_adds_required_sections():
    report = mod._append_report_guardrails(
        "## Weekly box office recap\n\nTop titles moved this week.",
        timeframe_label="last 7 day(s)",
    )
    assert "Time window: last 7 day(s)" in report
    assert "| Item | Metric | Value | Notes |" in report
    assert "Sources" in report
    assert "N/A" in report


def test_format_message_history_skips_bot_messages_and_keeps_attachments():
    transcript = mod._format_message_history(
        [
            _msg("OpenClaw", "Automated note", bot=True),
            _msg("Dave", "Need a weekly recap of sports updates", attachments=["schedule.png"]),
            _msg("Pat", "Let's track the ESPN games list."),
        ],
        max_chars=1000,
    )
    assert "OpenClaw" not in transcript
    assert "Dave" in transcript
    assert "schedule.png" in transcript
    assert "Pat" in transcript


@pytest.mark.asyncio
async def test_generate_channel_recap_report_requires_live_bot(monkeypatch):
    monkeypatch.setattr(mod, "get_bot", lambda: None)
    result = await mod.generate_channel_recap_report(channel_id=1234)
    assert "not available yet" in result


@pytest.mark.asyncio
async def test_generate_channel_recap_report_uses_bound_channel_context(monkeypatch):
    monkeypatch.setattr(mod, "get_bot", lambda: None)
    monkeypatch.setattr(mod, "get_current_channel_id", lambda: 4321)
    result = await mod.generate_channel_recap_report(channel_id=None)
    assert "not available yet" in result


@pytest.mark.asyncio
async def test_generate_channel_recap_report_without_context_guides_user(monkeypatch):
    monkeypatch.setattr(mod, "get_current_channel_id", lambda: None)
    result = await mod.generate_channel_recap_report(channel_id=None)
    assert "No Discord channel context" in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_aggregates_multiple_queries_and_sources(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append((query, provider))
        if "Saturday" in query:
            return (
                "**Web Search Results** (2 of 2 unique):\n"
                "**1. Maryland vs Johns Hopkins**\n🔗 <https://www.espn.com/lacrosse/game1>\n\n"
                "**2. Virginia vs Duke**\n🔗 <https://www.ncaa.com/schedule/lacrosse/game2>\n\n"
                "*Providers queried: serper, duckduckgo*"
            )
        return (
            "**Web Search Results** (2 of 2 unique):\n"
            "**1. Syracuse vs Notre Dame**\n🔗 <https://www.usila.org/game3>\n\n"
            "**2. Penn State at Michigan**\n🔗 <https://www.insidelacrosse.com/game4>\n\n"
            "*Providers queried: serper, duckduckgo*"
        )

    async def fake_llm_chat(user_message: str, model_preference: str = "gemini", tool_declarations=None):
        assert "Search results:" in user_message
        assert "espn.com" in user_message
        assert "ncaa.com" in user_message
        assert "Target at least 10 distinct game rows" in user_message
        return (
            "## D1 Lacrosse Weekend Recap\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Sat | Maryland vs Johns Hopkins | Final 12-10 | ESPN+ | Rivalry game | espn.com |\n"
            "| Sat | Virginia vs Duke | Final 14-11 | ACCN | Top-10 matchup | ncaa.com |\n"
            "| Sun | Syracuse vs Notre Dame | Final 9-8 | ESPNU | Overtime | usila.org |\n"
            "| Sun | Penn State at Michigan | Final 11-7 | BTN | Conference game | insidelacrosse.com |",
            [],
            "test-model",
        )

    monkeypatch.setitem(__import__("sys").modules, "skills.search_skills", SimpleNamespace(search_web=fake_search_web))
    monkeypatch.setitem(__import__("sys").modules, "llm", SimpleNamespace(chat=fake_llm_chat))

    output = await mod.generate_sports_watch_report(
        query="men's division 1 college lacrosse this weekend recap",
        days=3,
    )
    assert len(calls) >= 2
    assert mod._count_markdown_table_items(output) >= 4
    assert "Coverage Summary" in output
    assert "Source count: **" in output


@pytest.mark.asyncio
async def test_generate_sports_watch_report_uses_fallback_when_initial_coverage_is_thin(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append((query, provider))
        if "all games schedule" in query or provider == "serper":
            return (
                "**Web Search Results** (2 of 2 unique):\n"
                "**1. Maryland vs Rutgers**\n🔗 <https://www.espn.com/lacrosse/game5>\n\n"
                "**2. Yale vs Princeton**\n🔗 <https://www.ncaa.com/schedule/lacrosse/game6>\n\n"
                "*Providers queried: serper*"
            )
        return (
            "**Web Search Results** (1 of 1 unique):\n"
            "**1. Maryland vs Rutgers**\n🔗 <https://www.espn.com/lacrosse/game5>\n\n"
            "*Providers queried: auto*"
        )

    async def fake_llm_chat(user_message: str, model_preference: str = "gemini", tool_declarations=None):
        return (
            "## Weekend Recap\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Sat | Maryland vs Rutgers | Final 10-9 | ESPN+ | One-goal game | espn.com |\n"
            "| Sat | Yale vs Princeton | Final 13-12 | ESPNU | Overtime | ncaa.com |\n",
            [],
            "test-model",
        )

    monkeypatch.setitem(__import__("sys").modules, "skills.search_skills", SimpleNamespace(search_web=fake_search_web))
    monkeypatch.setitem(__import__("sys").modules, "llm", SimpleNamespace(chat=fake_llm_chat))

    output = await mod.generate_sports_watch_report(
        query="division 1 lacrosse weekend recap",
        days=3,
    )
    assert any(provider == "serper" for _, provider in calls)
    assert "Fallback broadening used: yes" in output


@pytest.mark.asyncio
async def test_generate_sports_watch_report_uses_reliable_search_flags(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        return "**Web Search Results**\n\n**1. Game**\nExample\n🔗 <https://example.com>"

    async def fake_chat(**kwargs):
        return ("# Sports Watch\n\n| Date | Matchup | Time (ET) | Watch | Notes |\n| --- | --- | --- | --- | --- |\n", [], "test-model")

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = await mod.generate_sports_watch_report(
        query="NCAA lacrosse games this week",
        include_watch_info=True,
    )

    assert "_via test-model_" in result
    assert calls
    assert any(call["num_results"] == 10 for call in calls)
    assert all(call["kwargs"].get("retry_on_low_results") is True for call in calls)
    assert all(call["kwargs"].get("expand_query") is True for call in calls)
    assert all(call["kwargs"].get("expansion_context") == "sports_recap" for call in calls)
    assert any(call["kwargs"].get("min_results") == 5 for call in calls)


@pytest.mark.asyncio
async def test_generate_box_office_report_uses_reliable_search_flags(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        captured["query"] = query
        captured["num_results"] = num_results
        captured["provider"] = provider
        captured["kwargs"] = kwargs
        return "**Web Search Results**\n\n**1. Title**\nExample\n🔗 <https://example.com>"

    async def fake_chat(**kwargs):
        return ("## Weekly Box Office\n\n| Film | Weekend Gross | Domestic Total | Worldwide Total |\n| --- | --- | --- | --- |\n| Test | $1 | $2 | $3 |\n\nSources: Example", [], "test-model")

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = await mod.generate_box_office_report(query="box office")

    assert "_via test-model_" in result
    kwargs = captured["kwargs"]
    assert kwargs["min_results"] == 6
    assert kwargs["retry_on_low_results"] is True
    assert kwargs["expand_query"] is True
    assert kwargs["expansion_context"] == "news_recap"


@pytest.mark.asyncio
async def test_generate_sports_watch_report_weekend_has_coverage_summary(monkeypatch):
    async def fake_search_web(_query: str, **kwargs) -> str:
        assert kwargs.get("num_results") == 10
        return (
            "1) ESPN schedule https://www.espn.com/lacrosse/schedule\n"
            "2) NCAA scoreboard https://www.ncaa.com/scoreboard/lacrosse-men/d1\n"
            "3) USA Lacrosse scores https://www.usalacrosse.com/college/men/scores\n"
        )

    async def fake_chat(**_kwargs):
        response = (
            "# D1 Men's Lacrosse Weekend Watch\n\n"
            "| Date | Matchup | Time (ET) | Watch | Notes |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Fri | Team A vs Team B | 7:00 PM | ESPN+ | Rivalry |\n"
            "| Sat | Team C vs Team D | 12:00 PM | ESPNU | Ranked matchup |\n"
            "| Sat | Team E vs Team F | 3:30 PM | ACCN | Conference game |\n"
            "| Sun | Team G vs Team H | 1:00 PM | ESPN+ | TBD lineups |\n"
            "| Sun | Team I vs Team J | 2:00 PM | ESPNU | Conference game |\n"
            "| Sun | Team K vs Team L | 4:00 PM | ACCN | Rivalry |\n"
            "| Sun | Team M vs Team N | 6:00 PM | ESPN+ | Ranked matchup |\n"
            "| Sun | Team O vs Team P | 8:00 PM | ESPNU | Prime slot |\n"
        )
        return response, {}, "test-model"

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr("llm.chat", fake_chat)

    result = await mod.generate_sports_watch_report(
        query="division 1 men's lacrosse this weekend",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    assert "## 📎 Coverage Summary" in result
    assert "Items listed: **8** (expected ≥ 8)" in result
    assert "Source count: **3** distinct domains (required ≥ 3)" in result
    assert "Coverage thresholds met" in result
    assert "Partial coverage warning" not in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_weekend_warns_on_partial_coverage(monkeypatch):
    events: list[tuple[str, str]] = []

    async def fake_search_web(_query: str, **kwargs) -> str:
        assert kwargs.get("num_results") == 10
        return "1) ESPN schedule https://www.espn.com/lacrosse/schedule\n"

    async def fake_chat(**_kwargs):
        response = (
            "# D1 Men's Lacrosse Weekend Watch\n\n"
            "| Date | Matchup | Time (ET) | Watch | Notes |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Sat | Team A vs Team B | 1:00 PM | TBD | Incomplete slate |\n"
        )
        return response, {}, "test-model"

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr("llm.chat", fake_chat)
    monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="reporting": events.append((event, context)))

    result = await mod.generate_sports_watch_report(
        query="full weekend division 1 men's lacrosse recap",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    assert "⚠️ **Partial coverage warning:**" in result
    assert "Source diversity floor missed (1/3 distinct domains)" in result
    assert "Actionable shortfall" in result
    assert "Retry scope hint" in result
    assert "Confidence posture" in result
    assert "Items listed: **1** (expected ≥ 8)" in result
    assert "Source count: **1** distinct domains (required ≥ 3)" in result
    assert "Source diversity shortfall: add **2** more distinct domain(s)" in result
    assert "Retry scope hint: Re-run with a tighter scope" in result
    assert "Coverage shortfall: add **7** more item(s) to hit the target." in result
    assert "Status: ⚠️ **Partial coverage**" in result
    assert ("recap_fallback_activation", "sports_recap") in events
    assert ("recap_partial_coverage_warning", "sports_recap") in events


@pytest.mark.asyncio
async def test_generate_sports_watch_report_today_full_slate_prefers_direct_perplexity_answer(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        if provider == "perplexity":
            return (
                "**Perplexity AI Answer:**\n"
                "Today, Saturday, April 11, 2026, is a loaded NCAA Division I men's lacrosse slate with ranked afternoon windows and the Army-Navy rivalry headlining the day.\n\n"
                "| Time (ET) | Matchup | Watch | Notes |\n"
                "| --- | --- | --- | --- |\n"
                "| 12:00 PM | Penn at Princeton | ESPN+ | Ivy matchup |\n"
                "| 12:00 PM | Johns Hopkins at Ohio State | BTN+ | Big Ten opener |\n"
                "| 1:00 PM | Michigan at Penn State | BTN+ | Big Ten matchup |\n"
                "| 2:30 PM | Navy at Army | CBS Sports Network | Rivalry game |\n"
                "| 4:00 PM | Virginia at Syracuse | ESPNU | Ranked matchup |\n"
                "| 5:00 PM | North Carolina at Notre Dame | ACCN | Ranked matchup |\n"
                "| 6:00 PM | Rutgers at Maryland | BTN | Late TV window |\n\n"
                "**Sources:**\n"
                "1. https://www.ncaa.com/scoreboard/lacrosse-men/d1\n"
                "2. https://www.espn.com/college-sports/\n"
            )
        return "should not be used"

    async def fake_chat(**_kwargs):
        raise AssertionError("llm.chat should not run when direct Perplexity output is available")

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        mod,
        "_get_reporting_reference_context",
        lambda: (dt.datetime(2026, 4, 11, 14, 0, 0), "America/New_York"),
    )

    result = await mod.generate_sports_watch_report(
        query="Show me the schedule for the men's division 1 college lacrosse games today",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    assert len(calls) == 1
    assert calls[0]["provider"] == "perplexity"
    assert "Today, Saturday, April 11, 2026" in result
    assert "| Time (ET) | Matchup | Watch | Notes |" in result
    assert "## 📎 Coverage Summary" not in result
    assert "_via perplexity-direct_" in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_structured_same_day_args_prefer_direct_perplexity(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        if provider == "perplexity":
            return (
                "**Perplexity AI Answer:**\n"
                "Today, Saturday, April 11, 2026, is a loaded NCAA Division I men's lacrosse slate with ranked afternoon windows and the Army-Navy rivalry headlining the day.\n\n"
                "| Time (ET) | Matchup | Watch | Notes |\n"
                "| --- | --- | --- | --- |\n"
                "| 12:00 PM | Penn at Princeton | ESPN+ | Ivy matchup |\n"
                "| 12:00 PM | Johns Hopkins at Ohio State | BTN+ | Big Ten matchup |\n"
                "| 1:00 PM | Michigan at Penn State | BTN+ | Big Ten matchup |\n"
                "| 1:00 PM | Navy at Army | CBS Sports Network | Rivalry game |\n"
                "| 4:00 PM | Virginia at Syracuse | ESPNU | Ranked matchup |\n\n"
                "**Sources:**\n"
                "1. https://www.ncaa.com/scoreboard/lacrosse-men/d1\n"
                "2. https://www.espn.com/college-sports/\n"
            )
        return "should not be used"

    async def fake_chat(**_kwargs):
        raise AssertionError("llm.chat should not run when structured same-day args hit direct Perplexity")

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        mod,
        "_get_reporting_reference_context",
        lambda: (dt.datetime(2026, 4, 11, 14, 0, 0), "America/New_York"),
    )

    result = await mod.generate_sports_watch_report(
        query="",
        sport="lacrosse",
        league="NCAA Division 1 Men",
        days=1,
        include_watch_info=True,
    )

    assert len(calls) == 1
    assert calls[0]["provider"] == "perplexity"
    assert "Only include NCAA Division I men's lacrosse games." in calls[0]["query"]
    assert "Today, Saturday, April 11, 2026" in result
    assert "_via perplexity-direct_" in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_today_full_slate_rejects_direct_answer_without_urls(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        if provider == "perplexity":
            return (
                "**Perplexity AI Answer:**\n"
                "Today is a loaded D1 men's lacrosse slate with many games and marquee rivalries across the country.\n\n"
                "**Sources:**\n"
                "Inside Lacrosse, team sites, and scoreboard coverage.\n"
            )
        return (
            "**Web Search Results** (3 of 3 unique):\n"
            "**1. Navy at Army**\n🔗 <https://www.ncaa.com/game1>\n\n"
            "**2. Virginia at Syracuse**\n🔗 <https://www.espn.com/game2>\n\n"
            "**3. North Carolina at Notre Dame**\n🔗 <https://www.insidelacrosse.com/game3>\n\n"
        )

    async def fake_chat(**kwargs):
        prompt = kwargs["user_message"]
        assert "Search results:" in prompt
        return (
            "## D1 Men's Lacrosse Today\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Apr 11 | Navy at Army | 2:30 PM ET | CBS Sports Network | Rivalry | ncaa.com |\n"
            "| Apr 11 | Virginia at Syracuse | 4:00 PM ET | ESPNU | Ranked | espn.com |\n"
            "| Apr 11 | North Carolina at Notre Dame | 5:00 PM ET | ACCN | Ranked | insidelacrosse.com |\n",
            [],
            "test-model",
        )

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        mod,
        "_get_reporting_reference_context",
        lambda: (dt.datetime(2026, 4, 11, 14, 0, 0), "America/New_York"),
    )

    result = await mod.generate_sports_watch_report(
        query="Show me the schedule for the men's division 1 college lacrosse games today",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    assert calls[0]["provider"] == "perplexity"
    assert any(call["provider"] == "" for call in calls[1:])
    assert "## 📎 Coverage Summary" in result
    assert "_via test-model_" in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_today_full_slate_rejects_sparse_direct_answer(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        if provider == "perplexity":
            return (
                "**Perplexity AI Answer:**\n"
                "Several top-ranked Division 1 men's lacrosse teams are in action today.\n\n"
                "| Time (ET) | Matchup | Watch | Notes |\n"
                "| --- | --- | --- | --- |\n"
                "| 2:00 PM | No. 7 Cornell vs. No. 11 Duke | TBD | Neutral site |\n"
                "| 4:00 PM | No. 13 Virginia at No. 5 Syracuse | ESPNU | Ranked matchup |\n"
                "| 5:00 PM | No. 1 North Carolina at No. 2 Notre Dame | ACCN | Top-two matchup |\n\n"
                "**Sources:**\n"
                "1. https://theacc.com/news/2026/4/10/this-weekend-in-acc-mens-lacrosse.aspx\n"
                "2. https://gohofstra.com/sports/mens-lacrosse/schedule/2026\n"
            )
        return (
            "**Web Search Results** (5 of 5 unique):\n"
            "**1. Penn at Princeton**\n🔗 <https://www.ncaa.com/game1>\n\n"
            "**2. Johns Hopkins at Ohio State**\n🔗 <https://www.espn.com/game2>\n\n"
            "**3. Michigan at Penn State**\n🔗 <https://btn.com/game3>\n\n"
            "**4. Navy at Army**\n🔗 <https://www.cbssports.com/game4>\n\n"
            "**5. Rutgers at Maryland**\n🔗 <https://www.ncaa.com/game5>\n\n"
        )

    async def fake_chat(**_kwargs):
        return (
            "## D1 Men's Lacrosse Today\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Apr 11 | Penn at Princeton | 12:00 PM ET | ESPN+ | Ivy | ncaa.com |\n"
            "| Apr 11 | Johns Hopkins at Ohio State | 12:00 PM ET | BTN+ | Big Ten | espn.com |\n"
            "| Apr 11 | Michigan at Penn State | 1:00 PM ET | BTN+ | Big Ten | btn.com |\n"
            "| Apr 11 | Navy at Army | 2:30 PM ET | CBS Sports Network | Rivalry | cbssports.com |\n"
            "| Apr 11 | Rutgers at Maryland | 6:00 PM ET | Big Ten Network | Big Ten | ncaa.com |\n",
            [],
            "test-model",
        )

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        mod,
        "_get_reporting_reference_context",
        lambda: (dt.datetime(2026, 4, 11, 14, 0, 0), "America/New_York"),
    )

    result = await mod.generate_sports_watch_report(
        query="Show me the schedule for the men's division 1 college lacrosse games today",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    assert calls[0]["provider"] == "perplexity"
    assert any(call["provider"] == "" for call in calls[1:])
    assert "_via perplexity-direct_" not in result
    assert "_via test-model_" in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_today_full_slate_rejects_cross_division_direct_answer(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        if provider == "perplexity":
            return (
                "**Perplexity AI Answer:**\n"
                "NCAA Division I men's lacrosse has a light slate today, though some women's and Division III games also appear in broad source coverage.\n\n"
                "| Time (ET) | Matchup | Watch | Notes |\n"
                "| --- | --- | --- | --- |\n"
                "| 12:00 PM | Penn at Princeton | ESPN+ | Ivy League |\n"
                "| 12:00 PM | Franklin & Marshall at Muhlenberg | TBD | Division III |\n"
                "| 3:00 PM | Harvard at Yale | TBD | Ivy League |\n\n"
                "**Sources:**\n"
                "1. https://www.ncaa.com/scoreboard/lacrosse-men/d1\n"
                "2. https://www.espn.com/college-sports/\n"
            )
        return (
            "**Web Search Results** (3 of 3 unique):\n"
            "**1. Penn at Princeton**\n🔗 <https://www.ncaa.com/game1>\n\n"
            "**2. Harvard at Yale**\n🔗 <https://ivyleague.com/game2>\n\n"
            "**3. Hofstra at Drexel**\n🔗 <https://gohofstra.com/game3>\n\n"
        )

    async def fake_chat(**_kwargs):
        return (
            "## D1 Men's Lacrosse Today\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Apr 11 | Penn at Princeton | 12:00 PM ET | ESPN+ | Ivy League | ncaa.com |\n"
            "| Apr 11 | Harvard at Yale | 3:00 PM ET | TBD | Ivy League | ivyleague.com |\n"
            "| Apr 11 | Hofstra at Drexel | 12:00 PM ET | TBD | CAA | gohofstra.com |\n",
            [],
            "test-model",
        )

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        mod,
        "_get_reporting_reference_context",
        lambda: (dt.datetime(2026, 4, 11, 14, 0, 0), "America/New_York"),
    )

    result = await mod.generate_sports_watch_report(
        query="Show me the schedule for the men's division 1 college lacrosse games today",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    assert calls[0]["provider"] == "perplexity"
    assert any(call["provider"] == "" for call in calls[1:])
    assert "_via test-model_" in result


@pytest.mark.asyncio
async def test_generate_sports_watch_report_today_full_slate_uses_stronger_queries(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_search_web(query: str, num_results: int = 5, provider: str = "", **kwargs):
        calls.append(
            {
                "query": query,
                "num_results": num_results,
                "provider": provider,
                "kwargs": kwargs,
            }
        )
        if provider == "perplexity":
            return "⚠️ Perplexity API key not configured."
        if "today all games schedule" in query:
            return (
                "**Web Search Results** (3 of 3 unique):\n"
                "**1. Navy at Army**\n🔗 <https://www.ncaa.com/game1>\n\n"
                "**2. Virginia at Syracuse**\n🔗 <https://www.espn.com/game2>\n\n"
                "**3. North Carolina at Notre Dame**\n🔗 <https://www.insidelacrosse.com/game3>\n\n"
            )
        return (
            "**Web Search Results** (1 of 1 unique):\n"
            "**1. Penn at Princeton**\n🔗 <https://www.espn.com/game4>\n\n"
        )

    async def fake_chat(**kwargs):
        prompt = kwargs["user_message"]
        assert "Window: today." in prompt
        assert "Reference date: Saturday, April 11, 2026 (America/New_York)." in prompt
        assert "do not shift the answer to a different calendar day" in prompt
        assert "Target at least 10 distinct game rows" in prompt
        assert "estimates the full slate size" in prompt
        assert "compact network abbreviations" in prompt
        assert "preserve ranking indicators" in prompt
        return (
            "## D1 Men's Lacrosse Today\n\n"
            "There are at least 8 notable games on today's slate, with Army-Navy and multiple ranked matchups headlining the TV window.\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Apr 11 | Navy at Army | 2:30 PM ET | CBS Sports Network | Rivalry | ncaa.com |\n"
            "| Apr 11 | Virginia at Syracuse | 4:00 PM ET | ESPNU | Ranked | espn.com |\n"
            "| Apr 11 | North Carolina at Notre Dame | 5:00 PM ET | ACC Network | Ranked | insidelacrosse.com |\n"
            "| Apr 11 | Rutgers at Maryland | 6:00 PM ET | Big Ten Network | Big Ten | btn.com |\n"
            "| Apr 11 | Penn at Princeton | 12:00 PM ET | ESPN+ | Ivy | espn.com |\n"
            "| Apr 11 | Johns Hopkins at Ohio State | 12:00 PM ET | BTN+ | Big Ten | btn.com |\n"
            "| Apr 11 | Michigan at Penn State | 1:00 PM ET | BTN+ | Big Ten | btn.com |\n"
            "| Apr 11 | Duke vs Cornell | 2:00 PM ET | Corrigan Sports | Neutral site | corrigansports.com |\n",
            [],
            "test-model",
        )

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        mod,
        "_get_reporting_reference_context",
        lambda: (dt.datetime(2026, 4, 11, 14, 0, 0), "America/New_York"),
    )

    result = await mod.generate_sports_watch_report(
        query="Show me the schedule for the men's division 1 college lacrosse games today",
        sport="lacrosse",
        league="NCAA Division 1",
    )

    search_pipeline_calls = [call for call in calls if call["provider"] != "perplexity"]
    assert calls[0]["provider"] == "perplexity"
    assert any("today all games schedule" in str(call["query"]) for call in calls)
    assert any("men's lacrosse" in str(call["query"]).lower() for call in calls[1:])
    assert any(call["num_results"] == 15 for call in search_pipeline_calls)
    assert all(call["kwargs"].get("retry_on_low_results") is True for call in search_pipeline_calls)
    assert "Context: same-day full-slate sports schedule" in result
    assert "Items listed: **8** (expected ≥ 8)" in result
    assert "Source count: **" in result


def test_merge_ranked_with_parsed_rows_skips_dedup_for_full_slate_mode():
    ranked_rows = [
        {"title": "Navy at Army", "url": "https://a.example/game1", "snippet": "Apr 11 2:30 PM ET"},
        {"title": "Navy at Army", "url": "https://b.example/game1", "snippet": "Apr 11 2:30 PM ET"},
    ]
    merged = mod._merge_ranked_with_parsed_rows(ranked_rows, [], max_rows=10, dedupe=False)
    assert len(merged) == 2


@pytest.mark.asyncio
async def test_e2e_weekend_sports_recap_contracts_include_coverage_and_confidence(monkeypatch):
    async def fake_search_web(query: str, **kwargs) -> str:
        assert "weekend recap" in query.lower()
        assert kwargs.get("retry_on_low_results") is True
        assert kwargs.get("expand_query") is True
        return (
            "**Web Search Results** (8 of 8 unique):\n"
            "**1. Maryland vs Johns Hopkins**\nFinal 12-10 updated today\n🔗 <https://www.espn.com/lacrosse/game-1>\n\n"
            "**2. Virginia vs Duke**\nFinal 14-11 updated today\n🔗 <https://www.ncaa.com/game-2>\n\n"
            "**3. Syracuse vs Notre Dame**\nFinal 9-8 updated today\n🔗 <https://www.usila.org/game-3>\n\n"
            "**4. Penn State vs Michigan**\nFinal 11-7 updated today\n🔗 <https://www.espn.com/lacrosse/game-4>\n\n"
            "**5. Yale vs Princeton**\nFinal 13-12 updated today\n🔗 <https://www.ncaa.com/game-5>\n\n"
            "**6. Army vs Navy**\nFinal 10-9 updated today\n🔗 <https://www.usila.org/game-6>\n\n"
            "**7. Georgetown vs Villanova**\nFinal 8-6 updated today\n🔗 <https://www.espn.com/lacrosse/game-7>\n\n"
            "**8. Cornell vs Harvard**\nFinal 15-13 updated today\n🔗 <https://www.ncaa.com/game-8>\n\n"
            "*Providers queried: perplexity, firecrawl*"
        )

    async def fake_chat(**_kwargs):
        response = (
            "## D1 Weekend Recap\n\n"
            "| Date | Matchup | Time/Result | Watch | Notes | Sources |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Fri | Team A vs Team B | Final 10-8 | ESPN+ | Top-20 matchup | espn.com |\n"
            "| Sat | Team C vs Team D | Final 13-11 | ACCN | Overtime | ncaa.com |\n"
            "| Sat | Team E vs Team F | Final 12-9 | ESPNU | Rivalry | usila.org |\n"
            "| Sat | Team G vs Team H | Final 11-10 | BTN | One-goal game | espn.com |\n"
            "| Sun | Team I vs Team J | Final 9-7 | ESPN+ | Defensive battle | ncaa.com |\n"
            "| Sun | Team K vs Team L | Final 15-12 | ACCN | Fast tempo | usila.org |\n"
            "| Sun | Team M vs Team N | Final 8-7 | ESPNU | Late winner | espn.com |\n"
            "| Sun | Team O vs Team P | Final 14-13 | BTN | Double OT | ncaa.com |\n"
        )
        return response, {}, "test-model"

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = await mod.generate_sports_watch_report(
        query="Drop a weekend recap for D1 lacrosse with scores, watch options, and sources for Discord",
        sport="lacrosse",
        league="NCAA Division 1",
        days=3,
    )

    assert "## 📎 Coverage Summary" in result
    assert "Items listed: **8** (expected ≥ 8)" in result
    assert "Source count: **3** distinct domains (required ≥ 3)" in result
    assert "Confidence posture:" in result
    assert "Status: ✅ Coverage thresholds met" in result
    assert "Fallback broadening used: no" in result


@pytest.mark.asyncio
async def test_e2e_incident_recap_adds_uncertainty_and_coverage_summary(monkeypatch):
    messages = [
        _msg("Oncall", "Impact: API latency spiked and 5xx errors increased."),
        _msg("SRE", "Timeline: deploy started at 13:02 UTC, rollback at 13:21 UTC."),
        _msg("IC", "Mitigation: rolled back canary and scaled read replicas."),
    ]

    class _FakeChannel:
        id = 9876
        name = "incident-war-room"

        async def history(self, **_kwargs):
            for item in messages:
                yield item

    fake_bot = SimpleNamespace(
        get_channel=lambda _cid: _FakeChannel(),
        fetch_channel=None,
    )

    async def fake_chat(**kwargs):
        assert "weekly Discord recap for #incident-war-room" in kwargs["user_message"]
        return (
            "## Incident Summary\n\n"
            "| Section | Detail | Sources |\n"
            "| --- | --- | --- |\n"
            "| Impact | Elevated API latency and intermittent 5xx for 19 minutes. | status.example.com |\n"
            "| Timeline | Some updates conflict across notes. | N/A |\n"
            "| Mitigation | Rolled back canary and scaled replicas. | github.com/example/openclaw/actions/1 |\n",
            [],
            "test-model",
        )

    monkeypatch.setattr(mod, "get_bot", lambda: fake_bot)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(mod, "set_anchor_state", lambda *args, **kwargs: None)
    monkeypatch.setenv("THREAD_DB_PATH", "/tmp/openclaw-reporting-e2e.db")

    result = await mod.generate_channel_recap_report(
        channel_id=9876,
        days=7,
        focus="Summarize this incident with impact, timeline, mitigation, and follow-ups",
    )

    assert "## Weekly recap for #incident-war-room" in result
    assert "Partial coverage warning" in result
    assert "Retry scope hint: limit recap scope" in result
    assert "Confidence note" in result
    assert "## 📎 Coverage Summary" in result
    assert "Coverage shortfall: add source backing for unsupported claim-like statements." in result
    assert "Evidence status: ⚠️ unsupported claim rows detected" in result
    assert "Evidence completeness:" in result


def test_evidence_health_summary_flags_stale_sources():
    rows = [
        {
            "title": "Archived box office table",
            "url": "https://example.com/old-report",
            "snippet": "Historical archive from 2012 with outdated totals.",
        },
        {
            "title": "Current box office weekend",
            "url": "https://www.the-numbers.com/weekend-box-office-chart",
            "snippet": "Updated today with latest weekend gross.",
        },
    ]

    ranked = mod._rank_search_evidence_rows(rows)
    health = mod._summarize_evidence_health(ranked)
    lines = mod._format_evidence_health_lines(health)

    assert health["stale_count"] >= 1
    assert any("stale evidence" in line for line in lines)


def test_evidence_health_summary_detects_conflicting_claims():
    rows = [
        {
            "title": "Maryland vs Johns Hopkins final",
            "url": "https://www.espn.com/game/1",
            "snippet": "Final score 12-10 reported after overtime.",
            "freshness_score": 80,
        },
        {
            "title": "Maryland vs Johns Hopkins final",
            "url": "https://www.ncaa.com/game/1",
            "snippet": "Final score 9-8 per official recap.",
            "freshness_score": 80,
        },
    ]

    health = mod._summarize_evidence_health(rows)
    lines = mod._format_evidence_health_lines(health)

    assert health["conflict_groups"] >= 1
    assert any("conflicting claims" in line for line in lines)


def test_compute_evidence_completeness_penalizes_unsupported_claim_rows():
    report = (
        "| Date | Matchup | Result | Sources |\n"
        "| --- | --- | --- | --- |\n"
        "| Sat | Team A vs Team B | Final 12-10 | espn.com |\n"
        "| Sun | Team C vs Team D | Final 9-8 | N/A |\n"
    )
    metric = mod._compute_evidence_completeness(report)
    assert metric["claim_like_count"] == 2
    assert metric["unsupported_claim_count"] == 1
    assert metric["evidence_completeness"] == 0.5


def test_compute_evidence_completeness_no_false_penalty_with_sources():
    report = (
        "| Date | Matchup | Result | Sources |\n"
        "| --- | --- | --- | --- |\n"
        "| Sat | Team A vs Team B | Final 12-10 | https://www.espn.com/game/1 |\n"
        "| Sun | Team C vs Team D | Final 9-8 | ncaa.com |\n"
    )
    metric = mod._compute_evidence_completeness(report)
    assert metric["claim_like_count"] == 2
    assert metric["unsupported_claim_count"] == 0
    assert metric["evidence_completeness"] == 1.0


def test_compute_evidence_completeness_fail_safe_without_source_fields():
    recap = "- Incident lasted 19 minutes and impacted API latency."
    metric = mod._compute_evidence_completeness(recap)
    assert metric["claim_like_count"] == 1
    assert metric["fail_safe"] is True
    assert metric["unsupported_claim_count"] == 0
    assert metric["evidence_completeness"] == 1.0


def test_evidence_health_detects_low_trust_low_freshness_risk():
    rows = [
        {
            "title": "Rumor recap",
            "url": "https://example-rumor-blog.com/post",
            "snippet": "Archived retrospective from 2016.",
            "trust_score": 42,
            "freshness_score": 21,
        },
        {
            "title": "Forum speculation",
            "url": "https://reddit.com/r/example",
            "snippet": "Historical discussion with no primary source.",
            "trust_score": 45,
            "freshness_score": 30,
        },
    ]
    health = mod._summarize_evidence_health(rows)
    lines = mod._format_evidence_health_lines(health)

    assert health["low_trust_low_fresh_count"] == 2
    assert health["strict_uncertainty_required"] is True
    assert any("low-trust + low-freshness evidence" in line for line in lines)
    assert any("tentative wording" in line for line in lines)


def test_uncertainty_wording_enforced_for_risky_evidence():
    report = "## Weekly recap\n\nMaryland will definitely win and this is clearly confirmed."
    guarded = mod._enforce_uncertainty_wording(report, require_uncertainty=True)

    assert "Confidence note" in guarded
    assert "tentative" in guarded.lower()


def test_conflict_detection_avoids_false_positives_for_coherent_evidence():
    rows = [
        {
            "title": "Maryland vs Johns Hopkins final",
            "url": "https://www.espn.com/game/1",
            "snippet": "Final score 12-10 after overtime.",
            "trust_score": 95,
            "freshness_score": 85,
        },
        {
            "title": "Maryland vs Johns Hopkins final",
            "url": "https://www.ncaa.com/game/1",
            "snippet": "Final score 12-10 official recap.",
            "trust_score": 95,
            "freshness_score": 85,
        },
    ]
    health = mod._summarize_evidence_health(rows)

    assert health["conflict_groups"] == 0
    assert health["strict_uncertainty_required"] is False


def test_filter_near_duplicate_rows_preserves_distinct_events():
    rows = [
        {
            "title": "Maryland vs Duke final",
            "url": "https://www.espn.com/lacrosse/game-100",
            "snippet": "Saturday final score 12-10.",
        },
        {
            "title": "Maryland vs Duke final update",
            "url": "https://www.espn.com/lacrosse/game-100",
            "snippet": "Saturday final score 12-10 (updated).",
        },
        {
            "title": "Maryland vs Duke final",
            "url": "https://www.espn.com/lacrosse/game-101",
            "snippet": "Sunday final score 11-10.",
        },
    ]

    filtered = mod._filter_near_duplicate_evidence_rows(rows)
    urls = [row["url"] for row in filtered]

    assert len(filtered) == 2
    assert "https://www.espn.com/lacrosse/game-100" in urls
    assert "https://www.espn.com/lacrosse/game-101" in urls


def test_merge_ranked_with_parsed_restores_distinct_event_rows():
    parsed_rows = [
        {
            "title": "Maryland vs Duke final",
            "url": "https://www.espn.com/lacrosse/game-100",
            "snippet": "Saturday final score 12-10.",
        },
        {
            "title": "Maryland vs Duke final",
            "url": "https://www.espn.com/lacrosse/game-101",
            "snippet": "Sunday final score 11-10.",
        },
    ]
    ranked_rows = [parsed_rows[0]]

    merged = mod._merge_ranked_with_parsed_rows(ranked_rows, parsed_rows, max_rows=10)
    urls = [row["url"] for row in merged]

    assert len(merged) == 2
    assert "https://www.espn.com/lacrosse/game-100" in urls
    assert "https://www.espn.com/lacrosse/game-101" in urls


@pytest.mark.asyncio
async def test_generate_box_office_report_low_diversity_surfaces_shortfall_and_counts(monkeypatch):
    async def fake_search_web(_query: str, **_kwargs):
        return (
            "**Web Search Results**\n"
            "**1. Box Office One**\nSnippet\n🔗 <https://www.the-numbers.com/weekend/1>\n\n"
            "**2. Box Office Two**\nSnippet\n🔗 <https://www.the-numbers.com/weekend/2>\n"
        )

    async def fake_chat(**_kwargs):
        return (
            "## Weekly Box Office\n\n"
            "| Film | Weekend Gross | Domestic Total | Worldwide Total | Sources |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| A | $1 | $2 | $3 | the-numbers.com |\n",
            [],
            "test-model",
        )

    monkeypatch.setattr("skills.search_skills.search_web", fake_search_web)
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = await mod.generate_box_office_report(query="weekly box office recap")

    assert "Source diversity floor missed (1/2 distinct domains)" in result
    assert "Actionable shortfall" in result
    assert "Retry scope hint" in result
    assert "Items listed: **1**" in result
    assert "Source count: **1** distinct domains (required ≥ 2)" in result
    assert "Source diversity shortfall: add **1** more distinct domain(s)" in result
    assert "Status: ⚠️ **Partial coverage**" in result
