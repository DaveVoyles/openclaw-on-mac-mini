"""Shared numeric constants for the OpenClaw Discord bot."""

# ---------------------------------------------------------------------------
# Discord embed limits (chars)
# ---------------------------------------------------------------------------
EMBED_DESC_LIMIT = 4000         # Discord max is 4096; leave headroom
EMBED_SPLIT_LIMIT = 3800        # Max chars per chunk when splitting long responses
EMBED_FIELD_LIMIT = 1024        # Discord embed field value max
EMBED_PROMPT_LIMIT = 3500       # Truncation limit for text stuffed into an LLM prompt

# ---------------------------------------------------------------------------
# Timing intervals (seconds)
# ---------------------------------------------------------------------------
PROACTIVE_SCAN_INTERVAL = 7200  # 2 hours between autonomous insight scans
CLEANUP_INTERVAL = 300          # 5 minutes between expired-conversation sweeps
AUDIT_FLUSH_INTERVAL = 30       # Seconds between audit-log buffer flushes
BRIEFING_CHECK_INTERVAL = 60    # Seconds between morning-briefing schedule checks

# ---------------------------------------------------------------------------
# Morning briefing schedule
# ---------------------------------------------------------------------------
BRIEFING_HOUR = 8               # Hour (0-23) to post the morning briefing
BRIEFING_MINUTE_WINDOW = 5      # Fire only within the first N minutes of BRIEFING_HOUR

# ---------------------------------------------------------------------------
# Content / size limits
# ---------------------------------------------------------------------------
LOG_SNIPPET_MAX_CHARS = 600     # Max chars per container log snippet (proactive scan)
MEMORY_SNIPPET_MAX_CHARS = 500  # Max chars saved to QMD memory from research
DOCUMENT_MAX_CHARS = 50_000     # Max chars sent to LLM for document analysis
ATTACHMENT_TEXT_MAX_CHARS = 8000 # Max chars extracted from a text attachment in /ask
PROACTIVE_LOG_LINES = 25        # Lines fetched per container in proactive scan
DEFAULT_ANALYZE_LINES = 50      # Default line count for /analyze command
PDF_MAX_PAGES = 50              # Max pages to extract from a PDF
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB max upload size
