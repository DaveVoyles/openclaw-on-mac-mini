"""Tests for zero-coverage simple modules."""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp/test_zero")
os.environ.setdefault("AUDIT_DIR", "/tmp/test_zero")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/test_zero.db")

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# ---------------------------------------------------------------------------
# Module imports under test
# ---------------------------------------------------------------------------
import openclaw_types
import profiler as profiler_mod
import reminder_manager as rm_mod
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
from profiler import Profiler, get_profiler, profile_memory
from reminder_manager import Reminder, ReminderManager, parse_time_expression

# =============================================================================
# openclaw_types.py
# =============================================================================


class TestOpenclawTypes:
    """TypedDict instantiation and type-alias reachability."""

    def test_type_aliases_importable(self):
        assert openclaw_types.JSON is not None
        assert openclaw_types.JSONValue is not None
        assert openclaw_types.Headers is not None
        assert openclaw_types.QueryParams is not None
        assert openclaw_types.UserID is not None
        assert openclaw_types.ChannelID is not None
        assert openclaw_types.GuildID is not None
        assert openclaw_types.MessageID is not None
        assert openclaw_types.RoleID is not None
        assert openclaw_types.APIKey is not None
        assert openclaw_types.URL is not None
        assert openclaw_types.FilePath is not None

    def test_zero_coverage_modules_callback_aliases(self):
        assert openclaw_types.ErrorHandler is not None
        assert openclaw_types.AsyncCallback is not None
        assert openclaw_types.AsyncCallbackWithResult is not None

    # -- MessageContext -------------------------------------------------------

    def test_zero_coverage_modules_message_context_full(self):
        ctx: MessageContext = {
            "user_id": "123",
            "channel_id": "456",
            "guild_id": "789",
            "message_id": "999",
            "timestamp": datetime.now(),
            "is_dm": False,
            "author_name": "Alice",
        }
        assert ctx["user_id"] == "123"
        assert ctx["is_dm"] is False
        assert ctx["author_name"] == "Alice"

    def test_message_context_partial(self):
        """total=False means all keys optional."""
        ctx: MessageContext = {"user_id": "1", "is_dm": True}
        assert ctx["is_dm"] is True

    def test_message_context_dm(self):
        ctx: MessageContext = {"user_id": "u1", "guild_id": None, "is_dm": True}
        assert ctx["guild_id"] is None

    # -- ConversationMessage --------------------------------------------------

    def test_conversation_message_user(self):
        msg: ConversationMessage = {
            "role": "user",
            "content": "Hello",
            "timestamp": datetime.now(),
            "metadata": None,
        }
        assert msg["role"] == "user"
        assert msg["metadata"] is None

    def test_conversation_message_assistant(self):
        msg: ConversationMessage = {
            "role": "assistant",
            "content": "Hi there",
            "timestamp": datetime.now(),
            "metadata": {"tool": "search", "calls": 1},
        }
        assert msg["metadata"]["tool"] == "search"

    def test_conversation_message_system(self):
        msg: ConversationMessage = {
            "role": "system",
            "content": "You are helpful.",
            "timestamp": datetime.now(),
            "metadata": None,
        }
        assert msg["role"] == "system"

    # -- SkillResult ----------------------------------------------------------

    def test_zero_coverage_modules_skill_result_success(self):
        r: SkillResult = {
            "status": "success",
            "data": {"answer": 42},
            "message": "Done",
            "error_type": None,
            "metadata": {"duration_ms": 120},
        }
        assert r["status"] == "success"
        assert r["data"]["answer"] == 42

    def test_skill_result_error(self):
        r: SkillResult = {
            "status": "error",
            "error_type": "api_error",
            "message": "API unavailable",
        }
        assert r["error_type"] == "api_error"

    def test_skill_result_partial(self):
        r: SkillResult = {"status": "partial", "data": [1, 2, 3]}
        assert r["status"] == "partial"

    def test_skill_result_all_error_types(self):
        for etype in ("api_error", "validation_error", "timeout", "not_found", "permission_denied"):
            r: SkillResult = {"status": "error", "error_type": etype}
            assert r["error_type"] == etype

    # -- APIResponse ----------------------------------------------------------

    def test_api_response_success(self):
        resp: APIResponse = {
            "status": 200,
            "data": {"items": []},
            "error": None,
            "rate_limit": 100,
            "rate_limit_reset": 1700000000,
            "headers": {"Content-Type": "application/json"},
        }
        assert resp["status"] == 200
        assert resp["rate_limit"] == 100

    def test_api_response_error(self):
        resp: APIResponse = {"status": "error", "data": None, "error": "Unauthorized"}
        assert resp["error"] == "Unauthorized"

    # -- NewsArticle ----------------------------------------------------------

    def test_news_article_full(self):
        a: NewsArticle = {
            "title": "Big News",
            "url": "https://example.com/news",
            "source": "Example News",
            "published_at": datetime.now(),
            "summary": "Something happened",
            "author": "Jane",
            "category": "tech",
            "sentiment": 0.75,
            "image_url": "https://example.com/img.png",
        }
        assert a["title"] == "Big News"
        assert a["sentiment"] == 0.75

    def test_zero_coverage_modules_news_article_minimal(self):
        a: NewsArticle = {"title": "Short", "url": "https://x.com", "source": "X"}
        assert a["source"] == "X"

    # -- SearchResult ---------------------------------------------------------

    def test_search_result(self):
        sr: SearchResult = {
            "title": "Page",
            "url": "https://example.com",
            "snippet": "A page about something",
            "rank": 1,
            "source": "Google",
            "published_at": "2024-01-01",
        }
        assert sr["rank"] == 1
        assert sr["source"] == "Google"

    # -- WeatherData ----------------------------------------------------------

    def test_weather_data_metric(self):
        wd: WeatherData = {
            "location": "Seattle",
            "temperature": 15.5,
            "feels_like": 12.0,
            "condition": "cloudy",
            "humidity": 80,
            "wind_speed": 5.2,
            "wind_direction": 270,
            "pressure": 1013.0,
            "units": "metric",
            "timestamp": datetime.now(),
        }
        assert wd["location"] == "Seattle"
        assert wd["units"] == "metric"

    def test_weather_data_imperial(self):
        wd: WeatherData = {
            "location": "Phoenix",
            "temperature": 110.0,
            "condition": "sunny",
            "units": "imperial",
            "timestamp": datetime.now(),
        }
        assert wd["units"] == "imperial"

    # -- HealthCheck ----------------------------------------------------------

    def test_health_check_healthy(self):
        hc: HealthCheck = {
            "service": "api",
            "status": "healthy",
            "timestamp": datetime.now(),
            "latency_ms": 45.0,
            "details": {"version": "1.0"},
        }
        assert hc["status"] == "healthy"

    def test_health_check_all_statuses(self):
        for status in ("healthy", "degraded", "unhealthy", "unknown"):
            hc: HealthCheck = {
                "service": "svc",
                "status": status,
                "timestamp": datetime.now(),
                "latency_ms": None,
                "details": None,
            }
            assert hc["status"] == status

    # -- MetricDataPoint ------------------------------------------------------

    def test_metric_data_point(self):
        mdp: MetricDataPoint = {
            "timestamp": datetime.now(),
            "metric": "cpu_usage",
            "value": 73.2,
            "unit": "percent",
            "tags": {"host": "server1", "env": "prod"},
        }
        assert mdp["metric"] == "cpu_usage"
        assert mdp["value"] == 73.2

    # -- UserPreferences ------------------------------------------------------

    def test_user_preferences_full(self):
        prefs: UserPreferences = {
            "user_id": "42",
            "timezone": "America/New_York",
            "language": "en",
            "notification_enabled": True,
            "digest_schedule": "daily",
            "topics": ["tech", "finance"],
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        assert prefs["user_id"] == "42"
        assert "tech" in prefs["topics"]

    def test_user_preferences_schedules(self):
        for sched in ("daily", "weekly", "custom", "manual"):
            p: UserPreferences = {"digest_schedule": sched}
            assert p["digest_schedule"] == sched

    # -- DBRow ----------------------------------------------------------------

    def test_db_row(self):
        row: DBRow = {"id": 1}
        assert row["id"] == 1


# =============================================================================
# profiler.py
# =============================================================================


class TestProfiler:
    """CPU profiler start/stop, stats, flame graph, and helper functions."""

    def test_zero_coverage_modules_init(self):
        p = Profiler()
        assert p._cpu_profiler is None
        assert p._is_profiling is False
        assert p._profile_start_time is None

    def test_start_stop(self):
        p = Profiler()
        p.start_cpu_profiling()
        assert p._is_profiling is True
        assert p._cpu_profiler is not None

        _ = sum(range(1000))  # generate some profiled activity

        result = p.stop_cpu_profiling()
        assert isinstance(result, str)
        assert "Profile Duration:" in result
        assert p._is_profiling is False
        assert p._cpu_profiler is None

    def test_start_twice_raises(self):
        p = Profiler()
        p.start_cpu_profiling()
        with pytest.raises(RuntimeError, match="already active"):
            p.start_cpu_profiling()
        p.stop_cpu_profiling()

    def test_zero_coverage_modules_stop_without_start_raises(self):
        p = Profiler()
        with pytest.raises(RuntimeError, match="No active profiling session"):
            p.stop_cpu_profiling()

    def test_get_cpu_stats_dict_before_profiling(self):
        p = Profiler()
        assert p.get_cpu_stats_dict() == {}

    def test_get_cpu_stats_dict_while_profiling(self):
        p = Profiler()
        p.start_cpu_profiling()
        _ = sum(range(500))
        stats = p.get_cpu_stats_dict()
        assert isinstance(stats, dict)
        p.stop_cpu_profiling()

    def test_generate_flame_graph_data_before_profiling(self):
        p = Profiler()
        assert p.generate_flame_graph_data() == {}

    def test_generate_flame_graph_data_while_profiling(self):
        p = Profiler()
        p.start_cpu_profiling()
        _ = list(range(100))
        data = p.generate_flame_graph_data()
        assert isinstance(data, dict)
        p.stop_cpu_profiling()

    def test_profile_async_function(self):
        p = Profiler()

        async def my_coro(x):
            return x * 2

        mock_loop = MagicMock()
        mock_loop.run_until_complete.return_value = 84
        with patch.object(asyncio, "get_event_loop", return_value=mock_loop):
            result, stats = p.profile_async_function(my_coro, 42)

        assert result == 84
        assert isinstance(stats, str)

    @pytest.mark.asyncio
    async def test_profile_for_duration(self):
        p = Profiler()
        result = await p.profile_for_duration(0)
        assert isinstance(result, str)
        assert "Profile Duration:" in result

    def test_stop_cpu_profiling_output_format(self):
        p = Profiler()
        p.start_cpu_profiling()
        for _ in range(10):
            _ = sorted([3, 1, 2])
        output = p.stop_cpu_profiling()
        assert "=" * 10 in output
        assert "Top 50 functions" in output

    def test_get_cpu_stats_dict_structure(self):
        """Stats dict keys include filename:line(func) format."""
        p = Profiler()
        p.start_cpu_profiling()
        # Do something non-trivial to ensure at least one entry
        import json as _json

        _json.dumps({"a": 1})
        stats = p.get_cpu_stats_dict()
        if stats:
            key = next(iter(stats))
            entry = stats[key]
            assert "ncalls" in entry
            assert "tottime" in entry
            assert "cumtime" in entry
            assert "percall_tottime" in entry
            assert "percall_cumtime" in entry
        p.stop_cpu_profiling()


class TestProfilerHelpers:
    def test_profile_memory_not_available(self):
        def my_func():
            pass

        with patch.object(profiler_mod, "MEMORY_PROFILER_AVAILABLE", False):
            result = profile_memory(my_func)
        assert result is my_func

    def test_profile_memory_available(self):
        def my_func():
            pass

        mock_decorator = MagicMock(return_value=my_func)
        with (
            patch.object(profiler_mod, "MEMORY_PROFILER_AVAILABLE", True),
            patch.object(profiler_mod, "memory_profile", mock_decorator),
        ):
            result = profile_memory(my_func)
        mock_decorator.assert_called_once_with(my_func)
        assert result is my_func

    def test_get_profiler_returns_profiler(self):
        profiler_mod._profiler = None
        p = get_profiler()
        assert isinstance(p, Profiler)

    def test_get_profiler_singleton(self):
        profiler_mod._profiler = None
        p1 = get_profiler()
        p2 = get_profiler()
        assert p1 is p2

    def test_get_profiler_reuses_existing(self):
        existing = Profiler()
        profiler_mod._profiler = existing
        assert get_profiler() is existing
        profiler_mod._profiler = None


# =============================================================================
# reminder_manager.py
# =============================================================================


@pytest.fixture
def rm(tmp_path, monkeypatch):
    """Fresh ReminderManager backed by a tmp_path file."""
    rf = tmp_path / "reminders.json"
    monkeypatch.setattr(rm_mod, "REMINDERS_FILE", rf)
    monkeypatch.setattr(rm_mod, "atomic_write", lambda path, data: path.write_text(data))
    return ReminderManager()


class TestReminder:
    def test_zero_coverage_modules_defaults(self):
        r = Reminder()
        assert isinstance(r.id, str)
        assert len(r.id) == 8
        assert r.user_id == 0
        assert r.channel_id == 0
        assert r.message == ""
        assert r.fire_at == 0.0
        assert r.recurring == ""
        assert r.fired is False
        assert r.created_at > 0

    def test_custom_fields(self):
        r = Reminder(user_id=1, channel_id=100, message="hi", fire_at=9999.0)
        assert r.user_id == 1
        assert r.message == "hi"
        assert r.fire_at == 9999.0


class TestReminderManager:
    def test_load_empty_file(self, rm):
        assert rm._reminders == []

    def test_load_existing_json(self, tmp_path, monkeypatch):
        rf = tmp_path / "reminders.json"
        data = [
            {
                "id": "abc12345",
                "user_id": 1,
                "channel_id": 100,
                "message": "hello",
                "fire_at": 9000.0,
                "recurring": "",
                "created_at": 1000.0,
                "fired": False,
            }
        ]
        rf.write_text(json.dumps(data))
        monkeypatch.setattr(rm_mod, "REMINDERS_FILE", rf)
        monkeypatch.setattr(rm_mod, "atomic_write", lambda p, d: p.write_text(d))
        mgr = ReminderManager()
        assert len(mgr._reminders) == 1
        assert mgr._reminders[0].id == "abc12345"
        assert mgr._reminders[0].message == "hello"

    def test_load_corrupt_json(self, tmp_path, monkeypatch):
        rf = tmp_path / "reminders.json"
        rf.write_text("not { valid json !!!")
        monkeypatch.setattr(rm_mod, "REMINDERS_FILE", rf)
        monkeypatch.setattr(rm_mod, "atomic_write", lambda p, d: p.write_text(d))
        mgr = ReminderManager()
        assert mgr._reminders == []

    def test_zero_coverage_modules_add_reminder(self, rm):
        r = rm.add(user_id=1, channel_id=200, message="Test", fire_at=time.time() + 60)
        assert r.user_id == 1
        assert r.channel_id == 200
        assert r.message == "Test"
        assert len(rm._reminders) == 1

    def test_add_recurring(self, rm):
        r = rm.add(1, 200, "Daily", time.time() + 3600, recurring="daily")
        assert r.recurring == "daily"

    def test_zero_coverage_modules_add_persists(self, rm, tmp_path, monkeypatch):
        """add() writes to the file."""
        rf = tmp_path / "reminders.json"
        monkeypatch.setattr(rm_mod, "REMINDERS_FILE", rf)
        monkeypatch.setattr(rm_mod, "atomic_write", lambda p, d: p.write_text(d))
        mgr = ReminderManager()
        mgr.add(1, 100, "Persist me", 9999.0)
        assert rf.exists()
        saved = json.loads(rf.read_text())
        assert len(saved) == 1
        assert saved[0]["message"] == "Persist me"

    def test_cancel_existing(self, rm):
        r = rm.add(1, 100, "Cancel me", time.time() + 60)
        result = rm.cancel(r.id, user_id=1)
        assert result is True
        assert len(rm._reminders) == 0

    def test_cancel_wrong_user(self, rm):
        r = rm.add(1, 100, "Mine", time.time() + 60)
        result = rm.cancel(r.id, user_id=999)
        assert result is False
        assert len(rm._reminders) == 1

    def test_cancel_nonexistent(self, rm):
        result = rm.cancel("no_such_id", user_id=1)
        assert result is False

    def test_list_for_user(self, rm):
        rm.add(1, 100, "R1", time.time() + 60)
        rm.add(1, 100, "R2", time.time() + 120)
        rm.add(2, 200, "R3", time.time() + 60)
        user1 = rm.list_for_user(1)
        assert len(user1) == 2
        assert all(r.user_id == 1 for r in user1)

    def test_list_for_user_excludes_fired(self, rm):
        r = rm.add(1, 100, "Fired", time.time() - 1)
        r.fired = True
        assert rm.list_for_user(1) == []

    def test_get_due(self, rm):
        rm.add(1, 100, "Past", time.time() - 10)
        rm.add(1, 100, "Future", time.time() + 9999)
        due = rm.get_due()
        assert len(due) == 1
        assert due[0].message == "Past"

    def test_get_due_excludes_fired(self, rm):
        r = rm.add(1, 100, "Past fired", time.time() - 1)
        r.fired = True
        assert rm.get_due() == []

    def test_zero_coverage_modules_mark_fired_one_shot(self, rm):
        r = rm.add(1, 100, "Once", time.time() - 1)
        rm.mark_fired(r.id)
        assert r.fired is True

    def test_mark_fired_daily(self, rm):
        r = rm.add(1, 100, "Daily", time.time() - 1, recurring="daily")
        old_fire_at = r.fire_at
        rm.mark_fired(r.id)
        assert r.fire_at == pytest.approx(old_fire_at + 86400)
        assert r.fired is False

    def test_mark_fired_weekly(self, rm):
        r = rm.add(1, 100, "Weekly", time.time() - 1, recurring="weekly")
        old_fire_at = r.fire_at
        rm.mark_fired(r.id)
        assert r.fire_at == pytest.approx(old_fire_at + 604800)
        assert r.fired is False

    def test_mark_fired_nonexistent(self, rm):
        """mark_fired with unknown ID is a no-op (just saves)."""
        rm.add(1, 100, "Existing", time.time() + 60)
        rm.mark_fired("does_not_exist")
        assert len(rm._reminders) == 1


class TestParseTimeExpression:
    def test_zero_coverage_modules_in_minutes(self):
        ts = parse_time_expression("in 30m")
        assert ts == pytest.approx(time.time() + 30 * 60, abs=2)

    def test_zero_coverage_modules_in_hours(self):
        ts = parse_time_expression("in 2h")
        assert ts == pytest.approx(time.time() + 2 * 3600, abs=2)

    def test_zero_coverage_modules_in_seconds(self):
        ts = parse_time_expression("in 10s")
        assert ts == pytest.approx(time.time() + 10, abs=2)

    def test_in_min_long(self):
        ts = parse_time_expression("in 5min")
        assert ts == pytest.approx(time.time() + 5 * 60, abs=2)

    def test_in_sec_long(self):
        ts = parse_time_expression("in 15sec")
        assert ts == pytest.approx(time.time() + 15, abs=2)

    def test_in_hr_long(self):
        ts = parse_time_expression("in 1hr")
        assert ts == pytest.approx(time.time() + 3600, abs=2)

    def test_in_hour_long(self):
        ts = parse_time_expression("in 1hour")
        assert ts == pytest.approx(time.time() + 3600, abs=2)

    def test_at_hour_pm(self):
        ts = parse_time_expression("at 3pm")
        assert ts is not None
        assert ts > time.time()

    def test_at_hour_am(self):
        ts = parse_time_expression("at 9am")
        assert ts is not None

    def test_at_noon_pm(self):
        # 12pm stays as 12
        ts = parse_time_expression("at 12pm")
        assert ts is not None

    def test_at_midnight_am(self):
        # 12am should become hour=0
        ts = parse_time_expression("at 12am")
        assert ts is not None

    def test_zero_coverage_modules_at_with_minutes(self):
        ts = parse_time_expression("at 10:30")
        assert ts is not None

    def test_at_with_minutes_pm(self):
        ts = parse_time_expression("at 2:45pm")
        assert ts is not None

    @pytest.mark.parametrize(
        "expr",
        [
            "tomorrow morning",
            "  ",
            "xyz abc 123",
        ],
    )
    def test_invalid_expressions(self, expr):
        assert parse_time_expression(expr) is None

