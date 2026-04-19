# Trend Detection and Alerting System
<!-- Updated: 2026-04-18 -->


Comprehensive trend tracking, analysis, and alerting for news, sports, and financial topics.

## Overview

The trend detection system monitors topics over time, identifies emerging trends, detects anomalies, and sends alerts when significant changes occur. It uses time-series analysis, statistical methods, and configurable thresholds to provide actionable intelligence.

## Features

- ✅ **Time-Series Storage**: SQLite-backed persistent storage for historical data
- ✅ **Trend Analysis**: Volume spikes, sentiment shifts, velocity tracking
- ✅ **Anomaly Detection**: Z-score based statistical outlier identification
- ✅ **Breakout Detection**: Identify newly emerging topics
- ✅ **Smart Alerting**: Rate-limited Discord notifications with rich formatting
- ✅ **Background Jobs**: Automated data collection and analysis
- ✅ **Multi-Source**: Integrates with NewsAPI, Alpha Vantage, API-Sports

## Architecture

### Components

```
src/
├── trend_tracker.py      # Time-series storage and analysis engine
├── alert_manager.py      # Discord alerting and formatting
└── discord_commands/
    └── trends.py         # User-facing Discord commands

skills/
└── trend_skills.py       # LLM-callable trend functions
```

### Data Flow

```
┌─────────────────┐
│  News APIs      │ (NewsAPI, Alpha Vantage, API-Sports)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Data Collection │ (trend_skills._collect_data_point)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ TrendTracker    │ (SQLite storage)
│  - track_entity │
│  - get_trend    │
│  - is_trending  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Analysis Engine │ (volume, sentiment, velocity, z-score)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Alert Manager   │ (rate limiting, formatting)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Discord Channel │ (formatted embeds)
└─────────────────┘
```

## Usage

### Discord Commands

#### `/track <topic> <category>`
Start tracking a topic for trend analysis.

```
/track "Bitcoin" category:Finance
/track "Moana 2" category:Entertainment
/track "Lakers" category:Sports
```

**Parameters:**
- `topic`: Topic name (e.g., "Bitcoin", "Lakers")
- `category`: Entertainment, Finance, Sports, News, General

**Response:**
```
✅ Tracking Started
Topic: Bitcoin
Category: Finance
```

---

#### `/trending [category] [timeframe] [limit]`
Show currently trending topics.

```
/trending
/trending category:Finance timeframe:7d
/trending category:Sports limit:5
```

**Parameters:**
- `category` (optional): Filter by category
- `timeframe` (optional): 24h, 7d, or 30d (default: 24h)
- `limit` (optional): Max results 1-20 (default: 10)

**Response:**
```
🔥 Trending Topics — 24h
Showing top 3 trending topics in Finance

1. 🚨 Bitcoin
Volume: 47 (+380%)
Sentiment: 🟢 0.82 (+0.15)
Category: Finance

2. 📈 Ethereum
Volume: 30 (+220%)
Sentiment: 🟢 0.75 (+0.10)
Category: Finance
```

---

#### `/trends <topic> [category] [timeframe]`
Show detailed trend trajectory for a topic.

```
/trends "Bitcoin"
/trends "Bitcoin" category:Finance timeframe:7d
```

**Parameters:**
- `topic`: Topic to analyze
- `category` (optional): Category filter
- `timeframe` (optional): 24h, 7d, or 30d

**Response:**
```
🚨 Bitcoin — Trend Analysis

Bitcoin is experiencing a major spike in coverage.
Coverage volume has surged by 380% compared to the 7-day average.
Sentiment is strongly positive (0.82).

Volume: 47 (+380%)
Sentiment: 0.82 (+0.15)
Trend: Up

Indicators: 🔥 TRENDING | 🚨 SPIKE | ⚡ Velocity: 4.2x

📊 Bitcoin — Last 24h
10:00 │████████████████████████████ 47
09:00 │██████████████████ 35
08:00 │████████████ 25
...
```

---

#### `/breaking [category] [threshold]`
Detect breaking news and spikes.

```
/breaking
/breaking category:Entertainment threshold:5.0
```

