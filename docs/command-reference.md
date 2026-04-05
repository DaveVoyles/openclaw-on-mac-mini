# OpenClaw Command Reference

> Complete guide to all 101+ available commands

## 📚 Research Commands

### `/research`
Perform autonomous web research on any topic with citations and summaries.

**Usage:**
```
/research topic:"AI agents" depth:3 max_sources:10
```

**Parameters:**
- `topic` - Research topic (required)
- `depth` - Search depth (1-5, default: 2)
- `max_sources` - Maximum sources (default: 10)

**Features:**
- Multi-source aggregation
- Citation tracking
- Automatic summarization
- Obsidian vault storage

---

### `/browse`
Browse and analyze web pages with full content extraction.

**Usage:**
```
/browse url:"https://example.com"
```

**Parameters:**
- `url` - Web page URL (required)
- `format` - Output format (text/markdown, default: markdown)

**Features:**
- JavaScript rendering support (Playwright)
- Clean content extraction (Trafilatura)
- Metadata extraction
- Link analysis

---

## 📊 Analytics Commands

### `/trends`
Analyze trends from your research and bookmarks.

**Usage:**
```
/trends timeframe:"7d" category:"ai"
```

**Parameters:**
- `timeframe` - Analysis period (1d, 7d, 30d, default: 7d)
- `category` - Filter by category (optional)

**Features:**
- Topic clustering
- Frequency analysis
- Trend visualization
- Anomaly detection

---

### `/digest`
Generate personalized daily/weekly digests.

**Usage:**
```
/digest type:"weekly" topics:"ai,crypto,real-estate"
```

**Parameters:**
- `type` - Digest type (daily/weekly, default: daily)
- `topics` - Comma-separated topics (optional)

**Features:**
- Multi-source aggregation
- Smart summarization
- Customizable templates
- Email delivery

---

## 📖 Bookmark Commands

### `/bookmark-add`
Save bookmarks with automatic metadata extraction.

**Usage:**
```
/bookmark-add url:"https://example.com" tags:"ai,research"
```

**Parameters:**
- `url` - Bookmark URL (required)
- `tags` - Comma-separated tags (optional)
- `notes` - Additional notes (optional)

**Features:**
- Automatic tagging
- Full-text search
- Duplicate detection
- Obsidian integration

---

### `/bookmark-search`
Search through your bookmark collection.

**Usage:**
```
/bookmark-search query:"machine learning" tags:"ai"
```

**Parameters:**
- `query` - Search query (required)
- `tags` - Filter by tags (optional)

**Features:**
- Full-text search
- Tag filtering
- Semantic search (ChromaDB)
- Relevance ranking

---

## 🏠 Real Estate Commands

### `/zillow-search`
Search for properties on Zillow with custom filters.

**Usage:**
```
/zillow-search location:"Seattle, WA" max_price:500000 bedrooms:3
```

**Parameters:**
- `location` - Search location (required)
- `max_price` - Maximum price (optional)
- `bedrooms` - Number of bedrooms (optional)
- `bathrooms` - Number of bathrooms (optional)

**Features:**
- Real-time listings
- Price tracking
- Automated alerts
- Market analysis

---

## 🎬 Media Commands

### `/trakt-search`
Search for movies and TV shows on Trakt.tv.

**Usage:**
```
/trakt-search query:"Breaking Bad" type:show
```

**Parameters:**
- `query` - Search query (required)
- `type` - Media type (movie/show, optional)

**Features:**
- IMDB/TMDB integration
- Watch history tracking
- Recommendations
- Collection management

---

## 🤖 System Commands

### `/health`
Check system health and status.

**Usage:**
```
/health
```

**Features:**
- Service status
- Resource utilization
- Uptime tracking
- Error logs

---

### `/backup`
Trigger manual backup of all data.

**Usage:**
```
/backup
```

**Features:**
- Full data backup
- Remote NAS storage
- Compression
- Verification

---

## 📧 Communication Commands

### `/sms`
Send SMS messages via Twilio integration.

**Usage:**
```
/sms to:"+1234567890" message:"Hello from OpenClaw"
```

**Parameters:**
- `to` - Phone number (required)
- `message` - Message text (required)

**Features:**
- Delivery confirmation
- Message history
- Template support
- Scheduling

---

## 🔧 Utility Commands

### `/schedule-task`
Schedule recurring tasks with cron expressions.

**Usage:**
```
/schedule-task name:"daily-backup" cron:"0 3 * * *" command:"backup"
```

**Parameters:**
- `name` - Task name (required)
- `cron` - Cron expression (required)
- `command` - Command to run (required)

**Features:**
- Cron scheduling
- Task management
- Execution history
- Error handling

---

### `/export-data`
Export data in various formats (CSV, JSON, Parquet).

**Usage:**
```
/export-data type:"bookmarks" format:"csv"
```

**Parameters:**
- `type` - Data type (required)
- `format` - Export format (csv/json/parquet, default: json)

**Features:**
- Multiple formats
- Compression
- Batch export
- Download links

---

## 📊 Reporting Commands

### `/weekly-recap`
Generate comprehensive weekly activity reports.

**Usage:**
```
/weekly-recap
```

**Features:**
- Activity summary
- Top commands
- Performance metrics
- Trend analysis

---

## 🔍 Advanced Search

### `/search-vault`
Search your Obsidian vault with semantic search.

**Usage:**
```
/search-vault query:"machine learning papers"
```

**Parameters:**
- `query` - Search query (required)

**Features:**
- Vector similarity search
- Full-text search
- Context snippets
- Relevance ranking

---

## 📈 Analytics Dashboard

### `/stats`
View comprehensive usage statistics.

**Usage:**
```
/stats timeframe:"30d"
```

**Parameters:**
- `timeframe` - Analysis period (7d/30d/90d, default: 30d)

**Features:**
- Command usage
- API costs
- Response times
- Success rates

---

## 🛠️ Development Commands

### `/test-api`
Test API integrations and connectivity.

**Usage:**
```
/test-api service:"gemini"
```

**Parameters:**
- `service` - API service name (required)

**Features:**
- Connection testing
- Rate limit checking
- Error diagnostics
- Performance metrics

---

## 📝 Notes

- All commands support auto-completion
- Commands are rate-limited per user
- Results are cached for performance
- Errors are logged and reported

## 🔗 Related Documentation

- [Architecture](ARCHITECTURE.html)
- [API Reference](API_REFERENCE.html)
- [Contributing](CONTRIBUTING.html)

---

[← Back to Home](index.html)
