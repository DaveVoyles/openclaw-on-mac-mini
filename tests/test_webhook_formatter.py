"""Tests for webhook_formatter.py — arr/plex/qbittorrent payload formatting."""

import discord

import webhook_formatter as mod


class TestFormatSonarr:
    def test_webhook_formatter_download_event(self):
        payload = {
            "eventType": "Download",
            "series": {"title": "Breaking Bad"},
            "episodes": [{"seasonNumber": 5, "episodeNumber": 16, "title": "Felina"}],
        }
        title, desc, color = mod.format_sonarr(payload)
        assert "Sonarr" in title
        assert "Breaking Bad" in desc
        assert "S05E16" in desc
        assert "Felina" in desc
        assert color == discord.Color.green()

    def test_download_with_upgrade(self):
        payload = {
            "eventType": "Download",
            "series": {"title": "The Office"},
            "episodes": [{"seasonNumber": 1, "episodeNumber": 1, "title": "Pilot"}],
            "isUpgrade": True,
        }
        _, desc, _ = mod.format_sonarr(payload)
        assert "⬆️" in desc

    def test_grab_event_delegates_to_format_arr(self):
        payload = {
            "eventType": "Grab",
            "series": {"title": "Seinfeld"},
            "episodes": [{"seasonNumber": 1, "episodeNumber": 1, "title": "The Pilot"}],
        }
        title, desc, color = mod.format_sonarr(payload)
        assert "Webhook" in title
        assert color == discord.Color.yellow()


class TestFormatRadarr:
    def test_webhook_formatter_download_event_v2(self):
        payload = {
            "eventType": "Download",
            "movie": {"title": "Inception", "year": 2010},
        }
        title, desc, color = mod.format_radarr(payload)
        assert "Radarr" in title
        assert "Inception" in desc
        assert "2010" in desc
        assert color == discord.Color.green()

    def test_download_upgrade(self):
        payload = {
            "eventType": "Download",
            "movie": {"title": "Tenet"},
            "isUpgrade": True,
        }
        _, desc, _ = mod.format_radarr(payload)
        assert "⬆️" in desc

    def test_non_download_delegates_to_format_arr(self):
        payload = {
            "eventType": "MovieFileDelete",
            "movie": {"title": "Old Movie"},
        }
        title, _, color = mod.format_radarr(payload)
        assert "Delete" in title
        assert color == discord.Color.red()


class TestFormatGenericAndUnknown:
    def test_unknown_source_uses_generic(self):
        payload = {"foo": "bar", "count": 42}
        title, desc, color = mod.format_generic("myservice", payload)
        assert "Myservice" in title
        assert "foo" in desc
        assert "42" in desc

    def test_empty_payload_shows_no_details(self):
        title, desc, _ = mod.format_generic("empty", {})
        assert "no details" in desc.lower()


class TestFormatterRegistry:
    def test_sonarr_in_registry(self):
        assert "sonarr" in mod.FORMATTERS

    def test_radarr_in_registry(self):
        assert "radarr" in mod.FORMATTERS

    def test_plex_in_registry(self):
        assert "plex" in mod.FORMATTERS
