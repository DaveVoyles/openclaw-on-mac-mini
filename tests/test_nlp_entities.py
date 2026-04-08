"""Tests for nlp_entities module."""

import pytest
import nlp_entities
from nlp_entities import (
    extract_entities,
    enrich_route_text_and_hints,
    _phrase_in_text,
    _dedupe,
)


class TestPhraseInText:
    def test_simple_match(self):
        assert _phrase_in_text("i use plex daily", "plex") is True

    def test_no_match(self):
        assert _phrase_in_text("nothing relevant here", "plex") is False

    def test_word_boundary_respected(self):
        # "nba" should not match inside "wnba"
        assert _phrase_in_text("i watch wnba games", "nba") is False

    def test_at_start_of_string(self):
        assert _phrase_in_text("plex is great", "plex") is True

    def test_at_end_of_string(self):
        assert _phrase_in_text("i love plex", "plex") is True

    def test_numeric_boundary(self):
        # "d1" should not match inside "d10"
        assert _phrase_in_text("d10 tournament", "d1") is False


class TestDedupe:
    def test_removes_duplicates(self):
        assert _dedupe(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_preserves_order(self):
        assert _dedupe(["c", "b", "a"]) == ["c", "b", "a"]

    def test_empty_list(self):
        assert _dedupe([]) == []

    def test_no_duplicates_unchanged(self):
        assert _dedupe(["x", "y", "z"]) == ["x", "y", "z"]

    def test_all_duplicates(self):
        assert _dedupe(["a", "a", "a"]) == ["a"]


class TestExtractEntities:
    def test_service_direct_match(self):
        result = extract_entities("plex is really slow today")
        assert result.get("services") == ["plex"]

    def test_service_alias_sabnzbd(self):
        result = extract_entities("sab is not downloading anything")
        assert "sabnzbd" in result.get("services", [])

    def test_service_alias_qbittorrent(self):
        result = extract_entities("qbit keeps crashing on me")
        assert "qbittorrent" in result.get("services", [])

    def test_service_alias_sab_nzbd_spaced(self):
        result = extract_entities("sab nzbd stopped seeding")
        assert "sabnzbd" in result.get("services", [])

    def test_multiple_services(self):
        result = extract_entities("plex and sonarr are both broken")
        services = result.get("services", [])
        assert "plex" in services
        assert "sonarr" in services

    def test_deduplication_same_service(self):
        result = extract_entities("plex plex plex is broken")
        assert result.get("services") == ["plex"]

    def test_league_nba(self):
        result = extract_entities("nba finals game 7 recap")
        assert "NBA" in result.get("leagues", [])

    def test_league_nfl(self):
        result = extract_entities("nfl draft picks announced")
        assert "NFL" in result.get("leagues", [])

    def test_league_ncaa_division1_alias(self):
        result = extract_entities("d1 basketball tournament")
        assert "NCAA Division I" in result.get("leagues", [])

    def test_league_mlb(self):
        result = extract_entities("mlb world series game 1")
        assert "MLB" in result.get("leagues", [])

    def test_wwe_raw_alias(self):
        result = extract_entities("monday night raw highlights")
        assert "WWE RAW" in result.get("wwe", [])

    def test_wwe_smackdown(self):
        result = extract_entities("smackdown results this week")
        assert "WWE SmackDown" in result.get("wwe", [])

    def test_wwe_nxt(self):
        result = extract_entities("nxt takeover was incredible")
        assert "WWE NXT" in result.get("wwe", [])

    def test_platform_playstation_ps5(self):
        result = extract_entities("playing on ps5 right now")
        assert "PlayStation" in result.get("platforms", [])

    def test_platform_xbox(self):
        result = extract_entities("xbox series x game pass")
        assert "Xbox" in result.get("platforms", [])

    def test_platform_nintendo_switch(self):
        result = extract_entities("nintendo switch update available")
        assert "Nintendo Switch" in result.get("platforms", [])

    def test_platform_steam(self):
        result = extract_entities("just bought a game on steam")
        assert "Steam" in result.get("platforms", [])

    def test_no_entities_returns_empty_dict(self):
        result = extract_entities("hello how are you doing today")
        assert result == {}

    def test_multiple_categories_in_one_message(self):
        result = extract_entities("plex nba playstation are all mentioned here")
        assert "services" in result
        assert "leagues" in result
        assert "platforms" in result

    def test_case_insensitive_input(self):
        # input is already lowercased; canonical aliases are lowercased internally
        result = extract_entities("sonarr is down")
        assert "sonarr" in result.get("services", [])

    def test_wnba_does_not_match_nba(self):
        result = extract_entities("wnba finals tonight")
        leagues = result.get("leagues", [])
        assert "WNBA" in leagues
        assert "NBA" not in leagues


class TestEnrichRouteTextAndHints:
    def test_hints_populated_with_entities(self):
        _, hints = enrich_route_text_and_hints("plex is down", {})
        assert "entities" in hints
        assert "plex" in hints["entities"]["services"]

    def test_services_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("sonarr stopped working", {})
        assert hints.get("services") == ["sonarr"]

    def test_existing_services_not_overridden(self):
        _, hints = enrich_route_text_and_hints("sonarr stopped working", {"services": ["custom"]})
        assert hints["services"] == ["custom"]

    def test_league_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("nba game tonight", {})
        assert hints.get("league") == "NBA"

    def test_platform_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("ps5 controller disconnecting", {})
        assert "PlayStation" in hints.get("platforms", [])

    def test_wwe_entities_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("raw results tonight", {})
        assert "WWE RAW" in hints.get("wwe_entities", [])

    def test_disambiguation_this_channel(self):
        _, hints = enrich_route_text_and_hints("what is this channel about", {})
        assert hints["disambiguated_references"]["channel"] == "current"
        assert hints["disambiguation_confidence"] >= 0.7

    def test_disambiguation_this_service_single_entity(self):
        _, hints = enrich_route_text_and_hints("plex is broken, this service keeps crashing", {})
        assert hints["disambiguated_references"]["service"] == "plex"

    def test_disambiguation_this_league_single_entity(self):
        _, hints = enrich_route_text_and_hints("nfl this league is intense", {})
        assert hints["disambiguated_references"]["league"] == "NFL"

    def test_disambiguation_this_platform_single_entity(self):
        _, hints = enrich_route_text_and_hints("ps5 is better, this platform wins", {})
        assert hints["disambiguated_references"]["platform"] == "PlayStation"

    def test_disambiguation_this_show_single_entity(self):
        _, hints = enrich_route_text_and_hints("raw was great, this show delivered", {})
        assert hints["disambiguated_references"]["show"] == "WWE RAW"

    def test_disambiguation_confidence_above_threshold(self):
        _, hints = enrich_route_text_and_hints("what is this channel for", {})
        assert hints.get("disambiguation_confidence", 0) >= 0.7

    def test_no_disambiguation_without_reference_pattern(self):
        _, hints = enrich_route_text_and_hints("plex is broken", {})
        assert "disambiguated_references" not in hints

    def test_unresolved_service_when_no_service_entity(self):
        _, hints = enrich_route_text_and_hints("this service is broken", {})
        assert "service" in hints.get("unresolved_references", [])

    def test_unresolved_league_when_multiple_entities(self):
        _, hints = enrich_route_text_and_hints("nba and nfl this league is great", {})
        assert "league" in hints.get("unresolved_references", [])

    def test_unresolved_platform_when_multiple_entities(self):
        _, hints = enrich_route_text_and_hints("ps5 and xbox this platform rules", {})
        assert "platform" in hints.get("unresolved_references", [])

    def test_unresolved_show_when_multiple_wwe_entities(self):
        _, hints = enrich_route_text_and_hints("raw and smackdown this show was great", {})
        assert "show" in hints.get("unresolved_references", [])

    def test_enriched_text_adds_canonical_name_not_in_message(self):
        # alias "sab" -> canonical "sabnzbd" not present in original message
        text, _ = enrich_route_text_and_hints("i use sab for downloads", {})
        assert "sabnzbd" in text

    def test_enriched_text_no_addition_when_canonical_present(self):
        # "plex" is already in the message text
        text, _ = enrich_route_text_and_hints("plex is great", {})
        assert text == "plex is great"

    def test_no_entities_returns_original_text_and_no_entities_key(self):
        text, hints = enrich_route_text_and_hints("hello world", {})
        assert text == "hello world"
        assert "entities" not in hints

    def test_plex_activity_bundle_disambiguates_service(self):
        # With no service entities but plex-activity bundle, service resolves to plex
        _, hints = enrich_route_text_and_hints(
            "this service is acting up", {},
            matched_bundle_names={"plex-activity"},
        )
        assert hints["disambiguated_references"]["service"] == "plex"
        assert hints["disambiguation_confidence"] >= 0.7

    def test_original_hints_preserved_alongside_new_keys(self):
        _, hints = enrich_route_text_and_hints("plex is down", {"custom_key": "custom_value"})
        assert hints["custom_key"] == "custom_value"
        assert "entities" in hints
