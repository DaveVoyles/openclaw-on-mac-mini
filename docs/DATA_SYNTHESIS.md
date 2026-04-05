# Data Synthesis Architecture

**Multi-source intelligence combining NewsAPI, API-Sports, and Alpha Vantage with LLM-powered insights.**

---

## Overview

The Data Synthesis engine combines data from multiple premium APIs with LLM-generated insights to create contextual, actionable reports. Instead of simply aggregating data, it **synthesizes** insights by identifying correlations, detecting patterns, and generating natural language summaries that connect the dots.

### Core Principle

> **"Synthesis, not concatenation"** — We don't just stack API responses. We analyze relationships, detect anomalies, and provide context-aware intelligence.

---

## Architecture

### Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                      User Request                                 │
│              "Generate Disney company report"                     │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Synthesis Orchestrator                           │
│              skills/synthesis_skills.py                           │
└────┬──────────┬─────────────┬──────────────┬────────────────────┘
     │          │             │              │
     ▼          ▼             ▼              ▼
┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐
│ Alpha   │ │ NewsAPI  │ │ API-     │ │ Circuit     │
│ Vantage │ │          │ │ Sports   │ │ Breakers    │
│ Stock   │ │ News     │ │ (future) │ │ + Cache     │
│ Sentiment│ │ Search   │ │          │ │             │
└────┬────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘
     │          │             │              │
     └──────────┴─────────────┴──────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Data Aggregation                               │
│    Parallel API calls with timeout + error handling              │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Correlation Detection                            │
│  - Stock-sentiment alignment/divergence                           │
│  - News-stock movement correlation                                │
│  - Sector trend identification                                    │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                     LLM Synthesis                                 │
│  Gemini 2.0 Flash generates 2-3 sentence insights                │
│  Connects data points, highlights cause-effect relationships      │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Structured Response                              │
│  JSON with synthesis, correlations, sources, timestamp            │
└──────────────────────────────────────────────────────────────────┘
```

---

## Synthesis Functions

### 1. Company Report (`synthesize_company_report`)

**Purpose:** Comprehensive company analysis combining financial and news data.

**Data Sources:**
- Stock price & movement (Alpha Vantage)
- Sentiment analysis (Alpha Vantage)
- Recent news articles (NewsAPI)

**Output:**
```python
{
    "entity": "Disney",
    "ticker": "DIS",
    "stock_data": {
        "price": 96.61,
        "change": "+1.23",
        "change_percent": "+1.31%"
    },
    "sentiment": {
        "score": 0.7,
        "label": "Bullish",
        "news_count": 15
    },
    "news_summary": "3 articles: Moana 2 box office, theme parks, streaming",
    "synthesis": "Disney stock rallied 5% as Moana 2 exceeded expectations...",
    "sources": ["Alpha Vantage", "NewsAPI"]
}
```

**Example Use Case:**
```python
# Before making investment decisions
report = await synthesize_company_report("TSLA")
if report["sentiment"]["score"] > 0.5 and "+" in report["stock_data"]["change"]:
    print(f"Bullish signal: {report['synthesis']}")
```

---

### 2. Entertainment Report (`synthesize_entertainment_report`)

**Purpose:** Track entertainment industry stocks correlated with box office/streaming news.

**Data Sources:**
- Entertainment stocks (Disney, Warner, Paramount, Netflix, etc.)
- Sentiment per studio
- Entertainment news headlines

**Output:**
```python
{
    "topic": "box office",
    "studios": {
        "Disney": {
            "ticker": "DIS",
            "price": 96.61,
            "change_percent": "+1.31%",
            "sentiment": {"score": 0.7, "label": "Bullish"}
        },
        ...
    },
    "key_correlations": [
        "Disney rose 5% following Moana 2 box office success",
        "Warner Bros declined 2% amid streaming concerns"
    ],
    "synthesis": "Entertainment stocks rallied with Disney leading..."
}
```

**Example Use Case:**
```python
# Weekend box office analysis
report = await synthesize_entertainment_report("box office")
for studio, data in report["studios"].items():
    if float(data["change_percent"].strip("%+")) > 3:
        print(f"⚡ {studio} had significant movement!")
