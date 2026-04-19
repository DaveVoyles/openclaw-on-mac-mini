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
import re
import time
from typing import Awaitable, Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """\
You are a meticulous research coordinator. Break the following research request \
into 3-5 specific sub-questions that together will provide a comprehensive answer.

For each sub-question, generate 2-3 keyword variations using different terms and \
phrasing. This helps find diverse sources. For example, instead of just \
"AI in healthcare", also try "artificial intelligence clinical outcomes" and \
"machine learning medical diagnosis trends".

Reply with ONLY a flat JSON array of all query strings (including variations), \
e.g. ["query 1a", "query 1b", "query 2a", "query 2b", "query 3a", "query 3b"].

Research request: {query}
"""

_SYNTHESIS_PROMPT = """\
You are an expert research analyst. Based on the following search results and \
browsed page content, write a structured research report answering the original \
question. Use markdown formatting with the following template:

# {{Topic}}: Research Report
*Generated: {{today's date}} | Sources: {{N}} analyzed | Search depth: {{number of searches}} queries*

## Executive Summary
(3-5 sentence overview of the most important findings)

## Key Findings
(bullet points with inline citations [1], [2] and confidence levels:
 - [High confidence] = 3+ sources agree on this claim
 - [Medium confidence] = 2 sources support this
 - [Low confidence] / [Single source] = only 1 source supports this)

## Detailed Analysis
(longer explanation with context, organized by theme)

## Sources
(numbered list of URLs with one-line descriptions)

## Methodology
- Queries executed: {{N}}
- Pages analyzed: {{M}}
- Source quality breakdown: (how many academic, news, blog, etc.)
- Date range of sources: (earliest to most recent source date observed)

Additional instructions:
- Cross-reference claims: if only ONE source supports a claim, prefix with \
"[Single source]"
- Prefer recent sources (last 12 months) over older ones when information conflicts
- If information conflicts between sources, present both perspectives clearly
- Be factual, cite sources inline (e.g. [1], [2])

Original question: {query}

Research data:
{data}
"""

_GAP_ANALYSIS_PROMPT = """\
You are a research quality reviewer. Given the research report below, identify \
important gaps, missing perspectives, or follow-up questions that would make \
the report more comprehensive.

Reply with ONLY a JSON array of 2-3 specific follow-up search queries that \
would fill those gaps. Each query should be precise and actionable. \
If the report is already comprehensive, reply with an empty array: []

Original question: {query}

Current report:
{report}
"""

_MERGE_PROMPT = """\
You are an expert research analyst. You have an existing research report and \
new findings from follow-up research. Merge the new findings into the existing \
report to create one comprehensive, unified report. Preserve the original \
structure (Summary, Key Findings, Detailed Analysis, Sources) but enrich it \
with the new data. Avoid duplication. Add new sources to the existing list.

Original question: {query}

Existing report:
{existing_report}

New findings:
{new_data}
"""


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------

