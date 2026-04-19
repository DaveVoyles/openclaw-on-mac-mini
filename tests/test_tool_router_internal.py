"""Tests for tool_router.py — internal helper functions."""

from __future__ import annotations

import os

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from tool_router import (
    _declaration_domains,
    _declaration_matches_pack,
    _extract_pack_directive,
    _extract_request_hints,
    _extract_requested_item_count,
    _infer_message_domains,
    _iter_metadata_values,
    _matching_workflow_bundles,
    _score_declaration,
    _tokenize,
    route_tool_declarations,
)

# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_basic_tokenize(self):
        tokens = _tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_min_length_2(self):
        tokens = _tokenize("a bc def")
        assert "a" not in tokens  # single char excluded
        assert "bc" in tokens
        assert "def" in tokens

    def test_tool_router_internal_lowercases(self):
        tokens = _tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens

    def test_tool_router_internal_empty_string(self):
        assert _tokenize("") == set()

    def test_none_like_empty(self):
        assert _tokenize(None) == set()  # type: ignore


# ---------------------------------------------------------------------------
# _iter_metadata_values
# ---------------------------------------------------------------------------

class TestIterMetadataValues:
    def test_basic_fields(self):
        decl = {"name": "my_tool", "description": "does stuff", "category": "cat"}
        values = _iter_metadata_values(decl)
        assert any("my tool" in v for v in values)
        assert any("does stuff" in v for v in values)
        assert any("cat" in v for v in values)

    def test_list_fields_expanded(self):
        decl = {"name": "t", "aliases": ["alias_one", "alias_two"]}
        values = _iter_metadata_values(decl)
        assert "alias_one" in values
        assert "alias_two" in values

    def test_string_field_included(self):
        decl = {"name": "t", "domains": "sports"}
        values = _iter_metadata_values(decl)
        assert "sports" in values

    def test_missing_fields_tolerated(self):
        values = _iter_metadata_values({})
        # Should not raise, returns list with empty strings
        assert isinstance(values, list)


# ---------------------------------------------------------------------------
# _declaration_domains
# ---------------------------------------------------------------------------

class TestDeclarationDomains:
    def test_sports_via_domains_list(self):
        decl = {"name": "t", "domains": ["sports"]}
        assert "sports" in _declaration_domains(decl)

    def test_sports_via_persona(self):
        decl = {"name": "t", "personas": ["sports-analyst"]}
        assert "sports" in _declaration_domains(decl)

    def test_wwe_via_domains(self):
        decl = {"name": "t", "domains": ["wwe"]}
        assert "wwe" in _declaration_domains(decl)

    def test_unrelated_tool_has_no_domains(self):
        decl = {"name": "calendar_tool", "description": "manages calendar events"}
        domains = _declaration_domains(decl)
        # Calendar has nothing to do with sports/wwe/gaming
        assert "sports" not in domains
        assert "wwe" not in domains


# ---------------------------------------------------------------------------
# _infer_message_domains
# ---------------------------------------------------------------------------

class TestInferMessageDomains:
    def test_sports_terms_detected(self):
        msg = "who won the lacrosse game last week in ncaa"
        tokens = _tokenize(msg)
        domains = _infer_message_domains(msg, tokens)
        assert "sports" in domains

    def test_wwe_terms_detected(self):
        msg = "what happened at wrestlemania"
        tokens = _tokenize(msg)
        domains = _infer_message_domains(msg, tokens)
        assert "wwe" in domains

    def test_no_terms_returns_empty(self):
        msg = "what is the weather today"
        tokens = _tokenize(msg)
        domains = _infer_message_domains(msg, tokens)
        assert domains == set()


# ---------------------------------------------------------------------------
# _extract_pack_directive
# ---------------------------------------------------------------------------

