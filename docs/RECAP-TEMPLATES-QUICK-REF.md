# Recap Templates Quick Reference

## Available Templates

| Template | Emoji | Focus | Key Data |
|----------|-------|-------|----------|
| **entertainment** | 🎬 | Hollywood & Streaming | Box office, streaming news, studio stocks (DIS, NFLX, WBD, PARA) |
| **sports** | 🏀 | NBA & Basketball | Recent games, standings, upcoming matchups, headlines |
| **tech** | 💻 | Tech Industry | Headlines, FAANG+ stocks, product launches, funding |
| **finance** | 💰 | Markets & Economy | Indices (SPY, QQQ, DIA), top movers, sector sentiment |
| **everything** | 🌍 | All Categories | Condensed cross-category summary |

## Usage

### Via LLM
```
Generate an entertainment recap for the last 7 days
Show me a tech industry recap for the past 2 weeks
Create an everything recap for the last month
```

### Via Code
```python
from skills.recap_templates import generate_recap_from_template

# Entertainment recap
recap = await generate_recap_from_template("entertainment", "7d")

# Tech recap
recap = await generate_recap_from_template("tech", "2w")

# Everything recap
recap = await generate_recap_from_template("everything", "1m")
```

## Date Range Formats

| Format | Days | Example |
|--------|------|---------|
| `"7d"` | 7 | Last week |
| `"14d"` | 14 | Last 2 weeks |
| `"1w"` | 7 | Last week |
| `"2w"` | 14 | Last 2 weeks |
| `"1m"` | 30 | Last month |
| `"30"` | 30 | Last 30 days |

## Template Sections

### Entertainment (5 sections)
- Box office top 5 news
- Streaming highlights
- Studio stocks (Disney, Warner, Netflix, Paramount)
- Industry news
- Sentiment analysis

### Sports (5 sections)
- NBA recent games (last 7 days)
- NBA standings (top 10 teams)
- Upcoming matchups (next 7 days)
- Sports headlines
- Trending stories

### Tech (5 sections)
- Top tech headlines
- Tech stock performance (AAPL, GOOGL, META, AMZN, MSFT, NVDA, TSLA)
- Product launches
- Funding announcements
- Industry sentiment

### Finance (5 sections)
- Market summary (S&P 500, Nasdaq, Dow)
- Top movers (gainers/losers)
- Sector sentiment
- Financial headlines
- Economic indicators

### Everything (5 sections)
- Top stories across all categories
- Key market moves
- Major sports results
- Tech highlights
- Entertainment updates

## Response Format

```json
{
  "status": "ok",
  "template": "entertainment",
  "recap": {
    "title": "Entertainment Industry Recap",
    "period": "2024-01-01 to 2024-01-08",
    "sections": { ... },
    "summary": "Generated 5 sections...",
    "generated_at": "2024-01-08T10:30:00Z"
  }
}
```

## Skills

- `get_available_templates()` - List all templates
- `generate_recap_from_template(template, date_range)` - Generate recap

## See Also

- [Full Documentation](./RECAP-TEMPLATES.md)
- [Example Code](../examples/recap_templates_example.py)
- [Test Suite](../tests/test_recap_templates.py)
