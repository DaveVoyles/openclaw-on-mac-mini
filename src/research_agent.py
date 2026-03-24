"""
OpenClaw Research Agent — Phase B
Autonomous multi-step research engine that:
  1. Decomposes a query into sub-searches
  2. Executes each search + browses top results
  3. Synthesizes a structured report with citations
  4. Streams progress updates via a callback (for Discord thread posting)
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger("openclaw.research")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """\
You are a meticulous research coordinator. Break the following research request \
into 3-5 specific search queries that together will provide a comprehensive answer. \
Each query should be precise and distinct (no overlap). \
Reply with ONLY a JSON array of strings, e.g. ["query 1", "query 2", "query 3"].

Research request: {query}
"""

_SYNTHESIS_PROMPT = """\
You are an expert research analyst. Based on the following search results and \
browsed page content, write a structured research report answering the original \
question. Use markdown formatting. Include:

## Summary
(2-3 sentence executive summary)

## Key Findings
(bullet points with the most important facts)

## Detailed Analysis
(longer explanation with context)

## Sources
(numbered list of URLs with one-line descriptions)

Be factual, cite sources inline (e.g. [1], [2]), and note any conflicting \
information or gaps.

Original question: {query}

Research data:
{data}
"""


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------

STEP_ICONS = {
    "plan": "🗺️",
    "search": "🔍",
    "browse": "🌐",
    "synthesize": "🧠",
    "done": "✅",
    "error": "❌",
}


def _step(kind: str, text: str) -> str:
    icon = STEP_ICONS.get(kind, "•")
    return f"{icon} {text}"


# ---------------------------------------------------------------------------
# Core research agent
# ---------------------------------------------------------------------------

class ResearchAgent:
    """
    Fire-and-forget research agent.

    Usage::

        agent = ResearchAgent()
        report = await agent.run("Find homes in Narberth PA under $450k with fenced yards",
                                  on_progress=my_callback)
    """

    def __init__(self,
                 max_searches: int = 4,
                 browse_top_n: int = 2,
                 timeout_seconds: int = 120):
        self.max_searches = max_searches
        self.browse_top_n = browse_top_n
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        query: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """
        Run a full research cycle. Returns the final markdown report.
        ``on_progress`` receives human-readable status strings and is awaited.
        """
        start = time.monotonic()

        async def post(kind: str, text: str):
            msg = _step(kind, text)
            log.info("Research[%s]: %s", kind, text)
            if on_progress:
                try:
                    await on_progress(msg)
                except Exception as e:
                    log.warning("on_progress callback error: %s", e)

        try:
            return await asyncio.wait_for(
                self._research(query, post),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            elapsed = round(time.monotonic() - start)
            await post("error", f"Research timed out after {elapsed}s — partial results may be available.")
            return f"⏱️ Research timed out after {elapsed} seconds. Try a more specific query."
        except Exception as e:
            log.error("Research agent error: %s", e, exc_info=True)
            await post("error", f"Unexpected error: {e}")
            return f"❌ Research failed: {e}"

    async def _research(
        self,
        query: str,
        post: Callable,
    ) -> str:
        from skills.advanced_skills import search_web, browse_url
        import json, re

        await post("plan", f"Planning research strategy for: *{query[:80]}*")

        # ── Step 1: Decompose query into sub-searches ─────────────────────────
        sub_queries = await self._plan_searches(query)
        if not sub_queries:
            sub_queries = [query]  # fallback: search the raw query
            await post("plan", "Using direct query (planning unavailable)")
        else:
            await post("plan", f"Decomposed into {len(sub_queries)} sub-searches")

        # ── Step 2: Execute searches ──────────────────────────────────────────
        raw_results: list[dict] = []  # [{query, results_text, urls}]

        for i, sq in enumerate(sub_queries[:self.max_searches], 1):
            await post("search", f"Search {i}/{min(len(sub_queries), self.max_searches)}: *{sq[:60]}*")
            try:
                results_text = await search_web(sq, num_results=5)
                urls = _extract_urls(results_text)
                raw_results.append({"query": sq, "results": results_text, "urls": urls})
            except Exception as e:
                raw_results.append({"query": sq, "results": f"Search failed: {e}", "urls": []})

        # ── Step 3: Browse top URLs ───────────────────────────────────────────
        all_urls = []
        for r in raw_results:
            all_urls.extend(r["urls"])

        # Deduplicate, prefer non-social-media URLs
        seen: set[str] = set()
        priority_urls = []
        fallback_urls = []
        skip_domains = {"twitter.com", "x.com", "facebook.com", "instagram.com", "reddit.com", "youtube.com"}
        for url in all_urls:
            if url in seen:
                continue
            seen.add(url)
            domain = url.split("/")[2] if url.count("/") >= 2 else ""
            if any(skip in domain for skip in skip_domains):
                fallback_urls.append(url)
            else:
                priority_urls.append(url)

        browse_targets = (priority_urls + fallback_urls)[:self.browse_top_n]
        browsed_pages: list[dict] = []

        for i, url in enumerate(browse_targets, 1):
            await post("browse", f"Reading source {i}/{len(browse_targets)}: `{url[:60]}`")
            try:
                content = await asyncio.wait_for(browse_url(url), timeout=20)
                if content and not content.startswith("❌"):
                    browsed_pages.append({"url": url, "content": content[:3000]})
            except asyncio.TimeoutError:
                log.warning("Browse timed out: %s", url)
            except Exception as e:
                log.warning("Browse error %s: %s", url, e)

        # ── Step 4: Synthesize ────────────────────────────────────────────────
        await post("synthesize", "Synthesizing findings with Gemini…")

        data_sections = []
        for r in raw_results:
            data_sections.append(f"### Search: {r['query']}\n{r['results']}")
        for p in browsed_pages:
            data_sections.append(f"### Page: {p['url']}\n{p['content']}")

        combined_data = "\n\n".join(data_sections)
        # Trim to stay within Gemini's context window
        if len(combined_data) > 40_000:
            combined_data = combined_data[:40_000] + "\n\n[...truncated for length...]"

        report = await self._synthesize(query, combined_data)

        elapsed = round(time.monotonic() - (time.monotonic() - 1))  # approx
        await post("done", f"Research complete — {len(raw_results)} searches, {len(browsed_pages)} pages read")

        return report

    async def _plan_searches(self, query: str) -> list[str]:
        """Ask Gemini to decompose the query into sub-searches."""
        import json
        try:
            from llm import chat_deep
            prompt = _PLAN_PROMPT.format(query=query)
            text, _ = await chat_deep(prompt)
            # Extract JSON array from response (tolerate markdown fences)
            text = text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            queries = json.loads(text.strip())
            if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
                return queries[:5]
        except Exception as e:
            log.warning("Query planning failed: %s", e)
        return []

    async def _synthesize(self, query: str, data: str) -> str:
        """Ask Gemini to synthesize all research data into a report."""
        try:
            from llm import chat_deep
            prompt = _SYNTHESIS_PROMPT.format(query=query, data=data)
            text, _ = await chat_deep(prompt)
            return text
        except Exception as e:
            log.error("Synthesis failed: %s", e)
            return f"❌ Synthesis failed: {e}\n\nRaw data preview:\n{data[:500]}"


def _extract_urls(text: str) -> list[str]:
    """Extract http(s) URLs from search result text."""
    import re
    return re.findall(r"https?://[^\s\)\]>\"']+", text)
