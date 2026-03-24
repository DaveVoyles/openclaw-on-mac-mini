# -*- coding: utf-8 -*-
"""
Web Search Tool v9.3.0
======================
Agent-friendly web search for Claude Code / OpenClaw.

Engine strategy:
  PRIMARY   → DuckDuckGo Lite (lite.duckduckgo.com, GET, table-based HTML)
  FALLBACK  → Bing (only when DDG returns < 3 usable results)

Accuracy & recency strategy:
  1. Auto-detect time-sensitive queries and inject the current year into the
     search string BEFORE sending to the engine — this is the only reliable
     way to surface fresh results, because reranking can't conjure new pages.
  2. Apply mild recency scoring always; amplify it when recency intent is
     detected in the query.
  3. Fetch page content from semantic containers (<article>/<main>) first,
     fall back to full-page paragraph extraction.
"""

import sys
import json
import time
import logging
import argparse
import urllib.parse
import random
import re
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

# ── Configuration ─────────────────────────────────────────────────────────────

_NOW = datetime.now()
_CURRENT_YEAR = _NOW.year

CONFIG = {
    "TIMEOUT": 20,
    "MAX_SEARCH_RESULTS": 15,
    "DEFAULT_FETCH_PAGES": 5,
    "MIN_CONTENT_LENGTH": 80,
    "MAX_TEXT_LENGTH": 1200,
    "PARAGRAPH_MIN_LENGTH": 40,
    "FALLBACK_THRESHOLD": 3,
    "FETCH_DELAY": (0.3, 0.8),
}

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


# ── HTTP Client Factory ───────────────────────────────────────────────────────

def make_client(**kwargs) -> httpx.Client:
    return httpx.Client(
        headers=BASE_HEADERS,
        timeout=CONFIG["TIMEOUT"],
        follow_redirects=True,
        **kwargs,
    )


# ── Query Normalization & Enhancement ─────────────────────────────────────────

_FILLER_PREFIXES = [
    "please search for", "search for", "look up", "find info about",
    "what is the latest on", "google", "browse for", "find",
]

# Keywords that signal the user wants fresh / current information
_RECENCY_INTENT_TOKENS = {
    "latest", "newest", "recent", "current", "now", "today",
    "update", "updated", "release", "released", "changelog",
    "2024", "2025", "2026",                      # explicit years
    "最新", "新版", "现在", "今天",               # Chinese equivalents
}

def normalize_query(raw: str) -> str:
    """Strip filler prefixes and collapse whitespace."""
    q = raw.strip()
    lower = q.lower()
    for filler in _FILLER_PREFIXES:
        if lower.startswith(filler):
            q = q[len(filler):].strip()
            break
    return " ".join(q.split())


def detect_recency_intent(query: str) -> bool:
    """Return True if the query is asking for time-sensitive / current info."""
    words = set(query.lower().split())
    return bool(words & _RECENCY_INTENT_TOKENS)


def enhance_query_for_recency(query: str) -> str:
    """
    Inject the current year into the query when recency intent is detected
    AND the query doesn't already contain an explicit year.

    This is the most effective way to surface fresh results — the year
    token influences the engine's ranking at index-retrieval time, which
    post-hoc reranking cannot replicate.
    """
    if not detect_recency_intent(query):
        return query

    # Don't double-inject if a 4-digit year is already present
    if re.search(r"\b20\d{2}\b", query):
        return query

    enhanced = f"{query} {_CURRENT_YEAR}"
    log.info(f"[query] Recency intent detected → injecting year: {enhanced!r}")
    return enhanced


# ── PRIMARY ENGINE: DuckDuckGo Lite ──────────────────────────────────────────

