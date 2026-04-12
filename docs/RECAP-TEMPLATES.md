# Recap Templates

Topic-specific templates for OpenClaw's weekly recap system.

## Quick Reference

| Template | Emoji | Focus | Key Data |
|----------|-------|-------|----------|
| `entertainment` | 🎬 | Hollywood & Streaming | Box office, streaming news, studio stocks (DIS, NFLX, WBD, PARA) |
| `sports` | 🏀 | NBA & Basketball | Recent games, standings, upcoming matchups, headlines |
| `tech` | 💻 | Tech Industry | Headlines, FAANG+ stocks, product launches, funding |
| `finance` | 💰 | Markets & Economy | Indices (SPY, QQQ, DIA), top movers, sector sentiment |
| `everything` | 🌍 | All Categories | Condensed cross-category summary |

**Usage:** `/ask Generate an entertainment recap for the last 7 days`

---

## Overview

Recap templates provide predefined configurations for generating topic-specific weekly recaps across five key areas:

- **Entertainment**: Box office, streaming, studio stocks, industry news
- **Sports**: NBA scores, standings, upcoming games, sports headlines  
- **Tech**: Tech headlines, FAANG+ stocks, product launches, funding
- **Finance**: Market indices, top movers, sector sentiment, financial news
- **Everything**: Condensed summary of all above categories

## Usage

### List Available Templates

```python
from skills.recap_templates import get_available_templates

templates = get_available_templates()
# Returns:
# {
#     "templates": ["entertainment", "sports", "tech", "finance", "everything"],
#     "details": {
#         "entertainment": {
#             "name": "Entertainment Industry Recap",
#             "description": "Box office news, streaming highlights...",
#             "format": "detailed",
#             "sections": ["box_office_top_5", "streaming_highlights", ...]
#         },
#         ...
#     }
# }
```

### Generate a Recap

```python
from skills.recap_templates import generate_recap_from_template

# Generate entertainment recap for last 7 days
recap = await generate_recap_from_template("entertainment", "7d")

# Generate tech recap for last 2 weeks  
recap = await generate_recap_from_template("tech", "14d")

# Generate everything recap for last month
recap = await generate_recap_from_template("everything", "1m")
```

### Date Range Formats

- `"7d"` - 7 days
- `"14d"` - 14 days
- `"2w"` - 2 weeks (14 days)
- `"1m"` - 1 month (30 days)
- `"30"` - 30 days (numeric)

## Template Details

### Entertainment Template

**Sections:**
- `box_office_top_5`: Top 5 box office news stories
- `streaming_highlights`: Streaming platform updates
- `studio_stocks`: Disney, Warner Bros, Netflix, Paramount stock performance
- `industry_news`: Entertainment industry headlines
- `sentiment_analysis`: Market sentiment for entertainment sector

**Stocks Tracked:** DIS, WBD, NFLX, PARA

### Sports Template

**Sections:**
- `nba_recent_games`: NBA games from last 7 days
- `nba_standings_top_10`: Current top 10 team standings
- `upcoming_matchups`: Next 7 days of marquee games
- `sports_headlines`: Latest NBA/basketball news
- `trending_stories`: Trending player and team stories

**Data Sources:** NBA API, sports news

### Tech Template

**Sections:**
- `top_tech_headlines`: Top 10 tech news stories
- `tech_stock_performance`: FAANG+ stock performance
- `product_launches`: New product announcements
- `funding_announcements`: Startup funding and VC news
- `industry_sentiment`: Tech sector market sentiment

**Stocks Tracked:** AAPL, GOOGL, META, AMZN, MSFT, NVDA, TSLA

### Finance Template

**Sections:**
- `market_summary`: S&P 500, Nasdaq, Dow performance
- `top_movers`: Biggest gainers and losers
- `sector_sentiment`: Sentiment across major sectors
- `financial_headlines`: Top finance news
- `economic_indicators`: GDP, inflation, Fed updates

**Indices Tracked:** SPY, QQQ, DIA

### Everything Template

**Sections:**
- `top_stories_all`: Top 20 headlines across all categories
- `key_market_moves`: Major stock movements
- `major_sports_results`: Recent game highlights
- `tech_highlights`: Tech news condensed
- `entertainment_updates`: Entertainment news condensed

**Format:** Condensed - prioritizes highest impact stories only

## Skills Registration

The templates are registered as LLM-callable skills:

```python
# In skills/__init__.py
SKILLS.update({
    "get_available_templates": get_available_templates,
    "generate_recap_from_template": generate_recap_from_template,
})

# Added to skill category
SKILL_CATEGORIES["📊 Weekly Recaps"] = [
    "get_available_templates",
    "generate_recap_from_template",
]
```

## Response Format

```python
{
    "status": "ok",
    "template": "entertainment",
    "recap": {
        "title": "Entertainment Industry Recap",
        "period": "2024-01-01 to 2024-01-08",
        "sections": {
            "box_office_top_5": [
                {
                    "title": "Box Office: Movie X Dominates",
                    "description": "...",
                    "url": "https://...",
                    "source": "Variety",
                    "publishedAt": "2024-01-05T10:00:00Z"
                },
                ...
            ],
            "studio_stocks": {
                "DIS": {"price": 95.42, "change": "+1.23%"},
                ...
            },
            ...
        },
        "summary": "Generated 5 sections for entertainment recap. Includes box office and streaming updates.",
        "generated_at": "2024-01-08T10:30:00Z"
    }
}
```

## Error Handling

- **Invalid template name**: Returns `{"status": "error", "message": "Unknown template 'xyz'"}`
- **API failures**: Individual sections include `{"error": "..."}` but recap continues
- **Missing data**: Empty arrays returned, recap generation doesn't fail

## Dependencies

Recap templates use the following skills:

- `skills/news_skills.py` - NewsAPI integration
- `skills/finance_skills.py` - Alpha Vantage stock data
- `skills/sports_skills.py` - API-Sports NBA data

## Testing

```bash
# Run tests
pytest tests/test_recap_templates.py -v

# Test specific template
python3 -c "from skills.recap_templates import apply_template; print(apply_template('tech', '7d'))"
```

## Future Enhancements

Potential additions:

- NFL/MLB/NHL templates (requires additional API setup)
- Crypto/DeFi template
- Gaming industry template
- Political/election template
- Weather/climate template
- Custom user-defined templates
- Template scheduling (auto-generate on intervals)
- Discord webhook integration for automated posting
