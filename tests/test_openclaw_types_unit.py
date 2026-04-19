"""Unit tests for openclaw_types.py — type aliases, TypedDicts, and structured types."""

from __future__ import annotations

from datetime import datetime

from openclaw_types import (
    APIResponse,
    ConversationMessage,
    DBRow,
    HealthCheck,
    MessageContext,
    MetricDataPoint,
    NewsArticle,
    SearchResult,
    SkillResult,
    UserPreferences,
    WeatherData,
)

# ---------------------------------------------------------------------------
# Type aliases are plain strings — smoke-check that they resolve
# ---------------------------------------------------------------------------


class TestTypeAliases:
    def test_import_json_alias(self):
        from openclaw_types import JSON

        d: JSON = {"key": "value"}
        assert d["key"] == "value"

    def test_import_jsonvalue_alias(self):
        from openclaw_types import JSONValue

        v: JSONValue = 42
        assert v == 42

    def test_import_headers(self):
        from openclaw_types import Headers

        h: Headers = {"Content-Type": "application/json"}
        assert h["Content-Type"] == "application/json"

    def test_import_query_params(self):
        from openclaw_types import QueryParams

        q: QueryParams = {"page": 1, "active": True}
        assert q["page"] == 1

    def test_discord_id_aliases(self):
        from openclaw_types import ChannelID, GuildID, MessageID, RoleID, UserID

        uid: UserID = "123456789"
        cid: ChannelID = "987654321"
        gid: GuildID = "111111111"
        mid: MessageID = "222222222"
        rid: RoleID = "333333333"
        assert uid == "123456789"
        assert cid and gid and mid and rid

    def test_api_aliases(self):
        from openclaw_types import URL, APIKey, FilePath

        key: APIKey = "secret"
        url: URL = "https://example.com"
        fp: FilePath = "/tmp/file.txt"
        assert key and url and fp

    def test_openclaw_types_unit_callback_aliases(self):
        from openclaw_types import AsyncCallback, AsyncCallbackWithResult, ErrorHandler

        assert isinstance(ErrorHandler, str)
        assert isinstance(AsyncCallback, str)
        assert isinstance(AsyncCallbackWithResult, str)


# ---------------------------------------------------------------------------
# MessageContext
# ---------------------------------------------------------------------------


class TestMessageContext:
    def test_minimal_creation(self):
        ctx: MessageContext = {"user_id": "u1", "channel_id": "c1"}
        assert ctx["user_id"] == "u1"

    def test_full_creation(self):
        ctx: MessageContext = {
            "user_id": "u1",
            "channel_id": "c1",
            "guild_id": "g1",
            "message_id": "m1",
            "timestamp": datetime(2025, 1, 1),
            "is_dm": False,
            "author_name": "Alice",
        }
        assert ctx["author_name"] == "Alice"
        assert ctx["is_dm"] is False

    def test_dm_has_no_guild(self):
        ctx: MessageContext = {
            "user_id": "u1",
            "channel_id": "c1",
            "guild_id": None,
            "is_dm": True,
        }
        assert ctx["guild_id"] is None
        assert ctx["is_dm"] is True


# ---------------------------------------------------------------------------
# ConversationMessage
# ---------------------------------------------------------------------------


class TestConversationMessage:
    def test_user_role(self):
        msg: ConversationMessage = {
            "role": "user",
            "content": "Hello",
            "timestamp": datetime(2025, 1, 1),
            "metadata": None,
        }
        assert msg["role"] == "user"

    def test_assistant_role(self):
        msg: ConversationMessage = {
            "role": "assistant",
            "content": "Hi there",
            "timestamp": datetime(2025, 1, 1),
            "metadata": {"tokens": 10},
        }
        assert msg["metadata"]["tokens"] == 10

    def test_system_role(self):
        msg: ConversationMessage = {
            "role": "system",
            "content": "You are helpful.",
            "timestamp": datetime(2025, 1, 1),
            "metadata": None,
        }
        assert msg["role"] == "system"


# ---------------------------------------------------------------------------
# SkillResult
# ---------------------------------------------------------------------------


class TestSkillResult:
    def test_success_result(self):
        result: SkillResult = {"status": "success", "data": {"answer": 42}}
        assert result["status"] == "success"
        assert result["data"]["answer"] == 42

    def test_error_result(self):
        result: SkillResult = {
            "status": "error",
            "message": "API failed",
            "error_type": "api_error",
        }
        assert result["error_type"] == "api_error"

    def test_partial_result(self):
        result: SkillResult = {
            "status": "partial",
            "data": [1, 2, 3],
            "message": "Only 3 results",
        }
        assert result["status"] == "partial"

    def test_empty_result(self):
        result: SkillResult = {}
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# APIResponse
# ---------------------------------------------------------------------------


