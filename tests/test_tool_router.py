"""Tests for plain-English tool routing."""

from llm_client import _get_tool_declarations
from tool_router import route_tool_declarations


def _route_names(prompt: str) -> tuple[set[str], dict]:
    selected, info = route_tool_declarations(prompt, _get_tool_declarations())
    names = {str(declaration.get("name", "")) for declaration in selected}
    return names, info


def test_route_tool_declarations_prefers_weekly_recap():
    names, info = _route_names("Give me a recap of this channel from the last week with highlights and action items.")
    assert info["strategy"] == "shortlist"
    assert "generate_channel_recap_report" in names


def test_route_tool_declarations_prefers_sports_watch_guides():
    names, info = _route_names(
        "Look at this week's upcoming men's division 1 college lacrosse games and make a table with times and where to watch."
    )
    assert info["strategy"] == "shortlist"
    assert "generate_sports_watch_report" in names
    assert "search_web" in names


def test_route_tool_declarations_prefers_media_health_bundle():
    names, info = _route_names("Is anything broken in the media stack right now?")
    assert info["strategy"] == "shortlist"
    assert "media-health" in info["bundles"]
    assert "create_status_report" in names
    assert "check_arr_health" in names
    assert "check_plex_status" in names


def test_route_tool_declarations_prefers_calendar_tools():
    names, info = _route_names("What's on my calendar this week, and what do I have tomorrow?")
    assert info["strategy"] == "shortlist"
    assert "calendar" in info["bundles"]
    assert "get_upcoming_events" in names
    assert "get_todays_events" in names or "create_calendar_event" in names


def test_route_tool_declarations_prefers_email_tools():
    names, info = _route_names("Search my inbox for anything from ESPN about lacrosse and email me a recap.")
    assert info["strategy"] == "shortlist"
    assert "email" in info["bundles"]
    assert "search_emails" in names
    assert "send_email" in names


def test_route_tool_declarations_extracts_hints_for_sports_prompts():
    _, info = _route_names("What games does Maryland have in the next 5 days in men's division 1 lacrosse?")
    assert info["hints"]["team"] == "Maryland"
    assert info["hints"]["sport"] == "lacrosse"
    assert info["hints"]["days"] == 5


def test_route_tool_declarations_prefers_sports_tool_for_weekend_recap():
    names, info = _route_names("Give me a men's division 1 lacrosse weekend recap with game results and sources.")
    assert info["strategy"] == "shortlist"
    assert info["hints"]["report_topic"] == "sports"
    assert info["hints"]["retrieval_profile"] == "sports"
    assert "generate_sports_watch_report" in names


def test_route_tool_declarations_prefers_box_office_report_bundle():
    names, info = _route_names(
        "Give me the box office financials and new releases for the last week in a markdown table with emojis."
    )
    assert info["strategy"] == "shortlist"
    assert "weekly-market-report" in info["bundles"]
    assert "generate_box_office_report" in names
    assert info["hints"]["report_topic"] == "box-office"
    assert info["hints"]["retrieval_profile"] == "news"
    assert info["hints"]["output_style"] == "table"
    assert info["hints"]["emoji_level"] in {"light", "rich"}


def test_route_tool_declarations_sets_engineering_retrieval_profile_hint():
    _, info = _route_names("Give me an engineering incident recap for this week's deploy outage and latency spikes.")
    assert info["hints"]["report_topic"] == "engineering"
    assert info["hints"]["retrieval_profile"] == "engineering"


def test_route_tool_declarations_falls_back_to_full_set_for_low_confidence():
    all_declarations = _get_tool_declarations()
    selected, info = route_tool_declarations("hello there", all_declarations)
    assert info["strategy"] in {"fallback-full", "guarded-fallback"}
    assert len(selected) <= len(all_declarations)


def test_route_tool_declarations_filters_finance_pack_from_use_prefix():
    declarations = [
        {
            "name": "search_web",
            "description": "Search the web",
            "always_available": True,
        },
        {
            "name": "finance_brief",
            "description": "Generate finance market updates",
            "keywords": ["finance", "markets", "stocks"],
        },
        {
            "name": "sports_watch",
            "description": "Build sports watch guide",
            "keywords": ["sports", "watch"],
        },
    ]
    selected, info = route_tool_declarations(
        "/ask use:finance summarize market movers this week",
        declarations,
    )
    names = {str(declaration.get("name", "")) for declaration in selected}
    assert info["pack"] == "finance"
    assert info["persona"] == "finance-analyst"
    assert "finance_brief" in names
    assert "sports_watch" not in names
    assert "search_web" in names


