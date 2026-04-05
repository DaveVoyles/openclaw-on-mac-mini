# Personalized User Digests

## Overview

The OpenClaw personalized digest system allows users to configure custom daily or weekly digest reports tailored to their individual interests. Users can specify topics, stock tickers, sports teams, and keywords to follow, and receive automated digests delivered on a schedule or on-demand.

## Features

- **Per-user customization** — Each user has independent preferences
- **Multi-source aggregation** — Combines news, stocks, sports, and more
- **Smart filtering** — Relevance scoring based on user preferences
- **Flexible scheduling** — Daily, weekly, or on-demand delivery
- **Multiple formats** — Concise, detailed, or bullet-point styles
- **Exclusion filters** — Block unwanted topics from appearing
- **Discord integration** — Slash commands and interactive configuration

## Quick Start

### 1. Configure Your First Digest

```
/digest topic add AI
/digest stock add TSLA
/digest team add Lakers
```

### 2. Preview Your Digest

```
/digest preview
```

### 3. Get Your Digest Now

```
/digest now
```

### 4. Set a Schedule

```
/digest schedule daily time:08:00
```

## Configuration

### User Preference Schema

Each user's preferences are stored in `/memory/preferences/digests/<user_id>.json`:

```json
{
  "user_id": "discord_user_id",
  "topics": ["AI", "space exploration", "electric vehicles"],
  "stocks": ["TSLA", "NVDA", "DIS"],
  "teams": ["Lakers", "Patriots"],
  "keywords": ["OpenAI", "SpaceX"],
  "exclude": ["celebrity gossip", "reality TV"],
  "schedule": "daily",
  "delivery_time": "08:00",
  "delivery_day": "Monday",
  "timezone": "UTC",
  "format": "concise",
  "max_items": 10,
  "channels": ["dm"],
  "enabled": true,
  "created_at": "2024-04-05T10:00:00",
  "updated_at": "2024-04-05T12:30:00"
}
```

### Preference Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `topics` | list[str] | Topics to follow | `[]` |
| `stocks` | list[str] | Stock tickers to watch | `[]` |
| `teams` | list[str] | Sports teams to follow | `[]` |
| `keywords` | list[str] | Keywords to match | `[]` |
| `exclude` | list[str] | Topics to exclude | `[]` |
| `schedule` | str | Delivery frequency: `daily`, `weekly`, `manual` | `"daily"` |
| `delivery_time` | str | Time in HH:MM format | `"08:00"` |
| `delivery_day` | str | Day for weekly (e.g., `"Monday"`) | `"Monday"` |
| `timezone` | str | User timezone | `"UTC"` |
| `format` | str | Output style: `concise`, `detailed`, `bullets` | `"concise"` |
| `max_items` | int | Max items per section | `10` |
| `channels` | list[str] | Delivery targets: `["dm"]` or channel IDs | `["dm"]` |
| `enabled` | bool | Enable/disable delivery | `true` |

## Discord Commands

### `/digest now`
Get an instant personalized digest based on your current preferences.

**Usage:**
```
/digest now
```

**Output:**
```
📰 YOUR DAILY DIGEST - April 5, 2024

🤖 NEWS & TOPICS (3 articles)
• OpenAI launches GPT-5 with 10T parameters
• Tesla FSD Beta 12.0 achieves Level 4 autonomy
• NVDA stock hits $1,200 (+8%) on AI chip demand

📈 YOUR STOCKS (2 symbols)
• TSLA: $245.50 (+2.3%) - FSD news driving rally
• NVDA: $1,203.40 (+8.1%) - New datacenter contracts

🏀 SPORTS UPDATES (1 team)
• Lakers beat Warriors 115-108, AD scored 35pts

---
Generated from 3 sources filtered by your preferences
```

### `/digest preview`
Preview what your next scheduled digest will contain.

**Usage:**
```
/digest preview
```

Shows a preview with a "PREVIEW" header and includes a note about when the actual digest will be delivered.

### `/digest config`
View your current digest configuration.

**Usage:**
```
/digest config
```

Shows all your configured topics, stocks, teams, keywords, exclusions, schedule, and delivery settings.

### `/digest topic`
Add or remove topics from your digest.

**Usage:**
```
/digest topic add <topic>
/digest topic remove <topic>
```

**Examples:**
```
/digest topic add artificial intelligence
/digest topic add climate change
/digest topic remove politics
```

### `/digest stock`
Add or remove stock tickers from your watchlist.

**Usage:**
```
/digest stock add <ticker>
/digest stock remove <ticker>
```

**Examples:**
```
/digest stock add TSLA
/digest stock add NVDA
/digest stock remove AAPL
```

### `/digest team`
Add or remove sports teams from your digest.

**Usage:**
```
/digest team add <team>
/digest team remove <team>
```

