# OpenClaw — Services & Integrations Reference

This document is the authoritative reference for every external service, API, and tool used in this project.
Agents working on this codebase should read this file to understand what is available, why it exists, and how to configure it.

---

## AI & Language Models

| Service | Link | Description | Why We Use It | Env Var(s) |
|---------|------|-------------|---------------|------------|
| **Google Gemini** | https://ai.google.dev | Google's flagship LLM (gemini-2.5-flash) | Primary LLM for all tool-calling, multi-step reasoning, and complex queries. Supports "Real Estate Investigator" and "Location Specialist" personas. | `GOOGLE_API_KEY` |
| **Ollama (Local)** | https://ollama.com | Self-hosted LLM runtime (gemma3:12b) | Low-latency local model for simple conversational turns; keeps costs near zero for lightweight tasks. Runs on M4 Neural Engine (~15–20 tok/s, 8.1 GB). Upgraded from llama3.2:3b. | `OLLAMA_URL`, `OLLAMA_MODEL` |
| **OpenAI** | https://platform.openai.com | GPT-family models | Optional fallback LLM if Gemini is unavailable | `OPENAI_API_KEY` *(optional)* |
| **Anthropic Claude** | https://www.anthropic.com | Claude-family models | Optional fallback LLM | `ANTHROPIC_API_KEY` *(optional)* |

---

## API Gateway & Managed OAuth

| Service | Link | Description | Why We Use It | Env Var(s) |
|---------|------|-------------|---------------|------------|
| **Maton API Gateway** | https://maton.ai | Managed OAuth proxy for 100+ third-party APIs (Slack, GitHub, Notion, HubSpot, Stripe, Google Workspace, etc.) | Eliminates per-service OAuth setup; single API key grants access to dozens of connected apps. Gateway URL: `https://gateway.maton.ai/{app}/{path}`. Manage connections at https://ctrl.maton.ai/connections. Rate limit: 10 req/s | `MATON_API_KEY` |

---

## Communication & Messaging

| Service | Link | Description | Why We Use It | Env Var(s) |
|---------|------|-------------|---------------|------------|
| **Discord** | https://discord.com/developers | Bot API for slash commands, buttons, embeds, approval flows | Primary user interface for OpenClaw — all commands and notifications flow through Discord | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `ALLOWED_USER_IDS` |
| **Gmail** | https://mail.google.com | SMTP/IMAP email via Google App Password | Read and send personal email without managing full OAuth for email | `GMAIL_USER`, `GMAIL_APP_PASSWORD` |
| **Outlook / Microsoft 365** | https://outlook.com | SMTP/IMAP email via App Password | Read and send Outlook email accounts | `OUTLOOK_USER`, `OUTLOOK_APP_PASSWORD` |
| **AgentMail** | https://agentmail.to | API-driven transactional email for AI agents | Programmatic email sending purpose-built for agent workflows | `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX` |

---

## Search & Web Tools

| Service | Link | Description | Why We Use It | Env Var(s) |
|---------|------|-------------|---------------|------------|
| **Tavily Search** | https://tavily.com | AI-optimized web search with semantic answer extraction | Best-in-class search results for agent queries; surfaces direct answers, not just links. Used for real estate listing research and local property tax analysis. | `TAVILY_API_KEY` |
| **DuckDuckGo (Lite)** | https://lite.duckduckgo.com | Privacy-focused web search, no API key required | Free fallback web search with no rate limits or registration | *(none)* |
| **Bing Search** | https://www.bing.com | General web search | Second fallback when DuckDuckGo fails | *(none)* |
| **wttr.in** | https://wttr.in | Free weather API (JSON format, no key) | Current conditions + 3-day forecast; used by `/weather` command and `get_weather` skill | `WEATHER_DEFAULT_LOCATION` *(optional)* |

---

## Media Stack

