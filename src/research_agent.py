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
                 timeout_seconds: int = 120,
                 max_concurrent: int = 3):
        self.max_searches = max_searches
        self.browse_top_n = browse_top_n
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)

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
        await post("plan", f"Planning research strategy for: *{query[:80]}*")

        # ── Step 0: Check for prior research on this topic ────────────────────
        prior_context = ""
        try:
            import vector_store
            prior = await vector_store.search(
                vector_store.RESEARCH_COLLECTION, query, top_k=2, threshold=0.6
            )
            if prior:
                snippets = []
                for r in prior:
                    meta = r.get("metadata", {})
                    old_query = meta.get("query", "prior research")
                    snippets.append(f"[Prior: {old_query}] {r['text'][:500]}")
                prior_context = "\n\n".join(snippets)
                await post("plan", f"Found {len(prior)} related prior research reports — will build on them")
        except Exception as e:
            log.debug("Prior research lookup failed (non-critical): %s", e)

        # ── Step 1: Decompose query into sub-searches ─────────────────────────
        sub_queries = await self._plan_searches(query)
        if not sub_queries:
            sub_queries = [query]  # fallback: search the raw query
            await post("plan", "Using direct query (planning unavailable)")
        else:
            await post("plan", f"Decomposed into {len(sub_queries)} sub-searches")

        # ── Step 2: Execute searches ──────────────────────────────────────────
        raw_results = await self._perform_searches(sub_queries, post)

        # ── Step 3: Browse top URLs ───────────────────────────────────────────
        all_urls: list[str] = []
        for r in raw_results:
            all_urls.extend(r["urls"])
        browse_targets = self._prioritize_urls(all_urls)
        browsed_pages = await self._fetch_pages(browse_targets, post)

        # ── Step 4: Synthesize ────────────────────────────────────────────────
        await post("synthesize", "Synthesizing findings with Gemini…")

        data_sections = []
        for r in raw_results:
            data_sections.append(f"### Search: {r['query']}\n{r['results']}")
        for p in browsed_pages:
            data_sections.append(f"### Page: {p['url']}\n{p['content']}")

        combined_data = "\n\n".join(data_sections)

        # Inject prior research context if found
        if prior_context:
            combined_data = f"### Prior Research (for context — build on this, don't repeat)\n{prior_context}\n\n{combined_data}"

        if len(combined_data) > 40_000:
            combined_data = combined_data[:40_000] + "\n\n[...truncated for length...]"

        report = await self._synthesize(query, combined_data)

        await post("done", f"Research complete — {len(raw_results)} searches, {len(browsed_pages)} pages read")

        # ── Step 5: Auto-save report to NAS (if configured) ──────────────────
        await self._auto_save(query, report, post)

        # ── Step 6: Index report in vector store for future recall ────────────
        try:
            import vector_store
            source_urls = [u for r in raw_results for u in r["urls"]]
            await vector_store.add_research_report(query, report, source_urls)
        except Exception as e:
            log.debug("Vector index for research failed (non-critical): %s", e)

        return report

    async def _perform_searches(
        self,
        sub_queries: list[str],
        post: Callable,
    ) -> list[dict]:
        """Execute all sub-queries in parallel using asyncio.gather()."""
        from skills.advanced_skills import search_web

        queries = sub_queries[:self.max_searches]
        total = len(queries)
        await post("search", f"Launching {total} parallel search workers\u2026")

        async def _one_search(i: int, sq: str) -> dict:
            async with self._semaphore:
                try:
                    results_text = await search_web(sq, num_results=5)
                    await post("search", f"Worker {i}/{total} done: *{sq[:50]}*")
                    return {"query": sq, "results": results_text, "urls": _extract_urls(results_text)}
                except Exception as e:
                    return {"query": sq, "results": f"Search failed: {e}", "urls": []}

        results = await asyncio.gather(*(_one_search(i + 1, sq) for i, sq in enumerate(queries)))
        return list(results)

    def _prioritize_urls(self, all_urls: list[str]) -> list[str]:
        """Deduplicate and prioritize URLs, deprioritizing social media."""
        seen: set[str] = set()
        priority_urls: list[str] = []
        fallback_urls: list[str] = []
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
        return (priority_urls + fallback_urls)[:self.browse_top_n]

    async def _fetch_pages(
        self,
        urls: list[str],
        post: Callable,
    ) -> list[dict]:
        """Browse top URLs, returning list of {url, content} dicts."""
        from skills.advanced_skills import browse_url

        browsed_pages: list[dict] = []
        for i, url in enumerate(urls, 1):
            await post("browse", f"Reading source {i}/{len(urls)}: `{url[:60]}`")
            async with self._semaphore:
                try:
                    content = await asyncio.wait_for(browse_url(url), timeout=20)
                    if content and not content.startswith("❌"):
                        browsed_pages.append({"url": url, "content": content[:3000]})
                        # Index source in vector store for future /sources lookup
                        try:
                            import vector_store
                            domain = url.split("/")[2] if url.count("/") >= 2 else url
                            await vector_store.add_document(
                                vector_store.RESEARCH_COLLECTION,
                                doc_id=f"source_{hash(url) % 100000}",
                                text=content[:2000],
                                metadata={
                                    "type": "source",
                                    "url": url,
                                    "domain": domain,
                                },
                            )
                        except Exception:
                            pass  # non-critical
                except asyncio.TimeoutError:
                    log.warning("Browse timed out: %s", url)
                except Exception as e:
                    log.warning("Browse error %s: %s", url, e)
        return browsed_pages

    async def _auto_save(self, query: str, report: str, post) -> None:
        """Save research report to the Obsidian vault (primary) and NAS (secondary)."""
        import re as _re
        import datetime as _dt

        safe_slug = _re.sub(r"[^a-zA-Z0-9]+", "_", query[:40]).strip("_").lower()
        date_str = _dt.date.today().isoformat()
        filename = f"research_{date_str}_{safe_slug}.md"
        header = f"# Research Report\n**Query**: {query}\n**Date**: {date_str}\n\n---\n\n"
        full_doc = header + report

        # Save to Obsidian vault (primary — always attempted)
        try:
            from obsidian_writer import save_to_vault
            vault_result = await asyncio.wait_for(
                save_to_vault(
                    title=f"Research: {query[:60]}",
                    content=report,
                    tags=["research", "auto-saved"],
                    content_type="research",
                ),
                timeout=10,
            )
            if vault_result.startswith("✅"):
                await post("done", vault_result)
        except Exception as e:
            log.debug("Research vault save skipped: %s", e)

        # Also sync to NAS
        try:
            from nas import nas_write_file
            nas_result = await asyncio.wait_for(
                nas_write_file(full_doc, remote_folder="/volume1/documents/research", filename=filename),
                timeout=20,
            )
            if nas_result.startswith("✅"):
                await post("done", f"Report also saved to NAS: `{filename}`")
                # Also try Google Docs if Maton is configured
                try:
                    from gateway import create_google_doc
                    import os as _os
                    if _os.getenv("MATON_API_KEY"):
                        doc_result = await asyncio.wait_for(
                            create_google_doc(title=f"Research: {query[:60]}", content=full_doc),
                            timeout=20,
                        )
                        if doc_result.startswith("✅"):
                            await post("done", "Also saved to Google Docs")
                except Exception as exc:
                    log.debug("Research auto-save to Google Docs failed: %s", exc)
        except Exception as e:
            log.debug("Research NAS save skipped: %s", e)

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