**Parameters:**
- `category` (optional): Category to analyze (default: News)
- `threshold` (optional): Spike multiplier (default: 3.0)

**Response:**
```
🚨 Breaking News — Entertainment
Detected 2 topics with significant spikes

1. Moana 2
Spike: 4.7x normal volume
Volume: 47 articles
Sentiment: 🟢 0.82
Peak: 2h ago
```

---

#### `/untrack <topic>`
Stop tracking a topic.

```
/untrack "Bitcoin"
```

---

#### `/tracked`
List all tracked topics.

```
/tracked
```

**Response:**
```
📋 Tracked Topics (5)

Finance (2)
✅ Bitcoin
✅ Ethereum

Sports (1)
✅ Lakers

Entertainment (2)
✅ Moana 2
✅ Wicked
```

---

### LLM-Callable Skills

These functions are available to the AI agent for autonomous trend analysis.

#### `track_topic(topic, category, user_id)`
Start tracking a topic.

```python
result = await track_topic("Bitcoin", "Finance", "user123")
# Returns: {"status": "ok", "message": "Now tracking Bitcoin...", ...}
```

#### `get_trending_topics(category, timeframe, limit)`
Get top trending topics.

```python
result = await get_trending_topics("Finance", "24h", 10)
# Returns: {"status": "ok", "trending_topics": [...], "count": 3}
```

#### `detect_breaking_news(category, spike_threshold)`
Identify sudden spikes.

```python
result = await detect_breaking_news("Entertainment", spike_threshold=3.0)
# Returns: {"status": "ok", "breaking_news": [...], "count": 2}
```

#### `get_topic_trajectory(topic, category, timeframe)`
Show detailed trend trajectory.

```python
result = await get_topic_trajectory("Bitcoin", "Finance", "7d")
# Returns: {"status": "ok", "chart": "...", "analysis": "..."}
```

#### `list_tracked_topics()`
Get all tracked topics.

```python
result = await list_tracked_topics()
# Returns: {"status": "ok", "tracked_topics": [...], "count": 5}
```

---

### Background Jobs

#### Automated Trend Updates

Add to scheduler for periodic updates (every 1-6 hours):

```python
from trend_skills import update_all_tracked_trends

# In scheduler configuration
scheduler.create(
    action="update_all_tracked_trends",
    interval_minutes=180,  # Every 3 hours
    created_by="system"
)
```

This will:
1. Update trend data for all tracked topics
2. Collect fresh data from APIs
3. Clean up old data (90-day retention)
4. Return summary report

#### Automated Alerts

Send alerts when trends are detected:

```python
from alert_manager import check_and_alert_all

# In scheduler configuration
scheduler.create(
    action="check_and_alert_all",
    interval_minutes=360,  # Every 6 hours
    notify_channel_id=YOUR_CHANNEL_ID,
    created_by="system"
)
```

---

## Detection Algorithms

### Volume Spike Detection

Identifies sudden increases in coverage volume.

**Algorithm:**
```python
spike = current_volume >= avg_volume_7d * SPIKE_THRESHOLD
# SPIKE_THRESHOLD = 3.0 (configurable per topic)
```

**Example:**
- 7-day average: 10 articles
- Current volume: 35 articles
- Spike: 35 >= 10 * 3.0 = True ✅

---

### Sentiment Shift Detection

Detects significant changes in sentiment.

**Algorithm:**
```python
shift = abs(current_sentiment - avg_sentiment_24h) >= SENTIMENT_SHIFT_THRESHOLD
# SENTIMENT_SHIFT_THRESHOLD = 0.3 (configurable)
```

**Example:**
- Previous sentiment: 0.2 (neutral)
- Current sentiment: 0.8 (bullish)
- Shift: |0.8 - 0.2| >= 0.3 = True ✅

---

### Velocity Analysis

Measures acceleration/deceleration of trends.

**Algorithm:**
```python
recent_growth = (avg_volume_24h - avg_volume_7d) / avg_volume_7d
historical_growth = (avg_volume_7d - avg_volume_30d) / avg_volume_30d
velocity = recent_growth / historical_growth
# VELOCITY_THRESHOLD = 2.0 (2x acceleration)
```