| Service | Link | Description | Why We Use It | Env Var(s) | Default Port |
|---------|------|-------------|---------------|------------|:---:|
| **Sonarr** | https://sonarr.tv | TV show collection manager | Automate TV show downloads, tracking, and library management | `SONARR_URL`, `SONARR_API_KEY` | 8989 |
| **Radarr** | https://radarr.video | Movie collection manager | Automate movie downloads and library management | `RADARR_URL`, `RADARR_API_KEY` | 7878 |
| **Lidarr** | https://lidarr.audio | Music collection manager | Automate music album downloads and library management | `LIDARR_URL`, `LIDARR_API_KEY` | 8686 |
| **Prowlarr** | https://prowlarr.com | Indexer manager for \*arr apps | Single place to manage all torrent/usenet indexers across the \*arr stack | `PROWLARR_URL`, `PROWLARR_API_KEY` | 9696 |
| **SABnzbd** | https://sabnzbd.org | Usenet (NZB) download client | Handles all usenet downloads; integrates with \*arr stack | `SABNZBD_URL`, `SABNZBD_API_KEY` | 8775 |
| **qBittorrent** | https://www.qbittorrent.org | BitTorrent download client | Handles torrent downloads for the \*arr stack | `QBIT_URL` | 8080 |
| **Plex Media Server** | https://www.plex.tv | Media streaming server | Serves movies, TV, music to all devices; monitored via Tautulli | *(via Tautulli)* | — |
| **Tautulli** | https://tautulli.com | Plex analytics and monitoring | Usage stats, recently-added items, server health for Plex | `TAUTULLI_URL`, `TAUTULLI_API_KEY` | 8181 |
| **Overseerr** | https://overseerr.dev | Media request management | Users submit and admins approve media requests; integrates with Sonarr/Radarr | `OVERSEERR_URL`, `OVERSEERR_API_KEY` | 5055 |

---

## Infrastructure & Monitoring

| Service | Link | Description | Why We Use It | Env Var(s) / Notes |
|---------|------|-------------|---------------|------------|
| **Docker Engine** | https://docs.docker.com/engine | Container runtime | All services run as Docker containers; OpenClaw manages them via the Docker socket | Unix socket: `/var/run/docker.sock` |
| **Glances** | https://nicolargo.github.io/glances | System stats REST API (CPU, memory, disk) | Lightweight system monitoring without standing up a full Prometheus stack on the host | `GLANCES_URL` (default port 61208) |
| **Prometheus** | https://prometheus.io | Metrics scraping & time-series database | Exposes `/metrics` endpoint at `:8765`; key metrics: `openclaw_up`, `openclaw_uptime_seconds`, `openclaw_latency_ms` | Endpoint: `http://host:8765/metrics` |
| **Uptime Kuma** | https://uptime.kuma.pet | Self-hosted uptime monitoring & status page | Polls the `/health` and `/metrics` endpoints every 60 s and alerts on downtime | Monitors: `:8765/health`, `:8765/metrics` |

---

## NAS & Storage

| Service | Link | Description | Why We Use It | Env Var(s) |
|---------|------|-------------|---------------|------------|
| **Synology DSM** | https://www.synology.com | Synology NAS operating system with REST API | Primary network storage; exposes volume stats, SMART health, Hyper Backup status, and system alerts | `NAS_URL`, `NAS_USER`, `NAS_PASSWORD`, `NAS_VERIFY_SSL` |
| **Traefik** | https://traefik.io | Reverse proxy & TLS termination | Routes external HTTPS traffic into internal services on the NAS; handles Let's Encrypt certs | Lives on NAS (ports 80/443) |
| **Synology DDNS** | https://www.synology.com/en-global/dsm/feature/ddns | Dynamic DNS for remote access | Provides a stable hostname (`davevoyles.synology.me`) when WAN IP changes | *(Synology account)* |

---

## Cloud & VPN

| Service | Link | Description | Why We Use It | Env Var(s) |
|---------|------|-------------|---------------|------------|
| **Tailscale** | https://tailscale.com | WireGuard-based mesh VPN | Zero-config secure remote access to all home-lab devices without opening firewall ports | Tailscale binary (auto-detected) |
| **Google Calendar** | https://developers.google.com/calendar | Google Calendar REST API | Read and create calendar events from agent commands | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN` |
| **Google OAuth2** | https://console.cloud.google.com | Google OAuth consent & token management | Provides tokens for Calendar and Gmail access; managed via `scripts/google_oauth_setup.py` | Same as above |
| **Cloudflare** | https://speed.cloudflare.com | Speed test endpoint | Measures WAN download throughput via `/__down?bytes=10000000` | *(public URL, no key)* |

