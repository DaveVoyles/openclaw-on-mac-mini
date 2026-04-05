"""Common type definitions for OpenClaw.

This module provides shared type aliases and structured types used across the codebase.
All new code should import and use these standard types for consistency and type safety.

Usage:
    from openclaw_types import JSON, UserID, SkillResult, MessageContext

    async def my_function(user: UserID, data: JSON) -> SkillResult:
        ...
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Literal, TypeAlias, TypedDict

# ---------------------------------------------------------------------------
# Common Type Aliases
# ---------------------------------------------------------------------------

# Generic data structures
JSON: TypeAlias = dict[str, Any]
"""Generic JSON object type."""

JSONValue: TypeAlias = str | int | float | bool | None | list[Any] | dict[str, Any]
"""Any valid JSON value."""

Headers: TypeAlias = dict[str, str]
"""HTTP headers dictionary."""

QueryParams: TypeAlias = dict[str, str | int | bool]
"""URL query parameters."""

# Discord identifiers
UserID: TypeAlias = str
"""Discord user ID (snowflake as string)."""

ChannelID: TypeAlias = str
"""Discord channel ID (snowflake as string)."""

GuildID: TypeAlias = str
"""Discord guild/server ID (snowflake as string)."""

MessageID: TypeAlias = str
"""Discord message ID (snowflake as string)."""

RoleID: TypeAlias = str
"""Discord role ID (snowflake as string)."""

# API identifiers
APIKey: TypeAlias = str
"""API key or token."""

URL: TypeAlias = str
"""URL string."""

FilePath: TypeAlias = str
"""File path string."""


# ---------------------------------------------------------------------------
# Structured Types - Message & Context
# ---------------------------------------------------------------------------


class MessageContext(TypedDict, total=False):
    """Context information for a Discord message.

    Attributes:
        user_id: Discord user who sent the message
        channel_id: Channel where message was sent
        guild_id: Server/guild ID (None for DMs)
        message_id: Discord message ID
        timestamp: When message was created
        is_dm: Whether this is a direct message
        author_name: Display name of message author
    """

    user_id: UserID
    channel_id: ChannelID
    guild_id: GuildID | None
    message_id: MessageID
    timestamp: datetime
    is_dm: bool
    author_name: str


class ConversationMessage(TypedDict):
    """A single message in a conversation history.

    Attributes:
        role: Message sender role (user, assistant, system)
        content: Message text content
        timestamp: When message was sent
        metadata: Optional extra data (tool calls, attachments, etc.)
    """

    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime
    metadata: JSON | None


# ---------------------------------------------------------------------------
# Structured Types - Skill Results
# ---------------------------------------------------------------------------


class SkillResult(TypedDict, total=False):
    """Standard result format for skill functions.

    All skill functions should return this structure for consistent
    error handling and response formatting.

    Attributes:
        status: Whether skill execution succeeded
        data: Result data (type varies by skill)
        message: Human-readable status/error message
        error_type: Classification of error if status is error
        metadata: Additional context (execution time, API calls, etc.)
    """

    status: Literal["success", "error", "partial"]
    data: Any
    message: str | None
    error_type: Literal["api_error", "validation_error", "timeout", "not_found", "permission_denied"] | None
    metadata: JSON | None


class APIResponse(TypedDict, total=False):
    """Standard API response structure.

    Use this for external API calls to normalize response handling.

    Attributes:
        status: HTTP status code or "success"/"error"
        data: Response payload
        error: Error message if request failed
        rate_limit: Remaining API quota
        rate_limit_reset: When rate limit resets (Unix timestamp)
        headers: Response headers
    """

    status: str | int
    data: list[dict[str, Any]] | dict[str, Any] | None
    error: str | None
    rate_limit: int | None
    rate_limit_reset: int | None
    headers: Headers | None


# ---------------------------------------------------------------------------
# Structured Types - Content & Media
# ---------------------------------------------------------------------------


class NewsArticle(TypedDict, total=False):
    """News article metadata.

    Attributes:
        title: Article headline
        url: Link to full article
        source: Publisher/source name
        published_at: Publication timestamp
        summary: Article summary/description
        author: Article author name
        category: News category/topic
        sentiment: Sentiment score (-1.0 to 1.0)
        image_url: Featured image URL
    """

    title: str
    url: URL
    source: str
    published_at: datetime | str
    summary: str | None
    author: str | None
    category: str | None
    sentiment: float | None
    image_url: URL | None


class SearchResult(TypedDict, total=False):
    """Web search result item.

    Attributes:
        title: Page title
        url: Page URL
        snippet: Text excerpt/description
        rank: Position in search results
        source: Search provider (Google, Bing, etc.)
        published_at: Publication date if available
    """

    title: str
    url: URL
    snippet: str
    rank: int | None
    source: str | None
    published_at: datetime | str | None


class WeatherData(TypedDict, total=False):
    """Weather information.

    Attributes:
        location: Location name
        temperature: Current temp (in specified units)
        feels_like: Apparent temperature
        condition: Weather description (clear, cloudy, rain, etc.)
        humidity: Humidity percentage
        wind_speed: Wind speed
        wind_direction: Wind direction in degrees
        pressure: Atmospheric pressure
        units: Temperature units (metric/imperial)
        timestamp: Data timestamp
    """

    location: str
    temperature: float
    feels_like: float | None
    condition: str
    humidity: int | None
    wind_speed: float | None
    wind_direction: int | None
    pressure: float | None
    units: Literal["metric", "imperial", "standard"]
    timestamp: datetime


# ---------------------------------------------------------------------------
# Structured Types - System & Monitoring
# ---------------------------------------------------------------------------


class HealthCheck(TypedDict):
    """Service health check result.

    Attributes:
        service: Service/component name
        status: Health status
        timestamp: Check timestamp
        latency_ms: Response time in milliseconds
        details: Additional diagnostic info
    """

    service: str
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    timestamp: datetime
    latency_ms: float | None
    details: JSON | None


class MetricDataPoint(TypedDict):
    """Time-series metric data point.

    Attributes:
        timestamp: Data point timestamp
        metric: Metric name
        value: Numeric value
        unit: Measurement unit
        tags: Classification tags
    """

    timestamp: datetime
    metric: str
    value: float
    unit: str | None
    tags: dict[str, str] | None


# ---------------------------------------------------------------------------
# Structured Types - User Preferences
# ---------------------------------------------------------------------------


class UserPreferences(TypedDict, total=False):
    """User configuration preferences.

    Attributes:
        user_id: Discord user ID
        timezone: User timezone (IANA format)
        language: Preferred language code
        notification_enabled: Whether to send notifications
        digest_schedule: Digest delivery schedule
        topics: Subscribed topics/interests
        created_at: Preference creation timestamp
        updated_at: Last update timestamp
    """

    user_id: UserID
    timezone: str
    language: str
    notification_enabled: bool
    digest_schedule: Literal["daily", "weekly", "custom", "manual"]
    topics: list[str]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Structured Types - Database
# ---------------------------------------------------------------------------


class DBRow(TypedDict):
    """Generic database row type.

    Use this as a base for specific row types.
    """

    id: int


# ---------------------------------------------------------------------------
# Callback Types
# ---------------------------------------------------------------------------

ErrorHandler: TypeAlias = "Callable[[Exception], None]"
"""Function that handles an error."""

AsyncCallback: TypeAlias = "Callable[..., Coroutine[Any, Any, None]]"
"""Async function with no return value."""

AsyncCallbackWithResult: TypeAlias = "Callable[..., Coroutine[Any, Any, Any]]"
"""Async function that returns a value."""