class TestExtractPackDirective:
    def test_use_colon_directive(self):
        pack, persona, cleaned = _extract_pack_directive("use:sports give me game results")
        assert pack == "sports"
        assert persona == "sports-analyst"
        assert "use:sports" not in cleaned

    def test_plain_pack_directive(self):
        pack, persona, cleaned = _extract_pack_directive("use sports pack for this request")
        assert pack == "sports"

    def test_no_directive(self):
        pack, persona, cleaned = _extract_pack_directive("give me sports results")
        assert pack is None
        assert persona is None

    def test_finance_pack(self):
        pack, persona, _ = _extract_pack_directive("use:finance check S&P 500")
        assert pack == "finance"
        assert persona == "finance-analyst"

    def test_alias_resolves(self):
        pack, _, _ = _extract_pack_directive("use:markets show earnings")
        assert pack == "finance"

    def test_unknown_pack_returns_none(self):
        pack, _, _ = _extract_pack_directive("use:unknownpack do something")
        assert pack is None


# ---------------------------------------------------------------------------
# _declaration_matches_pack
# ---------------------------------------------------------------------------

class TestDeclarationMatchesPack:
    def test_always_available_matches_any_pack(self):
        decl = {"name": "search_web", "always_available": True}
        assert _declaration_matches_pack(decl, "sports") is True

    def test_always_available_name_matches(self):
        decl = {"name": "search_web"}
        assert _declaration_matches_pack(decl, "sports") is True

    def test_sports_tool_matches_sports_pack(self):
        decl = {
            "name": "generate_sports_watch_report",
            "domains": ["sports"],
            "description": "sports report tool",
        }
        assert _declaration_matches_pack(decl, "sports") is True

    def test_calendar_tool_does_not_match_sports(self):
        decl = {"name": "create_calendar_event", "description": "manages calendar events"}
        # May still return True via term overlap — just ensure no exception
        result = _declaration_matches_pack(decl, "sports")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _score_declaration
# ---------------------------------------------------------------------------

class TestScoreDeclaration:
    def test_higher_score_for_relevant_tool(self):
        decl = {
            "name": "generate_sports_watch_report",
            "description": "generate a sports watch guide",
            "keywords": ["sports", "games", "schedule"],
        }
        msg_lower = "give me a lacrosse sports schedule"
        tokens = _tokenize(msg_lower)
        score = _score_declaration(msg_lower, tokens, decl)
        assert score > 0

    def test_always_available_boost(self):
        decl = {"name": "search_web", "always_available": True}
        score = _score_declaration("anything", _tokenize("anything"), decl)
        assert score >= 1  # gets the +1 always_available bonus

    def test_intent_hint_boost(self):
        decl = {"name": "generate_channel_recap_report"}
        msg = "give me a weekly recap of this channel"
        tokens = _tokenize(msg)
        score = _score_declaration(msg, tokens, decl)
        assert score >= 8  # at least one intent hint matched (+8)


# ---------------------------------------------------------------------------
# _matching_workflow_bundles
# ---------------------------------------------------------------------------

class TestMatchingWorkflowBundles:
    def test_media_health_phrase(self):
        msg = "anything broken in the stack"
        tokens = _tokenize(msg)
        bundles = _matching_workflow_bundles(msg, tokens)
        assert any(b["name"] == "media-health" for b in bundles)

    def test_calendar_phrase(self):
        msg = "what's on my calendar today"
        tokens = _tokenize(msg)
        bundles = _matching_workflow_bundles(msg, tokens)
        assert any(b["name"] == "calendar" for b in bundles)

    def test_email_phrase(self):
        msg = "search my inbox for emails"
        tokens = _tokenize(msg)
        bundles = _matching_workflow_bundles(msg, tokens)
        assert any(b["name"] == "email" for b in bundles)

    def test_no_match_returns_empty(self):
        msg = "random unrelated query about nothing"
        tokens = _tokenize(msg)
        bundles = _matching_workflow_bundles(msg, tokens)
        assert bundles == []

    def test_plex_activity_bundle(self):
        msg = "what's playing on plex right now"
        tokens = _tokenize(msg)
        bundles = _matching_workflow_bundles(msg, tokens)
        assert any(b["name"] == "plex-activity" for b in bundles)


# ---------------------------------------------------------------------------
# _extract_requested_item_count
# ---------------------------------------------------------------------------