def test_route_tool_declarations_supports_plain_english_pack_and_persona_metadata():
    declarations = [
        {
            "name": "search_web",
            "description": "Search the web",
            "always_available": True,
        },
        {
            "name": "wwe_recap",
            "description": "Generate pro wrestling recap",
            "personas": ["wwe-reporter"],
        },
        {
            "name": "gaming_news",
            "description": "Get gaming headlines",
            "domains": ["gaming"],
        },
    ]
    selected, info = route_tool_declarations(
        "Use wwe pack and give me this week's RAW and SmackDown recap.",
        declarations,
    )
    names = {str(declaration.get("name", "")) for declaration in selected}
    assert info["pack"] == "wwe"
    assert info["persona"] == "wwe-reporter"
    assert "wwe_recap" in names
    assert "gaming_news" not in names


def test_route_tool_declarations_guards_wwe_and_sports_for_non_matching_intent():
    declarations = [
        {"name": "search_web", "description": "Search the web", "always_available": True},
        {"name": "wwe_recap", "description": "Generate WWE RAW recap", "personas": ["wwe-reporter"]},
        {"name": "sports_watch", "description": "Build sports watch guide", "domains": ["sports"]},
        {"name": "project_recap", "description": "Summarize project updates and blockers"},
    ]
    selected, info = route_tool_declarations(
        "Give me a weekly recap of deployment blockers and engineering risks.",
        declarations,
    )
    names = {str(declaration.get("name", "")) for declaration in selected}
    assert "project_recap" in names
    assert "wwe_recap" not in names
    assert "sports_watch" not in names
    assert info["guard_suppressed"]
    assert "wwe_recap" in info["guard_suppressed"]


def test_route_tool_declarations_preserves_explicit_wwe_pack_directive():
    declarations = [
        {"name": "search_web", "description": "Search the web", "always_available": True},
        {"name": "wwe_recap", "description": "Generate WWE RAW recap", "personas": ["wwe-reporter"]},
        {"name": "sports_watch", "description": "Build sports watch guide", "domains": ["sports"]},
    ]
    selected, info = route_tool_declarations("use:wwe recap RAW and SmackDown this week", declarations)
    names = {str(declaration.get("name", "")) for declaration in selected}
    assert info["pack"] == "wwe"
    assert "wwe_recap" in names


def test_route_tool_declarations_preserves_explicit_sports_pack_directive():
    declarations = [
        {"name": "search_web", "description": "Search the web", "always_available": True},
        {"name": "sports_watch", "description": "Build sports watch guide", "domains": ["sports"]},
        {"name": "project_recap", "description": "Summarize project updates and blockers"},
    ]
    selected, info = route_tool_declarations("use:sports recap this week's launch blockers", declarations)
    names = {str(declaration.get("name", "")) for declaration in selected}
    assert info["pack"] == "sports"
    assert "sports_watch" in names


def test_route_tool_declarations_sets_gaming_retrieval_profile_hints():
    declarations = [
        {"name": "search_web", "description": "Search the web", "always_available": True},
        {"name": "gaming_news", "description": "Get gaming headlines", "domains": ["gaming"]},
        {"name": "sports_watch", "description": "Build sports watch guide", "domains": ["sports"]},
    ]
    selected, info = route_tool_declarations(
        "Give me a recap of top gaming stories this week on Xbox and PlayStation.",
        declarations,
    )
    names = {str(declaration.get("name", "")) for declaration in selected}
    assert info["hints"]["retrieval_profile"] == "gaming"
    assert info["hints"]["report_topic"] == "gaming"
    assert "gaming_news" in names


def test_route_tool_declarations_extracts_requested_item_count_hint():
    _, info = _route_names("Give me top 8 gaming stories from this weekend with links.")
    assert info["hints"]["requested_item_count"] == 8
    assert info["hints"]["timeframe"] == "this weekend"


def test_route_tool_declarations_extracts_last_weekend_timeframe_hint():
    _, info = _route_names("Need a last weekend recap of lacrosse results.")
    assert info["hints"]["timeframe"] == "last weekend"
    assert info["hints"]["days"] == 3


def test_route_tool_declarations_extracts_explicit_day_window_timeframe_hint():
    _, info = _route_names("Bring in 6 headlines from the past 7 days on PlayStation updates.")
    assert info["hints"]["requested_item_count"] == 6
    assert info["hints"]["timeframe"] == "past 7 days"
    assert info["hints"]["days"] == 7