**Example:**
- Historical growth: 10% per week
- Recent growth: 40% in 24h
- Velocity: 4.0x (accelerating rapidly) ✅

---

### Breakout Detection

Identifies new topics appearing suddenly.

**Algorithm:**
```python
breakout = (data_points < 3) and (current_volume >= min_volume)
# New topic with immediate activity
```

**Example:**
- Topic has only 2 data points (new)
- Volume: 15 articles
- Breakout: True ✅

---

### Anomaly Detection (Z-Score)

Statistical outlier detection using z-scores.

**Algorithm:**
```python
z_score = (current_value - mean) / std_dev
anomaly = abs(z_score) >= Z_SCORE_ANOMALY
# Z_SCORE_ANOMALY = 2.0 (2 standard deviations)
```

**Example:**
- Mean volume: 10
- Std dev: 5
- Current volume: 25
- Z-score: (25 - 10) / 5 = 3.0
- Anomaly: True ✅

---

## Alert Format

Alerts are sent as Discord embeds with rich formatting:

```
🚨 TRENDING ALERT: Bitcoin

Category: Finance
Volume: 47 articles ↑ 380% vs 7d avg
Sentiment: 🟢 0.82 Bullish (+0.15)
Trend: 🔥 Up
Peak: 2 hours ago
Sources: NewsAPI, Alpha Vantage

🚨 SPIKE DETECTED | ⚡ High Velocity (4.2x) | 📊 Z-Score: 3.5
```

---

## Configuration

### Topic-Specific Thresholds

Customize thresholds per topic:

```python
tracker.enable_tracking(
    topic="Bitcoin",
    category="Finance",
    spike_threshold=5.0,      # 5x instead of default 3x
    sentiment_threshold=0.5,   # 0.5 instead of default 0.3
)
```

### Rate Limiting

Prevent alert spam (default: 1 alert per hour per topic):

```python
# Check if alert is allowed
if tracker.can_alert("Bitcoin", cooldown_seconds=3600):
    send_alert()
    tracker.record_alert("Bitcoin")
```

### Data Retention

Configure how long to keep historical data:

```python
# Keep 90 days (default)
tracker.cleanup_old_data(days=90)

# Keep 30 days (shorter retention)
tracker.cleanup_old_data(days=30)
```

---

## Database Schema

### trend_data

Stores time-series data points.

| Column    | Type    | Description                    |
|-----------|---------|--------------------------------|
| id        | INTEGER | Primary key                    |
| timestamp | REAL    | Unix timestamp                 |
| topic     | TEXT    | Topic name                     |
| category  | TEXT    | Category                       |
| volume    | INTEGER | Number of mentions/articles    |
| sentiment | REAL    | Sentiment score (-1.0 to 1.0)  |
| sources   | TEXT    | Comma-separated source list    |
| metadata  | TEXT    | JSON metadata                  |

**Indexes:**
- `idx_trend_topic` on `topic`
- `idx_trend_category` on `category`
- `idx_trend_timestamp` on `timestamp`
- `idx_trend_lookup` on `(topic, category, timestamp)`

### trend_config

Stores tracking configuration.

| Column              | Type    | Description                        |
|---------------------|---------|------------------------------------|
| topic               | TEXT    | Primary key                        |
| category            | TEXT    | Category                           |
| enabled             | INTEGER | 1=enabled, 0=disabled              |
| spike_threshold     | REAL    | Custom spike threshold (default 3.0)|
| sentiment_threshold | REAL    | Custom sentiment threshold (0.3)   |
| alert_cooldown      | INTEGER | Seconds between alerts (3600)      |
| last_alert          | REAL    | Timestamp of last alert            |
| created_at          | REAL    | When tracking started              |
| user_id             | TEXT    | User who enabled tracking          |

---

## API Integration

### NewsAPI

Collects article volume and basic sentiment.

```python
news_data = await news_skills.search_news(topic, page_size=20)
volume = len(news_data["articles"])
sentiment = _calculate_simple_sentiment(articles)
```

### Alpha Vantage

Tracks stock prices and price change sentiment.

