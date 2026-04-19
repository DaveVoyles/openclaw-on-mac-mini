# API Cost Breakdown and Budgeting
<!-- Updated: 2026-04-18 -->


Financial planning guide for OpenClaw's external API usage.

---

## Monthly Cost Estimate

### Current Configuration (Recommended Free Tier)

| Service | Plan | Monthly Cost | Daily Limit | Usage Pattern |
|---------|------|-------------|-------------|---------------|
| **NewsAPI** | Free | $0 | 100 req/day | ~30-50/day |
| **API-Sports** | Free | $0 | 100 req/day | ~20-40/day |
| **Alpha Vantage** | Free | $0 | 25 req/day | ~5-10/day |
| **Tavily** | Free | $0 | 1,000 req/mo | ~100-200/mo |
| **Firecrawl** | Free | $0 | 500 pages/mo | ~50-100/mo |
| **Serper** | Prepaid | ~$5/mo | Credit-based | Optional |
| **OMDb** | Free | $0 | 1,000 req/day | ~50-100/day |
| **Gemini Flash 2.0** | Free | $0 | 1,500 req/day | ~200-500/day |
| **Gmail** | Free | $0 | 500 sends/day | ~10-20/day |
| **Ntfy.sh** | Free | $0 | 250 msg/day | ~5-10/day |
| **Total** | | **$0-5/mo** | | |

**Estimated actual monthly cost: $0** (staying within free tiers)

---

### Upgrade Path (Moderate Usage)

| Service | Plan | Monthly Cost | Daily Limit | When to Upgrade |
|---------|------|-------------|-------------|-----------------|
| **NewsAPI** | Free | $0 | 100 req/day | No upgrade needed |
| **API-Sports** | Basic | $10 | 1,000 req/day | >100 sports queries/day |
| **Alpha Vantage** | Free | $0 | 25 req/day | Rarely hit limit |
| **Tavily** | Basic | $29 | 15,000 req/mo | >1,000 searches/mo |
| **Perplexity** | Standard | $20 | 50 req/day | Need AI search |
| **Gemini** | Pay-as-go | ~$10-30 | Usage-based | >1,500 req/day |
| **Total** | | **$69-99/mo** | | |

**When to consider:** Daily active usage, multiple users, production workload

---

### Production Scale (High Usage)

| Service | Plan | Monthly Cost | Daily Limit | When to Upgrade |
|---------|------|-------------|-------------|-----------------|
| **NewsAPI** | Free | $0 | 100 req/day | Business plan not needed |
| **API-Sports** | Pro | $35 | 5,000 req/day | Multi-sport tracking |
| **Alpha Vantage** | Premium | $49.99 | Unlimited | Real-time trading data |
| **Tavily** | Professional | $149 | 100,000 req/mo | Heavy search usage |
| **Perplexity** | Pro | $200 | Unlimited | Primary search engine |
| **Gemini** | Pay-as-go | $50-100 | Usage-based | High tool-calling volume |
| **Total** | | **$484-534/mo** | | |

**When to consider:** Commercial use, high-frequency automation, team access

---

## Detailed Cost Breakdown

### News & Data APIs

#### NewsAPI
- **Free Tier:** 100 requests/day
  - Perfect for personal use
  - Development only (no commercial)
  - ~3,000 requests/month
- **Business Tier:** $449/month
  - 250,000 requests/month
  - Commercial license
  - **Recommendation:** Free tier sufficient for OpenClaw

#### API-Sports
- **Free Tier:** 100 requests/day
  - All sports included
  - ~3,000 requests/month
  - Good for casual sports tracking
- **Basic:** $10/month
  - 1,000 requests/day
  - ~30,000 requests/month
  - **Upgrade when:** Tracking multiple teams daily
- **Pro:** $35/month
  - 5,000 requests/day
  - **Upgrade when:** Multi-sport automation

**Cost Optimization:**
- Cache NBA scores for 15 minutes
- Only fetch standings once per day
- Use circuit breakers to prevent waste

#### Alpha Vantage
- **Free Tier:** 25 requests/day
  - 5 requests/minute
  - ~750 requests/month
  - Sufficient for daily stock checks
- **Premium:** $49.99/month
  - Unlimited intraday requests
  - 120 calls/minute
  - **Upgrade when:** Real-time trading data needed

**Cost Optimization:**
- Cache stock quotes for 5 minutes during market hours
- Only fetch after-hours data once
- Batch multiple symbol lookups

---

### Search APIs