**Examples:**
```
/digest team add Lakers
/digest team add New England Patriots
/digest team remove Yankees
```

### `/digest schedule`
Configure your digest delivery schedule.

**Usage:**
```
/digest schedule <frequency> [time] [day]
```

**Parameters:**
- `frequency`: `daily`, `weekly`, or `manual`
- `time`: Delivery time in HH:MM format (default: `08:00`)
- `day`: Day of week for weekly digests (default: `Monday`)

**Examples:**
```
/digest schedule daily time:08:00
/digest schedule weekly time:09:00 day:Monday
/digest schedule manual
```

### `/digest enable` / `/digest disable`
Enable or disable scheduled digest delivery.

**Usage:**
```
/digest enable
/digest disable
```

## LLM-Callable Skills

The digest system provides LLM-callable skills that can be invoked by the AI agent:

### `configure_digest(preferences_dict)`
Configure digest preferences programmatically.

```python
await configure_digest({
    "topics": ["AI", "robotics"],
    "stocks": ["TSLA", "NVDA"],
    "schedule": "daily",
    "delivery_time": "08:00",
    "format": "concise"
})
```

### `get_my_digest()`
Generate an instant digest for the calling user.

```python
digest = await get_my_digest()
```

### `preview_digest()`
Preview the next scheduled digest.

```python
preview = await preview_digest()
```

### `add_digest_topic(topic)` / `remove_digest_topic(topic)`
Add or remove topics.

```python
await add_digest_topic("quantum computing")
await remove_digest_topic("politics")
```

### `add_digest_stock(ticker)` / `remove_digest_stock(ticker)`
Add or remove stocks.

```python
await add_digest_stock("TSLA")
await remove_digest_stock("AAPL")
```

### `add_digest_team(team)` / `remove_digest_team(team)`
Add or remove sports teams.

```python
await add_digest_team("Lakers")
await remove_digest_team("Yankees")
```

### `update_digest_preferences(key, value)`
Update a specific preference field.

```python
await update_digest_preferences("schedule", "weekly")
await update_digest_preferences("format", "detailed")
```

### `get_digest_config()`
Get the current digest configuration.

```python
config = await get_digest_config()
```

## Content Filtering & Relevance

### Relevance Scoring

The digest system calculates relevance scores for each piece of content:

- **Exact topic match** (in title/first 100 chars): `1.0`
- **Keyword match** (anywhere in content): `0.7`
- **Topic mention** (anywhere in content): `0.5`
- **Base score** (all content): `0.3`

Scores are cumulative, capped at `3.0`.

### Filtering Process

1. **Fetch** content from configured sources (news, stocks, sports)
2. **Score** each item based on user preferences
3. **Exclude** items matching exclusion filters
4. **Sort** by relevance × recency
5. **Limit** to `max_items` per section
6. **Deduplicate** across sources

### Example Relevance Calculation

For content: "OpenAI announces breakthrough in AI safety research"

User preferences:
- Topics: `["AI", "machine learning"]`
- Keywords: `["OpenAI", "safety"]`

Relevance score:
- Topic "AI" in first 100 chars: `+1.0`
- Keyword "OpenAI" match: `+0.7`
- Keyword "safety" match: `+0.7`
- **Total: 2.4**

## Scheduled Delivery

### Integration with Scheduler

Digest delivery integrates with the OpenClaw scheduler (`src/scheduler.py`):

```python
from scheduler import scheduler
from digest_manager import get_digest_manager

# Schedule daily digest for a user
scheduler.create(
    action="send_user_digest",
    args={"user_id": "123456"},
    cron_hour=8,
    cron_minute=0,
    created_by="system"
)

# Schedule weekly digest
scheduler.create(
    action="send_user_digest",
    args={"user_id": "123456"},
    cron_expression="0 9 * * 1",  # Monday at 9am
    created_by="system"
)
```

### Per-User Scheduling

Each user can have independent delivery times:
- User A: Daily at 08:00 UTC
- User B: Weekly on Monday at 09:00 EST
- User C: Manual only (no scheduled delivery)

## Storage & Persistence

### File Structure

```
/memory/preferences/digests/
├── 123456789.json          # User 1 preferences
├── 987654321.json          # User 2 preferences
└── 555555555.json          # User 3 preferences
```

### Atomic Writes

Preferences are saved atomically using `utils.atomic_write()` to prevent corruption:

```python
from utils import atomic_write

atomic_write(path, json.dumps(preferences, indent=2))
```

### Backwards Compatibility

New preferences are merged with defaults, ensuring backwards compatibility:

```python
prefs = {**DEFAULT_DIGEST_PREFERENCES, **loaded_prefs}
```

## Examples

### Example 1: Tech Enthusiast