```

---

### 3. Market Overview (`synthesize_market_overview`)

**Purpose:** High-level market snapshot with sector sentiment and economic news.

**Data Sources:**
- Business news headlines (NewsAPI)
- Market news with sentiment (Alpha Vantage)
- Sector aggregation (technology, finance, energy, etc.)

**Output:**
```python
{
    "market_summary": "Markets mixed as tech sector outperforms...",
    "top_news": [
        {
            "title": "Fed Holds Rates Steady",
            "sentiment": {"score": 0.3, "label": "Somewhat-Bullish"}
        }
    ],
    "sector_sentiment": {
        "technology": {"score": 0.5, "label": "Bullish"},
        "energy": {"score": -0.2, "label": "Somewhat-Bearish"}
    }
}
```

**Example Use Case:**
```python
# Daily market briefing
overview = await synthesize_market_overview()
print(overview["market_summary"])
for sector, sentiment in overview["sector_sentiment"].items():
    print(f"{sector}: {sentiment['label']}")
```

---

### 4. Correlation Finder (`find_correlations`)

**Purpose:** Detect relationships between stock movements, sentiment, and news.

**Output:**
```python
{
    "entity": "Apple",
    "correlations": [
        {
            "type": "stock_sentiment_alignment",
            "description": "Stock +1.8% aligns with Bullish sentiment (0.6)",
            "confidence": "high",
            "data_points": {"stock_change": 1.8, "sentiment_score": 0.6}
        },
        {
            "type": "news_coverage",
            "description": "5 recent articles may be influencing movement",
            "confidence": "medium"
        }
    ],
    "synthesis": "Strong alignment suggests news-driven rally..."
}
```

**Example Use Case:**
```python
# Investigate unusual stock movement
corr = await find_correlations("NVDA", entity_type="company")
for c in corr["correlations"]:
    if c["confidence"] == "high":
        print(f"⚠️ {c['description']}")
```

---

## LLM Integration

### How It Works

1. **Context Building:** Aggregate data from all APIs into structured prompt
2. **LLM Call:** Send to Gemini 2.0 Flash with low temperature (0.3) for factual output
3. **Synthesis:** Generate 2-3 sentence summary connecting data points
4. **Fallback:** If LLM fails, use basic template synthesis

### Example Prompt

```
Synthesize this data about Disney (DIS) into 2-3 concise sentences:

Stock: $96.61 (+1.31%)
Sentiment: Bullish (score: 0.70)
Recent News: 3 articles: Moana 2 box office, theme park attendance, streaming growth

Connect the stock movement with sentiment and news. Highlight any correlations.
```

### LLM Response

```
Disney stock rallied 1.3% to $96.61 as Moana 2 exceeded box office expectations, 
driving bullish market sentiment to 0.70. Positive theme park attendance and 
streaming growth reports further reinforced investor confidence.
```

### Cost Management

- **Model:** Gemini 2.0 Flash (cost-effective)
- **Max Tokens:** 200 per synthesis (keeps costs low)
- **Temperature:** 0.3 (factual, consistent output)
- **Caching:** 15-minute TTL reduces redundant calls
- **Circuit Breakers:** Skip LLM if APIs fail (no point synthesizing empty data)

---

## Caching Strategy

### Why Cache?

1. **API Rate Limits:** NewsAPI (100/day), Alpha Vantage (25/day) are precious
2. **Speed:** Instant responses for repeated queries
3. **Cost:** Reduce LLM API calls

### Cache Implementation

```python
_synthesis_cache: dict[str, tuple[float, Any]] = {}
_SYNTHESIS_CACHE_TTL = 900  # 15 minutes

def _get_cached(key: str) -> Any | None:
    if key in _synthesis_cache:
        timestamp, data = _synthesis_cache[key]
        if datetime.now().timestamp() - timestamp < _SYNTHESIS_CACHE_TTL:
            return data
    return None
```

### Cache Keys

- Company report: `company_report:DIS:2024-01-15-10` (hourly granularity)
- Entertainment: `entertainment_report:box office:2024-01-15-10`
- Market overview: `market_overview:2024-01-15-10`
- Correlations: `correlations:company:AAPL:2024-01-15` (daily)

**Rationale:** Hourly for stock/news (fast-moving), daily for correlations (slower analysis).

---

## Error Handling

### Circuit Breakers

Prevents hammering failing APIs:

```python
from tool_health import circuit_breaker

if circuit_breaker.is_open("alphavantage"):
    # Skip Alpha Vantage, use cached/partial data
    sources_failed.append("Alpha Vantage (circuit open)")