#### Perplexity AI
- **No Free Tier**
- **Standard:** $20/month
  - 50 requests/day
  - AI-powered answers
  - Citations included
- **Pro:** $200/month
  - Unlimited requests
  - Priority processing
  - **Upgrade when:** Primary search engine replacement

**Cost Optimization:**
- Use as first-choice search for quality
- Fall back to Tavily/DuckDuckGo for simple queries
- Cache results for 1 hour

#### Tavily
- **Free Tier:** 1,000 requests/month
  - ~33 requests/day average
  - No daily cap
  - Good for fallback search
- **Basic:** $29/month
  - 15,000 requests/month
  - ~500 requests/day average
- **Professional:** $149/month
  - 100,000 requests/month
  - **Upgrade when:** >1,000 searches/month

**Cost Optimization:**
- Use after Perplexity in cascade
- 1-hour result caching
- Deduplicate similar queries

#### Firecrawl
- **Free Tier:** 500 pages/month
  - ~16 pages/day average
  - Good for occasional scraping
- **Starter:** $25/month
  - 5,000 pages/month
- **Standard:** $75/month
  - 25,000 pages/month
  - **Upgrade when:** Heavy web scraping

**Cost Optimization:**
- Use for final fallback in search cascade
- Cache extracted content for 24 hours
- Only use when structured extraction needed

#### Serper
- **Pay-as-you-go:** $0.002 per search
  - $5 = 2,500 searches
  - No free tier
  - Direct Google results

**Monthly Cost Examples:**
- 100 searches/month: $0.20
- 500 searches/month: $1.00
- 2,500 searches/month: $5.00

**Cost Optimization:**
- Only use when Google-specific results needed
- Not in default search cascade
- Good for SEO/SERP analysis

---

### Media APIs

#### OMDb
- **Free Tier:** 1,000 requests/day
  - ~30,000 requests/month
  - More than sufficient
- **Patron:** $1/month
  - Unlimited requests
  - Support development

**Recommendation:** Free tier is plenty. Consider Patron tier to support the project.

#### TMDB
- **Free:** Unlimited (with attribution)
  - 40 requests per 10 seconds
  - Must display TMDB logo
  - Commercial use allowed

**Recommendation:** Always free, just follow attribution requirements.

#### Self-Hosted Media (*arr Stack, Plex, Overseerr)
- **Infrastructure Cost:** $0 (already running)
- **Electricity Cost:** ~$5-10/month for Mac Mini
- **No API fees:** All self-hosted

---

### Infrastructure APIs

#### Google Gemini
- **Free Tier:** 
  - 15 requests/minute
  - 1,500 requests/day
  - 1.5M tokens/month
  - **Cost:** Free up to limits

- **Pay-as-you-go (Gemini 2.5 Flash):**
  - Input: $0.10 per 1M tokens
  - Output: $0.40 per 1M tokens
  - **Monthly estimate (500 req/day):**
    - ~15M input tokens: $1.50
    - ~3M output tokens: $1.20
    - **Total: ~$2.70/month**

- **Pay-as-you-go (Gemini 2.0 Pro):**
  - Input: $1.25 per 1M tokens
  - Output: $10.00 per 1M tokens
  - **Monthly estimate (500 req/day):**
    - ~15M input tokens: $18.75
    - ~3M output tokens: $30.00
    - **Total: ~$48.75/month**

**Cost Optimization:**
- Stay on free tier for personal use
- Use Ollama for simple queries (hybrid routing saves ~50% Gemini calls)
- Use Flash over Pro (12.5x cheaper)
- Shorter system prompts
- Limit max_tokens to 8192
- Set monthly budget alert: `GEMINI_BUDGET_LIMIT=30.00`

**Estimated Monthly Cost:**
- Light usage (100-200 req/day): **$0** (free tier)
- Moderate usage (500 req/day): **$0-5** (near free tier limit)
- Heavy usage (1,500+ req/day): **$10-30** (paid)

#### Ollama (Local LLM)
- **Infrastructure:** Free (self-hosted on Mac Mini)
- **Model:** gemma4:e4b (9.6 GB)
- **Cost:** $0 (unlimited inference)
- **Electricity:** Included in Mac Mini operational cost

**ROI:** Saves ~$10-20/month in Gemini API costs via hybrid routing.

---

### Productivity APIs

#### Gmail
- **Free (Personal):** 500 sends/day
- **Google Workspace:** $6-18/user/month
  - 2,000 sends/day
  - Custom domain

**Recommendation:** Free tier is sufficient.

#### Google Calendar
- **Free:** 1,000,000 queries/day
- **Cost:** $0

