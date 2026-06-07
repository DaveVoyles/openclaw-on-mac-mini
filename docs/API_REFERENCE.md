# API Reference
<!-- Updated: 2026-04-18 -->


Complete reference for all external APIs used in OpenClaw.

---

## Table of Contents

1. [News & Data APIs](#news--data-apis)
2. [Search APIs](#search-apis)
3. [Media APIs](#media-apis)
4. [Infrastructure APIs](#infrastructure-apis)
5. [Productivity APIs](#productivity-apis)
6. [Other Services](#other-services)
7. [API Health Monitoring](#api-health-monitoring)

---

## News & Data APIs

### NewsAPI.org

- **Purpose:** News aggregation from 80,000+ sources worldwide
- **Endpoint:** `https://newsapi.org/v2`
- **Authentication:** API key via query parameter (`apiKey=xxx`)
- **Rate Limits:** 
  - Free tier: 100 requests/day
  - Development tier only (no commercial use)
- **Cost:** 
  - Free: 100 req/day
  - Business: $449/mo (250,000 req/mo)
  - Mega: $1,799/mo (1,000,000 req/mo)
- **Skills Using It:**
  - `search_news()` in `skills/news_skills.py`
  - `get_top_headlines()` in `skills/news_skills.py`
  - `get_news_sources()` in `skills/news_skills.py`
- **Configuration:**
  ```bash
  NEWSAPI_KEY=your_key_here
  ```
- **Status:** ✅ Active
- **Documentation:** https://newsapi.org/docs

**Example Response:**
```json
{
  "status": "ok",
  "totalResults": 123,
  "articles": [
    {
      "source": {"name": "CNN"},
      "author": "John Doe",
      "title": "Breaking News",
      "description": "...",
      "url": "https://...",
      "publishedAt": "2024-01-15T10:30:00Z"
    }
  ]
}
```

---

### API-Sports

- **Purpose:** Live sports scores, schedules, statistics (NBA, NFL, MLB, NHL, Soccer, etc.)
- **Endpoint:** `https://v1.basketball.api-sports.io` (NBA endpoint)
  - NFL: `https://v1.american-football.api-sports.io`
  - MLB: `https://v1.baseball.api-sports.io`
  - NHL: `https://v1.hockey.api-sports.io`
- **Authentication:** API key via header (`x-apisports-key`)
- **Rate Limits:**
  - Free tier: 100 requests/day (shared across all sports)
  - Note: One request consumes one credit regardless of endpoint
- **Cost:**
  - Free: 100 req/day
  - Basic: $10/mo (1,000 req/day)
  - Pro: $35/mo (5,000 req/day)
- **Skills Using It:**
  - `get_nba_scores()` in `skills/sports_skills.py`
  - `get_nba_standings()` in `skills/sports_skills.py`
  - `get_nba_team_stats()` in `skills/sports_skills.py`
- **Configuration:**
  ```bash
  APISPORTS_KEY=your_key_here
  ```
- **Status:** ✅ Active (NBA only; other sports planned)
- **Documentation:** https://api-sports.io/documentation

**Example Request:**
```python
headers = {"x-apisports-key": APISPORTS_KEY}
params = {"date": "2024-01-15", "league": "12", "season": "2024-2025"}
url = "https://v1.basketball.api-sports.io/games"
```

---

### Alpha Vantage

- **Purpose:** Financial market data, stock quotes, sentiment analysis, technical indicators
- **Endpoint:** `https://www.alphavantage.co/query`
- **Authentication:** API key via query parameter (`apikey=xxx`)
- **Rate Limits:**
  - Free tier: 25 requests/day, 5 requests/minute
  - Very strict rate limiting
- **Cost:**
  - Free: 25 req/day
  - Premium: $49.99/mo (unlimited intraday, 120 calls/min)
  - Enterprise: Custom pricing
- **Skills Using It:**
  - `get_stock_info()` in `skills/finance_skills.py`
  - `get_market_news()` in `skills/finance_skills.py`
  - `get_sentiment_analysis()` in `skills/finance_skills.py`
- **Configuration:**
  ```bash
  ALPHAVANTAGE_KEY=your_key_here
  ```
- **Status:** ✅ Active
- **Documentation:** https://www.alphavantage.co/documentation/

**Supported Functions:**
- `GLOBAL_QUOTE` — real-time stock price
- `TIME_SERIES_DAILY` — historical daily prices
- `NEWS_SENTIMENT` — market news with sentiment scores
- `OVERVIEW` — company fundamentals

---

### OpenWeatherMap

- **Purpose:** Weather data, forecasts, and air quality information
- **Endpoint:** `https://api.openweathermap.org/data/2.5`
- **Authentication:** API key via query parameter (`appid=xxx`)
- **Rate Limits:**
  - Free tier: 1,000 calls/day, 60 calls/minute
  - Generous free tier for personal/small projects
- **Cost:**
  - Free: 1,000 req/day
  - Startup: $40/mo (100,000 calls/mo)
  - Developer: $125/mo (1,000,000 calls/mo)
  - Professional: $600+/mo (custom limits)
- **Skills Using It:**
  - `get_current_weather()` in `skills/weather_skills.py`
  - `get_forecast()` in `skills/weather_skills.py`
  - `get_air_quality()` in `skills/weather_skills.py`
- **Configuration:**
  ```bash
  OPENWEATHER_API_KEY=your_key_here
  ```
- **Status:** ✅ Active
- **Documentation:** https://openweathermap.org/api

**Features:**
- Current weather conditions (temperature, humidity, wind, pressure)
- 5-day forecast with 3-hour intervals (40 data points)
- Air quality index (AQI) and pollutant levels
- Supports city names, country codes, ZIP codes, and coordinates
- Multiple unit systems (metric, imperial, standard)
- Sunrise/sunset times

**Example Response (Current Weather):**
```json
{
  "status": "ok",
  "location": "Seattle",
  "country": "US",
  "temperature": 15.3,
  "feels_like": 14.2,
  "conditions": "Clouds",
  "description": "scattered clouds",
  "humidity": 72,
  "wind_speed": 5.2,
  "units": "metric"
}
```

---

## Search APIs

### Perplexity AI

- **Purpose:** AI-powered web search with citations and summarization
- **Endpoint:** `https://api.perplexity.ai`
- **Authentication:** Bearer token
- **Rate Limits:**
  - Free tier: Not publicly available (requires paid account)
  - Standard: 50 requests/day
  - Pro: Unlimited (fair use policy)
- **Cost:**
  - Standard: $20/mo
  - Pro: $200/mo
  - Enterprise: Custom
- **Skills Using It:**
  - `search_web()` in `skills/search_skills.py` (primary provider)
  - `/websearch` Slack command
  - `/research` autonomous research workflow
- **Configuration:**
  ```bash
  PERPLEXITY_API_KEY=pplx-xxxxx
  ```
- **Status:** ✅ Active (primary search provider)
- **Documentation:** https://docs.perplexity.ai

**Features:**
- AI-generated answers with inline citations
- Real-time web access
- Source ranking and relevance scoring
- Follow-up question suggestions

---

### Tavily

- **Purpose:** AI-powered search with structured extraction and summarization
- **Endpoint:** `https://api.tavily.com/search`
- **Authentication:** API key in request body
- **Rate Limits:**
  - Free tier: 1,000 requests/month
  - No daily limit, monthly cap only
- **Cost:**
  - Free: 1,000 req/mo
  - Basic: $29/mo (15,000 req/mo)
  - Professional: $149/mo (100,000 req/mo)
- **Skills Using It:**
  - `search_web()` in `skills/search_skills.py` (fallback after Perplexity)
  - `skills/openclaw-tavily-search/scripts/tavily_search.py` (standalone)
  - `/research` command
- **Configuration:**
  ```bash
  TAVILY_API_KEY=tvly-xxxxx
  ```
- **Status:** ✅ Active (secondary search provider)
- **Documentation:** https://docs.tavily.com

**Advantages:**
- Structured JSON responses
- Built-in content extraction
- Domain filtering
- Search depth control

---

### Firecrawl

- **Purpose:** Web scraping and search with built-in extraction
- **Endpoint:** `https://api.firecrawl.dev/v1`
- **Authentication:** Bearer token
- **Rate Limits:**
  - Free tier: 500 pages/month
  - Rate: 10 requests/minute
- **Cost:**
  - Free: 500 pages/mo
  - Starter: $25/mo (5,000 pages)
  - Standard: $75/mo (25,000 pages)
  - Growth: $250/mo (100,000 pages)
- **Skills Using It:**
  - `search_web()` in `skills/search_skills.py` (tertiary fallback)
  - `browse_url()` content extraction
- **Configuration:**
  ```bash
  FIRECRAWL_API_KEY=fc-xxxxx
  ```
- **Status:** ✅ Active (fallback search/scraping)
- **Documentation:** https://docs.firecrawl.dev

**Features:**
- Search + extract in one API call
- Automatic content cleaning
- Markdown conversion
- Screenshot capture

---

### Serper

- **Purpose:** Google Search API wrapper (SERP results)
- **Endpoint:** `https://google.serper.dev/search`
- **Authentication:** API key via header (`X-API-KEY`)
- **Rate Limits:**
  - $5 credit = 2,500 searches
  - No explicit rate limit, credit-based
- **Cost:**
  - Pay-as-you-go: $5/2,500 searches ($0.002 per search)
  - No free tier
- **Skills Using It:**
  - `search_web()` in `skills/search_skills.py` (available as fallback)
  - Not in cascade by default (paid only)
- **Configuration:**
  ```bash
  SERPER_API_KEY=xxxxx
  ```
- **Status:** ⚠️ Optional (requires prepaid credits)
- **Documentation:** https://serper.dev/docs

**Use Cases:**
- Direct Google search results
- Organic + paid results
- Related searches
- Knowledge graph data

---

## Media APIs

### OMDb API

- **Purpose:** Movie and TV metadata (IMDb data wrapper)
- **Endpoint:** `http://www.omdbapi.com/`
- **Authentication:** API key via query parameter (`apikey=xxx`)
- **Rate Limits:**
  - Free tier: 1,000 requests/day
- **Cost:**
  - Free: 1,000 req/day
  - Patron: $1/mo (unlimited)
- **Skills Using It:**
  - `/media movie` command in `src/cogs/imdb_cog.py`
  - `/media tv` command in `src/cogs/imdb_cog.py`
  - `/media search` command in `src/cogs/imdb_cog.py`
- **Configuration:**
  ```bash
  OMDB_API_KEY=xxxxx
  ```
- **Status:** ✅ Active
- **Documentation:** https://www.omdbapi.com/

**Data Provided:**
- Title, year, plot, cast, ratings
- Poster images
- IMDb ID
- Awards, box office, runtime

---

### TMDB (The Movie Database)

- **Purpose:** Comprehensive movie/TV metadata and images
- **Endpoint:** `https://api.themoviedb.org/3`
- **Authentication:** API key via query parameter or Bearer token
- **Rate Limits:**
  - Free tier: 40 requests/10 seconds
  - No daily limit
- **Cost:**
  - Free (with attribution)
  - Commercial use allowed
- **Skills Using It:**
  - `add_to_radarr()` in `skills/media_skills.py` (TMDB ID lookup)
  - Overseerr media requests (via TMDB IDs)
- **Configuration:**
  ```bash
  TMDB_API_KEY=xxxxx  # Optional - not currently in .env.example
  ```
- **Status:** 🟡 Indirectly used (via TMDB IDs in Radarr/Overseerr)
- **Documentation:** https://developers.themoviedb.org/3

---

### Overseerr

- **Purpose:** Media request management for Plex
- **Endpoint:** `http://192.168.1.93:5055/api/v1`
- **Authentication:** API key via header (`X-Api-Key`)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `request_media()` in `src/overseerr.py`
  - `/request` Slack command
  - Auto-approve system for media requests
- **Configuration:**
  ```bash
  OVERSEERR_URL=http://192.168.1.93:5055
  OVERSEERR_API_KEY=xxxxx
  ```
- **Status:** ✅ Active
- **Documentation:** https://api-docs.overseerr.dev/

**Key Endpoints:**
- `/api/v1/request` — submit media requests
- `/api/v1/request/{id}` — check request status
- `/api/v1/search` — search TMDB via Overseerr

---

### Sonarr

- **Purpose:** TV show automation (search, download, organize)
- **Endpoint:** `http://192.168.1.93:8989/api/v3`
- **Authentication:** API key via header (`X-Api-Key`)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `get_sonarr_series()` in `skills/media_skills.py`
  - `add_to_sonarr()` in `skills/media_skills.py`
  - `trigger_sonarr_search()` in `skills/media_skills.py`
  - `/search` command (TV shows)
- **Configuration:**
  ```bash
  SONARR_URL=http://192.168.1.93:8989
  SONARR_API_KEY=xxxxx
  ```
- **Status:** ✅ Active
- **Documentation:** https://sonarr.tv/docs/api/

---

### Radarr

- **Purpose:** Movie automation (search, download, organize)
- **Endpoint:** `http://192.168.1.93:7878/api/v3`
- **Authentication:** API key via header (`X-Api-Key`)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `get_radarr_movies()` in `skills/media_skills.py`
  - `add_to_radarr()` in `skills/media_skills.py`
  - `trigger_radarr_search()` in `skills/media_skills.py`
  - `/search` command (movies)
- **Configuration:**
  ```bash
  RADARR_URL=http://192.168.1.93:7878
  RADARR_API_KEY=xxxxx
  ```
- **Status:** ✅ Active
- **Documentation:** https://radarr.video/docs/api/

---

### Lidarr

- **Purpose:** Music automation (search, download, organize)
- **Endpoint:** `http://192.168.1.93:8686/api/v1`
- **Authentication:** API key via header (`X-Api-Key`)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `get_lidarr_artists()` in `skills/media_skills.py`
  - `add_to_lidarr()` in `skills/media_skills.py`
  - `/search` command (music)
- **Configuration:**
  ```bash
  LIDARR_URL=http://192.168.1.93:8686
  LIDARR_API_KEY=xxxxx
  ```
- **Status:** ✅ Active
- **Documentation:** https://lidarr.audio/docs/api/

---

### Plex / Tautulli

- **Purpose:** Media server monitoring and statistics
- **Endpoint:** `http://192.168.1.93:8181/api/v2` (Tautulli)
- **Authentication:** API key via query parameter (`apikey=xxx`)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `get_plex_activity()` in `skills/media_skills.py`
  - `get_recently_added()` in `skills/media_skills.py`
  - `/recent` command
  - `/watching` command
- **Configuration:**
  ```bash
  TAUTULLI_URL=http://192.168.1.93:8181
  TAUTULLI_API_KEY=xxxxx
  PLEX_PORT=32400  # Optional - for direct Plex API access
  ```
- **Status:** ✅ Active
- **Documentation:** https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference

**Key Functions:**
- `get_activity` — currently playing media
- `get_recently_added` — new media in library
- `get_library_media_info` — library statistics

---

## Infrastructure APIs

### Google Gemini

- **Purpose:** Primary cloud LLM for AI capabilities
- **Endpoint:** `https://generativelanguage.googleapis.com/v1beta`
- **Authentication:** API key via header (`x-goog-api-key`)
- **Rate Limits:**
  - Free tier: 15 RPM, 1,500 RPD, 1.5M TPM
  - Flash 2.0: 10 RPM, 1,000 RPD, 4M TPM
- **Cost:**
  - Free tier (Flash 2.0): $0.10 per 1M input tokens, $0.40 per 1M output tokens
  - Pro 2.0: $1.25 / $10 per 1M tokens
- **Skills Using It:**
  - All `/ask` queries requiring tool calling
  - `/research` deep analysis
  - `/analyze` log analysis
  - Image/document analysis
  - Worker agents (`spawn_worker()`)
- **Configuration:**
  ```bash
  GOOGLE_API_KEY=xxxxx
  LLM_MODEL=gemini-2.5-flash
  THINKING_MODEL=gemini-2.5-flash
  LLM_MAX_TOKENS=8192
  LLM_TEMPERATURE=0.7
  ```
- **Status:** ✅ Active (primary LLM)
- **Documentation:** https://ai.google.dev/docs

**Models Used:**
- `gemini-2.5-flash` — primary model (fast, tool calling)
- `gemini-2.5-flash-thinking` — extended reasoning mode

---

### Ollama (Local LLM)

- **Purpose:** Local LLM for unlimited conversational queries
- **Endpoint:** `http://host.docker.internal:11434` (from Docker)
- **Authentication:** None (local)
- **Rate Limits:** None (local inference)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - Simple `/ask` queries (hybrid routing)
  - Conversational responses without tool calling
  - Offline fallback mode
- **Configuration:**
  ```bash
  OLLAMA_URL=http://host.docker.internal:11434
  OLLAMA_MODEL=gemma4:e4b
  LOCAL_LLM_ENABLED=true
  DEFAULT_MODEL_PREFERENCE=auto  # auto | local | gemini | openai | anthropic | copilot
  ROUTING_PROFILE=copilot-first  # copilot-first | balanced | gemini-first | cost-saver
  ```
- **Status:** ✅ Active (hybrid routing)
- **Documentation:** https://ollama.ai/docs

**Model Details:**
- `gemma4:e4b` — 9.6 GB, multimodal (text/image/audio)
- Tool calling supported via native function calling
- Runs on Mac Mini M4 Pro (64GB RAM)

---

### Docker API

- **Purpose:** Container management and monitoring
- **Endpoint:** Unix socket `/var/run/docker.sock` (mounted into container)
- **Authentication:** None (socket access = full control)
- **Rate Limits:** None
- **Cost:** Free
- **Skills Using It:**
  - `/containers` command
  - `/status <service>` command
  - `/logs <service>` command
  - `/restart <service>` command
  - Container health monitoring
- **Configuration:**
  ```yaml
  # docker-compose.yml
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
  ```
- **Status:** ✅ Active
- **Documentation:** https://docs.docker.com/engine/api/

**Key Operations:**
- List containers
- Inspect container details
- Read container logs
- Restart containers
- Network inspection

---

### Glances

- **Purpose:** System performance monitoring (CPU, memory, disk, network)
- **Endpoint:** `http://host.docker.internal:61208/api/3`
- **Authentication:** None (local network)
- **Rate Limits:** None
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `/system` command
  - `/report` comprehensive report
  - Autonomous monitoring checks
- **Configuration:**
  ```bash
  GLANCES_URL=http://host.docker.internal:61208
  ```
- **Status:** ✅ Active
- **Documentation:** https://glances.readthedocs.io/en/latest/api.html

**Metrics Collected:**
- CPU usage per core
- Memory usage (total, available, percent)
- Disk I/O and space
- Network traffic
- Process counts

---

## Productivity APIs

### Google Calendar

- **Purpose:** Calendar event management
- **Endpoint:** `https://www.googleapis.com/calendar/v3`
- **Authentication:** OAuth 2.0 (refresh token)
- **Rate Limits:**
  - Free tier: 1,000,000 queries/day
  - 10 queries/second per user
- **Cost:** Free
- **Skills Using It:**
  - `/calendar` commands (planned)
  - Morning briefing integration
  - Event creation via `/ask`
- **Configuration:**
  ```bash
  GOOGLE_OAUTH_CLIENT_ID=xxxxx
  GOOGLE_OAUTH_CLIENT_SECRET=xxxxx
  GOOGLE_OAUTH_REFRESH_TOKEN=xxxxx
  ```
- **Status:** 🟡 Partially implemented
- **Documentation:** https://developers.google.com/calendar/api

---

### Gmail

- **Purpose:** Email reading, searching, and sending
- **Endpoint:** IMAP/SMTP (imap.gmail.com:993, smtp.gmail.com:587)
- **Authentication:** App password (2FA required)
- **Rate Limits:**
  - Sending: 500 emails/day (free), 2,000/day (Workspace)
  - IMAP: No published limit
- **Cost:** Free
- **Skills Using It:**
  - `check_inbox()` in `src/email_skills.py`
  - `search_emails()` in `src/email_skills.py`
  - `send_email_smtp()` in `src/email_skills.py`
  - Morning briefing inbox summary
- **Configuration:**
  ```bash
  GMAIL_USER=your.email@gmail.com
  GMAIL_APP_PASSWORD=xxxxx  # From https://myaccount.google.com/apppasswords
  ```
- **Status:** ✅ Active
- **Documentation:** https://support.google.com/mail/answer/7126229

---

### Outlook

- **Purpose:** Microsoft email integration (alternative to Gmail)
- **Endpoint:** IMAP/SMTP (outlook.office365.com)
- **Authentication:** App password
- **Rate Limits:** Similar to Gmail
- **Cost:** Free
- **Skills Using It:**
  - Email skills with `provider='outlook'` parameter
- **Configuration:**
  ```bash
  OUTLOOK_USER=your.email@outlook.com
  OUTLOOK_APP_PASSWORD=xxxxx
  ```
- **Status:** 🟡 Optional (not primary email)
- **Documentation:** https://support.microsoft.com/en-us/office/pop-imap-and-smtp-settings

---

### AgentMail

- **Purpose:** Programmatic email for AI agents (bot-to-bot communication)
- **Endpoint:** `https://api.agentmail.to/v0`
- **Authentication:** Bearer token
- **Rate Limits:** Not publicly documented
- **Cost:**
  - Free tier available
  - Paid tiers not publicly listed
- **Skills Using It:**
  - `/mail` command in `src/agentmail.py`
  - Agent-to-agent communication
- **Configuration:**
  ```bash
  AGENTMAIL_API_KEY=xxxxx
  AGENTMAIL_INBOX=your-inbox-id
  ```
- **Status:** ✅ Active
- **Documentation:** https://agentmail.to/docs

---

## Other Services

### Stable Diffusion

- **Purpose:** Local image generation
- **Endpoint:** `http://host.docker.internal:7861` (Automatic1111 WebUI API)
- **Authentication:** None (local)
- **Rate Limits:** None (hardware-limited)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `/generate-image` command (planned)
  - Image generation via `/ask`
- **Configuration:**
  ```bash
  SD_URL=http://host.docker.internal:7861
  SD_TIMEOUT=120  # Image gen can take time
  ```
- **Status:** 🟡 Configured but not actively used
- **Documentation:** https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API

---

### Ntfy

- **Purpose:** Push notifications to mobile devices
- **Endpoint:** `https://ntfy.sh` (or self-hosted)
- **Authentication:** Topic name acts as secret (public ntfy.sh)
- **Rate Limits:**
  - Public ntfy.sh: 250 messages/day/topic
  - Self-hosted: unlimited
- **Cost:**
  - Free (public ntfy.sh)
  - Self-hosted: free
- **Skills Using It:**
  - `/notify` command in `src/cogs/ntfy_cog.py`
  - Alert notifications
  - Morning briefing mobile push
- **Configuration:**
  ```bash
  NTFY_URL=https://ntfy.sh
  NTFY_TOPIC=openclaw-alerts  # Keep secret!
  NTFY_TOKEN=xxxxx  # Optional for self-hosted with auth
  ```
- **Status:** ✅ Active
- **Documentation:** https://docs.ntfy.sh/

**Security Note:** Topic name is the only authentication for public ntfy.sh. Use a long, random topic name.

---

### Uptime Kuma

- **Purpose:** Uptime monitoring and alerting
- **Endpoint:** `http://192.168.1.93:3001`
- **Authentication:** Web UI only (no API key needed for monitoring)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - Monitors OpenClaw `/health` endpoint
  - No direct integration (external monitoring)
- **Configuration:**
  - Monitor URL: `http://192.168.1.93:8765/health`
  - Check interval: 60 seconds
- **Status:** ✅ Active (external monitoring)
- **Documentation:** https://github.com/louislam/uptime-kuma

---

### AdGuard Home

- **Purpose:** Network-wide DNS filtering and ad blocking
- **Endpoint:** `http://192.168.1.8:3053/control`
- **Authentication:** Basic auth (username + password)
- **Rate Limits:** None (self-hosted)
- **Cost:** Free (self-hosted)
- **Skills Using It:**
  - `/network` DNS statistics
  - `/adguard` query stats (planned)
- **Configuration:**
  ```bash
  ADGUARD_URL=http://192.168.1.8:3053
  ADGUARD_USER=admin
  ADGUARD_PASSWORD=xxxxx
  ```
- **Status:** 🟡 Configured, minimal usage
- **Documentation:** https://github.com/AdguardTeam/AdGuardHome/wiki/API

---

## API Health Monitoring

OpenClaw includes automatic API health monitoring with circuit breakers.

### Health Check System

**Tool Health Tracking** (`src/tool_health.py`):
- Records success/failure for each API
- Calculates success rate over last 100 calls
- Opens circuit breaker after consecutive failures
- Auto-recovery after cooldown period

**Circuit Breaker States:**
- **Closed:** Normal operation
- **Open:** API disabled after failures (5-minute cooldown)
- **Half-Open:** Testing if API has recovered

### Monitored APIs

APIs with circuit breakers:
- NewsAPI (`newsapi`)
- Alpha Vantage (`alphavantage`)
- API-Sports (`apisports`)
- Synthesis skills (composite tracking)
- Perplexity (`perplexity`)
- Tavily (`tavily`)
- Firecrawl (`firecrawl`)

---

## Data Synthesis APIs

**NEW:** Multi-source intelligence combining APIs with LLM-powered insights.

### Synthesis Skills

OpenClaw now includes 4 data synthesis functions that combine multiple APIs:

#### 1. Company Report (`synthesize_company_report`)

**Combines:**
- Stock data (Alpha Vantage)
- Sentiment analysis (Alpha Vantage)
- Recent news (NewsAPI)
- LLM synthesis (Gemini)

**Usage:**
```python
from skills.synthesis_skills import synthesize_company_report

report = await synthesize_company_report("DIS")
print(report["synthesis"])
# "Disney stock rallied 5% as Moana 2 exceeded box office expectations..."
```

**API Calls:** 2 Alpha Vantage + 1 NewsAPI + 1 LLM

---

#### 2. Entertainment Report (`synthesize_entertainment_report`)

**Combines:**
- Entertainment stocks (Alpha Vantage)
- Sector sentiment (Alpha Vantage)
- Entertainment news (NewsAPI)
- Correlation detection (LLM)

**Usage:**
```python
report = await synthesize_entertainment_report("box office")
for studio, data in report["studios"].items():
    print(f"{studio}: ${data['price']} ({data['change_percent']})")
```

**API Calls:** 2-8 Alpha Vantage (depends on studio count) + 1 NewsAPI + 1 LLM

---

#### 3. Market Overview (`synthesize_market_overview`)

**Combines:**
- Business news (NewsAPI)
- Market news with sentiment (Alpha Vantage)
- Sector aggregation (computed)
- Market summary (LLM)

**Usage:**
```python
overview = await synthesize_market_overview()
print(overview["market_summary"])
for sector, sentiment in overview["sector_sentiment"].items():
    print(f"{sector}: {sentiment['label']} ({sentiment['score']})")
```

**API Calls:** 1 NewsAPI + 1 Alpha Vantage + 1 LLM

---

#### 4. Correlation Finder (`find_correlations`)

**Combines:**
- Company report data
- Pattern detection (stock-sentiment alignment/divergence)
- Causal analysis (LLM)

**Usage:**
```python
corr = await find_correlations("AAPL", entity_type="company")
for c in corr["correlations"]:
    print(f"{c['type']}: {c['description']} (confidence: {c['confidence']})")
```

**API Calls:** Uses company report (3 calls) + 1 LLM

---

### Synthesis Architecture

```
User Request → Parallel API Calls → Data Aggregation → LLM Synthesis → Response
     ↓              ↓                    ↓                  ↓             ↓
  DIS ticker    Stock Price         Combine data      Generate      Structured
                Sentiment           Handle errors     insights         JSON
                News articles       Detect patterns   2-3 sentences
```

**Key Features:**
- **Parallel API calls:** 3x faster than sequential
- **Circuit breakers:** Skip failing APIs gracefully
- **Caching:** 15-minute TTL reduces API calls
- **LLM fallback:** Basic synthesis if LLM unavailable
- **Error tracking:** `sources` vs `sources_failed` transparency

**Rate Limit Impact:**
- Company report: 3 API calls (2 Alpha Vantage + 1 NewsAPI)
- Daily capacity: ~8-12 company reports (limited by Alpha Vantage 25/day)
- Cache extends capacity: Repeated queries within 15min use 0 API calls

**Documentation:**
- Architecture: See [DATA_SYNTHESIS.md](./DATA_SYNTHESIS.md)
- Skills reference: `skills/synthesis_skills.py`
- Tests: `tests/test_synthesis_skills.py`

---

### Health Endpoint
- API-Sports (`apisports`)
- Alpha Vantage (`alphavantage`)
- Perplexity (`perplexity`)
- Tavily (`tavily`)
- Firecrawl (`firecrawl`)

### Health Endpoint

Check API health status:
```bash
curl http://192.168.1.93:8765/health
```

**Response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 86400,
  "tools": {
    "newsapi": {"success_rate": 0.95, "total_calls": 42},
    "gemini": {"success_rate": 0.99, "total_calls": 1234}
  }
}
```

---

## Rate Limit Summary

| API | Free Tier Limit | Type |
|-----|----------------|------|
| **NewsAPI** | 100/day | Daily cap |
| **API-Sports** | 100/day | Daily cap |
| **Alpha Vantage** | 25/day, 5/min | Daily + minute |
| **Perplexity** | No free tier | Paid only |
| **Tavily** | 1,000/month | Monthly cap |
| **Firecrawl** | 500 pages/month | Monthly cap |
| **Serper** | Credit-based | Pay per use |
| **OMDb** | 1,000/day | Daily cap |
| **TMDB** | 40/10s | Burst limit |
| **Gemini** | 15 RPM, 1,500 RPD | Minute + daily |
| **Gmail** | 500 sends/day | Daily cap |

---

## Getting Help

- **Setup Issues:** See [API_SETUP.md](./API_SETUP.md)
- **Cost Planning:** See [API_COSTS.md](./API_COSTS.md)
- **Troubleshooting:** See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
- **GitHub Issues:** https://github.com/davevoyles/openclaw/issues