```python
stock_data = await finance_skills.get_stock_info(symbol)
sentiment = price_change_pct / 10.0  # Map to -1.0 to 1.0
```

### API-Sports

Monitors sports team mentions.

```python
games = await sports_skills.get_nba_scores()
relevant_games = [g for g in games if topic in str(g)]
volume = len(relevant_games)
```

---

## Performance

- **Database**: SQLite with WAL mode for concurrent reads/writes
- **Indexing**: Optimized indexes for fast lookups by topic, category, timestamp
- **Memory**: Efficient rolling window calculations (no full table scans)
- **Caching**: API responses cached by underlying skill modules
- **Rate Limiting**: Alert cooldown prevents notification spam

---

## Testing

Run tests with pytest:

```bash
# Run all trend tests
pytest tests/test_trend_tracker.py tests/test_trend_skills.py -v

# Run with coverage
pytest tests/test_trend_tracker.py tests/test_trend_skills.py --cov=src --cov=skills --cov-report=term-missing
```

**Test Coverage:**
- ✅ Basic entity tracking
- ✅ Time window filtering
- ✅ Spike detection
- ✅ Breakout detection
- ✅ Sentiment shift detection
- ✅ Anomaly detection (z-score)
- ✅ Velocity calculation
- ✅ Trend direction
- ✅ Alert rate limiting
- ✅ Data cleanup
- ✅ Multi-source collection
- ✅ LLM skill functions

---

## Troubleshooting

### No trends detected

**Issue:** `/trending` returns no results.

**Solutions:**
1. Check if topics are being tracked: `/tracked`
2. Add topics: `/track "Bitcoin" category:Finance`
3. Wait for data collection (run `update_all_tracked_trends`)
4. Verify API keys are configured in `.env`

### False positives

**Issue:** Too many spike alerts.

**Solutions:**
1. Increase spike threshold per topic
2. Increase alert cooldown period
3. Adjust minimum volume threshold

### Missing data points

**Issue:** Gaps in trend data.

**Solutions:**
1. Check background job is running
2. Verify API rate limits not exceeded
3. Check logs for API errors
4. Ensure scheduler is enabled

---

## Future Enhancements

Potential improvements for future versions:

- [ ] Real sentiment analysis (ML models instead of keyword matching)
- [ ] Correlation detection (related topics trending together)
- [ ] Predictive analytics (forecast future trends)
- [ ] Topic clustering (group related trending topics)
- [ ] Custom alert templates (user-defined formats)
- [ ] Web dashboard (visualize trends over time)
- [ ] Export reports (CSV, JSON, PDF)
- [ ] Webhook integrations (Slack, Teams, email)

---

## Examples

### Example 1: Track Box Office Release

```
# Track new movie release
/track "Moana 2" category:Entertainment

# Check if trending
/trending category:Entertainment

# View detailed trajectory
/trends "Moana 2"

# Output:
🚨 Moana 2 — Trend Analysis
🆕 BREAKOUT DETECTED
Volume: 47 articles (+380%)
Sentiment: 🟢 0.82 Bullish
```

### Example 2: Monitor Stock Volatility

```
# Track cryptocurrency
/track "Bitcoin" category:Finance

# Detect breaking news
/breaking category:Finance threshold:3.0

# Output:
🚨 Breaking News — Finance
1. Bitcoin
Spike: 4.7x normal volume
Volume: 47 articles
Sentiment: 🟢 0.82
Peak: 2h ago
```

### Example 3: Sports Team Performance

```
# Track NBA team
/track "Lakers" category:Sports

# Check trending
/trends "Lakers" timeframe:7d

# Output:
📊 Lakers — Trend Analysis
Volume: 25 articles (+120%)
Sentiment: ⚪ 0.15 Neutral
Trend: 🔥 Up
```

---

## Support

For issues or questions:
1. Check logs: `logs/openclaw.log`
2. Run tests: `pytest tests/test_trend*.py -v`
3. Review Discord audit logs: `/audit`
4. Check API health: Check tool_health dashboard

---

**Last Updated:** April 2024  
**Version:** 1.0.0  
**Author:** OpenClaw Development Team