class TestAPIResponse:
    def test_success_response(self):
        resp: APIResponse = {"status": "success", "data": {"key": "value"}}
        assert resp["status"] == "success"

    def test_error_response(self):
        resp: APIResponse = {"status": 500, "error": "Internal error"}
        assert resp["status"] == 500
        assert resp["error"] == "Internal error"

    def test_rate_limit_fields(self):
        resp: APIResponse = {
            "status": 200,
            "data": [],
            "rate_limit": 100,
            "rate_limit_reset": 1700000000,
        }
        assert resp["rate_limit"] == 100
        assert resp["rate_limit_reset"] == 1700000000


# ---------------------------------------------------------------------------
# NewsArticle
# ---------------------------------------------------------------------------


class TestNewsArticle:
    def test_minimal_article(self):
        article: NewsArticle = {"title": "Big News", "url": "https://example.com/news"}
        assert article["title"] == "Big News"

    def test_full_article(self):
        article: NewsArticle = {
            "title": "AI Advances",
            "url": "https://example.com/ai",
            "source": "TechCrunch",
            "published_at": "2025-01-01T00:00:00Z",
            "summary": "AI is growing fast",
            "author": "Jane Doe",
            "category": "technology",
            "sentiment": 0.8,
            "image_url": "https://example.com/img.jpg",
        }
        assert article["sentiment"] == 0.8
        assert article["category"] == "technology"


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_minimal_result(self):
        result: SearchResult = {"title": "Page", "url": "https://example.com", "snippet": "..."}
        assert result["snippet"] == "..."

    def test_ranked_result(self):
        result: SearchResult = {
            "title": "Python Docs",
            "url": "https://docs.python.org",
            "snippet": "Official docs",
            "rank": 1,
            "source": "Google",
        }
        assert result["rank"] == 1


# ---------------------------------------------------------------------------
# WeatherData
# ---------------------------------------------------------------------------


class TestWeatherData:
    def test_basic_weather(self):
        weather: WeatherData = {
            "location": "Seattle",
            "temperature": 15.5,
            "condition": "cloudy",
            "units": "metric",
            "timestamp": datetime(2025, 1, 1),
        }
        assert weather["location"] == "Seattle"
        assert weather["units"] == "metric"

    def test_imperial_units(self):
        weather: WeatherData = {
            "location": "Dallas",
            "temperature": 72.0,
            "condition": "sunny",
            "units": "imperial",
            "timestamp": datetime(2025, 6, 1),
            "humidity": 45,
            "wind_speed": 10.0,
        }
        assert weather["humidity"] == 45


# ---------------------------------------------------------------------------
# HealthCheck
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_healthy_service(self):
        check: HealthCheck = {
            "service": "database",
            "status": "healthy",
            "timestamp": datetime(2025, 1, 1),
            "latency_ms": 5.2,
            "details": None,
        }
        assert check["status"] == "healthy"
        assert check["latency_ms"] == 5.2

    def test_unhealthy_service(self):
        check: HealthCheck = {
            "service": "redis",
            "status": "unhealthy",
            "timestamp": datetime(2025, 1, 1),
            "latency_ms": None,
            "details": {"error": "connection refused"},
        }
        assert check["status"] == "unhealthy"
        assert check["details"]["error"] == "connection refused"

    def test_degraded_service(self):
        check: HealthCheck = {
            "service": "api",
            "status": "degraded",
            "timestamp": datetime(2025, 1, 1),
            "latency_ms": 850.0,
            "details": {"p99": 900},
        }
        assert check["status"] == "degraded"


# ---------------------------------------------------------------------------
# MetricDataPoint
# ---------------------------------------------------------------------------


class TestMetricDataPoint:
    def test_basic_metric(self):
        dp: MetricDataPoint = {
            "timestamp": datetime(2025, 1, 1),
            "metric": "cpu_usage",
            "value": 72.5,
            "unit": "percent",
            "tags": {"host": "server1"},
        }
        assert dp["value"] == 72.5
        assert dp["tags"]["host"] == "server1"

    def test_no_unit_or_tags(self):
        dp: MetricDataPoint = {
            "timestamp": datetime(2025, 1, 1),
            "metric": "errors",
            "value": 3.0,
            "unit": None,
            "tags": None,
        }
        assert dp["unit"] is None


# ---------------------------------------------------------------------------
# UserPreferences
# ---------------------------------------------------------------------------


class TestUserPreferences:
    def test_minimal_preferences(self):
        prefs: UserPreferences = {"user_id": "u1"}
        assert prefs["user_id"] == "u1"

    def test_full_preferences(self):
        prefs: UserPreferences = {
            "user_id": "u1",
            "timezone": "America/Seattle",
            "language": "en",
            "notification_enabled": True,
            "digest_schedule": "daily",
            "topics": ["tech", "news"],
            "created_at": datetime(2025, 1, 1),
            "updated_at": datetime(2025, 6, 1),
        }
        assert prefs["timezone"] == "America/Seattle"
        assert prefs["digest_schedule"] == "daily"
        assert "tech" in prefs["topics"]


# ---------------------------------------------------------------------------
# DBRow
# ---------------------------------------------------------------------------


class TestDBRow:
    def test_basic_row(self):
        row: DBRow = {"id": 42}
        assert row["id"] == 42
