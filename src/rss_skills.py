"""
OpenClaw RSS Skills — Feed fetching, summarization, and monitoring.

Lets the agent monitor RSS/Atom feeds from news sources, blogs, subreddits,
tech sites, GitHub releases, etc. — no additional API key required.

Skills:
  fetch_rss_feed(url, limit)  — list recent items from any RSS/Atom feed
  search_rss(url, query)      — filter feed items by keyword
  get_rss_digest(urls_json)   — fetch multiple feeds and LLM-summarize into a digest
  list_rss_feeds()            — list all saved/watched feed URLs

Feeds can also be used as scheduled tasks:
  scheduler.create(action="get_rss_digest", args={"urls_json": "[...]"},
                   interval_minutes=360)  → auto-digest every 6 hours
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import aiohttp

log = logging.getLogger("openclaw.rss")

_SSRF_PRIVATE = re.compile(
    r"^(https?://)?(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.).*",
    re.IGNORECASE,
)

from config import TIMEOUT_DEFAULT, cfg as _cfg

MEMORY_DIR = _cfg.memory_dir
_FEEDS_FILE = MEMORY_DIR / "rss_feeds.json"

_TIMEOUT = aiohttp.ClientTimeout(total=TIMEOUT_DEFAULT)
_MAX_ITEMS = 20

from http_session import SessionManager

_sessions = SessionManager(timeout=TIMEOUT_DEFAULT, name="rss")
_get_session = _sessions.get
close_session = _sessions.close


# ---------------------------------------------------------------------------
# Saved feed registry (persisted to /memory/rss_feeds.json)
# ---------------------------------------------------------------------------

def _load_feeds() -> list[dict]:
    """Load saved feed subscriptions."""
    if _FEEDS_FILE.exists():
        try:
            return json.loads(_FEEDS_FILE.read_text())
        except Exception as exc:
            log.debug("Failed to load feeds: %s", exc)
    return []


def _save_feeds(feeds: list[dict]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _FEEDS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(feeds, indent=2))
    tmp.replace(_FEEDS_FILE)


# ---------------------------------------------------------------------------
# XML parsing helpers (works for RSS 2.0 and Atom 1.0)
# ---------------------------------------------------------------------------

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _text(el: ET.Element, *paths: str) -> str:
    """Try multiple tag paths; return first non-empty text or empty string."""
    for path in paths:
        node = el.find(path, _NS)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def _parse_feed(xml_text: str, limit: int = 10) -> tuple[str, list[dict]]:
    """
    Parse RSS 2.0 or Atom 1.0 XML.
    Returns (feed_title, [{"title", "url", "date", "summary"}])
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return ("(parse error)", [{"title": str(e), "url": "", "date": "", "summary": ""}])

    items: list[dict] = []
    feed_title = ""

    # ── RSS 2.0 ─────────────────────────────────────────────────────────────
    channel = root.find("channel")
    if channel is not None:
        feed_title = _text(channel, "title") or "RSS Feed"
        for item in channel.findall("item")[:limit]:
            title = _text(item, "title")
            url = _text(item, "link")
            date_raw = _text(item, "pubDate", "dc:date")
            summary = _text(item, "description", "content:encoded")
            # Strip HTML tags from summary (crude but avoids deps)

            summary = re.sub(r"<[^>]+>", "", summary)[:250]
            date_fmt = ""
            if date_raw:
                try:
                    dt = parsedate_to_datetime(date_raw)
                    date_fmt = dt.strftime("%Y-%m-%d")
                except Exception as exc:
                    log.debug("RSS date parse failed for %r: %s", date_raw, exc)
                    date_fmt = date_raw[:10]
            items.append({"title": title, "url": url, "date": date_fmt, "summary": summary.strip()})

    # ── Atom 1.0 ────────────────────────────────────────────────────────────
    elif root.tag == "{http://www.w3.org/2005/Atom}feed" or "feed" in root.tag.lower():
        ft_el = root.find("atom:title", _NS) or root.find("{http://www.w3.org/2005/Atom}title")
        feed_title = ft_el.text.strip() if (ft_el is not None and ft_el.text) else "Atom Feed"
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f"{ns}entry")[:limit]:
            title_el = entry.find(f"{ns}title")
            title = title_el.text.strip() if (title_el is not None and title_el.text) else "(no title)"
            link_el = entry.find(f"{ns}link")
            url = (link_el.get("href") or "") if link_el is not None else ""
            updated_el = entry.find(f"{ns}updated") or entry.find(f"{ns}published")
            date_fmt = updated_el.text[:10] if (updated_el is not None and updated_el.text) else ""
            summary_el = entry.find(f"{ns}summary") or entry.find(f"{ns}content")

            summary_raw = (summary_el.text or "") if summary_el is not None else ""
            summary = re.sub(r"<[^>]+>", "", summary_raw)[:250].strip()
            items.append({"title": title, "url": url, "date": date_fmt, "summary": summary})

    return feed_title, items


# ---------------------------------------------------------------------------
# Public skills
# ---------------------------------------------------------------------------

