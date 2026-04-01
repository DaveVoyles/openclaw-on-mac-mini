"""
OpenClaw Search Skills — web search providers and cascade logic.
Extracted from advanced_skills.py for modularity.
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp

from config import TIMEOUT_DEFAULT, TIMEOUT_SLOW
from config import cfg as _cfg
from http_session import SessionManager
from search_provider import get_stats, retry_once

log = logging.getLogger("openclaw.search_skills")

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_sessions = SessionManager(
    timeout=TIMEOUT_SLOW,
    name="search_skills",
    connector_limit=50,
    connector_limit_per_host=15,
    ttl_dns_cache=600,
)
_get_session = _sessions.get
close_session = _sessions.close

# ---------------------------------------------------------------------------
# Search provider configuration
# ---------------------------------------------------------------------------

PERPLEXITY_API_KEY = _cfg.perplexity_api_key

FIRECRAWL_API_KEY = _cfg.firecrawl_api_key
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"

SERPER_API_KEY = _cfg.serper_api_key

TAVILY_API_KEY = _cfg.tavily_api_key
TAVILY_API_URL = "https://api.tavily.com/search"

_SKILLS_DIR = Path(__file__).parent
_TAVILY_SCRIPT = _SKILLS_DIR / "openclaw-tavily-search" / "scripts" / "tavily_search.py"
_DDG_SCRIPT = _SKILLS_DIR / "free-web-search" / "scripts" / "web_search.py"

COMMAND_TIMEOUT = 15

# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------


async def search_web(query: str, num_results: int = 5, provider: str = "") -> str:
    """Search the web using the best available provider.

    Search priority: Perplexity (AI-powered) → Firecrawl (search+extract) → Tavily (structured) → DuckDuckGo (free) → Bing Lite (fallback)

    Args:
        query: Search query
        num_results: Max results (1-10)
        provider: Force a specific provider: 'perplexity', 'firecrawl', 'tavily',
                  'serper', 'duckduckgo'. Empty = auto cascade.
    """
    num_results = min(max(num_results, 1), 10)
    provider = provider.lower().strip()

    # ── Forced provider selection ──────────────────────────────────────────
    if provider:
        if provider == "perplexity":
            if not PERPLEXITY_API_KEY:
                return "⚠️ Perplexity API key not configured."
            return await _perplexity_search(query, num_results) or "❌ Perplexity returned no results."
        elif provider == "firecrawl":
            if not FIRECRAWL_API_KEY:
                return "⚠️ Firecrawl API key not configured."
            return await _firecrawl_search(query, num_results) or "❌ Firecrawl returned no results."
        elif provider == "serper":
            if not SERPER_API_KEY:
                return "⚠️ Serper API key not configured. Uncomment SERPER_API_KEY in .env."
            return await serper_search(query, num_results)
        elif provider in ("tavily",):
            if not TAVILY_API_KEY:
                return "⚠️ Tavily API key not configured."
            # Fall through to Tavily section below
        elif provider in ("duckduckgo", "ddg"):
            pass  # Fall through to DDG section below
        else:
            return f"⚠️ Unknown provider `{provider}`. Options: perplexity, firecrawl, serper, tavily, duckduckgo."

    # ── Auto cascade (when no provider forced) ─────────────────────────────

    # ── Perplexity path (AI-synthesized answers with citations) ────────────
    if not provider and PERPLEXITY_API_KEY:
        stats = get_stats("perplexity")
        start = time.monotonic()
        try:
            log.info("Using Perplexity for search: %s", query[:80])
            result = await retry_once(
                lambda: _perplexity_search(query, num_results),
                "perplexity",
            )
            if result:
                stats.record_success((time.monotonic() - start) * 1000)
                return result
            stats.record_failure()
        except Exception as e:
            stats.record_failure()
            log.debug("Perplexity search failed after retry: %s", e)

    # ── Firecrawl path (search + full page extraction in one call) ─────────
    if not provider and FIRECRAWL_API_KEY:
        stats = get_stats("firecrawl")
        start = time.monotonic()
        try:
            log.info("Using Firecrawl for search: %s", query[:80])
            result = await retry_once(
                lambda: _firecrawl_search(query, num_results),
                "firecrawl",
            )
            if result:
                stats.record_success((time.monotonic() - start) * 1000)
                return result
            stats.record_failure()
        except Exception as e:
            stats.record_failure()
            log.debug("Firecrawl search failed after retry: %s", e)

    # ── Tavily path (higher quality, needs API key) ────────────────────────
    if TAVILY_API_KEY and _TAVILY_SCRIPT.exists():
        stats = get_stats("tavily")
        start = time.monotonic()
        clean_num = int(float(num_results))
        cmd = [
            sys.executable,
            str(_TAVILY_SCRIPT),
            "--query", query,
            "--max-results", str(clean_num),
            "--include-answer",
            "--format", "raw",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TAVILY_API_KEY": TAVILY_API_KEY},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_DEFAULT)
            if proc.returncode != 0:
                err = stderr.decode().strip()[:300]
                log.warning("Tavily script failed: %s", err)
                stats.record_failure()
            else:
                try:
                    data = json.loads(stdout.decode())
                    result = _format_tavily_results(data, int(float(num_results)))
                    stats.record_success((time.monotonic() - start) * 1000)
                    return result
                except json.JSONDecodeError:
                    log.error("Tavily script returned invalid JSON: %s", stdout.decode()[:200])
                    stats.record_failure()
        except asyncio.TimeoutError:
            log.warning("Tavily script timed out")
            stats.record_failure()
        except Exception as e:
            log.warning("Tavily script error: %s", e)
            stats.record_failure()

    # ── Free DuckDuckGo fallback (no API key required) ─────────────────────
    if _DDG_SCRIPT.exists():
        stats = get_stats("duckduckgo")
        start = time.monotonic()
        clean_num = int(float(num_results))
        cmd = [
            sys.executable,
            str(_DDG_SCRIPT),
            query,
            "--json",
            "--pages", str(min(clean_num, 5)),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
            if proc.returncode != 0:
                stats.record_failure()
                return f"❌ Web search failed: {stderr.decode().strip()[:200]}"
            data = json.loads(stdout.decode())
            if "error" in data:
                stats.record_failure()
                return f"❌ {data['error']}"
            result = _format_ddg_results(data, int(float(num_results)))
            stats.record_success((time.monotonic() - start) * 1000)
            return result
        except asyncio.TimeoutError:
            stats.record_failure()
            return "❌ Web search timed out (25s)."
        except Exception as e:
            stats.record_failure()
            return f"❌ Web search error: {e}"

    # ── Bing lite fallback (multi-search-engine skill provides pattern) ──────
    log.info("Falling back to Bing lite for: %s", query)
    stats = get_stats("bing")
    start = time.monotonic()
    try:
        import urllib.parse
        bing_url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
        bing_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        session = await _get_session()
        async with session.get(
            bing_url, headers=bing_headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT_DEFAULT)
        ) as resp:
            if resp.status == 200:
                html = await resp.text()
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "html.parser")
                    lines = []
                    for i, result in enumerate(soup.select("li.b_algo")[:int(float(num_results))], 1):
                        title_el = result.select_one("h2 a")
                        snippet_el = result.select_one(".b_caption p")
                        if title_el:
                            title = title_el.get_text(strip=True)
                            url_link = title_el.get("href", "")
                            snippet = snippet_el.get_text(strip=True)[:250] if snippet_el else ""
                            lines.append(f"**{i}. {title}**\n{snippet}\n🔗 <{url_link}>")
                    if lines:
                        stats.record_success((time.monotonic() - start) * 1000)
                        return "\n\n".join(lines) + "\n\n*via Bing (fallback)*"
                except ImportError:
                    pass
        stats.record_failure()
    except Exception as e:
        stats.record_failure()
        log.warning("Bing fallback failed: %s", e)

    # ── Nothing worked ────────────────────────────────────────────────────
    if not TAVILY_API_KEY and not PERPLEXITY_API_KEY:
        return (
            "⚠️ Web search not configured. Either:\n"
            "• Set `PERPLEXITY_API_KEY` in .env for Perplexity AI Search, or\n"
            "• Set `TAVILY_API_KEY` in .env for Tavily AI Search, or\n"
            "• Ensure `skills/free-web-search/` is installed (run: "
            "`npx clawhub@latest install free-web-search`)"
        )
    return "❌ All web search methods exhausted. Check logs for details."


async def _perplexity_search(query: str, num_results: int = 5) -> str:
    """Search via Perplexity API — returns AI-synthesized answer with citations."""
    from spending import tracker as spending_tracker

    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": "Be precise and concise. Cite sources."},
            {"role": "user", "content": query},
        ],
        "max_tokens": 1024,
        "return_citations": True,
        "search_recency_filter": "month",
    }

    session = await _get_session()
    async with session.post(
        url, json=payload, headers=headers,
        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SLOW),
    ) as resp:
        if resp.status != 200:
            log.debug("Perplexity returned HTTP %d", resp.status)
            return ""
        data = await resp.json()

    # Track usage
    await spending_tracker.record_perplexity(model="sonar")

    answer = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])

    lines = [f"**Perplexity AI Answer:**\n{answer}"]
    if citations:
        lines.append("\n**Sources:**")
        for i, cite in enumerate(citations[:num_results], 1):
            lines.append(f"{i}. {cite}")

    return "\n".join(lines)


async def _firecrawl_search(query: str, num_results: int = 5) -> str:
    """Search via Firecrawl API — returns search results with full page content."""
    if not FIRECRAWL_API_KEY:
        return ""

    headers = {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "limit": num_results,
        "lang": "en",
        "scrapeOptions": {"formats": ["markdown"]},
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{FIRECRAWL_API_URL}/search",
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SLOW),
        ) as resp:
            if resp.status != 200:
                log.debug("Firecrawl search returned HTTP %d", resp.status)
                return ""
            data = await resp.json()

        if not data.get("success") or not data.get("data"):
            return ""

        results = data["data"]
        lines = [f"**Firecrawl Search** ({len(results)} results):\n"]
        for i, r in enumerate(results[:num_results], 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            markdown = r.get("markdown", "")
            snippet = markdown[:500] + "…" if len(markdown) > 500 else markdown
            lines.append(f"**{i}. [{title}]({url})**")
            if snippet:
                lines.append(f"> {snippet}\n")

        log.info("Firecrawl search: %d results for: %s", len(results), query[:60])
        from spending import tracker as spending_tracker
        await spending_tracker.record_firecrawl(pages=len(results), action="search")
        return "\n".join(lines)
    except Exception as e:
        log.debug("Firecrawl search failed: %s", e)
        return ""


async def firecrawl_scrape(url: str) -> str:
    """Scrape a URL via Firecrawl and return clean markdown content."""
    if not FIRECRAWL_API_KEY:
        return "⚠️ Firecrawl API key not configured. Set `FIRECRAWL_API_KEY` in .env."

    headers = {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "formats": ["markdown"],
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{FIRECRAWL_API_URL}/scrape",
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SLOW),
        ) as resp:
            if resp.status != 200:
                return f"❌ Firecrawl returned HTTP {resp.status}"
            data = await resp.json()

        if not data.get("success"):
            return f"❌ Firecrawl scrape failed: {data.get('error', 'unknown')}"

        markdown = data.get("data", {}).get("markdown", "")
        title = data.get("data", {}).get("metadata", {}).get("title", "")
        if not markdown:
            return "⚠️ Firecrawl returned no content."

        if len(markdown) > 6000:
            markdown = markdown[:6000] + "\n… (truncated)"

        header = f"**{title}**\n*Source: {url}*\n\n" if title else f"*Source: {url}*\n\n"
        log.info("Firecrawl scrape: %d chars from %s", len(markdown), url)
        from spending import tracker as spending_tracker
        await spending_tracker.record_firecrawl(pages=1, action="scrape")
        return header + markdown
    except Exception as e:
        log.debug("Firecrawl scrape failed: %s", e)
        return f"❌ Firecrawl error: {e}"


async def serper_search(query: str, num_results: int = 5, search_type: str = "search") -> str:
    """Search Google via Serper API. Returns structured SERP results.

    Args:
        query: Search query
        num_results: Max results (1-10)
        search_type: 'search' (web), 'news', 'images', or 'places'

    Returns Google SERP data including organic results, knowledge graph,
    People Also Ask, and featured snippets.
    """
    if not SERPER_API_KEY:
        return "⚠️ Serper API key not configured. Set `SERPER_API_KEY` in .env (uncomment the line)."

    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "num": min(max(num_results, 1), 10),
    }

    endpoint = {
        "search": "https://google.serper.dev/search",
        "news": "https://google.serper.dev/news",
        "images": "https://google.serper.dev/images",
        "places": "https://google.serper.dev/places",
    }.get(search_type, "https://google.serper.dev/search")

    try:
        session = await _get_session()
        async with session.post(
            endpoint, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_DEFAULT),
        ) as resp:
            if resp.status != 200:
                return f"❌ Serper returned HTTP {resp.status}"
            data = await resp.json()

        lines = ["**Google Search Results** (via Serper):\n"]

        kg = data.get("knowledgeGraph", {})
        if kg:
            title = kg.get("title", "")
            desc = kg.get("description", "")
            if title:
                lines.append(f"📋 **{title}**: {desc}\n")

        ab = data.get("answerBox", {})
        if ab:
            answer = ab.get("answer") or ab.get("snippet", "")
            if answer:
                lines.append(f"💡 **Answer:** {answer}\n")

        organic = data.get("organic", [])
        for i, r in enumerate(organic[:num_results], 1):
            title = r.get("title", "Untitled")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            lines.append(f"**{i}. [{title}]({link})**")
            if snippet:
                lines.append(f"> {snippet}\n")

        paa = data.get("peopleAlsoAsk", [])
        if paa:
            lines.append("**People Also Ask:**")
            for q in paa[:3]:
                lines.append(f"- {q.get('question', '')}")

        log.info("Serper %s: %d results for: %s", search_type, len(organic), query[:60])
        return "\n".join(lines)
    except Exception as e:
        log.debug("Serper search failed: %s", e)
        return f"❌ Serper error: {e}"


def _format_tavily_results(data: dict, num_results: int) -> str:
    """Format Tavily API JSON response into Discord-friendly markdown."""
    lines = []
    if data.get("answer"):
        lines.append(f"**Answer**: {data['answer']}\n")
    results = data.get("results", [])
    if not results:
        return f"No web results found for: {data.get('query', '?')}"
    for i, r in enumerate(results[:num_results], 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = (r.get("content") or "")[:300].strip()
        lines.append(f"**{i}. {title}**\n{content}\n🔗 <{url}>")
    return "\n\n".join(lines)


def _format_ddg_results(data: dict, num_results: int) -> str:
    """Format free-web-search JSON response into Discord-friendly markdown."""
    results = data.get("results", [])[:num_results]
    if not results:
        return f"No web results found for: {data.get('query', '?')}"
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        text = (r.get("text") or r.get("snippet") or "")[:300].strip()
        lines.append(f"**{i}. {title}**\n{text}\n🔗 <{url}>")
    src = "DuckDuckGo (free)"
    return "\n\n".join(lines) + f"\n\n*via {src}*"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SEARCH_SKILLS = {
    "search_web": search_web,
    "firecrawl_scrape": firecrawl_scrape,
    "serper_search": serper_search,
}
