"""
OpenClaw Search Skills — web search providers and cascade logic.
Extracted from advanced_skills.py for modularity.
"""

import asyncio
import datetime as dt
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from ask_orchestrator import (
    apply_retrieval_budget,
    get_latency_load_snapshot,
    select_latency_budget_policy,
)
from channel_profiles import resolve_retrieval_profile_settings
from config import TIMEOUT_DEFAULT, TIMEOUT_SLOW
from config import cfg as _cfg
from http_session import SessionManager
from search_provider import get_stats, retry_once

log = logging.getLogger("openclaw.search_skills")


def _record_quality_metric(event: str, context: str = "search") -> None:
    """Best-effort metric emission for reliability signals."""
    try:
        from metrics_collector import get_collector

        get_collector().record_quality_event(event=event, context=context)
    except Exception:
        # Metrics must never break user-facing search behavior.
        pass


def _record_budget_policy_metric(
    *,
    path: str,
    profile: str,
    load_tier: str,
    decision: str,
) -> None:
    try:
        from metrics_collector import get_collector

        get_collector().record_budget_policy_decision(
            path=path,
            profile=profile,
            load_tier=load_tier,
            decision=decision,
        )
    except Exception:
        pass

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

# ---------------------------------------------------------------------------
# Perplexity result cache — short TTL to avoid duplicate API calls for
# back-to-back queries on the same topic within a session.
# ---------------------------------------------------------------------------

_PERPLEXITY_CACHE_TTL_SECONDS = 300  # 5 minutes
_perplexity_cache: dict[str, tuple[str, float]] = {}  # key → (result, timestamp)
_perplexity_cache_hits: int = 0  # session hit counter


def _perplexity_cache_key(query: str) -> str:
    """Normalize query to a stable cache key."""
    return query.lower().strip()[:200]


def _perplexity_cache_get(query: str) -> str | None:
    """Return cached result if fresh, else None."""
    global _perplexity_cache_hits
    key = _perplexity_cache_key(query)
    entry = _perplexity_cache.get(key)
    if entry and (time.monotonic() - entry[1]) < _PERPLEXITY_CACHE_TTL_SECONDS:
        log.debug("Perplexity cache hit for query: %.60s", query)
        _perplexity_cache_hits += 1
        return entry[0]
    return None


def _perplexity_cache_set(query: str, result: str) -> None:
    """Store result in cache, evicting entries beyond 100."""
    if len(_perplexity_cache) >= 100:
        # Evict oldest entry
        oldest_key = min(_perplexity_cache, key=lambda k: _perplexity_cache[k][1])
        del _perplexity_cache[oldest_key]
    _perplexity_cache[_perplexity_cache_key(query)] = (result, time.monotonic())


def get_perplexity_cache_stats() -> dict:
    """Return current Perplexity cache statistics for dashboard display."""
    import time as _time
    now = _time.monotonic()
    live_entries = sum(
        1 for (_, ts) in _perplexity_cache.values()
        if (now - ts) < _PERPLEXITY_CACHE_TTL_SECONDS
    )
    return {
        "size": len(_perplexity_cache),
        "live_entries": live_entries,
        "hits": _perplexity_cache_hits,
        "ttl_seconds": _PERPLEXITY_CACHE_TTL_SECONDS,
    }

FIRECRAWL_API_KEY = _cfg.firecrawl_api_key
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"

SERPER_API_KEY = _cfg.serper_api_key

TAVILY_API_KEY = _cfg.tavily_api_key
TAVILY_API_URL = "https://api.tavily.com/search"

_SKILLS_DIR = Path(__file__).parent
_TAVILY_SCRIPT = _SKILLS_DIR / "openclaw-tavily-search" / "scripts" / "tavily_search.py"
_DDG_SCRIPT = _SKILLS_DIR / "free-web-search" / "scripts" / "web_search.py"

COMMAND_TIMEOUT = 15

_SEARCH_CONTEXT_SPORTS = {
    "sports",
    "sports_recap",
    "sports-recap",
    "sports_watch",
    "sports-watch",
}
_SEARCH_CONTEXT_NEWS = {
    "news",
    "news_recap",
    "news-recap",
    "box_office",
    "box-office",
}
_SEARCH_CONTEXT_GAMING = {
    "gaming",
    "gaming_recap",
    "gaming-recap",
    "gaming_news",
    "gaming-news",
}