def _ddg_lite_search(query: str) -> list:
    """
    GET lite.duckduckgo.com — DDG's ultra-minimal text-browser endpoint.

    Page structure (table-based, no JS):
      Each result is an <a class="result-link"> inside a <tr>.
      The immediately following <tr> contains <td class="result-snippet">.

    This endpoint never issues CAPTCHA challenges (unlike html.duckduckgo.com
    which returns 202 + challenge page under automated load).
    """
    url = "https://lite.duckduckgo.com/lite/"
    params = {"q": query, "kl": "us-en"}
    headers = {**BASE_HEADERS, "Referer": "https://lite.duckduckgo.com/"}

    try:
        with make_client() as client:
            resp = client.get(url, params=params, headers=headers)

        if resp.status_code != 200:
            log.warning(f"[DDG-Lite] Unexpected status {resp.status_code}")

        soup = BeautifulSoup(resp.text, "lxml")
        result_links = soup.select("a.result-link")

        if not result_links:
            log.warning("[DDG-Lite] No result links — possible block or empty SERP")
            log.debug(f"[DDG-Lite] Page preview: {soup.get_text()[:200].replace(chr(10), ' ').strip()!r}")
            return []

        results = []
        for a in result_links:
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or not title:
                continue

            # Unwrap DDG redirect: //duckduckgo.com/l/?uddg=<encoded-url>
            if "duckduckgo.com/l/" in href:
                full = href if href.startswith("http") else "https:" + href
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(full).query)
                href = urllib.parse.unquote(qs.get("uddg", [href])[0])

            if not href.startswith(("http://", "https://")):
                continue

            # Snippet is in the next sibling <tr>
            snippet = ""
            parent_tr = a.find_parent("tr")
            if parent_tr:
                next_tr = parent_tr.find_next_sibling("tr")
                if next_tr:
                    snip_el = next_tr.select_one("td.result-snippet")
                    if snip_el:
                        snippet = snip_el.get_text(" ", strip=True)

            results.append({"title": title, "url": href, "snippet": snippet})
            if len(results) >= CONFIG["MAX_SEARCH_RESULTS"]:
                break

        log.info(f"[DDG-Lite] {len(results)} results for {query!r}")
        return results

    except httpx.TimeoutException:
        log.error("[DDG-Lite] Timed out")
    except httpx.HTTPStatusError as e:
        log.error(f"[DDG-Lite] HTTP {e.response.status_code}")
    except Exception as e:
        log.error(f"[DDG-Lite] Error: {e}")
    return []


# ── FALLBACK ENGINE: Bing ─────────────────────────────────────────────────────

def _bing_search(query: str) -> list:
    """Bing HTML search — activated only when DDG returns < FALLBACK_THRESHOLD results."""
    url = "https://www.bing.com/search"
    params = {"q": query, "setlang": "en", "cc": "US", "count": "15"}
    headers = {
        **BASE_HEADERS,
        "Referer": "https://www.bing.com/",
        "Cookie": "SRCHHPGUSR=SRCHLANG=en; SRCHD=AF=NOFORM;",
    }

    try:
        with make_client() as client:
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            snippet_el = li.select_one(".b_caption p, .b_algoSlug")
            if not a:
                continue
            href = a.get("href", "")
            title = a.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if href.startswith(("http://", "https://")) and title:
                results.append({"title": title, "url": href, "snippet": snippet})
                if len(results) >= CONFIG["MAX_SEARCH_RESULTS"]:
                    break

        log.info(f"[Bing] {len(results)} results for {query!r}")
        return results

    except httpx.TimeoutException:
        log.error("[Bing] Timed out")
    except httpx.HTTPStatusError as e:
        log.error(f"[Bing] HTTP {e.response.status_code}")
    except Exception as e:
        log.error(f"[Bing] Error: {e}")
    return []


# ── Search Dispatcher ─────────────────────────────────────────────────────────

def search(query: str) -> tuple:
    """DDG Lite first; Bing fallback only when DDG is insufficient."""
    results = _ddg_lite_search(query)
    if len(results) >= CONFIG["FALLBACK_THRESHOLD"]:
        return results, "duckduckgo-lite"

    log.info(
        f"DDG Lite returned {len(results)} result(s) "
        f"(threshold={CONFIG['FALLBACK_THRESHOLD']}), activating Bing fallback…"
    )
    bing = _bing_search(query)
    return (bing, "bing") if len(bing) >= len(results) else (results, "duckduckgo-lite")