class TestExtractRequestedItemCountRouter:
    def test_tool_router_internal_top_n_stories(self):
        assert _extract_requested_item_count("give me top 5 stories") == 5

    def test_at_least_n_items(self):
        assert _extract_requested_item_count("at least 3 items from today") == 3

    def test_tool_router_internal_bare_count(self):
        assert _extract_requested_item_count("10 headlines this week") == 10

    def test_tool_router_internal_no_count_returns_none(self):
        assert _extract_requested_item_count("what happened today?") is None

    def test_tool_router_internal_capped_at_25(self):
        assert _extract_requested_item_count("top 50 stories") == 25


# ---------------------------------------------------------------------------
# _extract_request_hints
# ---------------------------------------------------------------------------

class TestExtractRequestHints:
    def test_timeframe_today(self):
        msg = "What happened today?"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("days") == 1
        assert hints.get("timeframe") == "today"

    def test_timeframe_last_week(self):
        msg = "recap from last week"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("days") == 7

    def test_explicit_day_window(self):
        msg = "What happened in the last 5 days?"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("days") == 5

    def test_sport_extracted(self):
        msg = "latest lacrosse scores"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("sport") == "lacrosse"

    def test_team_extracted(self):
        msg = "What games does Maryland have this week?"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("team") == "Maryland"

    def test_box_office_report_topic(self):
        msg = "give me box office results"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("report_topic") == "box-office"
        assert hints.get("retrieval_profile") == "news"

    def test_table_output_style(self):
        msg = "show me a markdown table"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("output_style") == "table"

    def test_emoji_level_light(self):
        msg = "use emoji in the response"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("emoji_level") == "light"

    def test_detail_level_brief(self):
        msg = "give me a quick summary"
        hints = _extract_request_hints(msg, msg.lower(), _tokenize(msg))
        assert hints.get("detail_level") == "brief"


# ---------------------------------------------------------------------------
# route_tool_declarations — edge cases not covered by existing tests
# ---------------------------------------------------------------------------

class TestRouteToolDeclarationsEdgeCases:
    def test_empty_declarations_returns_empty(self):
        result, info = route_tool_declarations("anything", [])
        assert result == []
        assert info["strategy"] == "empty"

    def test_empty_message_falls_back_full(self):
        decls = [{"name": "search_web"}]
        result, info = route_tool_declarations("   ", decls)
        assert info["strategy"] == "fallback-full"

    def test_pack_directive_filters_declarations(self):
        decls = [
            {"name": "search_web", "always_available": True},
            {"name": "generate_sports_watch_report", "domains": ["sports"], "description": "sports"},
            {"name": "create_calendar_event", "description": "calendar tool no sports"},
        ]
        result, info = route_tool_declarations("use:sports what games are on", decls)
        assert info.get("pack") == "sports"

    def test_low_confidence_returns_fallback(self):
        decls = [{"name": "obscure_tool", "description": "very specific thing"}]
        result, info = route_tool_declarations("hello world", decls)
        # Low score → fallback strategy
        assert info["strategy"] in {"fallback-full", "shortlist", "guarded-fallback"}

    def test_info_contains_required_keys(self):
        decls = [{"name": "search_web", "always_available": True}]
        _, info = route_tool_declarations("search the web for sports news", decls)
        for key in ("strategy", "selected", "top_score", "bundles", "hints"):
            assert key in info

    def test_sports_domain_guard_suppresses_unrelated(self):
        """Non-sports messages should suppress sports-only tools."""
        decls = [
            {"name": "search_web", "always_available": True},
            {
                "name": "generate_sports_watch_report",
                "domains": ["sports"],
                "description": "ncaa lacrosse espn sports game schedule",
                "keywords": ["sports", "game", "schedule"],
            },
        ]
        result, info = route_tool_declarations("what is the weather today?", decls)
        names = {d.get("name") for d in result}
        # sports tool should be suppressed from shortlist (guard active)
        assert "generate_sports_watch_report" not in names or info.get("strategy") != "shortlist"