_TRUST_HIGH_DOMAINS = {
    "apnews.com",
    "bbc.com",
    "bloomberg.com",
    "cdc.gov",
    "espn.com",
    "ft.com",
    "ncaa.com",
    "nih.gov",
    "npr.org",
    "reuters.com",
    "sec.gov",
    "the-numbers.com",
    "usatoday.com",
    "whitehouse.gov",
    "who.int",
    "wsj.com",
}

_TRUST_MEDIUM_DOMAINS = {
    "imdb.com",
    "insidelacrosse.com",
    "rottentomatoes.com",
    "wikipedia.org",
}

_LOW_TRUST_DOMAINS = {
    "medium.com",
    "reddit.com",
    "substack.com",
    "x.com",
}

_FRESH_SIGNAL_RE = re.compile(
    r"\b(today|just now|hours? ago|yesterday|this week|this weekend|latest|updated|breaking|live)\b",
    re.IGNORECASE,
)
_STALE_SIGNAL_RE = re.compile(
    r"\b(archive|archived|historical|history|classic|retrospective)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# Matches a "Sources" section header near the end of a Perplexity LLM answer.
# Used to strip the LLM-generated Sources block before we append the JSON citations.
_LLM_SOURCES_HEADER_RE = re.compile(
    r"\n{1,3}(?:#{1,3}\s+)?\*{0,2}sources?\*{0,2}\s*:?\*{0,2}\s*\n",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------


async def search_web(
    query: str,
    num_results: int = 5,
    provider: str = "",
    *,
    min_results: int = 1,
    retry_on_low_results: bool = False,
    expand_query: bool = False,
    expansion_context: str = "",
) -> str:
    """Search the web using the best available provider.

    Search priority: Perplexity (AI-powered) → Firecrawl (search+extract) → Tavily (structured) → DuckDuckGo (free) → Bing Lite (fallback)

    Args:
        query: Search query
        num_results: Max results (1-10)
        provider: Force a specific provider: 'perplexity', 'firecrawl', 'tavily',
                  'serper', 'duckduckgo'. Empty = auto cascade.
        min_results: Minimum unique results desired before accepting a response.
        retry_on_low_results: Continue provider fallback when result count is below
            min_results.
        expand_query: When True, tries additional query variants (weekend/date windows).
        expansion_context: Optional hint for expansion patterns (e.g. "sports_recap").
    """
    num_results = min(max(num_results, 1), 10)
    min_results = min(max(int(min_results), 1), num_results)
    provider = provider.lower().strip()
    expansion_context = (expansion_context or "").strip()

    profile_settings = resolve_retrieval_profile_settings(query)
    use_profile_defaults = (
        min_results == 1
        and not retry_on_low_results
        and not expand_query
        and not expansion_context
    )
    if use_profile_defaults:
        desired_min_results = max(int(profile_settings.get("min_results", min_results)), 1)
        num_results = min(max(num_results, desired_min_results), 10)
        min_results = min(
            desired_min_results,
            num_results,
        )
        retry_on_low_results = bool(profile_settings.get("retry_on_low_results", retry_on_low_results))
        expand_query = bool(profile_settings.get("expand_query", expand_query))
        expansion_context = str(profile_settings.get("expansion_context", expansion_context) or expansion_context)

    max_query_variants = int(profile_settings.get("max_query_variants", 3))
    provider_attempt_cap = int(profile_settings.get("provider_attempt_cap", 5))
    profile_name = str(profile_settings.get("profile_name", "general") or "general")

    load_stats = get_latency_load_snapshot(command_hint="search")
    policy = select_latency_budget_policy(
        profile_name=profile_name,
        load_stats=load_stats,
    )
    effective_budget = apply_retrieval_budget(
        min_results=min_results,
        max_query_variants=max_query_variants,
        provider_attempt_cap=provider_attempt_cap,
        num_results=num_results,
        policy=policy,
    )
    min_results = int(effective_budget["min_results"])
    max_query_variants = int(effective_budget["max_query_variants"])
    provider_attempt_cap = int(effective_budget["provider_attempt_cap"])
    if load_stats is None:
        _record_quality_metric("search_budget_metrics_missing", context=expansion_context or "search")
    if policy.get("decision") == "latency":
        _record_quality_metric("search_budget_tightened_for_latency", context=expansion_context or "search")
    _record_budget_policy_metric(
        path="search_retrieval",
        profile=profile_name,
        load_tier=str(policy.get("load_tier", "unknown")),
        decision=str(policy.get("decision", "failsafe")),
    )

    if not provider and (retry_on_low_results or expand_query or min_results > 1):
        return await _search_web_reliable(
            query=query,
            num_results=num_results,
            min_results=min_results,
            expand_query=expand_query,
            expansion_context=expansion_context,
            max_query_variants=max_query_variants,
            provider_attempt_cap=provider_attempt_cap,
        )

    # ── Forced provider selection ──────────────────────────────────────────
    if provider:
        if provider == "perplexity":
            if not PERPLEXITY_API_KEY:
                return "⚠️ Perplexity API key not configured."
            cached = _perplexity_cache_get(query)
            if cached:
                return cached
            result = await _perplexity_search(query, num_results) or "❌ Perplexity returned no results."
            if not result.startswith("❌"):
                _perplexity_cache_set(query, result)
            return result
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


def _build_query_variants(query: str, *, expand_query: bool, expansion_context: str = "") -> list[str]:
    base = (query or "").strip()
    if not base:
        return []
    if not expand_query:
        return [base]

    context = (expansion_context or "").strip().lower()
    candidates = [base]
    if context in _SEARCH_CONTEXT_SPORTS:
        candidates.extend(
            [
                f"{base} this weekend schedule",
                f"{base} next 7 days league schedule",
                f"{base} date window game times",
            ]
        )
    elif context in _SEARCH_CONTEXT_NEWS:
        candidates.extend(
            [
                f"{base} this weekend",
                f"{base} this week timeline by date",
                f"{base} latest updates analysis",
            ]
        )
    elif context in _SEARCH_CONTEXT_GAMING:
        candidates.extend(
            [
                f"{base} this week game releases and patch notes",
                f"{base} esports and platform updates this week",
                f"{base} this weekend gaming recap by title",
                f"{base} top stories this week by date",
            ]
        )
    else:
        candidates.extend(
            [
                f"{base} this weekend",
                f"{base} this week by date",
            ]
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = " ".join(item.split()).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(" ".join(item.split()).strip())
    return deduped


def _canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        if not parts.scheme or not parts.netloc:
            return url.strip()
        path = re.sub(r"/+$", "", parts.path or "/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
    except Exception:
        return url.strip()


def _normalize_title(title: str) -> str:
    collapsed = re.sub(r"\s+", " ", (title or "").strip().lower())
    return re.sub(r"[^a-z0-9 ]", "", collapsed).strip()


def _derive_title_from_url(url: str) -> str:
    canonical = _canonicalize_url(url)
    if not canonical:
        return "Source"
    try:
        path = urlsplit(canonical).path.strip("/")
        if not path:
            return canonical
        tail = path.split("/")[-1].replace("-", " ").replace("_", " ")
        tail = re.sub(r"\s+", " ", tail).strip()
        return tail.title() if tail else canonical
    except Exception:
        return canonical


def _extract_domain(url: str) -> str:
    canonical = _canonicalize_url(url)
    if not canonical:
        return ""
    try:
        host = (urlsplit(canonical).netloc or "").lower().strip()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_matches_any(domain: str, domain_pool: set[str]) -> bool:
    if not domain:
        return False
    return any(domain == item or domain.endswith(f".{item}") for item in domain_pool)


def _score_trust(hit: dict[str, str]) -> float:
    domain = _extract_domain(hit.get("url", ""))
    provider = (hit.get("provider", "") or "").strip().lower()
    source = (hit.get("source", "") or "").strip().lower()

    if domain.endswith(".gov") or domain.endswith(".edu"):
        return 0.98
    if _domain_matches_any(domain, _TRUST_HIGH_DOMAINS):
        return 0.95
    if _domain_matches_any(domain, _TRUST_MEDIUM_DOMAINS):
        return 0.8
    if _domain_matches_any(domain, _LOW_TRUST_DOMAINS):
        return 0.45
    if domain:
        return 0.65
    if provider in {"perplexity", "serper", "tavily", "firecrawl"} or source in {
        "perplexity",
        "serper",
        "tavily",
        "firecrawl",
    }:
        return 0.6
    return 0.5


def _score_freshness(hit: dict[str, str]) -> float:
    text = " ".join(
        part
        for part in (
            hit.get("title", ""),
            hit.get("snippet", ""),
            hit.get("url", ""),
        )
        if part
    )
    lowered = text.lower()
    score = 0.5

    if _FRESH_SIGNAL_RE.search(lowered):
        score += 0.25
    if _STALE_SIGNAL_RE.search(lowered):
        score -= 0.25

    years = [int(year) for year in _YEAR_RE.findall(lowered)]
    if years:
        newest_year = max(years)
        current_year = dt.datetime.now(dt.timezone.utc).year
        age = current_year - newest_year
        if age <= 1:
            score += 0.25
        elif age <= 3:
            score += 0.12
        elif age <= 6:
            score -= 0.08
        else:
            score -= 0.25

    return max(0.0, min(score, 1.0))


def score_hit_for_evidence(hit: dict[str, str]) -> dict[str, str]:
    """Annotate a hit with deterministic trust/freshness scores."""
    normalized = {
        "title": (hit.get("title", "") or "").strip(),
        "url": _canonicalize_url(hit.get("url", "")),
        "snippet": " ".join((hit.get("snippet", "") or "").split()).strip(),
        "provider": (hit.get("provider", "") or "").strip(),
        "source": (hit.get("source", "") or "").strip(),
    }
    trust_score = _score_trust(normalized)
    freshness_score = _score_freshness(normalized)
    evidence_score = (trust_score * 0.68) + (freshness_score * 0.32)
    if normalized["url"]:
        evidence_score += 0.02

    normalized["domain"] = _extract_domain(normalized["url"])
    normalized["trust_score"] = round(max(0.0, min(trust_score, 1.0)) * 100.0, 1)
    normalized["freshness_score"] = round(max(0.0, min(freshness_score, 1.0)) * 100.0, 1)
    normalized["evidence_score"] = round(max(0.0, min(evidence_score, 1.0)) * 100.0, 1)
    normalized["stale_signal"] = normalized["freshness_score"] < 40.0
    normalized["low_trust_low_fresh_signal"] = (
        normalized["trust_score"] < 55.0 and normalized["freshness_score"] < 45.0
    )
    return normalized


def rank_hits_for_evidence(hits: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return hits ranked by trust/freshness evidence priority."""
    scored = [score_hit_for_evidence(hit) for hit in hits if isinstance(hit, dict)]
    return sorted(
        scored,
        key=lambda hit: (
            -float(hit.get("evidence_score", 0.0)),
            -float(hit.get("trust_score", 0.0)),
            -float(hit.get("freshness_score", 0.0)),
            _canonicalize_url(hit.get("url", "")),
            _normalize_title(hit.get("title", "")),
        ),
    )


def _hit_key(hit: dict[str, str]) -> str:
    canonical_url = _canonicalize_url(hit.get("url", ""))
    if canonical_url:
        return f"url::{canonical_url}"

    title_key = _normalize_title(hit.get("title", ""))
    source_key = _normalize_title(hit.get("source", ""))
    snippet_key = _normalize_title(hit.get("snippet", ""))[:120]
    return f"text::{title_key}::{source_key}::{snippet_key}"


def _merge_unique_hits(*hit_lists: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for hits in hit_lists:
        for hit in hits:
            normalized_hit = score_hit_for_evidence(hit)
            key = _hit_key(normalized_hit)
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized_hit)
    return merged


def _format_aggregated_results(
    hits: list[dict[str, str]],
    *,
    queried_providers: list[str],
    num_results: int,
) -> str:
    if not hits:
        return "No web results found."

    ranked_hits = rank_hits_for_evidence(hits)
    selected = ranked_hits[:num_results]
    lines = [f"**Web Search Results** ({len(selected)} of {len(hits)} unique):\n"]
    for i, hit in enumerate(selected, 1):
        title = hit.get("title") or "Untitled"
        snippet = hit.get("snippet", "")
        url = hit.get("url", "")
        provider = hit.get("provider", "")
        source = hit.get("source", "")

        lines.append(f"**{i}. {title}**")
        if snippet:
            lines.append(snippet[:300])
        meta = " • ".join(part for part in (provider.title() if provider else "", source) if part)
        if meta:
            lines.append(f"*{meta}*")
        if url:
            lines.append(f"🔗 <{url}>")
        lines.append("")

    provider_text = ", ".join(queried_providers) if queried_providers else "n/a"
    lines.append(f"*Providers queried: {provider_text}*")
    return "\n".join(lines).strip()


async def _search_web_reliable(
    *,
    query: str,
    num_results: int,
    min_results: int,
    expand_query: bool,
    expansion_context: str,
    max_query_variants: int = 3,
    provider_attempt_cap: int = 5,
) -> str:
    query_variants = _build_query_variants(
        query,
        expand_query=expand_query,
        expansion_context=expansion_context,
    )
    max_query_variants = min(max(int(max_query_variants), 1), 6)
    provider_attempt_cap = min(max(int(provider_attempt_cap), 1), 6)
    query_variants = query_variants[:max_query_variants]
    if not query_variants:
        return "❌ Search query cannot be empty."

    aggregated_hits: list[dict[str, str]] = []
    queried_providers: list[str] = []
    provider_attempts = 0

    def _can_attempt_more() -> bool:
        return provider_attempts < provider_attempt_cap

    def _format_success_output() -> str:
        if len(queried_providers) > 1:
            _record_quality_metric("search_fallback_activation", context=expansion_context or "search")
        return _format_aggregated_results(
            aggregated_hits,
            queried_providers=queried_providers,
            num_results=num_results,
        )

    for current_query in query_variants:
        if not _can_attempt_more():
            break
        if PERPLEXITY_API_KEY:
            if not _can_attempt_more():
                break
            provider_attempts += 1
            stats = get_stats("perplexity")
            start = time.monotonic()
            try:
                _, hits = await retry_once(
                    lambda: _perplexity_search(current_query, num_results, return_hits=True),
                    "perplexity",
                )
                queried_providers.append("perplexity")
                if hits:
                    stats.record_success((time.monotonic() - start) * 1000)
                    aggregated_hits = _merge_unique_hits(aggregated_hits, hits)
                else:
                    stats.record_failure()
            except Exception as e:
                stats.record_failure()
                log.debug("Perplexity search failed after retry: %s", e)
            if len(aggregated_hits) >= min_results:
                return _format_success_output()

        if FIRECRAWL_API_KEY:
            if not _can_attempt_more():
                break
            provider_attempts += 1
            stats = get_stats("firecrawl")
            start = time.monotonic()
            try:
                _, hits = await retry_once(
                    lambda: _firecrawl_search(current_query, num_results, return_hits=True),
                    "firecrawl",
                )
                queried_providers.append("firecrawl")
                if hits:
                    stats.record_success((time.monotonic() - start) * 1000)
                    aggregated_hits = _merge_unique_hits(aggregated_hits, hits)
                else:
                    stats.record_failure()
            except Exception as e:
                stats.record_failure()
                log.debug("Firecrawl search failed after retry: %s", e)
            if len(aggregated_hits) >= min_results:
                return _format_success_output()

        if TAVILY_API_KEY and _TAVILY_SCRIPT.exists():
            if not _can_attempt_more():
                break
            provider_attempts += 1
            stats = get_stats("tavily")
            start = time.monotonic()
            clean_num = int(float(num_results))
            cmd = [
                sys.executable,
                str(_TAVILY_SCRIPT),
                "--query", current_query,
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
                queried_providers.append("tavily")
                if proc.returncode != 0:
                    err = stderr.decode().strip()[:300]
                    log.warning("Tavily script failed: %s", err)
                    stats.record_failure()
                else:
                    try:
                        data = json.loads(stdout.decode())
                        _, hits = _format_tavily_results(data, clean_num, return_hits=True)
                        if hits:
                            stats.record_success((time.monotonic() - start) * 1000)
                            aggregated_hits = _merge_unique_hits(aggregated_hits, hits)
                        else:
                            stats.record_failure()
                    except json.JSONDecodeError:
                        log.error("Tavily script returned invalid JSON: %s", stdout.decode()[:200])
                        stats.record_failure()
            except asyncio.TimeoutError:
                log.warning("Tavily script timed out")
                stats.record_failure()
            except Exception as e:
                log.warning("Tavily script error: %s", e)
                stats.record_failure()
            if len(aggregated_hits) >= min_results:
                return _format_success_output()

        if _DDG_SCRIPT.exists():
            if not _can_attempt_more():
                break
            provider_attempts += 1
            stats = get_stats("duckduckgo")
            start = time.monotonic()
            clean_num = int(float(num_results))
            cmd = [
                sys.executable,
                str(_DDG_SCRIPT),
                current_query,
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
                queried_providers.append("duckduckgo")
                if proc.returncode != 0:
                    stats.record_failure()
                else:
                    data = json.loads(stdout.decode())
                    if "error" in data:
                        stats.record_failure()
                    else:
                        _, hits = _format_ddg_results(data, clean_num, return_hits=True)
                        if hits:
                            stats.record_success((time.monotonic() - start) * 1000)
                            aggregated_hits = _merge_unique_hits(aggregated_hits, hits)
                        else:
                            stats.record_failure()
            except Exception:
                stats.record_failure()
            if len(aggregated_hits) >= min_results:
                return _format_success_output()

        if not _can_attempt_more():
            break
        provider_attempts += 1
        stats = get_stats("bing")
        start = time.monotonic()
        try:
            import urllib.parse
            bing_url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(current_query)
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
                queried_providers.append("bing")
                if resp.status == 200:
                    html = await resp.text()
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html, "html.parser")
                        hits: list[dict[str, str]] = []
                        for result in soup.select("li.b_algo")[:int(float(num_results))]:
                            title_el = result.select_one("h2 a")
                            snippet_el = result.select_one(".b_caption p")
                            if not title_el:
                                continue
                            title = title_el.get_text(strip=True)
                            url_link = title_el.get("href", "")
                            snippet = snippet_el.get_text(strip=True)[:300] if snippet_el else ""
                            hits.append({
                                "title": title,
                                "url": url_link,
                                "snippet": snippet,
                                "provider": "bing",
                                "source": "Bing",
                            })
                        if hits:
                            stats.record_success((time.monotonic() - start) * 1000)
                            aggregated_hits = _merge_unique_hits(aggregated_hits, hits)
                        else:
                            stats.record_failure()
                    except ImportError:
                        stats.record_failure()
                else:
                    stats.record_failure()
        except Exception as e:
            stats.record_failure()
            log.warning("Bing fallback failed: %s", e)
        if len(aggregated_hits) >= min_results:
            return _format_success_output()

    if aggregated_hits:
        base_output = _format_aggregated_results(
            aggregated_hits,
            queried_providers=queried_providers,
            num_results=num_results,
        )
        if len(aggregated_hits) < min_results:
            _record_quality_metric("search_low_results_incident", context=expansion_context or "search")
            base_output += (
                f"\n\n⚠️ Only {len(aggregated_hits)} unique results found "
                f"(target: {min_results})."
            )
        return base_output

    if not TAVILY_API_KEY and not PERPLEXITY_API_KEY:
        return (
            "⚠️ Web search not configured. Either:\n"
            "• Set `PERPLEXITY_API_KEY` in .env for Perplexity AI Search, or\n"
            "• Set `TAVILY_API_KEY` in .env for Tavily AI Search, or\n"
            "• Ensure `skills/free-web-search/` is installed (run: "
            "`npx clawhub@latest install free-web-search`)"
        )
    return "❌ All web search methods exhausted. Check logs for details."


def _strip_llm_sources_section(answer: str) -> str:
    """Remove a trailing Sources section that Perplexity's LLM may generate.

    The JSON citations array is the canonical source list appended afterwards.
    Stripping the LLM-generated block here prevents duplicate Sources sections.
    Only strips when the header appears in the latter half of the text so we
    don't accidentally drop real answer content.
    """
    matches = list(_LLM_SOURCES_HEADER_RE.finditer(answer))
    if not matches:
        return answer
    last_match = matches[-1]
    if last_match.start() >= len(answer) * 0.4:
        return answer[: last_match.start()].rstrip()
    return answer


async def _perplexity_search(query: str, num_results: int = 5, *, return_hits: bool = False) -> str | tuple[str, list[dict[str, str]]]:
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

    # Strip any LLM-generated Sources section before appending the JSON citations.
    # Perplexity may include its own Sources block when the prompt asks for one,
    # which would duplicate the block we append below.
    answer = _strip_llm_sources_section(answer)

    lines = [f"**Perplexity AI Answer:**\n{answer}"]
    if citations:
        lines.append("\nSources:")
        for i, cite in enumerate(citations[:num_results], 1):
            lines.append(f"{i}. {cite}")

    output = "\n".join(lines)
    hits: list[dict[str, str]] = []
    for cite in citations[:num_results]:
        hits.append({
            "title": _derive_title_from_url(cite),
            "url": cite,
            "snippet": answer[:300],
            "provider": "perplexity",
            "source": "Perplexity",
        })
    if not hits and answer.strip():
        hits.append({
            "title": "Perplexity answer",
            "url": "",
            "snippet": answer[:300],
            "provider": "perplexity",
            "source": "Perplexity",
        })

    if return_hits:
        return output, hits
    return output


async def _firecrawl_search(
    query: str,
    num_results: int = 5,
    *,
    return_hits: bool = False,
) -> str | tuple[str, list[dict[str, str]]]:
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
            return ("", []) if return_hits else ""

        results = data["data"]
        lines = [f"**Firecrawl Search** ({len(results)} results):\n"]
        hits: list[dict[str, str]] = []
        for i, r in enumerate(results[:num_results], 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            markdown = r.get("markdown", "")
            snippet = markdown[:500] + "…" if len(markdown) > 500 else markdown
            lines.append(f"**{i}. [{title}]({url})**")
            if snippet:
                lines.append(f"> {snippet}\n")
            hits.append({
                "title": title,
                "url": url,
                "snippet": snippet[:300],
                "provider": "firecrawl",
                "source": "Firecrawl",
            })

        log.info("Firecrawl search: %d results for: %s", len(results), query[:60])
        from spending import tracker as spending_tracker
        await spending_tracker.record_firecrawl(pages=len(results), action="search")
        output = "\n".join(lines)
        if return_hits:
            return output, hits
        return output
    except Exception as e:
        log.debug("Firecrawl search failed: %s", e)
        return ("", []) if return_hits else ""


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


def _format_tavily_results(
    data: dict,
    num_results: int,
    *,
    return_hits: bool = False,
) -> str | tuple[str, list[dict[str, str]]]:
    """Format Tavily API JSON response into Discord-friendly markdown."""
    lines = []
    if data.get("answer"):
        lines.append(f"**Answer**: {data['answer']}\n")
    results = data.get("results", [])
    if not results:
        empty = f"No web results found for: {data.get('query', '?')}"
        return (empty, []) if return_hits else empty
    hits: list[dict[str, str]] = []
    for i, r in enumerate(results[:num_results], 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = (r.get("content") or "")[:300].strip()
        lines.append(f"**{i}. {title}**\n{content}\n🔗 <{url}>")
        hits.append({
            "title": title,
            "url": url,
            "snippet": content,
            "provider": "tavily",
            "source": "Tavily",
        })
    output = "\n\n".join(lines)
    if return_hits:
        return output, hits
    return output


def _format_ddg_results(
    data: dict,
    num_results: int,
    *,
    return_hits: bool = False,
) -> str | tuple[str, list[dict[str, str]]]:
    """Format free-web-search JSON response into Discord-friendly markdown."""
    results = data.get("results", [])[:num_results]
    if not results:
        empty = f"No web results found for: {data.get('query', '?')}"
        return (empty, []) if return_hits else empty
    lines = []
    hits: list[dict[str, str]] = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        text = (r.get("text") or r.get("snippet") or "")[:300].strip()
        lines.append(f"**{i}. {title}**\n{text}\n🔗 <{url}>")
        hits.append({
            "title": title,
            "url": url,
            "snippet": text,
            "provider": "duckduckgo",
            "source": "DuckDuckGo",
        })
    src = "DuckDuckGo (free)"
    output = "\n\n".join(lines) + f"\n\n*via {src}*"
    if return_hits:
        return output, hits
    return output


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SEARCH_SKILLS = {
    "search_web": search_web,
    "firecrawl_scrape": firecrawl_scrape,
    "serper_search": serper_search,
}