STEP_ICONS = {
    "plan": "🗺️",
    "search": "🔍",
    "browse": "🌐",
    "synthesize": "🧠",
    "deep": "🔬",
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
                 max_searches: int = 10,
                 browse_top_n: int = 4,
                 timeout_seconds: int = 180,
                 max_concurrent: int = 4):
        self.max_searches = max_searches
        self.browse_top_n = browse_top_n
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._last_receipts: dict = {}

    def get_last_receipts(self) -> dict:
        """Return persistence receipts from the most recent research run."""
        return dict(self._last_receipts or {})

    async def run(
        self,
        query: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        deep: bool = False,
    ) -> str:
        """
        Run a full research cycle. Returns the final markdown report.
        ``on_progress`` receives human-readable status strings and is awaited.
        When ``deep=True``, performs up to 3 iterative passes, refining searches
        based on gaps found in each pass (max 5 minutes total).
        """
        start = time.monotonic()
        timeout = min(self.timeout_seconds, 300) if deep else self.timeout_seconds

        async def post(kind: str, text: str):
            msg = _step(kind, text)
            log.info("Research[%s]: %s", kind, text)
            if on_progress:
                try:
                    await on_progress(msg)
                except (OSError, AttributeError, ValueError, RuntimeError) as e:
                    log.warning("on_progress callback error: %s", e)

        try:
            return await asyncio.wait_for(
                self._research(query, post, deep=deep),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed = round(time.monotonic() - start)
            await post("error", f"Research timed out after {elapsed}s — partial results may be available.")
            return f"⏱️ Research timed out after {elapsed} seconds. Try a more specific query."
        except Exception as e:  # broad: intentional
            log.error("Research agent error: %s", e, exc_info=True)
            await post("error", f"Unexpected error: {e}")
            return f"❌ Research failed: {e}"

    async def _research(
        self,
        query: str,
        post: Callable,
        deep: bool = False,
    ) -> str:
        """Execute multi-stage research workflow with search, analysis, and synthesis.

        Process:
        1. Check vector store for prior research (avoid duplicate work)
        2. Generate research plan with 2-4 questions
        3. Search web for each question (parallel execution)
        4. Synthesize results into comprehensive report
        5. Store findings in vector store for future reference

        Args:
            query: Research topic or question
            post: Async callback(section, content) to stream progress updates
            deep: If True, generates more questions and deeper analysis

        Returns:
            Final research report with sources and citations
        """
        await post("plan", f"Planning research strategy for: *{query[:80]}*")

        # ── Step 0: Check for prior research on this topic ────────────────────
        receipts = {
            "vault": {"saved": False, "location": "", "detail": "Not attempted"},
            "session": {"saved": False, "location": "", "detail": "Recorded by caller"},
            "vector": {"saved": False, "location": "", "detail": "Not attempted"},
            "gdoc": {"saved": False, "location": "", "detail": "Not attempted"},
        }

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
        except Exception as e:  # broad: intentional — chromadb/pydantic may raise unpredictable errors
            log.debug("Prior research lookup failed (non-critical): %s", e)

        # ── Step 1: Decompose query into sub-searches ─────────────────────────
        sub_queries = await self._plan_searches(query)
        if not sub_queries:
            sub_queries = [query]  # fallback: search the raw query
            await post("plan", "Using direct query (planning unavailable)")
        else:
            await post("plan", f"Decomposed into {len(sub_queries)} sub-searches (with keyword variations)")

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

        # ── Step 4b: Deep research — iterative multi-pass refinement ──────────
        if deep:
            report = await self._deep_research_passes(
                query, report, raw_results, browsed_pages, post,
            )

        await post("done", f"Research complete — {len(raw_results)} searches, {len(browsed_pages)} pages read")

        # ── Step 5: Auto-save report to NAS (if configured) ──────────────────
        save_receipts = await self._auto_save(query, report, post)
        if isinstance(save_receipts, dict):
            receipts.update(save_receipts)

        # ── Step 6: Index report in vector store for future recall ────────────
        try:
            import vector_store
            source_urls = [u for r in raw_results for u in r["urls"]]
            report_id = await vector_store.add_research_report(query, report, source_urls)
            receipts["vector"] = {
                "saved": True,
                "location": f"{vector_store.RESEARCH_COLLECTION}/{report_id}",
                "detail": "Indexed for /research-search and follow-up context",
            }
        except (ImportError, OSError, AttributeError, ValueError, RuntimeError, KeyError) as e:
            receipts["vector"] = {
                "saved": False,
                "location": "research",
                "detail": f"Indexing failed: {e}",
            }
            log.debug("Vector index for research failed (non-critical): %s", e)

        self._last_receipts = receipts
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
                except (OSError, ConnectionError, ValueError, AttributeError, RuntimeError) as e:
                    return {"query": sq, "results": f"Search failed: {e}", "urls": []}

        results = await asyncio.gather(*(_one_search(i + 1, sq) for i, sq in enumerate(queries)))
        return list(results)

    @staticmethod
    def _rank_source_quality(url: str) -> int:
        """Rank URL by source quality. Higher = better."""
        domain = urlparse(url).netloc.lower()
        if any(d in domain for d in ['.gov', '.edu', 'nature.com', 'science.org', 'arxiv.org', 'pubmed']):
            return 4  # Academic/official
        if any(d in domain for d in [
            '.org', 'reuters.com', 'bbc.com', 'nytimes.com', 'washingtonpost.com',
            'wsj.com', 'bloomberg.com', 'techcrunch.com', 'arstechnica.com',
        ]):
            return 3  # Reputable news/org
        if any(d in domain for d in ['medium.com', 'substack.com', 'dev.to', 'hackernews', 'wikipedia.org']):
            return 2  # Quality blogs/wikis
        if any(d in domain for d in [
            'reddit.com', 'twitter.com', 'x.com', 'facebook.com', 'youtube.com',
            'instagram.com', 'tiktok.com', 'pinterest.com',
        ]):
            return 0  # Social media (deprioritize)
        return 1  # Default

    def _prioritize_urls(self, all_urls: list[str]) -> list[str]:
        """Deduplicate and rank URLs by source quality."""
        seen: set[str] = set()
        unique: list[str] = []
        for url in all_urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        unique.sort(key=self._rank_source_quality, reverse=True)
        return unique[:self.browse_top_n]

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
                        except Exception:  # broad: intentional — chromadb/pydantic may raise unpredictable errors
                            pass  # non-critical
                except asyncio.TimeoutError:
                    log.warning("Browse timed out: %s", url)
                except (OSError, ConnectionError, ValueError, AttributeError, RuntimeError) as e:
                    log.warning("Browse error %s: %s", url, e)
        return browsed_pages

    async def _auto_save(self, query: str, report: str, post) -> dict:
        """Save research report to the Obsidian vault (primary) and NAS (secondary)."""
        import datetime as _dt
        import re as _re

        receipts = {
            "vault": {"saved": False, "location": "data/vault/Research", "detail": "Not saved"},
            "gdoc": {"saved": False, "location": "google-docs", "detail": "Not attempted"},
        }

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
                vault_path_match = re.search(r"`([^`]+)`", vault_result)
                receipts["vault"] = {
                    "saved": True,
                    "location": f"data/vault/{vault_path_match.group(1)}" if vault_path_match else "data/vault/Research",
                    "detail": "Auto-saved research markdown",
                }
            else:
                receipts["vault"] = {
                    "saved": False,
                    "location": "data/vault/Research",
                    "detail": vault_result,
                }
        except (ImportError, OSError, ValueError, AttributeError) as e:
            receipts["vault"] = {
                "saved": False,
                "location": "data/vault/Research",
                "detail": f"Vault save skipped: {e}",
            }
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
                    import os as _os

                    from gateway import create_google_doc
                    if _os.getenv("MATON_API_KEY"):
                        doc_result = await asyncio.wait_for(
                            create_google_doc(title=f"Research: {query[:60]}", content=full_doc),
                            timeout=20,
                        )
                        if doc_result.startswith("✅"):
                            await post("done", "Also saved to Google Docs")
                            link_match = re.search(r"(https://docs\.google\.com/document/d/[^\s`]+)", doc_result)
                            receipts["gdoc"] = {
                                "saved": True,
                                "location": link_match.group(1) if link_match else "google-docs",
                                "detail": "Auto-created Google Doc",
                            }
                        else:
                            receipts["gdoc"] = {
                                "saved": False,
                                "location": "google-docs",
                                "detail": doc_result,
                            }
                    else:
                        receipts["gdoc"] = {
                            "saved": False,
                            "location": "google-docs",
                            "detail": "Skipped (MATON_API_KEY not set)",
                        }
                except (ImportError, OSError, ValueError, AttributeError) as exc:
                    receipts["gdoc"] = {
                        "saved": False,
                        "location": "google-docs",
                        "detail": f"Google Docs save failed: {exc}",
                    }
                    log.debug("Research auto-save to Google Docs failed: %s", exc)
        except (ImportError, OSError, ValueError, AttributeError) as e:
            receipts["gdoc"] = {
                "saved": False,
                "location": "google-docs",
                "detail": "Skipped (NAS save unavailable)",
            }
            log.debug("Research NAS save skipped: %s", e)

        return receipts

    # ── Deep research helpers ────────────────────────────────────────────────

    _MAX_DEEP_PASSES = 3  # including the initial pass

    async def _deep_research_passes(
        self,
        query: str,
        report: str,
        all_raw_results: list[dict],
        all_browsed_pages: list[dict],
        post: Callable,
    ) -> str:
        """Run 1-2 additional research passes, refining based on gap analysis."""
        total_searches = len(all_raw_results)
        total_pages = len(all_browsed_pages)

        for pass_num in range(2, self._MAX_DEEP_PASSES + 1):
            pass_start = time.monotonic()
            await post("deep", f"Pass {pass_num}/{self._MAX_DEEP_PASSES}: Analyzing gaps in current report…")

            follow_ups = await self._identify_gaps(query, report)
            if not follow_ups:
                await post("deep", f"Pass {pass_num}/{self._MAX_DEEP_PASSES}: No significant gaps found — stopping early")
                break

            pass_raw: list[dict] = []
            pass_pages: list[dict] = []

            for i, fq in enumerate(follow_ups, 1):
                await post("deep", f"Pass {pass_num}/{self._MAX_DEEP_PASSES}: Investigating ({i}/{len(follow_ups)}) *{fq[:60]}*…")

                sub_queries = await self._plan_searches(fq)
                if not sub_queries:
                    sub_queries = [fq]

                raw_results = await self._perform_searches(sub_queries, post)
                pass_raw.extend(raw_results)

                urls: list[str] = []
                for r in raw_results:
                    urls.extend(r["urls"])
                browse_targets = self._prioritize_urls(urls)
                pages = await self._fetch_pages(browse_targets, post)
                pass_pages.extend(pages)

            if not pass_raw and not pass_pages:
                await post("deep", f"Pass {pass_num}/{self._MAX_DEEP_PASSES}: No new data found — stopping early")
                break

            # Merge new findings into existing report
            new_data_sections = []
            for r in pass_raw:
                new_data_sections.append(f"### Search: {r['query']}\n{r['results']}")
            for p in pass_pages:
                new_data_sections.append(f"### Page: {p['url']}\n{p['content']}")
            new_data = "\n\n".join(new_data_sections)

            if len(new_data) > 20_000:
                new_data = new_data[:20_000] + "\n\n[...truncated for length...]"

            await post("synthesize", f"Pass {pass_num}/{self._MAX_DEEP_PASSES}: Merging new findings into report…")
            report = await self._merge_findings(query, report, new_data)

            total_searches += len(pass_raw)
            total_pages += len(pass_pages)
            elapsed = round(time.monotonic() - pass_start, 1)
            log.info(
                "Deep research pass %d/%d complete: %d searches, %d pages, %.1fs",
                pass_num, self._MAX_DEEP_PASSES, len(pass_raw), len(pass_pages), elapsed,
            )
            all_raw_results.extend(pass_raw)
            all_browsed_pages.extend(pass_pages)

        await post("deep", f"Deep research complete — {total_searches} total searches, {total_pages} total pages across all passes")
        return report

    async def _identify_gaps(self, query: str, report: str) -> list[str]:
        """Ask Gemini to identify gaps in the current report and return follow-up queries."""
        import json
        try:
            from llm import chat_deep
            prompt = _GAP_ANALYSIS_PROMPT.format(
                query=query,
                report=report[:8000],
            )
            text, _ = await chat_deep(prompt)
            text = text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            queries = json.loads(text.strip())
            if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
                return queries[:3]
        except (ImportError, OSError, ValueError, AttributeError, RuntimeError) as e:
            log.warning("Gap analysis failed: %s", e)
        return []

    async def _merge_findings(self, query: str, existing_report: str, new_data: str) -> str:
        """Ask Gemini to merge new research findings into the existing report."""
        try:
            from llm import chat_deep
            prompt = _MERGE_PROMPT.format(
                query=query,
                existing_report=existing_report,
                new_data=new_data,
            )
            text, _ = await chat_deep(prompt)
            return text
        except (ImportError, OSError, ValueError, AttributeError, RuntimeError) as e:
            log.error("Merge synthesis failed: %s", e)
            return existing_report + f"\n\n---\n## Additional Findings\n\n{new_data[:2000]}"

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
                return queries[:10]
        except (ImportError, OSError, ValueError, AttributeError, RuntimeError) as e:
            log.warning("Query planning failed: %s", e)
        return []

    async def _synthesize(self, query: str, data: str) -> str:
        """Synthesize all research data into a report.

        Prefers the Copilot proxy for text-only synthesis to save Gemini quota;
        falls back to Gemini thinking mode when Copilot is unavailable.
        """
        try:
            from llm.providers import COPILOT_PROXY_ENABLED, chat_openai
            from model_routing_policy import select_research_synthesis_route

            route = select_research_synthesis_route(copilot_available=COPILOT_PROXY_ENABLED)
            prompt = _SYNTHESIS_PROMPT.format(query=query, data=data)

            if route.provider == "copilot":
                log.debug("Research synthesis → Copilot (%s)", route.reason)
                text = await chat_openai(
                    prompt,
                    [],
                    "You are a thorough research analyst. Synthesize the provided data into a well-structured report.",
                )
                return text or ""

            log.debug("Research synthesis → Gemini (%s)", route.reason)
            from llm import chat_deep
            text, _ = await chat_deep(prompt)
            return text
        except (ImportError, OSError, ValueError, AttributeError, RuntimeError) as e:
            log.error("Synthesis failed: %s", e)
            return f"❌ Synthesis failed: {e}\n\nRaw data preview:\n{data[:500]}"


    async def generate_follow_ups(self, query: str, report: str) -> list[str]:
        """Generate 2-3 follow-up research questions based on the completed report."""
        prompt = (
            "Based on this research query and report, suggest exactly 3 concise follow-up "
            "research questions that would deepen understanding. Each should be a single "
            "line, actionable, and explore a different angle.\n\n"
            f"Original query: {query}\n\n"
            f"Report excerpt: {report[:2000]}\n\n"
            "Follow-up questions (one per line, no numbering or bullets):"
        )
        try:
            from llm.providers import COPILOT_PROXY_ENABLED, chat_openai
            if COPILOT_PROXY_ENABLED:
                text = await chat_openai(prompt, [], "You are a concise research assistant.")
            else:
                from llm import chat_deep
                text, _ = await chat_deep(prompt)
            lines = [
                ln.strip().lstrip("0123456789.-) ")
                for ln in (text or "").strip().split("\n")
                if ln.strip()
            ]
            return lines[:3]
        except (ImportError, OSError, ValueError, AttributeError, RuntimeError) as e:
            log.warning("Failed to generate follow-ups: %s", e)
            return []


async def run_scheduled_research(query: str, channel_id: str = "", deep: bool = False) -> str:
    """Run a research query autonomously. Schedulable skill.

    Called by the scheduler for recurring research tasks (e.g. weekly
    house-listing checks, monthly security-update sweeps).  Works without
    Discord — ``channel_id`` is accepted for metadata only.
    Set ``deep=True`` for iterative multi-pass research.
    """
    agent = ResearchAgent()
    result = await agent.run(query, on_progress=None, deep=deep)

    # Annotate with prior-research diff note when a previous report exists
    try:
        import vector_store

        previous = await vector_store.search(
            vector_store.RESEARCH_COLLECTION,
            query,
            top_k=1,
            threshold=0.85,
        )
        if previous:
            added_at = previous[0].get("metadata", {}).get("added_at", "unknown")
            result += (
                f"\n\n---\n📊 *This is a recurring research update. "
                f"Previous report was {added_at}.*"
            )
    except (ImportError, OSError, AttributeError, ValueError) as exc:
        log.debug("Prior-research annotation skipped: %s", exc)

    return result


def _extract_urls(text: str) -> list[str]:
    """Extract http(s) URLs from search result text."""
    import re
    return re.findall(r"https?://[^\s\)\]>\"']+", text)