async def fetch_rss_feed(url: str, limit: int = 10) -> str:
    """
    Fetch recent items from any RSS or Atom feed URL.

    Args:
        url:   Full URL of the RSS/Atom feed.
        limit: Number of items to return (1-20, default 10).

    Returns a formatted list of articles with titles, dates, and links.
    """
    # Basic SSRF guard — block non-HTTP and private IP ranges
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "❌ Only http/https URLs are supported."
    if _SSRF_PRIVATE.match(url):
        return "❌ Fetching private/localhost URLs is not allowed."

    limit = max(1, min(limit, _MAX_ITEMS))

    try:
        session = await _get_session()
        async with session.get(
            url,
            headers={"User-Agent": "OpenClaw-RSS/1.0"},
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                return f"❌ Feed returned HTTP {resp.status} for `{url}`"
            xml_text = await resp.text(errors="replace")
    except asyncio.TimeoutError:
        return f"❌ Feed timed out: `{url}`"
    except Exception as e:
        return f"❌ Could not fetch feed `{url}`: {e}"

    feed_title, items = _parse_feed(xml_text, limit)

    if not items:
        return f"⚠️ No items found in feed: `{url}`"

    lines = [f"**{feed_title}** — {len(items)} items"]
    for i, it in enumerate(items, 1):
        date_part = f" `{it['date']}`" if it["date"] else ""
        lines.append(f"{i}. **{it['title']}**{date_part}")
        if it["url"]:
            lines.append(f"   <{it['url']}>")
        if it["summary"]:
            lines.append(f"   *{it['summary'][:180]}*")

    # Auto-save this feed URL for future use
    feeds = _load_feeds()
    known_urls = {f["url"] for f in feeds}
    if url not in known_urls:
        feeds.append({"url": url, "title": feed_title, "added": datetime.now(timezone.utc).isoformat()})
        _save_feeds(feeds)

    return "\n".join(lines)[:1900]


async def search_rss(url: str, query: str) -> str:
    """
    Fetch a feed and filter items matching a keyword.

    Args:
        url:   The RSS/Atom feed URL.
        query: Keyword(s) to search for in titles and summaries.

    Returns matching items only, or a note when no matches are found.
    """
    full = await fetch_rss_feed(url, limit=_MAX_ITEMS)
    if full.startswith("❌"):
        return full

    # Re-parse to get structured items for filtering
    try:
        session = await _get_session()
        async with session.get(
            url,
            headers={"User-Agent": "OpenClaw-RSS/1.0"},
            allow_redirects=True,
        ) as resp:
            xml_text = await resp.text(errors="replace")
    except Exception as e:
        return f"❌ {e}"

    feed_title, items = _parse_feed(xml_text, _MAX_ITEMS)
    q_lower = query.lower()
    matched = [
        it for it in items
        if q_lower in it["title"].lower() or q_lower in it["summary"].lower()
    ]

    if not matched:
        return f"🔍 No items in **{feed_title}** matching `{query}`."

    lines = [f"🔍 **{feed_title}** — {len(matched)} result(s) for `{query}`"]
    for it in matched:
        date_part = f" `{it['date']}`" if it["date"] else ""
        lines.append(f"• **{it['title']}**{date_part}")
        if it["url"]:
            lines.append(f"  <{it['url']}>")
        if it["summary"]:
            lines.append(f"  *{it['summary'][:160]}*")

    return "\n".join(lines)[:1900]


async def get_rss_digest(urls_json: str, topic: str = "") -> str:
    """
    Fetch multiple RSS/Atom feeds in parallel and produce a combined digest.

    Uses the LLM to synthesize headlines into a coherent summary.

    Args:
        urls_json: JSON array of feed URLs, e.g. '["https://feeds.bbci.co.uk/news/rss.xml"]'
        topic:     Optional focus — only include articles related to this topic.

    Returns a bullet-point digest of the most important/relevant headlines.
    """
    try:
        urls = json.loads(urls_json)
        if not isinstance(urls, list) or not urls:
            return "❌ urls_json must be a non-empty JSON array of URL strings."
    except json.JSONDecodeError as e:
        return f"❌ Invalid urls_json: {e}"

    # Clip to a reasonable number of feeds
    urls = urls[:8]

    # Fetch all feeds in parallel
    raw_results = await asyncio.gather(
        *[fetch_rss_feed(url, limit=8) for url in urls],
        return_exceptions=True,
    )

    feed_texts = []
    for url, result in zip(urls, raw_results):
        if isinstance(result, Exception):
            feed_texts.append(f"[{url}: error — {result}]")
        elif isinstance(result, str) and not result.startswith("❌"):
            feed_texts.append(result)

    if not feed_texts:
        return "❌ All feeds failed to load."

    combined = "\n\n---\n\n".join(feed_texts)[:4000]

    topic_clause = f" Focus specifically on news about: **{topic}**." if topic else ""
    prompt = (
        "You are an editorial assistant synthesizing RSS feed headlines into a digest.\n"
        f"Below are the latest articles from {len(feed_texts)} feed(s).{topic_clause}\n"
        "Summarize the 5-8 most notable items into a concise bullet-point digest. "
        "For each item include: one clear sentence, and the article URL in angle brackets.\n\n"
        f"{combined}"
    )

    try:
        from llm import chat as _llm_chat
        digest, _, _ = await asyncio.wait_for(_llm_chat(prompt), timeout=30)
        return digest[:1900]
    except Exception as e:
        log.warning("RSS digest LLM call failed: %s", e)
        # Fallback: return raw truncated feed text
        return f"📰 **RSS Digest** (raw — LLM unavailable)\n\n{combined[:1800]}"


async def list_rss_feeds() -> str:
    """List all RSS/Atom feed URLs that have been fetched or saved."""
    feeds = _load_feeds()
    if not feeds:
        return "No feeds saved yet. Use `fetch_rss_feed` to subscribe to a feed."
    lines = ["**Saved RSS Feeds**"]
    for f in feeds:
        title = f.get("title", "Unknown")
        url = f.get("url", "")
        added = f.get("added", "")[:10]
        lines.append(f"• **{title}** — `{url}` (added {added})")
    return "\n".join(lines)


RSS_SKILLS = {
    "fetch_rss_feed": fetch_rss_feed,
    "search_rss": search_rss,
    "get_rss_digest": get_rss_digest,
    "list_rss_feeds": list_rss_feeds,
}
