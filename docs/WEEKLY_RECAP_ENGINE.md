# Weekly Recap Engine

## Overview

The Weekly Recap Engine is a unified reporting system that aggregates data from multiple premium APIs to generate comprehensive, Discord-ready markdown reports.

## Features

### Multi-API Integration
- **NewsAPI.org** - 80,000+ news sources across categories
- **API-Sports** - NBA scores, standings, and schedules
- **Alpha Vantage** - Stock prices, market news, and sentiment analysis

### Intelligent Aggregation
- Fetches data in parallel from multiple sources
- Graceful degradation when APIs hit rate limits
- Automatic error tracking and reporting
- Source attribution for all data

### Flexible Configuration
- **Topics**: entertainment, sports, tech, finance, general
- **Date Ranges**: last_week (7 days), last_3_days, last_month (30 days), custom
- **Format**: Discord-optimized markdown with section emojis

## Usage

### Basic Example

```python
from skills.reporting_skills import generate_weekly_recap

# Generate a full recap for the last week
recap = await generate_weekly_recap(
    topics=["entertainment", "sports", "tech", "finance"],
    date_range="last_week"
)
```

### Custom Date Range

```python
# Generate recap for specific dates
recap = await generate_weekly_recap(
    topics=["tech", "finance"],
    date_range="custom",
    from_date="2025-01-01",
    to_date="2025-01-15"
)
```

### Sports-Only Recap

```python
# Focus on sports news and NBA data
recap = await generate_weekly_recap(
    topics=["sports"],
    date_range="last_3_days"
)
```

## API Parameters

### `generate_weekly_recap()`

**Parameters:**
- `topics: list[str] | None` - Topics to include (default: all)
  - Options: `"entertainment"`, `"sports"`, `"tech"`, `"finance"`, `"general"`
  - If `None`, includes all topics
  
- `date_range: str` - Preset date range (default: `"last_week"`)
  - `"last_week"` - Last 7 days
  - `"last_3_days"` - Last 3 days
  - `"last_month"` - Last 30 days
  - `"custom"` - Use `from_date` and `to_date`
  
- `from_date: str | None` - Start date in YYYY-MM-DD format
  - Required if `date_range="custom"`
  
- `to_date: str | None` - End date in YYYY-MM-DD format
  - Optional, defaults to today

**Returns:**
- `str` - Markdown-formatted report

**Raises:**
- Returns error string if `date_range="custom"` but `from_date` not provided

## Report Structure

The generated report includes these sections:

### 1. 🗞️ News Highlights
Top stories by topic from NewsAPI:
- Entertainment: Box office, streaming, celebrity news
- Tech: Technology, startups, AI developments
- Finance: Business, markets, earnings
- Sports: Game highlights, player news
- General: Top headlines across categories

### 2. 🏀 Sports Recap (if "sports" in topics)
- NBA scores from yesterday
- Current NBA standings (Top 5)

### 3. 💰 Financial Summary (if "finance" or "entertainment" in topics)
- Entertainment stock prices (Disney, Warner Bros, Netflix, Paramount)
- Market news with AI-powered sentiment analysis
- Stock movement indicators (🟢 up, 🔴 down, ⚪ neutral)

### 4. 📊 Key Trends
- Article counts by category
- Notable patterns
- Source availability warnings

### 5. 📚 Data Sources
- Active sources used successfully
- Unavailable sources with error details
- Rate limit information

## Rate Limits

The engine respects API rate limits:

| API | Free Tier Limit | Used Per Call |
|-----|----------------|---------------|
| NewsAPI | 100 req/day | 1-5 (depends on topics) |
| API-Sports | 100 req/day | 0-2 (if sports enabled) |
| Alpha Vantage | 25 req/day | 0-5 (if finance enabled) |

### Rate Limit Strategy
- **Parallel requests** - Maximize efficiency
- **Graceful degradation** - Continue with available sources
- **Error tracking** - Report which APIs failed
- **Caching recommendation** - Cache results for 1+ hours

## Discord Integration

### Message Length Handling
- Reports optimized for Discord's 2000 char per field limit
- Warns if total length exceeds 5500 characters
- Suggests message splitting when needed

### Formatting
- Uses GitHub-flavored markdown
- Section headers with emojis for scannability
- Inline links for source citations
- Bullet lists for readability

## Error Handling

### Graceful Degradation
If an API fails (rate limit, timeout, network error):
1. Error is logged and tracked
2. Report continues with available data
3. Failed source listed in "Unavailable" section
4. User sees partial report, not complete failure

### Common Errors
- **Rate limit exceeded** - Wait 24 hours or upgrade API tier
- **Timeout** - Network issue, retry in a few minutes
- **Invalid API key** - Check `.env` configuration
- **No data available** - Try different date range

## Testing

Run the test suite:

```bash
cd ~/openclaw
source .venv/bin/activate
python test_weekly_recap.py
```

### Test Coverage
- ✅ Full recap with all topics
- ✅ Sports-only recap
- ✅ Custom date range
- ✅ Error handling for missing parameters

## LLM Integration

The function is registered in `REPORTING_SKILLS` and can be called by the LLM:

```python
# LLM can invoke this skill when users ask:
# - "Generate a weekly recap"
# - "What's happening in tech and sports this week?"
# - "Show me the latest entertainment news and stock updates"
```

The LLM automatically:
- Parses user intent for topics
- Determines appropriate date range
- Formats parameters correctly
- Presents the report to the user

## Configuration

### Environment Variables
Required in `.env`:

```bash
# NewsAPI (100 req/day free)
NEWSAPI_KEY=your_key_here

# API-Sports (100 req/day free)
APISPORTS_KEY=your_key_here

# Alpha Vantage (25 req/day free)
ALPHAVANTAGE_KEY=your_key_here
```

## Architecture

### Dependencies
- `skills/news_skills.py` - NewsAPI wrapper
- `skills/sports_skills.py` - API-Sports wrapper
- `skills/finance_skills.py` - Alpha Vantage wrapper
- `src/http_session.py` - Shared HTTP session manager

### SessionManager Pattern
Each API skill uses a shared `SessionManager` instance for HTTP requests:

```python
from http_session import SessionManager

_sessions = SessionManager(timeout=30, name="news_skills")

# In async functions:
session = await _sessions.get()
async with session.get(url, params=params) as resp:
    # handle response
```

This pattern:
- Reuses connections for efficiency
- Manages timeouts centrally
- Enables bulk shutdown during bot cleanup

## Future Enhancements

Potential improvements:
- [ ] Redis/SQLite caching layer to reduce API calls
- [ ] Webhook notifications when reports are ready
- [ ] Configurable section ordering
- [ ] PDF export option
- [ ] Scheduled automatic generation
- [ ] Historical trend analysis
- [ ] Custom topic keywords beyond categories
- [ ] Multi-language support
- [ ] Rich embeds for Discord with thumbnails

## Troubleshooting

### "SessionManager has no attribute 'get_session'"
**Fix:** Ensure all API skills use the instance pattern:
```python
_sessions = SessionManager(...)
session = await _sessions.get()
```

### "NEWSAPI_KEY not configured"
**Fix:** Add API key to `.env` file and reload environment

### "'NoneType' object is not subscriptable"
**Fix:** Handle None values from API responses:
```python
description = (article.get("description") or "")[:150]
```

### Tests fail with rate limit errors
**Wait:** API limits reset daily. Test tomorrow or use mock data.

## License

This feature is part of OpenClaw and follows the project's license.

## Credits

Built for OpenClaw by GitHub Copilot 🤖
Integrates with NewsAPI.org, API-Sports, and Alpha Vantage