# ── Relevance & Recency Scoring ───────────────────────────────────────────────

_AUTHORITY_DOMAINS = {
    "docs.", "developer.", "github.com", "stackoverflow.com", "wikipedia.org",
    "arxiv.org", "pypi.org", "npmjs.com", ".gov", ".edu",
    "readthedocs.io", "rust-lang.org", "python.org", "man7.org",
}
_PENALTY_PATTERNS = {"ad.", "track.", "click.", "redirect.", "affiliate"}


def extract_year(item: dict) -> Optional[int]:
    """
    Extract the most recent plausible year from title + snippet + URL.
    Covers any year from 2020 onward (not capped at 2029).
    """
    text = f"{item.get('title', '')} {item.get('snippet', '')} {item.get('url', '')}"

    # Full date patterns: 2024-03-15, 2024/03, 2024年3月
    match = re.search(r"\b(20[2-9]\d)[年/\-]\d{1,2}", text)
    if match:
        year = int(match.group(1))
        if year <= _CURRENT_YEAR + 1:
            return year

    # Standalone 4-digit year: pick the most recent valid one
    years = [int(y) for y in re.findall(r"\b(20[2-9]\d)\b", text)
             if int(y) <= _CURRENT_YEAR + 1]
    if years:
        return max(years)

    # URL date segments: /2025/03/ or -20250315
    url_match = re.search(r"[/\-](20[2-9]\d)[/\-]?\d{0,2}", item.get("url", ""))
    if url_match:
        year = int(url_match.group(1))
        if year <= _CURRENT_YEAR + 1:
            return year

    return None


def recency_score(item: dict) -> float:
    """
    Score [0, 10] based on content freshness.
    Applied with a lower weight when recency_boost=False (always-on mild scoring).
    """
    year = extract_year(item)
    if year is None:
        return 0.0
    distance = _CURRENT_YEAR - year
    if distance <= 0:
        return 10.0
    elif distance == 1:
        return 5.0
    elif distance == 2:
        return 2.0
    return 0.0


def relevance_score(item: dict, query: str, recency_boost: bool = False) -> float:
    """
    Combined relevance score:
      - Keyword overlap in title (3×) and snippet (1×)
      - Authority domain bonus
      - Recency: always applied at 0.5× weight; 1.5× when recency_boost=True
      - Spam/ad penalty
    """
    title = item["title"].lower()
    snippet = item.get("snippet", "").lower()
    url = item["url"].lower()
    q_words = set(query.lower().split())
    score = 0.0

    # Keyword overlap
    score += len(q_words & set(title.split())) * 3.0
    score += len(q_words & set(snippet.split())) * 1.0

    # Authority bonus
    if any(d in url for d in _AUTHORITY_DOMAINS):
        score += 3.0

    # Recency: mild always, amplified when boost is active
    r = recency_score(item)
    score += r * (1.5 if recency_boost else 0.5)

    # Spam penalty
    if any(p in url for p in _PENALTY_PATTERNS):
        score -= 5.0

    return score


# ── Page Content Extraction ───────────────────────────────────────────────────

_NOISE_TAGS = ["script", "style", "nav", "header", "footer",
               "noscript", "aside", "form", "iframe"]
_BLOCK_SIGNALS = ["captcha", "access denied", "403 forbidden",
                  "cf-error", "enable javascript", "you have been blocked"]


def fetch_page_content(url: str) -> str:
    try:
        with make_client() as client:
            resp = client.get(url, headers={**BASE_HEADERS, "Referer": url})
            resp.raise_for_status()

        html = resp.text
        if any(sig in html.lower() for sig in _BLOCK_SIGNALS):
            log.warning(f"Anti-bot page: {url}")
            return ""

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(_NOISE_TAGS):
            tag.decompose()

        container = soup.find("article") or soup.find("main") or soup

        seen: set = set()
        paragraphs = []
        for p in container.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) >= CONFIG["PARAGRAPH_MIN_LENGTH"] and text not in seen:
                seen.add(text)
                paragraphs.append(text)

        return " ".join(paragraphs)[: CONFIG["MAX_TEXT_LENGTH"]]

    except httpx.TimeoutException:
        log.warning(f"Timeout: {url}")
    except httpx.HTTPStatusError as e:
        log.warning(f"HTTP {e.response.status_code}: {url}")
    except Exception as e:
        log.warning(f"Fetch failed: {url} — {e}")
    return ""


