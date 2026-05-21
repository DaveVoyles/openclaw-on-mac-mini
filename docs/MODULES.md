# OpenClaw — Module Index

<!-- Updated: 2026-05-21 -->

A flat index of what lives where in `src/` and `skills/`. For "how do I add X", see [`AGENT-EXTENSION-GUIDE.md`](AGENT-EXTENSION-GUIDE.md). For "how does it fit together", see [`ARCHITECTURE.md`](ARCHITECTURE.md).

> **Heads up (May 2026):** Discord, browser dashboard, `src/cogs/`, `src/discord_commands/`, `src/dashboard/`, and `src/api/` were removed. The historical Discord-era inventory was replaced by this Slack-only version.

---

## Entrypoint

| File | Purpose |
| --- | --- |
| `src/slack_bot.py` | Slack Bolt app, `/health` aiohttp server, scheduler, all slash-command handlers. The thing the container runs. |

## Packages

| Package | Contents |
| --- | --- |
| `src/llm/` | LLM orchestration: `chat.py` (driver), `providers.py` (Gemini/Copilot/OpenAI/Ollama + circuit breaker) |
| `src/builders/` | Embed/block-kit/markdown builders shared across responses |
| `src/utils/` | Generic helpers (formatting, dates, retries, hash, throttling) |
| `src/plugin_system/` | Plugin loader for `plugins/` directory |

## Core modules (alphabetical, by responsibility)

| Module | Responsibility |
| --- | --- |
| `agent_loop.py` | Autonomous worker-agent loop spawned by `spawn_worker()` tool call |
| `agentmail.py` | Email-bridge skill (Gmail draft + send) |
| `analyzer.py` | Generic document analyzer (PDF/text/image → summary) |
| `answer_policy.py` | Routing decision policy for `chat()` |
| `approval_models.py` · `approval_store.py` | Approval workflow (high-risk skills require user confirmation) |
| `ask_executor.py` | Single-shot agent-ask flow (`/chat`, `@mention`); preserved across the dashboard removal |
| `ask_orchestrator.py` | Tool-call loop: model → tool → model until model stops requesting tools |
| `audit.py` | Append-only audit log under `data/audit/` |
| `bot_formatting.py` | Slack message formatting helpers (block kit, code blocks, mrkdwn) |
| `calendar_skills.py` | Google Calendar integration |
| `channel_profile_state.py` · `channel_profiles.py` | Per-channel behavior profile state |
| `code_sandbox.py` | Sandboxed Python execution (used by code-eval skills) |
| `config.py` · `constants.py` | Settings + tunables |
| `cooldowns.py` | Per-user/per-skill rate limiting |
| `decision_workflows.py` | Multi-step decision workflows (approval chains) |
| `dropbox_sync.py` | `/dropbox` slash command + background watch loop |
| `gateway.py` | Outbound HTTP with caching, retry, and gateway-level rate limits |
| `host_bridge.py` | **Host-side** process that spawns `gh copilot` CLI sessions on the Mac mini; talks to the container via named pipes |
| `llm_tools.py` | Tool-definition shims that map tool names to `SKILLS[...]` callables |
| `nas.py` | Synology NAS file operations and backup status |
| `quality_helpers.py` | Quality-eval scoring helpers |
| `research_agent.py` | Multi-step web research agent |
| `vector_store_client.py` · `vector_store_memory.py` | ChromaDB long-term memory |
| `openclaw_cli*.py` (~12 files) | Local CLI surface (separate from the container; runs on the host directly) |

## Skills package (`skills/`)

| Module | Domain |
| --- | --- |
| `skills/__init__.py` | Central `SKILLS` dict — 182 entries — assembled by importing all sibling modules |
| `advanced_skills.py` | Multi-step planning, summarization, rewriting |
| `browser_skills.py` | Headless-browser fetch (Playwright) |
| `digest_skills.py` | Daily digest formatting |
| `finance_skills.py` | Alpha Vantage + market data |
| `health_skills.py` | Container/system health checks (Docker, NAS, /health probes) |
| `media_skills.py` | Plex/Sonarr/Radarr operations |
| `news_skills.py` | NewsAPI + summarization |
| `ocr_skill.py` | Image → text (Tesseract) |
| `patreon_skills.py` | Patreon monitoring |
| `reporting_skills.py` | Status/uptime reports |
| `+27 more` | Domain-specific skill modules — grep for the tool name in `skills/` to find the owning file |

## Background loops

Started by `src/slack_bot.py` at boot. Defined in `slack_bot.py` itself (search for `asyncio.create_task`).

| Loop | Purpose |
| --- | --- |
| `_proactive_file_alert_loop` | Watch Slack file uploads, DM user when summarizable |
| `_dropbox_watch_loop` | Poll Dropbox watch folder, alert on new files |
| `_digest_loop` | Hourly digest scheduler |
| `_health_loop` | Self-ping `/health` for liveness telemetry |

## Config

| File | Purpose |
| --- | --- |
| `config/tools.yaml` | LLM-facing tool manifest — the subset of `SKILLS` exposed to the model (113 entries vs. 182 internal skills) |
| `pyproject.toml` · `requirements.txt` | Python deps |
| `pytest.ini` · `conftest.py` | Test config |

## Tests

`tests/` mirrors `src/` and `skills/`. Naming convention: `test_<module>.py` or `test_<module>_unit.py`. Run with `pytest`.

Pre-existing failures: ~24 as of May 2026 — baseline before any change.
