---
name: free-web-search
description: >
  Real-time web search skill for Claude Code / OpenClaw agents. Use this skill
  whenever you need to look up current information, recent events, documentation,
  library versions, error messages, or anything that may have changed since your
  training cutoff. Triggers on: "search for", "look up", "find info about",
  "what is the latest", "check online", "google", "browse", or any task where
  up-to-date external knowledge would meaningfully improve the answer. Always
  prefer this skill over guessing when the information might be stale.
version: 9.3.0
tags: [search, web, duckduckgo, bing, realtime, fast, lightweight, json]
author: ucsdzehualiu
dependencies:
  - httpx>=0.27.0
  - beautifulsoup4>=4.12.3
  - lxml>=5.0.0
runtime: python3.10
setup: |
  pip install httpx beautifulsoup4 lxml
---

# Web Search Skill (v9.3.0)

Lightweight, fast web search for Claude Code / OpenClaw agents.
Uses `httpx` (no browser required) with DuckDuckGo → Bing fallback.

## When to Use

- Looking up current docs, API changes, library releases
- Researching error messages or Stack Overflow solutions
- Fact-checking anything time-sensitive
- Competitor/technology landscape research

## Quick Start

```bash
# Basic search — returns structured text
python scripts/web_search.py "your query here"

# JSON output (recommended for agent pipelines)
python scripts/web_search.py "your query here" --json

# Force recency boost + year injection
python scripts/web_search.py "your query here" --recent --json

# Control how many pages to fetch (default: 5, max: 10)
python scripts/web_search.py "your query here" --pages 3
```

## Output Format (JSON)

```json
{
  "query": "original query",
  "engine": "duckduckgo",
  "total_results": 12,
  "fetched_pages": 3,
  "results": [
    {
      "rank": 1,
      "title": "Page Title",
      "url": "https://...",
      "snippet": "Search engine snippet",
      "text": "Extracted page content (up to 1000 chars)"
    }
  ]
}
```

## Query Writing Tips (for agents)

| Goal | Good Query |
|------|-----------|
| Library version | `httpx latest version 2024` |
| Error fix | `python asyncio RuntimeError exact error message` |
| API docs | `openai chat completions API parameters` |
| News | `rust 2024 edition features` |

- **Be specific** — include version numbers, language, framework
- **Add year** for time-sensitive topics
- **Use error text verbatim** for debugging queries

## Workflow for Agents

1. **Analyze the task** — identify what external knowledge is needed
2. **Generate a focused query** — extract keywords, add context
3. **Run search** — prefer `--json` for structured parsing
4. **Parse results** — use `results[].text` for content, `results[].url` for citations
5. **Synthesize** — combine findings to answer the original task

## Error Handling

If both engines fail, the script returns:
```json
{"error": "No results found for query: ...", "query": "..."}
```

Check `stderr` for per-step debug logs.