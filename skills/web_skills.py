"""
OpenClaw Web Skills — URL browsing, content extraction, multi-source comparison.
Extracted from advanced_skills.py for modularity.
"""

import asyncio
import logging

import aiohttp

from config import TIMEOUT_DEFAULT, TIMEOUT_SLOW
from http_session import SessionManager

log = logging.getLogger("openclaw.web_skills")

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_sessions = SessionManager(
    timeout=TIMEOUT_SLOW,
    name="web_skills",
    connector_limit=50,
    connector_limit_per_host=15,
    ttl_dns_cache=600,
)
_get_session = _sessions.get
close_session = _sessions.close

# ---------------------------------------------------------------------------
# Web browsing
# ---------------------------------------------------------------------------


async def browse_url(url: str) -> str:
    """Fetch a URL and extract clean readable text.

    Uses a 3-tier extraction chain:
      1. trafilatura — fast HTML-based extraction (no JS support).
      2. Jina AI Reader — free service at r.jina.ai; handles JS-rendered
         sites and returns clean markdown without running a browser.
      3. Playwright — headless Chromium as a last resort.
    """
    import ipaddress as _ipaddress
    import socket as _socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "❌ Only HTTP and HTTPS URLs are supported."

    # SSRF guard: block private / loopback addresses
    hostname = parsed.hostname or ""
    try:
        resolved = _socket.getaddrinfo(hostname, None, _socket.AF_UNSPEC, _socket.SOCK_STREAM)
        for _, _, _, _, addr in resolved:
            ip = _ipaddress.ip_address(addr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return "❌ Cannot browse private or loopback addresses."
    except (OSError, ValueError):
        pass  # DNS failure will be caught by the HTTP request below

    try:
        import trafilatura
    except ImportError:
        return "❌ trafilatura is not installed (run: pip install trafilatura)."

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    try:
        session = await _get_session()
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_DEFAULT),
        ) as resp:
            if resp.status == 403:
                return (
                    f"🚫 {url} returned HTTP 403 (bot-blocking). "
                    "This site (e.g. Zillow, Redfin, Realtor.com) actively blocks automated access. "
                    "Do NOT retry this URL. Instead, use your own knowledge about this neighborhood, "
                    "typical prices, and property tax rates to provide a detailed, helpful answer."
                )
            if resp.status == 429:
                return (
                    f"🚫 {url} returned HTTP 429 (rate-limited/bot-blocked). "
                    "This site is blocking automated requests. "
                    "Do NOT retry. Use your own knowledge to answer the user's question."
                )
            if resp.status != 200:
                return f"❌ Could not fetch URL (HTTP {resp.status})."
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > 5 * 1024 * 1024:
                return "❌ Page too large (>5MB)."
            chunks: list[bytes] = []
            total = 0
            _MAX_DOWNLOAD = 5 * 1024 * 1024
            async for chunk in resp.content.iter_chunked(8192):
                total += len(chunk)
                if total > _MAX_DOWNLOAD:
                    break
                chunks.append(chunk)
            html = b"".join(chunks).decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return "❌ URL fetch timed out (20s)."
    except Exception as e:
        return f"❌ Could not fetch URL: {e}"

    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )

    if not text:
        # Fallback 1: try Jina AI Reader (free, handles JS sites, returns markdown)
        try:
            log.info("Trying Jina Reader fallback for: %s", url)
            text = await _jina_fetch(url)
        except Exception as e:
            log.debug("Jina Reader fallback failed: %s", e)

    if not text:
        # Fallback 2: try Playwright headless browser (last resort)
        try:
            log.info("Trying Playwright fallback for: %s", url)
            text = await _playwright_fetch(url)
        except Exception as e:
            log.debug("Playwright fallback failed: %s", e)

    if not text:
        return (
            f"⚠️ Could not extract readable content from `{url}`. "
            "The page may be JavaScript-rendered or paywalled."
        )

    if len(text) > 6000:
        text = text[:6000] + "\n… (truncated)"

    return f"**Source**: {url}\n\n{text}"


async def _jina_fetch(url: str) -> str:
    """Fetch clean markdown content via Jina AI Reader. Free, handles JS sites."""
    jina_url = f"https://r.jina.ai/{url}"
    headers = {"Accept": "text/markdown", "X-No-Cache": "true"}
    try:
        session = await _get_session()
        async with session.get(
            jina_url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_DEFAULT),
        ) as resp:
            if resp.status != 200:
                log.debug("Jina Reader returned HTTP %d for %s", resp.status, url)
                return ""
            text = await resp.text()
            return text[:6000] if text else ""
    except Exception as e:
        log.debug("Jina Reader failed for %s: %s", url, e)
        return ""


async def _playwright_fetch(url_or_html: str) -> str:
    """Fetch page content using headless browser for JS-rendered sites.

    Accepts a URL (http/https) or raw HTML string. When given already-fetched
    HTML it renders it in Chromium so JS executes, then extracts text via
    trafilatura.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            if url_or_html.startswith(("http://", "https://")):
                await page.goto(url_or_html, timeout=20000, wait_until="networkidle")
            else:
                await page.set_content(url_or_html, wait_until="networkidle")
            rendered = await page.content()
            await browser.close()

        import trafilatura
        text = trafilatura.extract(
            rendered, include_tables=True, include_comments=False,
        )
        return text or ""
    except Exception as e:
        log.debug("Playwright render failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Multi-source comparison
# ---------------------------------------------------------------------------

async def compare_sources(urls_json: str, question: str) -> str:
    """
    Browse multiple URLs in parallel and synthesize a comparison answer.

    Fetches up to 5 URLs concurrently, then asks the LLM to compare/contrast
    the content and answer the question. Great for competitive analysis,
    comparing documentation pages, or fact-checking across sources.

    Args:
        urls_json: JSON array of URLs, e.g. '["https://site1.com","https://site2.com"]'
        question:  What to compare or answer from these sources.

    Returns a synthesized comparison using all successfully fetched pages.
    """
    import json as _json

    try:
        urls = _json.loads(urls_json)
        if not isinstance(urls, list) or not urls:
            return "❌ urls_json must be a non-empty JSON array of URL strings."
    except _json.JSONDecodeError as e:
        return f"❌ Invalid urls_json: {e}"

    urls = urls[:5]  # cap at 5

    pages = await asyncio.gather(
        *[asyncio.wait_for(browse_url(u), timeout=20) for u in urls],
        return_exceptions=True,
    )

    sections: list[str] = []
    for url, result in zip(urls, pages):
        if isinstance(result, Exception):
            sections.append(f"[{url}: error — {result}]")
        elif isinstance(result, str):
            sections.append(f"=== Source: {url} ===\n{result[:2000]}")
        else:
            sections.append(f"[{url}: no content]")

    if not sections:
        return "❌ Could not fetch any of the provided URLs."

    combined = "\n\n".join(sections)[:7000]
    prompt = (
        f"You have {len(sections)} source(s) to compare. "
        f"Answer this question: **{question}**\n\n"
        "Use only the source content below. Cite which source supports each point. "
        "Note any contradictions or gaps.\n\n"
        f"{combined}"
    )

    try:
        from llm import chat as _llm_chat
        synthesis, _, _ = await asyncio.wait_for(_llm_chat(prompt), timeout=35)
        return synthesis[:1900]
    except Exception as e:
        log.warning("compare_sources LLM synthesis failed: %s", e)
        return f"📄 **Raw sources** (LLM synthesis unavailable):\n\n{combined[:1800]}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

WEB_SKILLS = {
    "browse_url": browse_url,
    "compare_sources": compare_sources,
}