else:
    result = await finance_skills.get_stock_info(ticker)
    if result["status"] == "ok":
        circuit_breaker.record_success("alphavantage")
    else:
        circuit_breaker.record_failure("alphavantage")
```

**Settings:**
- Max failures: 3 consecutive
- Cooldown: 5 minutes
- Half-open retry: After cooldown, allow 1 test request

### Graceful Degradation

**Scenario:** Alpha Vantage rate limit hit, but NewsAPI works.

**Response:**
```python
{
    "status": "ok",  # Still useful!
    "stock_data": {},  # Missing
    "sentiment": {},   # Missing
    "news_articles": [...],  # Available
    "synthesis": "Limited data available. News coverage suggests...",
    "sources": ["NewsAPI"],
    "sources_failed": ["Alpha Vantage (Stock)", "Alpha Vantage (Sentiment)"]
}
```

**User Experience:** Partial data > No data. User sees what's available + clear indication of gaps.

---

## Performance Optimizations

### 1. Parallel API Calls

```python
tasks = [
    ("stock", finance_skills.get_stock_info(ticker)),
    ("sentiment", finance_skills.get_sentiment_analysis(ticker)),
    ("news", news_skills.search_news(company_name)),
]

results = await asyncio.gather(*[task[1] for task in tasks], return_exceptions=True)
```

**Impact:** 3x faster than sequential calls (1s vs 3s for company report).

### 2. Timeout Protection

```python
result = await asyncio.wait_for(
    finance_skills.get_stock_info(ticker),
    timeout=15,  # Prevent hanging
)
```

### 3. Minimal API Calls

- Company report: 3 API calls (stock + sentiment + news)
- Entertainment report: 2-8 calls (depending on studio count)
- Market overview: 2 calls (news + market news)

**Daily Budget Example:**
- NewsAPI: 100 calls/day → ~20-30 synthesis requests
- Alpha Vantage: 25 calls/day → ~8-12 company reports

---

## Testing Strategy

### Unit Tests (80%+ Coverage)

**Test Categories:**
1. **Skill Registration:** Verify all functions exported to `SKILLS`
2. **Data Structure:** Validate output format for each synthesis function
3. **Partial Failure:** Test graceful degradation when APIs fail
4. **Caching:** Verify cache hits/misses work correctly
5. **Correlation Detection:** Test alignment/divergence logic
6. **LLM Fallback:** Ensure basic synthesis when LLM unavailable
7. **Error Handling:** Circuit breaker behavior, timeouts

### Integration Tests

**Mocked API Calls:**
```python
@patch("skills.synthesis_skills.finance_skills.get_stock_info")
@patch("skills.synthesis_skills.news_skills.search_news")
async def test_company_report(mock_stock, mock_news):
    mock_stock.return_value = {"status": "ok", "price": 100}
    mock_news.return_value = {"status": "ok", "articles": [...]}
    
    result = await synthesize_company_report("AAPL")
    assert result["status"] == "ok"
```

### Live API Tests (Manual)

Use `verify_apis.py` pattern:

```python
# Test with real APIs (consume rate limits!)
async def test_live_synthesis():
    report = await synthesize_company_report("DIS")
    print(f"Stock: ${report['stock_data']['price']}")
    print(f"Sentiment: {report['sentiment']['label']}")
    print(f"Synthesis: {report['synthesis']}")
```

**Run sparingly** to preserve API quotas.

---

## Future Enhancements

### Phase 2: Sports Integration

```python
async def synthesize_sports_betting_report(league: str):
    """
    Correlate:
    - Game scores (API-Sports)
    - Betting odds (if available)
    - Injury reports
    - Team sentiment
    """
    pass
```

### Phase 3: Time-Series Analysis

```python
async def detect_trending_sentiment(ticker: str, days: int = 7):
    """
    Track sentiment changes over time:
    - Day 1: 0.2 (Neutral)
    - Day 7: 0.8 (Very Bullish)
    
    Identify inflection points and correlate with news events.
    """
    pass
```

### Phase 4: Predictive Insights

```python
async def predict_stock_movement(ticker: str):
    """
    Use historical correlation patterns:
    - If sentiment rises >0.3 in 24h → 70% chance of +2% stock move
    - If box office beats estimates → Studio stock +3-5% next day
    """
    pass