```python
# Configuration
{
    "topics": ["AI", "quantum computing", "space exploration"],
    "stocks": ["TSLA", "NVDA", "GOOGL"],
    "keywords": ["OpenAI", "SpaceX", "NVIDIA"],
    "exclude": ["celebrity news"],
    "schedule": "daily",
    "delivery_time": "07:00",
    "format": "detailed"
}

# Sample digest output
📰 YOUR DAILY DIGEST - April 5, 2024

🤖 AI & TECH (4 articles)
• OpenAI releases GPT-5 with multimodal capabilities
• Google's quantum computer achieves new milestone
• NVIDIA announces next-gen AI chips
• Tesla Optimus robot demonstrated at shareholder meeting

📈 YOUR STOCKS
• TSLA: $245.50 (+2.3%)
• NVDA: $1,203.40 (+8.1%)
• GOOGL: $142.30 (+1.5%)

🚀 SPACE NEWS (2 articles)
• SpaceX Starship completes Mars cargo mission
• NASA announces new exoplanet discoveries
```

### Example 2: Sports Fan

```python
# Configuration
{
    "topics": ["basketball", "football"],
    "teams": ["Lakers", "Patriots", "Dodgers"],
    "schedule": "daily",
    "delivery_time": "18:00",
    "format": "concise"
}

# Sample digest output
📰 YOUR DAILY DIGEST - April 5, 2024

🏀 SPORTS UPDATES
• Lakers beat Warriors 115-108, LeBron had 28pts
• Patriots sign new quarterback in free agency
• Dodgers win opener 5-3 against Giants

---
Generated from 3 sources filtered by your preferences
```

### Example 3: Investor

```python
# Configuration
{
    "stocks": ["TSLA", "NVDA", "AAPL", "MSFT", "GOOGL"],
    "topics": ["stock market", "earnings"],
    "keywords": ["earnings report", "revenue", "guidance"],
    "schedule": "daily",
    "delivery_time": "09:00",
    "format": "detailed",
    "max_items": 15
}
```

## API Reference

### DigestManager

Main class for managing digest preferences and generation.

#### Methods

##### `save_preferences(user_id: str, preferences: dict) -> None`
Save digest preferences for a user.

##### `get_preferences(user_id: str) -> dict`
Get digest preferences for a user (returns defaults if not found).

##### `update_preference(user_id: str, key: str, value: Any) -> None`
Update a single preference field.

##### `add_to_list(user_id: str, key: str, value: str) -> None`
Add an item to a list preference (topics, stocks, teams, keywords, exclude).

##### `remove_from_list(user_id: str, key: str, value: str) -> None`
Remove an item from a list preference.

##### `list_all_users() -> list[str]`
Get list of all users with digest preferences.

##### `async generate_digest(user_id: str, preview: bool = False) -> str`
Generate a personalized digest for a user.

## Troubleshooting

### Common Issues

#### "You haven't configured any digest preferences yet"
**Cause:** No topics, stocks, or teams configured.  
**Solution:** Add at least one preference using `/digest topic add`, `/digest stock add`, or `/digest team add`.

#### "Your digest is currently disabled"
**Cause:** Digest delivery is disabled.  
**Solution:** Enable with `/digest enable`.

#### Empty sections in digest
**Cause:** No matching content found for configured preferences.  
**Solution:** Try broader topics or different keywords.

#### Digest not delivered at scheduled time
**Cause:** Schedule may not be configured, or scheduler may be disabled.  
**Solution:** Check `/digest config` and verify schedule is set correctly.

## Performance Considerations

- **Rate limits**: Digest generation respects API rate limits for news, stocks, and sports APIs
- **Timeouts**: Individual API calls timeout after 10-15 seconds
- **Caching**: Consider caching digest results for a short period to avoid regenerating
- **Batch processing**: For multiple users, schedule deliveries with staggered times

## Security & Privacy

- **User isolation**: Each user's preferences are stored separately
- **File permissions**: Preference files are stored with appropriate permissions
- **No data sharing**: User preferences are never shared between users
- **Opt-in only**: Users must explicitly configure preferences; no automatic enrollment

## Future Enhancements

Potential improvements for future versions:

- **Timezone support**: Automatic conversion to user's local timezone
- **Multi-channel delivery**: Post to multiple channels or DM
- **Digest history**: View past digests
- **Smart recommendations**: Suggest topics/stocks based on reading patterns
- **Email delivery**: Send digests via email in addition to Discord
- **RSS feed integration**: Include specific RSS feeds in digest
- **Sentiment analysis**: Include sentiment scores for news/stocks
- **Trending topics**: Auto-detect trending topics matching user interests

## Contributing

To extend the digest system:

1. Add new content sources in `src/digest_manager.py`
2. Create new section generators (e.g., `_generate_crypto_section`)
3. Add new preference fields to `DEFAULT_DIGEST_PREFERENCES`
4. Update tests in `tests/test_digest_manager.py`
5. Document new features in this file

## License

Part of the OpenClaw project. See main repository for license information.