#### AgentMail
- **Free tier:** Available but limits not documented
- **Paid tier:** Not publicly listed

**Estimated cost:** $0-10/month (minimal usage)

---

### Other Services

#### Ntfy (Push Notifications)
- **Free (ntfy.sh):** 250 messages/day/topic
- **Self-hosted:** $0 (unlimited)

**Recommendation:** Use free ntfy.sh or self-host for unlimited.

#### Self-Hosted Services (Uptime Kuma, AdGuard, Glances, Stable Diffusion)
- **API Cost:** $0
- **Infrastructure Cost:** Included in Mac Mini hosting

---

## Budget Planning

### Monthly Budget by Tier

#### Tier 1: Personal Use (Current)
**Target:** Stay within all free tiers
- **Monthly Cost:** $0
- **Daily Limits:**
  - 100 news queries
  - 100 sports queries
  - 25 financial queries
  - 1,000 web searches/month
  - 1,500 Gemini requests
  - 1,000 OMDb lookups

**Monitoring:**
```bash
# Check current usage
curl http://192.168.1.93:8765/health | jq '.tools'

# View Gemini spending
cat /memory/spending.json
```

**Alerts:**
- Set `GEMINI_BUDGET_LIMIT=30.00` in `.env`
- Auto-notification at 80% of free tier limits
- Circuit breaker opens on rate limit errors

#### Tier 2: Power User
**Target:** Upgrade only necessary APIs
- **Monthly Cost:** $50-75
- **Recommended Upgrades:**
  - Perplexity Standard: $20
  - Tavily Basic: $29
  - API-Sports Basic: $10
  - Gemini pay-as-go: ~$10

**When to upgrade:**
- Hitting free tier limits 3+ days/week
- Need faster/better search results
- Multiple users
- Production automation

#### Tier 3: Team/Commercial
**Target:** Production-ready infrastructure
- **Monthly Cost:** $300-500
- **Recommended Upgrades:**
  - Perplexity Pro: $200
  - Tavily Professional: $149
  - API-Sports Pro: $35
  - Alpha Vantage Premium: $50
  - Gemini pay-as-go: ~$50

**When to upgrade:**
- Commercial use
- Team of 5+ users
- High-frequency automation
- SLA requirements

---

## Cost Tracking

### Built-in Spending Tracker

OpenClaw tracks Gemini API costs automatically:

**File:** `/memory/spending.json`
```json
{
  "month": "2024-01",
  "input_tokens": 1234567,
  "output_tokens": 234567,
  "estimated_cost": 2.37,
  "requests": 342
}
```

**Environment Variables:**
```bash
GEMINI_BUDGET_LIMIT=30.00
GEMINI_PRICE_INPUT_PER_M=0.10
GEMINI_PRICE_OUTPUT_PER_M=0.40
SPENDING_FILE=/memory/spending.json
```

**Alerts:**
- 80% of budget: Warning notification
- 100% of budget: Alert + auto-throttling
- 110% of budget: Switch to Ollama-only mode

### Manual Tracking

For other APIs without built-in tracking:

**Create:** `data/api_usage.json`
```json
{
  "month": "2024-01",
  "apis": {
    "newsapi": {"requests": 450, "limit": 3000},
    "apisports": {"requests": 1200, "limit": 3000},
    "alphavantage": {"requests": 180, "limit": 750},
    "tavily": {"requests": 850, "limit": 1000}
  }
}
```

**Monitor script:** `scripts/check_api_usage.py` (to be created)

---

## Cost Optimization Strategies

### 1. Caching
- **Search results:** 1 hour TTL
- **News articles:** 1 hour TTL
- **Stock quotes:** 5 minutes during market hours
- **Sports scores:** 15 minutes during games
- **Media metadata:** 24 hours

**Savings:** ~40-60% reduction in API calls

### 2. Hybrid LLM Routing
- Simple queries → Ollama (free)
- Tool-calling queries → Gemini (paid)

**Current routing keywords:**
- Ollama: greetings, questions, explanations, comparisons
- Gemini: "search", "check", "get", "list", "show", "analyze"

**Savings:** ~$10-20/month in Gemini costs

### 3. Rate Limit Awareness
- Circuit breakers prevent wasted calls on errors
- Exponential backoff on rate limits
- Queue requests during high usage

**Savings:** Prevents burning through daily limits

### 4. Batch Operations
- Fetch multiple sports scores in one call
- Batch email checks (every 15 min vs. per-message)
- Combine multiple stocks into single query