```

### Phase 5: Alert System

```python
async def monitor_unusual_correlations():
    """
    Alert when:
    - Stock moves >5% without news (insider trading signal?)
    - Sentiment diverges from price (contrarian opportunity?)
    - Multiple studios move together (sector rotation?)
    """
    pass
```

---

## Best Practices

### 1. Always Check Circuit Breakers

```python
if not circuit_breaker.is_open("newsapi"):
    # Safe to call
    news = await news_skills.search_news(query)
```

### 2. Record Tool Health

```python
from tool_health import tool_health

if result["status"] == "ok":
    tool_health.record("alphavantage", success=True)
else:
    tool_health.record("alphavantage", success=False)
```

### 3. Provide Fallback Synthesis

```python
synthesis = await _generate_llm_summary(prompt)

if not synthesis:
    # Fallback to template
    synthesis = f"{company} trading at ${price} ({change}). Sentiment: {sentiment}."
```

### 4. Log Source Failures

```python
sources_failed.append("Alpha Vantage (Sentiment) - rate limit")
log.warning("Alpha Vantage rate limit hit for sentiment analysis")
```

### 5. Return Timestamps

```python
"timestamp": datetime.now().isoformat()
```

**Why?** Users can see data freshness, especially important for cached results.

---

## Troubleshooting

### "All sources failed"

**Cause:** Rate limits hit across all APIs.

**Solution:**
1. Check `sources_failed` in response
2. Wait for rate limit reset (next UTC day for NewsAPI, 1 min for Alpha Vantage)
3. Use cached data if available
4. Consider upgrading to paid API tiers

### "Synthesis is empty"

**Cause:** LLM call failed or no data to synthesize.

**Solution:**
1. Check logs for LLM errors
2. Verify `stock_data`, `sentiment`, `news_articles` have content
3. Fallback synthesis should still work with partial data

### "Cache not working"

**Cause:** Cache key mismatch or TTL expired.

**Solution:**
1. Check cache key format: `company_report:DIS:2024-01-15-10`
2. Verify hour component matches (hourly granularity)
3. TTL is 15 minutes (900s) — older cache entries purge

### "Correlations not detected"

**Cause:** Stock movement < 2% threshold or sentiment neutral.

**Solution:**
1. Lower threshold in `find_correlations` (currently 2%)
2. Expand to more correlation types (news volume, competitor movements)
3. Use LLM synthesis for subtle patterns

---

## Metrics & Monitoring

### Track These KPIs

1. **API Success Rate:** `tool_health.get_stats("alphavantage")`
2. **Cache Hit Rate:** `cache_hits / (cache_hits + cache_misses)`
3. **Synthesis Quality:** Manual review of LLM outputs
4. **Response Time:** P50, P95, P99 for synthesis calls
5. **Daily API Usage:** Stay under rate limits

### Dashboard Example

```
┌─────────────────────────────────────────────────┐
│ Data Synthesis Health (Last 24h)                │
├─────────────────────────────────────────────────┤
│ Alpha Vantage:  18/25 calls used (72%)          │
│ NewsAPI:        45/100 calls used (45%)         │
│ Cache Hit Rate: 67%                             │
│ Avg Response:   1.2s                            │
│ LLM Success:    94%                             │
│ Correlations:   23 detected                     │
└─────────────────────────────────────────────────┘
```

---

## API Reference

See [API_REFERENCE.md](./API_REFERENCE.md) for detailed endpoint documentation.

---

## Contributing

When adding new synthesis functions:

1. **Follow naming:** `synthesize_<domain>_<type>`
2. **Return structure:**
   ```python
   {
       "status": "ok" | "error" | "partial",
       "synthesis": "LLM-generated summary",
       "sources": ["API1", "API2"],
       "sources_failed": ["API3"],
       "timestamp": "ISO-8601"
   }
   ```
3. **Add tests:** Minimum 80% coverage
4. **Update docs:** This file + API_REFERENCE.md
5. **Register skill:** Add to `SYNTHESIS_SKILLS` dict

---

## Credits

- **LLM:** Gemini 2.0 Flash (Google)
- **APIs:** NewsAPI, Alpha Vantage, API-Sports
- **Framework:** OpenClaw Discord Bot
- **Architecture:** Multi-source synthesis pattern inspired by financial analyst workflows