---

## ClawHub Skill Bundles Installed

These are discrete skill packages installed under `skills/<slug>/`. Some are invoked via subprocess, while others are local instruction bundles that guide agent behavior or future integrations. See `copilot-instructions.md` for the integration pattern.

| Slug | Version | Script | Purpose | API Key |
|------|---------|--------|---------|---------|
| `free-web-search` | 9.3.0 | `scripts/web_search.py` | DuckDuckGo + Bing web search; no key required | *(none)* |
| `openclaw-tavily-search` | 0.1.0 | `scripts/tavily_search.py` | Tavily AI web search with semantic answer extraction | `TAVILY_API_KEY` |
| `mission-control` | latest | `scripts/mc-update.sh` | Kanban task board with GitHub Pages dashboard. Tasks stored in `data/tasks.json`; dashboard at https://davevoyles.github.io/openclaw-dashboard/ | *(none — GitHub token only needed for dashboard UI sync)* |
| `ontology` | 1.0.4 | `scripts/ontology.py` | Local typed knowledge graph for structured memory, entity linking, dependency tracking, and cross-skill state sharing | *(none)* |
| `self-improving` | 1.2.16 | `SKILL.md` + local memory templates | Local self-reflection, correction logging, and tiered memory patterns for compounding agent behavior | *(none)* |
| `skill-vetter` | 1.0.0 | `SKILL.md` | Security checklist for evaluating future ClawHub skills before installation | *(none)* |

---

## Cost & Budget Tracking

| Item | Notes |
|------|-------|
| **Gemini API pricing** | Input $0.10/1M tokens, output $0.40/1M tokens (configurable via `GEMINI_PRICE_INPUT_PER_M` / `GEMINI_PRICE_OUTPUT_PER_M`) |
| **Monthly budget cap** | `GEMINI_BUDGET_LIMIT` (default: $30.00); tracked in `spending.py` and `data/memory/spending.json` |
| **Tavily** | Paid API; usage billed per search request. Use DuckDuckGo skill for high-volume or unpaid searches |
| **Maton** | Paid API gateway; billed per connected app / request volume. Check https://maton.ai for current pricing |

---

## Quick Reference — Key Environment Variables

```
# AI
GOOGLE_API_KEY
OLLAMA_URL / OLLAMA_MODEL
OPENAI_API_KEY         (optional)
ANTHROPIC_API_KEY      (optional)

# Bot & Auth
DISCORD_BOT_TOKEN
DISCORD_GUILD_ID
ALLOWED_USER_IDS

# API Gateway
MATON_API_KEY

# Search & Weather
TAVILY_API_KEY
WEATHER_DEFAULT_LOCATION     # default city for /weather (e.g. "Philadelphia")

# Proactive Notifications
ALERT_CHANNEL_ID             # Discord channel ID for morning briefings (optional)

# Research / Deep Reasoning
THINKING_MODEL               # Gemini model for /research synthesis (default: gemini-2.5-flash)
THINKING_BUDGET              # Thinking token budget (used when google-genai SDK is installed)

# Email
GMAIL_USER / GMAIL_APP_PASSWORD
OUTLOOK_USER / OUTLOOK_APP_PASSWORD
AGENTMAIL_API_KEY / AGENTMAIL_INBOX

# Google OAuth
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REFRESH_TOKEN

# Media Stack
SONARR_URL / SONARR_API_KEY
RADARR_URL / RADARR_API_KEY
LIDARR_URL / LIDARR_API_KEY
PROWLARR_URL / PROWLARR_API_KEY
SABNZBD_URL / SABNZBD_API_KEY
QBIT_URL
TAUTULLI_URL / TAUTULLI_API_KEY
OVERSEERR_URL / OVERSEERR_API_KEY

# NAS
NAS_URL / NAS_USER / NAS_PASSWORD / NAS_VERIFY_SSL

# Monitoring
GLANCES_URL

# Mission Control
MC_TASKS_FILE          # path to tasks.json inside container (default /app/data/tasks.json)

# Budget
GEMINI_PRICE_INPUT_PER_M
GEMINI_PRICE_OUTPUT_PER_M
GEMINI_BUDGET_LIMIT
```