# ── Main Orchestrator ─────────────────────────────────────────────────────────

def web_search(
    raw_query: str,
    max_pages: int = CONFIG["DEFAULT_FETCH_PAGES"],
    use_json: bool = False,
    force_recent: bool = False,
) -> str:
    """
    Full pipeline:
      1. Normalize query
      2. Auto-detect recency intent; inject current year into query if needed
      3. Search (DDG Lite → Bing fallback)
      4. Score & rank results
      5. Fetch page content
      6. Return JSON or human-readable text
    """
    max_pages = max(1, min(max_pages, 10))
    query = normalize_query(raw_query)

    if not query:
        err = {"error": "Empty query", "raw_query": raw_query}
        return json.dumps(err, indent=2) if use_json else "Error: empty query"

    # Auto-detect recency or respect explicit flag
    recency_boost = force_recent or detect_recency_intent(query)
    # Enhance the engine query (year injection) — separate from display query
    engine_query = enhance_query_for_recency(query)

    # 1. Search
    results, engine_used = search(engine_query)

    if not results:
        err = {"error": f"No results for: {query!r}", "query": query}
        return json.dumps(err, indent=2) if use_json else f"No results for: {query}"

    # 2. Rank
    results.sort(key=lambda r: relevance_score(r, query, recency_boost), reverse=True)
    top = results[:max_pages]

    # 3. Fetch content
    fetched = []
    for rank, item in enumerate(top, start=1):
        log.info(f"Fetching [{rank}/{len(top)}] {item['url']}")
        text = fetch_page_content(item["url"])
        time.sleep(random.uniform(*CONFIG["FETCH_DELAY"]))
        fetched.append({
            "rank": rank,
            "title": item["title"],
            "url": item["url"],
            "snippet": item.get("snippet", ""),
            "text": text if len(text) >= CONFIG["MIN_CONTENT_LENGTH"] else item.get("snippet", ""),
        })

    # 4. Output
    if use_json:
        return json.dumps({
            "query": query,
            "engine_query": engine_query,   # shows injected year for transparency
            "engine": engine_used,
            "recency_boost": recency_boost,
            "total_results": len(results),
            "fetched_pages": len(fetched),
            "results": fetched,
        }, ensure_ascii=False, indent=2)

    lines = [
        f"Search: {query!r}  [{engine_used}]"
        + (f"  (recency boost, engine query: {engine_query!r})" if engine_query != query else ""),
        f"Total hits: {len(results)}  |  Pages fetched: {len(fetched)}\n",
    ]
    for item in fetched:
        lines.append(f"── [{item['rank']}] {item['title']}")
        lines.append(item["text"] or item["snippet"] or "(no content)")
        lines.append(f"   {item['url']}\n")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Web search — DDG Lite primary, Bing fallback, auto recency",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python web_search.py "httpx python async"
  python web_search.py "openai API changes" --json          # auto-injects year
  python web_search.py "rust ownership explained" --pages 3
  python web_search.py "LLM benchmarks" --recent --json    # force recency boost
""",
    )
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    parser.add_argument("--pages", type=int, default=CONFIG["DEFAULT_FETCH_PAGES"],
                        metavar="N", help="Pages to fetch (1–10)")
    parser.add_argument("--recent", action="store_true",
                        help="Force recency boost + year injection regardless of query content")
    args = parser.parse_args()
    print(web_search(" ".join(args.query), max_pages=args.pages,
                     use_json=args.json, force_recent=args.recent))


if __name__ == "__main__":
    main()