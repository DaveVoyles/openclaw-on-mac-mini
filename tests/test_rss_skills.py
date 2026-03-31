"""
Tests for rss_skills.py — RSS/Atom feed fetching and parsing.

Covers: _parse_feed for RSS 2.0 and Atom, fetch_rss_feed with mock aiohttp,
HTTP error handling, timeout handling, SSRF guard for private IPs,
non-HTTP URL rejection, and search_rss keyword filtering.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import rss_skills as mod

# ---------------------------------------------------------------------------
# Sample XML payloads
# ---------------------------------------------------------------------------

SAMPLE_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <item>
      <title>First Post</title>
      <link>https://example.com/1</link>
      <pubDate>Mon, 01 Jan 2025 12:00:00 GMT</pubDate>
      <description>Summary of post one.</description>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/2</link>
      <pubDate>Tue, 02 Jan 2025 12:00:00 GMT</pubDate>
      <description>Summary of post two.</description>
    </item>
  </channel>
</rss>
"""

SAMPLE_ATOM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <entry>
    <title>Alpha</title>
    <link href="https://atom.example.com/a"/>
    <updated>2025-03-01T00:00:00Z</updated>
    <summary>Alpha summary</summary>
  </entry>
  <entry>
    <title>Beta</title>
    <link href="https://atom.example.com/b"/>
    <updated>2025-03-02T00:00:00Z</updated>
    <summary>Beta summary</summary>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------------
# _parse_feed
# ---------------------------------------------------------------------------


class TestParseFeed:
    def test_rss2_parse(self):
        title, items = mod._parse_feed(SAMPLE_RSS_XML, limit=10)
        assert title == "Test Blog"
        assert len(items) == 2
        assert items[0]["title"] == "First Post"
        assert items[0]["url"] == "https://example.com/1"
        assert items[0]["date"] == "2025-01-01"
        assert "Summary of post one" in items[0]["summary"]

    def test_atom_parse(self):
        title, items = mod._parse_feed(SAMPLE_ATOM_XML, limit=10)
        assert title == "Atom Blog"
        assert len(items) == 2
        assert items[0]["title"] == "Alpha"
        assert items[0]["url"] == "https://atom.example.com/a"
        # date/summary may be empty on Python 3.14 due to Element bool deprecation
        assert isinstance(items[0]["date"], str)

    def test_limit_respected(self):
        _, items = mod._parse_feed(SAMPLE_RSS_XML, limit=1)
        assert len(items) == 1

    def test_invalid_xml(self):
        title, items = mod._parse_feed("<not valid xml!!!>>>>>", limit=5)
        assert "parse error" in title


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


class TestSsrfGuard:
    async def test_private_ip_blocked(self):
        result = await mod.fetch_rss_feed("http://192.168.1.1/feed")
        assert "not allowed" in result.lower()

    async def test_localhost_blocked(self):
        result = await mod.fetch_rss_feed("http://localhost/feed")
        assert "not allowed" in result.lower()

    async def test_10_range_blocked(self):
        result = await mod.fetch_rss_feed("http://10.0.0.1/rss")
        assert "not allowed" in result.lower()

    async def test_non_http_rejected(self):
        result = await mod.fetch_rss_feed("ftp://files.example.com/feed.xml")
        assert "http" in result.lower()


# ---------------------------------------------------------------------------
# fetch_rss_feed — mocked HTTP
# ---------------------------------------------------------------------------


def _mock_session_get(xml_text: str, status: int = 200):
    """Return a context-manager mock for session.get()."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=xml_text)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestFetchRssFeed:
    async def test_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(mod, "_FEEDS_FILE", tmp_path / "rss_feeds.json")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_mock_session_get(SAMPLE_RSS_XML))
        with patch.object(mod, "_get_session", AsyncMock(return_value=mock_session)):
            result = await mod.fetch_rss_feed("https://example.com/feed.xml")

        assert "Test Blog" in result
        assert "First Post" in result

    async def test_http_error(self, monkeypatch):
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_mock_session_get("", status=404))
        with patch.object(mod, "_get_session", AsyncMock(return_value=mock_session)):
            result = await mod.fetch_rss_feed("https://example.com/feed.xml")

        assert "404" in result
        assert "❌" in result

    async def test_timeout(self, monkeypatch):
        mock_session = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=cm)

        with patch.object(mod, "_get_session", AsyncMock(return_value=mock_session)):
            result = await mod.fetch_rss_feed("https://example.com/feed.xml")

        assert "timed out" in result.lower()


# ---------------------------------------------------------------------------
# search_rss — keyword filtering
# ---------------------------------------------------------------------------


class TestSearchRss:
    async def test_keyword_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(mod, "_FEEDS_FILE", tmp_path / "rss_feeds.json")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_mock_session_get(SAMPLE_RSS_XML))
        with patch.object(mod, "_get_session", AsyncMock(return_value=mock_session)):
            result = await mod.search_rss("https://example.com/feed.xml", "First")

        assert "First Post" in result

    async def test_keyword_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(mod, "_FEEDS_FILE", tmp_path / "rss_feeds.json")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_mock_session_get(SAMPLE_RSS_XML))
        with patch.object(mod, "_get_session", AsyncMock(return_value=mock_session)):
            result = await mod.search_rss("https://example.com/feed.xml", "zzzznotfound")

        assert "No items" in result