**Savings:** ~20-30% reduction

### 5. Smart Cascading
- Start with best free API (Tavily)
- Fall back to paid only when necessary (Perplexity)
- Final fallback to free alternatives (DuckDuckGo)

**Current cascade:**
1. Perplexity (paid, best quality)
2. Firecrawl (free tier, structured)
3. Tavily (free tier, good quality)
4. DuckDuckGo (free, unlimited)
5. Bing Lite (free, unlimited)

**Optimization:** Reverse order for non-critical queries

---

## ROI Analysis

### Self-Hosting Savings

**Ollama (Local LLM):**
- **Setup cost:** $0 (Mac Mini already owned)
- **Monthly savings:** $10-20 (vs. all-Gemini)
- **ROI:** Immediate

**Media Stack (*arr + Plex):**
- **Setup cost:** $0 (already running)
- **Monthly savings:** $15-30 (vs. cloud services like Radarr Cloud)
- **ROI:** Immediate

**Glances (System Monitoring):**
- **Setup cost:** $0
- **Monthly savings:** $10-20 (vs. Datadog, New Relic)
- **ROI:** Immediate

**Total Self-Hosting Savings:** ~$35-70/month

### Free Tier Maximization

**Current free tier value:**
- NewsAPI: $0 (vs. $449/mo paid plan)
- API-Sports: $0 (vs. $10-35/mo)
- Alpha Vantage: $0 (vs. $50/mo)
- Tavily: $0 (vs. $29/mo)
- Firecrawl: $0 (vs. $25/mo)
- OMDb: $0 (vs. $1/mo)
- Gemini: $0 (vs. $10-30/mo)

**Total value if paid:** ~$564-620/month
**Actual cost:** $0/month
**Savings:** 100%

---

## Recommended Budget Alerts

Add to `.env`:

```bash
# Gemini monthly budget (triggers alert at 80%)
GEMINI_BUDGET_LIMIT=30.00

# Per-API daily limits (for monitoring)
NEWSAPI_DAILY_ALERT=80      # Alert at 80/100 requests
APISPORTS_DAILY_ALERT=80
ALPHAVANTAGE_DAILY_ALERT=20  # Alert at 20/25 requests
```

**Notification Channels:**
- Discord `ALERT_CHANNEL_ID`
- Ntfy push notification
- Email summary in morning briefing

---

## Future Cost Considerations

### Scaling to Multiple Users

**Single user (current):**
- 200-500 Gemini requests/day
- 20-40 news queries/day
- 10-20 sports queries/day
- **Cost:** $0/month

**5 users:**
- 1,000-2,500 Gemini requests/day (need paid tier)
- 100-200 news queries/day (still free)
- 50-100 sports queries/day (upgrade to Basic)
- **Cost:** ~$50-75/month

**20 users (team/commercial):**
- 4,000-10,000 Gemini requests/day (paid tier)
- 400-800 news queries/day (need Business tier)
- 200-400 sports queries/day (Pro tier)
- **Cost:** ~$500-700/month

### Adding Premium Features

**Image Generation (Stable Diffusion):**
- Self-hosted: $0 (uses Mac Mini GPU)
- Cloud (Replicate): ~$0.002-0.02 per image

**Speech-to-Text (Whisper):**
- Self-hosted: $0
- Cloud (OpenAI): $0.006 per minute

**Embeddings (for RAG/memory):**
- Ollama (local): $0
- OpenAI: $0.0001 per 1K tokens

---

## Summary

**Current Monthly Cost:** **$0**

**Recommended Budget:** $0-5/month (add Serper credits)

**Upgrade Threshold:** When hitting free tier limits >3 days/week

**Maximum Expected Cost:** $50-100/month (power user)

**Commercial Scale:** $300-500/month (team access)

**Best Value:** Maximize free tiers + self-hosting = $0/month with $500-700/month equivalent value

---

## Cost Monitoring Commands

```bash
# Check API health and usage
curl http://192.168.1.93:8765/health | jq '.tools'

# View Gemini spending
cat /memory/spending.json | jq

# Check circuit breaker status
# (APIs with repeated failures will show success_rate < 0.8)
curl http://192.168.1.93:8765/health | jq '.tools[] | select(.success_rate < 0.8)'

# Morning briefing includes cost summary
/briefing
```

---

## Questions?

- **Free tier limits:** See [API_REFERENCE.md](./API_REFERENCE.md)
- **Setup help:** See [API_SETUP.md](./API_SETUP.md)
- **Issues:** https://github.com/davevoyles/openclaw/issues
