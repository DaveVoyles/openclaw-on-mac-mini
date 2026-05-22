# API Setup Guide
<!-- Updated: 2026-04-18 -->


Step-by-step instructions for configuring all external APIs used in OpenClaw.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start (Essential APIs)](#quick-start-essential-apis)
3. [News & Data APIs](#news--data-apis)
4. [Search APIs](#search-apis)
5. [Media APIs](#media-apis)
6. [Infrastructure APIs](#infrastructure-apis)
7. [Productivity APIs](#productivity-apis)
8. [Other Services](#other-services)
9. [Feature Configuration (W9–W13)](#feature-configuration-w9w13)
10. [Verification](#verification)
11. [Troubleshooting](#troubleshooting)

---

## Prerequisites

**Before you begin:**

For the full first-time deployment path, including local vs production compose guidance, verification, and rollback basics, see the [Deployment & Environment Guide](DEPLOYMENT.md).

1. **Clone the repository:**
   ```bash
   git clone https://github.com/davevoyles/openclaw.git
   cd openclaw
   ```

2. **Copy environment file:**
   ```bash
   cp .env.example .env
   ```

3. **Required tools:**
   - Docker Desktop (for containerized deployment)
   - Text editor for `.env` file
   - Web browser for API registration

**Security Note:** Never commit `.env` to Git. It's already in `.gitignore`.

---

## Quick Start (Essential APIs)

**Minimum setup to get OpenClaw running:**

### 1. Slack App (Required)

**Slack is the primary interface for OpenClaw.** Follow the [Slack Setup Guide](SLACK-SETUP.md) for full OAuth, socket mode, and slash command registration instructions.

**Minimum `.env` settings:**
```bash
SLACK_BOT_TOKEN=xoxb-your_bot_token
SLACK_APP_TOKEN=xapp-your_app_token
ALLOWED_USER_IDS=your_slack_user_id
ALERT_CHANNEL_ID=C0XXXXXXXXX
```

**Get Slack IDs:**
- Your User ID: Open Slack → click your avatar → Profile → copy Member ID
- Channel ID: Right-click any channel → View channel details → copy from URL

---

### 2. Google Gemini (Required for AI features)

**Get API key:**
1. Go to https://aistudio.google.com/apikey
2. Click **Get API Key** → **Create API key**
3. Copy the key (starts with `AIza...`)

**Configure `.env`:**
```bash
GOOGLE_API_KEY=AIzaSy...your_key_here
LLM_MODEL=gemini-2.5-flash
ROUTING_PROFILE=copilot-first
LLM_MAX_TOKENS=8192
LLM_TEMPERATURE=0.7
```

`ROUTING_PROFILE` controls auto-routing for non-tool asks. Supported values: `copilot-first`, `balanced`, `gemini-first`, `cost-saver`.

**Free tier limits:**
- 15 requests/minute
- 1,500 requests/day
- 1.5M tokens/month

---

### 3. Basic Testing

**Start OpenClaw:**
```bash
docker compose up -d
docker compose logs -f openclaw
```

**Test in Slack:**
```
/ping
/about
/ask What's the weather like?
```

**If working, continue with optional APIs below.**

---

## News & Data APIs

### NewsAPI.org

**Purpose:** News aggregation from 80,000+ sources

**Setup:**
1. Go to https://newsapi.org/register
2. Enter email → Verify email
3. Copy API key from dashboard

**Configure `.env`:**
```bash
NEWSAPI_KEY=your_key_here
```

**Test:**
```bash
curl "https://newsapi.org/v2/top-headlines?country=us&apiKey=YOUR_KEY"
```

**In Slack:**
```
/ask Show me today's top tech news
```

**Free tier:** 100 requests/day (development only)

---

### API-Sports

**Purpose:** Live sports scores and statistics

**Setup:**
1. Go to https://api-sports.io/register
2. Create account → Verify email
3. Go to **Dashboard** → **My Subscriptions**
4. Copy API key

**Configure `.env`:**
```bash
APISPORTS_KEY=your_key_here
```

**Test:**
```bash
curl -H "x-apisports-key: YOUR_KEY" \
  "https://v1.basketball.api-sports.io/games?date=2024-01-15&league=12&season=2024-2025"
```

**In Slack:**
```
/ask NBA scores for today
```

**Free tier:** 100 requests/day (all sports combined)

---

### Alpha Vantage

**Purpose:** Financial market data and stock quotes

**Setup:**
1. Go to https://www.alphavantage.co/support/#api-key
2. Enter email → Click **GET FREE API KEY**
3. Copy API key from confirmation page

**Configure `.env`:**
```bash
ALPHAVANTAGE_KEY=your_key_here
```

**Test:**
```bash
curl "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=DIS&apikey=YOUR_KEY"
```

**In Slack:**
```
/ask What's the current Disney stock price?
```

**Free tier:** 25 requests/day, 5 requests/minute

---

## Search APIs

### Perplexity AI

**Purpose:** AI-powered search with citations

**Setup:**
1. Go to https://www.perplexity.ai/settings/api
2. Sign in or create account
3. Click **Generate API Key**
4. Copy key (starts with `pplx-...`)

**Configure `.env`:**
```bash
PERPLEXITY_API_KEY=pplx-your_key_here
```

**Test:**
```bash
curl -X POST https://api.perplexity.ai/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-sonar-small-128k-online","messages":[{"role":"user","content":"What is the capital of France?"}]}'
```

**In Slack:**
```
/websearch latest developments in AI
```

**Note:** No free tier. Requires paid subscription ($20/mo Standard, $200/mo Pro).

---

### Tavily

**Purpose:** AI search with structured extraction

**Setup:**
1. Go to https://tavily.com
2. Click **Get API Key** → Sign up
3. Verify email → Go to dashboard
4. Copy API key

**Configure `.env`:**
```bash
TAVILY_API_KEY=tvly-your_key_here
```

**Test:**
```bash
curl -X POST https://api.tavily.com/search \
  -H "Content-Type: application/json" \
  -d '{"api_key":"YOUR_KEY","query":"artificial intelligence news","search_depth":"basic"}'
```

**In Slack:**
```
/websearch climate change latest research
```

**Free tier:** 1,000 requests/month

---

### Firecrawl

**Purpose:** Web scraping and content extraction

**Setup:**
1. Go to https://firecrawl.dev
2. Click **Get Started** → Sign up
3. Verify email → Dashboard
4. Click **API Keys** → Copy key

**Configure `.env`:**
```bash
FIRECRAWL_API_KEY=fc-your_key_here
```

**Test:**
```bash
curl -X POST https://api.firecrawl.dev/v1/scrape \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

**In Slack:**
```
/browse https://news.ycombinator.com
```

**Free tier:** 500 pages/month

---

### Serper (Optional)

**Purpose:** Google SERP API

**Setup:**
1. Go to https://serper.dev
2. Sign up → Verify email
3. Dashboard → **API Key** → Copy
4. Add credits: **Billing** → Add $5

**Configure `.env`:**
```bash
SERPER_API_KEY=your_key_here
```

**Test:**
```bash
curl -X POST https://google.serper.dev/search \
  -H "X-API-KEY: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"q":"openai"}'
```

**Cost:** $0.002 per search ($5 = 2,500 searches)

---

## Media APIs

### OMDb API

**Purpose:** Movie and TV metadata (IMDb wrapper)

**Setup:**
1. Go to https://www.omdbapi.com/apikey.aspx
2. Select **FREE (1,000 daily limit)**
3. Enter email → Submit
4. Check email for activation link
5. Click link → API key shown

**Configure `.env`:**
```bash
OMDB_API_KEY=your_key_here
```

**Test:**
```bash
curl "http://www.omdbapi.com/?t=Inception&apikey=YOUR_KEY"
```

**In Slack:**
```
/media movie Inception
/media tv Breaking Bad
```

**Free tier:** 1,000 requests/day

---

### Overseerr

**Purpose:** Media request management for Plex

**Prerequisites:**
- Overseerr installed and running
- Plex server configured

**Setup:**
1. Open Overseerr web UI (e.g., http://192.168.1.93:5055)
2. Go to **Settings** → **General**
3. Scroll to **API Key** → Click **Generate**
4. Copy the key

**Configure `.env`:**
```bash
OVERSEERR_URL=http://192.168.1.93:5055
OVERSEERR_API_KEY=your_key_here
```

**Test:**
```bash
curl http://192.168.1.93:5055/api/v1/status \
  -H "X-Api-Key: YOUR_KEY"
```

**In Slack:**
```
/request The Matrix
```

---

### Sonarr

**Purpose:** TV show automation

**Prerequisites:**
- Sonarr installed and running

**Setup:**
1. Open Sonarr web UI (e.g., http://192.168.1.93:8989)
2. Go to **Settings** → **General**
3. **Security** section → Copy **API Key**

**Configure `.env`:**
```bash
SONARR_URL=http://192.168.1.93:8989
SONARR_API_KEY=your_key_here
```

**Test:**
```bash
curl http://192.168.1.93:8989/api/v3/system/status \
  -H "X-Api-Key: YOUR_KEY"
```

**In Slack:**
```
/search Breaking Bad
/ask Add Breaking Bad to Sonarr
```

---

### Radarr

**Purpose:** Movie automation

**Setup:** (Same process as Sonarr)

1. Open Radarr web UI (e.g., http://192.168.1.93:7878)
2. **Settings** → **General** → Copy **API Key**

**Configure `.env`:**
```bash
RADARR_URL=http://192.168.1.93:7878
RADARR_API_KEY=your_key_here
```

**Test:**
```bash
curl http://192.168.1.93:7878/api/v3/system/status \
  -H "X-Api-Key: YOUR_KEY"
```

---

### Lidarr

**Purpose:** Music automation

**Setup:** (Same process as Sonarr/Radarr)

1. Open Lidarr web UI (e.g., http://192.168.1.93:8686)
2. **Settings** → **General** → Copy **API Key**

**Configure `.env`:**
```bash
LIDARR_URL=http://192.168.1.93:8686
LIDARR_API_KEY=your_key_here
```

**Test:**
```bash
curl http://192.168.1.93:8686/api/v1/system/status \
  -H "X-Api-Key: YOUR_KEY"
```

---

### Tautulli (Plex Monitoring)

**Purpose:** Plex server statistics and monitoring

**Prerequisites:**
- Tautulli installed and running
- Connected to Plex server

**Setup:**
1. Open Tautulli web UI (e.g., http://192.168.1.93:8181)
2. **Settings** → **Web Interface**
3. Scroll to **API** section → Copy **API Key**

**Configure `.env`:**
```bash
TAUTULLI_URL=http://192.168.1.93:8181
TAUTULLI_API_KEY=your_key_here
PLEX_PORT=32400
```

**Test:**
```bash
curl "http://192.168.1.93:8181/api/v2?apikey=YOUR_KEY&cmd=get_activity"
```

**In Slack:**
```
/recent
/ask What's currently playing on Plex?
```

---

## Infrastructure APIs

### Ollama (Local LLM)

**Purpose:** Free local AI for unlimited queries

**Setup:**
1. Install Ollama: https://ollama.ai/download
2. Pull the model:
   ```bash
   ollama pull gemma4:e4b
   ```
3. Start Ollama (auto-starts on macOS/Windows)
4. Verify it's running:
   ```bash
   curl http://localhost:11434/api/tags
   ```

**Configure `.env`:**
```bash
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=gemma4:e4b
LOCAL_LLM_ENABLED=true
DEFAULT_MODEL_PREFERENCE=auto
```

**Test:**
```bash
curl http://localhost:11434/api/generate -d '{
  "model": "gemma4:e4b",
  "prompt": "Why is the sky blue?",
  "stream": false
}'
```

**In Slack:**
```
/ask model:local Tell me a joke
/model set local
```

**Note:** Docker containers use `host.docker.internal` to access host's localhost.

---

### Glances (System Monitoring)

**Purpose:** CPU, memory, disk, network monitoring

**Setup:**
1. Install Glances:
   ```bash
   # macOS
   brew install glances
   
   # Linux
   pip install glances
   ```

2. Start in web mode:
   ```bash
   glances -w --port 61208
   ```

3. Or run as service:
   ```bash
   # macOS (launchd)
   brew services start glances
   
   # Linux (systemd)
   sudo systemctl enable glances
   sudo systemctl start glances
   ```

**Configure `.env`:**
```bash
GLANCES_URL=http://host.docker.internal:61208
```

**Test:**
```bash
curl http://localhost:61208/api/3/cpu
```

**In Slack:**
```
/system
/report
```

---

## Productivity APIs

### Gmail

**Purpose:** Email reading and sending

**Setup:**
1. Enable 2-factor authentication on your Google account
2. Go to https://myaccount.google.com/apppasswords
3. Select **App** → "Mail", **Device** → "Other (OpenClaw)"
4. Click **Generate**
5. Copy the 16-character password (no spaces)

**Configure `.env`:**
```bash
GMAIL_USER=your.email@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop  # Spaces don't matter
```

**Test:**
```python
# In Python shell
import imaplib
mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login("your.email@gmail.com", "your_app_password")
print("Success!")
mail.logout()
```

**In Slack:**
```
/ask Check my inbox
/ask Send email to someone@example.com
```

**Security Note:** App passwords bypass 2FA. Treat like a regular password.

---

### Google Calendar (Optional)

**Purpose:** Calendar event management

**Setup (OAuth 2.0):**
1. Go to https://console.cloud.google.com/
2. Create project → Enable Google Calendar API
3. Create OAuth 2.0 credentials
4. Download credentials JSON
5. Run OAuth flow to get refresh token

**Configure `.env`:**
```bash
GOOGLE_OAUTH_CLIENT_ID=your_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
GOOGLE_OAUTH_REFRESH_TOKEN=your_refresh_token
```

**Note:** Full OAuth setup is complex. See Google Calendar API docs.

---

### AgentMail (Optional)

**Purpose:** Bot-to-bot email communication

**Setup:**
1. Go to https://agentmail.to
2. Sign up → Verify email
3. Dashboard → **API Keys** → Create key
4. Create inbox → Copy inbox ID

**Configure `.env`:**
```bash
AGENTMAIL_API_KEY=your_key_here
AGENTMAIL_INBOX=your_inbox_id
```

**Test:**
```bash
curl https://api.agentmail.to/v0/inboxes/YOUR_INBOX/messages \
  -H "Authorization: Bearer YOUR_KEY"
```

**In Slack:**
```
/mail recipient@example.com "Hello from OpenClaw"
```

---

## Other Services

### Ntfy (Push Notifications)

**Purpose:** Mobile push notifications

**Setup (Free ntfy.sh):**
1. Choose a unique topic name (e.g., `openclaw-alerts-abc123`)
2. Download ntfy app on phone
3. Subscribe to your topic

**Configure `.env`:**
```bash
NTFY_URL=https://ntfy.sh
NTFY_TOPIC=openclaw-alerts-abc123  # Make it unique!
```

**Test:**
```bash
curl -d "Hello from OpenClaw" https://ntfy.sh/openclaw-alerts-abc123
```

**Check your phone for notification.**

**In Slack:**
```
/notify Test notification
```

**Security Note:** Topic name is the only security on ntfy.sh. Use a long, random name.

**Self-Hosted Alternative:**
```bash
# Run ntfy server
docker run -p 80:80 binwiederhier/ntfy serve

# Update .env
NTFY_URL=http://192.168.1.93:80
NTFY_TOKEN=optional_auth_token
```

---

### Stable Diffusion (Optional)

**Purpose:** Local image generation

**Setup:**
1. Install Automatic1111 WebUI: https://github.com/AUTOMATIC1111/stable-diffusion-webui
2. Start with `--api` flag:
   ```bash
   ./webui.sh --api --port 7861
   ```
3. Verify API:
   ```bash
   curl http://localhost:7861/sdapi/v1/sd-models
   ```

**Configure `.env`:**
```bash
SD_URL=http://host.docker.internal:7861
SD_TIMEOUT=120
```

**In Slack:**
```
/generate-image a cat wearing a top hat
```

---

### Uptime Kuma

**Purpose:** External uptime monitoring

**Setup:**
1. Install Uptime Kuma: https://github.com/louislam/uptime-kuma
2. Add monitor:
   - **Monitor Type:** HTTP(s)
   - **Friendly Name:** OpenClaw
   - **URL:** http://192.168.1.93:8765/health
   - **Heartbeat Interval:** 60 seconds

**Configure `.env`:**
```bash
HEALTH_PORT=8765
```

**Test:**
```bash
curl http://192.168.1.93:8765/health
```

Expected response:
```json
{
  "status": "healthy",
  "uptime_seconds": 12345,
  "version": "0.6.0"
}
```

---

### AdGuard Home (Optional)

**Purpose:** DNS filtering and ad blocking

**Prerequisites:**
- AdGuard Home installed and running

**Setup:**
1. Open AdGuard web UI (e.g., http://192.168.1.8:3053)
2. Log in with admin credentials

**Configure `.env`:**
```bash
ADGUARD_URL=http://192.168.1.8:3053
ADGUARD_USER=admin
ADGUARD_PASSWORD=your_password
```

**Test:**
```bash
curl -u admin:password http://192.168.1.8:3053/control/status
```

**In Slack:**
```
/network  # Includes DNS stats
```

---

## Feature Configuration (W9–W13)

Environment variables for features added in waves W9–W13. These supplement `config/config.yaml` and can be set in `.env`.

### Routing & Provider Selection (W9)
- `ROUTING_LATENCY_THRESHOLD_MS` — Skip providers with p95 latency above this threshold in milliseconds (default: `10000`)
- `GEMINI_STREAMING_ENABLED` — Set to `true` to enable real-time Gemini streaming responses (default: `false`)
- `PROVIDER_STREAM_INTERVAL_CHARS` — Characters to buffer before sending a streaming chunk (default: `200`)

### Memory & Recall (W6)
- `RECALL_DOMAIN_GUARD_STRICT` — Set to `true` to suppress off-topic memories more aggressively (default: `false`)

### Alerts & Notifications (W13)
- `OWNER_USER_ID` — Slack user ID to DM for CRITICAL severity alerts (falls back to `BOT_OWNER_ID`)
- `BOT_OWNER_ID` — Alias for `OWNER_USER_ID` (legacy; prefer `OWNER_USER_ID`)

### Quality Repair (W11)
- `QUALITY_REPAIR_MAX_ATTEMPTS` — Maximum LLM repair attempts for low-quality responses (default: `2`)

---

## Verification

### 1. Environment Variables

**Check all required vars are set:**
```bash
cd /Users/davevoyles/openclaw

# Essential vars
grep -E "^(SLACK_BOT_TOKEN|SLACK_APP_TOKEN|GOOGLE_API_KEY)=" .env

# Optional API keys
grep -E "^(NEWSAPI_KEY|APISPORTS_KEY|ALPHAVANTAGE_KEY)=" .env
```

**Missing variables?** Add them to `.env`.

---

### 2. API Connectivity

**Run verification script:**
```bash
cd /Users/davevoyles/openclaw
python verify_apis.py
```

Expected output:
```
✅ Slack: Connected
✅ Gemini: Connected
✅ NewsAPI: Connected (100 requests remaining)
✅ Tavily: Connected (1000 requests remaining)
⚠️  Perplexity: Not configured (optional)
❌ AgentMail: Failed (check API key)
```

---

### 3. Test in Slack

**Basic commands:**
```
/ping          # Should respond "Pong!"
/about         # Should show version info
/help          # Should list all commands
```

**AI features:**
```
/ask What's 2+2?          # Test Gemini/Ollama
/websearch latest news    # Test search APIs
/media movie Inception    # Test OMDb
```

**Media features:**
```
/containers               # Test Docker API
/system                   # Test Glances
/search Breaking Bad      # Test Sonarr/Radarr
/recent                   # Test Tautulli
```

---

### 4. Health Check

**HTTP endpoint:**
```bash
curl http://192.168.1.93:8765/health | jq
```

**Expected response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "version": "0.6.0",
  "tools": {
    "gemini": {"success_rate": 0.99, "total_calls": 50},
    "newsapi": {"success_rate": 1.0, "total_calls": 10},
    "tavily": {"success_rate": 1.0, "total_calls": 5}
  }
}
```

**Red flags:**
- `success_rate < 0.5` → API is failing, check key
- `status: "degraded"` → Some features unavailable
- HTTP 500 error → OpenClaw is down

---

### 5. Log Inspection

**Check for errors:**
```bash
docker compose logs openclaw | grep -i error
docker compose logs openclaw | grep -i "API key"
docker compose logs openclaw | grep -i "rate limit"
```

**Common errors:**
```
❌ "Invalid API key" → Double-check key in .env
❌ "Rate limit exceeded" → Wait or upgrade tier
❌ "Connection refused" → Service not running
```

---

## Troubleshooting

### Slack Bot Not Responding

**Check:**
1. Bot is connected (Socket Mode — no green dot in Slack; check `docker logs openclaw` for "Connected")
2. Slash commands are registered correctly in the Slack App manifest
3. Bot token and app token are correct in `.env`
4. Bot has been invited to the channel

**Fix:**
```bash
# Restart bot
docker compose restart openclaw

# Check logs for connection status
docker compose logs -f openclaw | grep -E "socket|connected|error"
```

---

### API Key Errors

**Symptoms:**
- "Invalid API key" in logs
- "Unauthorized" responses
- Commands fail silently

**Fix:**
1. Verify key in `.env` (no extra spaces, quotes, or newlines)
2. Test key directly with curl
3. Regenerate key if necessary
4. Restart OpenClaw after changes:
   ```bash
   docker compose restart openclaw
   ```

---

### Rate Limit Errors

**Symptoms:**
- "429 Too Many Requests"
- "Rate limit exceeded" messages
- Circuit breaker opens

**Fix:**
1. Check current usage:
   ```bash
   curl http://192.168.1.93:8765/health | jq '.tools'
   ```
2. Wait for rate limit reset (usually 24 hours)
3. Enable caching to reduce calls
4. Upgrade to paid tier if needed

**Circuit breaker cooldown:** 5 minutes

---

### Self-Hosted Services Not Found

**Symptoms:**
- "Connection refused" for Sonarr/Radarr/Tautulli
- "Service unreachable" errors

**Fix:**
1. Verify service is running:
   ```bash
   docker ps | grep sonarr
   curl http://192.168.1.93:8989/api/v3/system/status
   ```
2. Check URL in `.env` matches actual service
3. Verify API key is correct
4. Check firewall rules
5. Test from Docker container:
   ```bash
   docker exec openclaw curl http://192.168.1.93:8989/api/v3/system/status
   ```

---

### Ollama Connection Issues

**Symptoms:**
- "Ollama unavailable, falling back to Gemini"
- "Connection refused to localhost:11434"

**Fix:**
1. Verify Ollama is running:
   ```bash
   curl http://localhost:11434/api/tags
   ```
2. Start Ollama:
   ```bash
   ollama serve
   ```
3. Check model is pulled:
   ```bash
   ollama list
   # Should show gemma4:e4b
   ollama pull gemma4:e4b
   ```
4. Docker uses `host.docker.internal`, not `localhost`:
   ```bash
   # From inside container
   docker exec openclaw curl http://host.docker.internal:11434/api/tags
   ```

---

### Environment Variable Not Loading

**Symptoms:**
- Feature not working
- "Not configured" messages
- Default values used instead of custom

**Check:**
1. Variable exists in `.env`:
   ```bash
   grep NEWSAPI_KEY .env
   ```
2. No syntax errors (quotes, spaces):
   ```bash
   # ❌ Bad
   NEWSAPI_KEY = "abc123"
   
   # ✅ Good
   NEWSAPI_KEY=abc123
   ```
3. Restart after changes:
   ```bash
   docker compose down
   docker compose up -d
   ```
4. Verify inside container:
   ```bash
   docker exec openclaw env | grep NEWSAPI_KEY
   ```

---

### Testing Individual APIs

**Create test script:**
```python
# test_api.py
import os
import requests

# NewsAPI test
key = os.getenv("NEWSAPI_KEY")
resp = requests.get(
    "https://newsapi.org/v2/top-headlines",
    params={"country": "us", "apiKey": key}
)
print(f"NewsAPI: {resp.status_code}")
print(resp.json())
```

**Run:**
```bash
export NEWSAPI_KEY=your_key_here
python test_api.py
```

---

## Next Steps

**After setup:**

1. **Test all features:**
   - `/ask` with various queries
   - `/websearch` for live search
   - `/media movie` for OMDb
   - `/search` for Sonarr/Radarr
   - `/recent` for Tautulli

2. **Configure advanced features:**
   - Email integration (Gmail)
   - Push notifications (Ntfy)
   - Calendar sync (Google Calendar)

3. **Set up monitoring:**
   - Uptime Kuma for health checks
   - Morning briefing for daily summaries
   - Alert channel for notifications

4. **Optimize costs:**
   - Review [API_COSTS.md](./API_COSTS.md)
   - Set budget alerts
   - Enable caching

5. **Join the community:**
   - Report issues: https://github.com/davevoyles/openclaw/issues
   - Contribute: https://github.com/davevoyles/openclaw/pulls

---

## Quick Reference

### Essential APIs (Get Started)
- ✅ Slack Bot Token + App Token
- ✅ Google Gemini API Key

### Recommended APIs (Free Tier)
- ⭐ NewsAPI (news)
- ⭐ Tavily (search)
- ⭐ OMDb (movies/TV)
- ⭐ Ollama (local LLM)

### Optional APIs (Power Users)
- 🔧 Perplexity (premium search)
- 🔧 API-Sports (live sports)
- 🔧 Alpha Vantage (finance)
- 🔧 Firecrawl (web scraping)

### Self-Hosted (No API Keys)
- 🏠 Sonarr/Radarr/Lidarr
- 🏠 Tautulli
- 🏠 Overseerr
- 🏠 Glances
- 🏠 AdGuard Home

---

**Need help?** See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) or open an issue on GitHub.
