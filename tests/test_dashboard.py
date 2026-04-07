"""
Tests for dashboard.py — HTML and JSON dashboard handlers.

Covers: dashboard_handler returns HTML, guide_handler returns HTML,
api_dashboard_handler returns JSON, and _command_list structure.

dashboard.py reads HTML template files at import time via _TEMPLATES_DIR.
When running from source (outside Docker), templates live at <repo>/templates/
instead of <repo>/src/templates/, so we patch the module-level constants
before importing.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the module loads templates from the correct location
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "templates"
if not _TEMPLATES_DIR.exists():
    pytest.skip("templates/ directory not found", allow_module_level=True)

# Patch _TEMPLATES_DIR before the module reads the files
# Remove cached module so we can re-import with correct paths
sys.modules.pop("dashboard", None)
# Temporarily monkey-patch pathlib resolution by pre-loading the HTML
_dashboard_html = (_TEMPLATES_DIR / "dashboard.html").read_text()
_guide_html = (_TEMPLATES_DIR / "guide.html").read_text()

with patch.dict("os.environ", {}):
    # We need to intercept the module-level read_text calls.
    # Easiest: patch Path.__truediv__ — but that's fragile.
    # Instead: just mock the two constants after import via a wrapper.
    pass

# Do the actual import — it will try src/templates which may not exist.
# Pre-create the path so the import succeeds.
_src_templates = _REPO_ROOT / "src" / "templates"
_src_templates.mkdir(parents=True, exist_ok=True)
_src_dash = _src_templates / "dashboard.html"
_src_guide = _src_templates / "guide.html"
_created_dash = not _src_dash.exists()
_created_guide = not _src_guide.exists()
if _created_dash:
    _src_dash.write_text(_dashboard_html)
if _created_guide:
    _src_guide.write_text(_guide_html)

try:
    import dashboard as mod
finally:
    # Clean up symlinks/copies we created
    if _created_dash and _src_dash.exists():
        _src_dash.unlink()
    if _created_guide and _src_guide.exists():
        _src_guide.unlink()
    # Remove empty dir if we created it
    try:
        _src_templates.rmdir()
    except OSError:
        pass


def _fake_request(
    app_data: dict | None = None,
    *,
    method: str = "GET",
    query: dict | None = None,
    json_payload: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    """Build a minimal mock aiohttp.web.Request."""
    req = MagicMock()
    req.app = app_data or {}
    req.method = method
    req.query = query or {}
    req.headers = headers or {}
    req.json = AsyncMock(return_value=json_payload or {})
    return req


# ---------------------------------------------------------------------------
# Static HTML handlers
# ---------------------------------------------------------------------------


class TestDashboardHandler:
    async def test_returns_html(self):
        req = _fake_request()
        resp = await mod.dashboard_handler(req)
        assert resp.content_type == "text/html"
        assert len(resp.text) > 100
        assert "<html" in resp.text.lower() or "<!doctype" in resp.text.lower()
        assert "Run History Timeline" in resp.text
        assert "Channel Memory Inspector" in resp.text
        assert "Channel Profile Assistant" in resp.text
        assert "Inspect Scope / Preview" in resp.text
        assert "Workflow Lanes" in resp.text
        assert "openclaw_api_action_token" in resp.text
        assert "Quality Eval Scorecards" in resp.text
        assert "Discord Answer Quality Telemetry" in resp.text
        assert "Quality score distribution" in resp.text
        assert "Retry outcomes" in resp.text
        assert "Discord answer feedback loop" in resp.text
        assert "Helpful rate" in resp.text
        assert "persistence receipts" in resp.text
        assert "rerun action schedules a full research" in resp.text
        assert "cycle every 24h" in resp.text


class TestGuideHandler:
    async def test_returns_html(self):
        req = _fake_request()
        resp = await mod.guide_handler(req)
        assert resp.content_type == "text/html"
        assert len(resp.text) > 100
        assert "guide-command-search" in resp.text
        assert "Live Command Finder" in resp.text
        assert "Workflow Lanes (Fast Navigation)" in resp.text
        assert "Re-run full research in 24h" in resp.text
        assert "Persistence receipts" in resp.text


# ---------------------------------------------------------------------------
# _command_list
# ---------------------------------------------------------------------------


class TestCommandList:
    def test_returns_list_of_categories(self):
        from dashboard.helpers import _command_list
        cmds = _command_list()
        assert isinstance(cmds, list)
        assert len(cmds) > 0
        first = cmds[0]
        assert "category" in first
        assert "commands" in first
        assert isinstance(first["commands"], list)
        assert "name" in first["commands"][0]
        assert "desc" in first["commands"][0]
        assert "keywords" in first["commands"][0]

    def test_metadata_fields_are_consistent(self):
        from dashboard.helpers import _command_list

        cmds = _command_list()
        for category in cmds:
            assert isinstance(category["category"], str)
            for cmd in category["commands"]:
                assert isinstance(cmd["name"], str) and cmd["name"]
                assert isinstance(cmd["desc"], str) and cmd["desc"]
                assert isinstance(cmd["keywords"], list)
                assert len(cmd["keywords"]) > 0


# ---------------------------------------------------------------------------
# api_dashboard_handler (heavy mocking — verifies JSON shape)
# ---------------------------------------------------------------------------


class TestApiDashboard:
    async def test_returns_json_response(self):
        mock_bot = MagicMock()
        mock_bot.start_time = 0
        mock_bot.user = MagicMock(__str__=lambda s: "TestBot#0001")
        mock_bot.guilds = [1, 2]
        mock_bot.latency = 0.05

        req = _fake_request({"bot": mock_bot})

        fake_skills = {"skill_a": lambda: None}
        with (
            patch.dict(
                "sys.modules",
                {
                    "skills": MagicMock(
                        SKILLS=fake_skills,
                        list_containers=AsyncMock(return_value="❌ docker offline"),
                        get_docker_stats=AsyncMock(return_value="❌ no stats"),
                        get_system_stats=AsyncMock(return_value="**CPU**: 10%\n**Memory**: 4/16 GB\n**Disk**: 50%"),
                    ),
                    "ontology_skills": MagicMock(
                        ontology_query=AsyncMock(return_value="❌ empty"),
                    ),
                    "llm": MagicMock(
                        _TOOL_DECLARATIONS=[{"name": "skill_a", "description": "A skill"}],
                        get_rate_info=MagicMock(return_value="ok"),
                        MODEL_NAME="test-model",
                        OLLAMA_MODEL="",
                        LOCAL_LLM_ENABLED=False,
                    ),
                },
            ),
        ):
            resp = await mod.api_dashboard_handler(req)

        assert resp.content_type == "application/json"
        payload = json.loads(resp.text)
        assert "commands" in payload and isinstance(payload["commands"], list)
        assert "command_quickstart" in payload and isinstance(payload["command_quickstart"], list)


class TestQualityMetricsApi:
    @pytest.mark.asyncio
    async def test_quality_metrics_returns_signal_summary(self):
        req = _fake_request()

        metrics_mod = MagicMock(
            get_quality_event_snapshot=MagicMock(
                return_value={
                    "total_events": 12,
                    "event_counts": {
                        "search_fallback_activation": 5,
                        "search_low_results_incident": 2,
                        "recap_fallback_activation": 3,
                        "recap_partial_coverage_warning": 2,
                        "ask_feedback_helpful": 7,
                        "ask_feedback_not_helpful": 1,
                        "ask_feedback_accepted": 8,
                        "ask_feedback_suppressed": 3,
                        "ask_feedback_suppressed_dedupe": 2,
                        "ask_feedback_suppressed_rate_limited_user": 1,
                    },
                    "context_counts": {"sports_recap": 10, "search": 2},
                    "top_events": [
                        {"event": "search_fallback_activation", "count": 5},
                        {"event": "recap_fallback_activation", "count": 3},
                    ],
                    "top_contexts": [
                        {"context": "sports_recap", "count": 10},
                    ],
                }
            )
        )
        error_tracker_mod = MagicMock(
            get_recent_outcomes=MagicMock(
                return_value=[
                    {
                        "explainability": {
                            "answer_quality": {
                                "status": "low",
                                "reasons": ["Limited item coverage detected."],
                            },
                            "answer_quality_retry": {"attempted": True, "outcome": "improved"},
                        },
                    },
                    {
                        "explainability": {
                            "answer_quality": {"status": "high", "reasons": []},
                            "answer_quality_retry": {"attempted": False, "outcome": "skipped"},
                        },
                    },
                ]
            )
        )

        with (
            patch.dict("sys.modules", {"metrics_collector": metrics_mod, "error_tracker": error_tracker_mod}),
            patch(
                "dashboard.api_handlers._build_offline_quality_calibration_payload",
                return_value={
                    "available": True,
                    "drift": {
                        "baseline_available": True,
                        "status": "drifted",
                        "regressed_metrics": ["coverage_proxy"],
                        "severity": {"level": "severe", "severe": True, "score": 5, "reasons": ["test"]},
                    },
                },
            ),
        ):
            resp = await mod.api_quality_metrics_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["total_events"] == 12
        assert payload["signals"]["search_fallback_activation"] == 5
        assert payload["signals"]["recap_partial_coverage_warning"] == 2
        assert payload["status"] == "degraded"
        assert payload["calibration_drift"]["severe"] is True
        assert payload["calibration_drift"]["severity_level"] == "severe"
        assert payload["score_distribution"]["high"] == 1
        assert payload["score_distribution"]["low"] == 1
        assert payload["low_confidence"]["prompt_count"] >= 1
        assert payload["low_confidence"]["top_reasons"][0]["reason"] == "Limited item coverage detected."
        assert payload["retry_outcomes"]["attempted"] >= 1
        assert payload["retry_outcomes"]["improved"] >= 1
        assert payload["retry_outcomes"]["skipped"] >= 1
        assert payload["feedback"]["helpful"] == 7
        assert payload["feedback"]["not_helpful"] == 1
        assert payload["feedback"]["total"] == 8
        assert payload["feedback"]["helpful_rate"] == 0.875
        assert payload["feedback"]["accepted"] == 8
        assert payload["feedback"]["suppressed"] == 3
        assert payload["feedback"]["suppressed_dedupe"] == 2
        assert payload["feedback"]["suppressed_rate_limited"] == 1
        assert isinstance(payload["domain_trends"], list)
        assert len(payload["domain_trends"]) <= 6
        assert any(item.get("domain") == "search" for item in payload["domain_trends"])
        assert isinstance(payload["top_recurring_failures"], list)
        assert payload["top_recurring_failures"][0]["count"] >= payload["top_recurring_failures"][-1]["count"]
        assert isinstance(payload["top_quality_failure_categories"], list)
        assert len(payload["top_quality_failure_categories"]) <= 6
        assert payload["top_quality_failure_categories"][0]["count"] >= payload["top_quality_failure_categories"][-1]["count"]
        assert "quality_failure_categories" in payload
        assert isinstance(payload["quality_failure_categories"]["counts"], dict)
        assert payload["quality_failure_categories"]["counts"].get("requested_item_shortfall", 0) >= 2
        assert "recent_signal_slices" in payload
        assert set(payload["recent_signal_slices"].keys()) == {"mitigation", "degrade"}
        assert isinstance(payload["recent_signal_slices"]["degrade"], list)

    @pytest.mark.asyncio
    async def test_quality_metrics_fallback_contains_new_sections(self):
        req = _fake_request()
        metrics_mod = MagicMock(
            get_quality_event_snapshot=MagicMock(side_effect=RuntimeError("boom"))
        )
        with patch.dict("sys.modules", {"metrics_collector": metrics_mod}):
            resp = await mod.api_quality_metrics_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["score_distribution"] == {"high": 0, "medium": 0, "low": 0}
        assert payload["calibration_drift"]["severity_level"] == "unknown"
        assert payload["calibration_drift"]["severe"] is False
        assert payload["low_confidence"]["prompt_count"] == 0
        assert payload["retry_outcomes"]["attempted"] == 0
        assert payload["feedback"] == {
            "helpful": 0,
            "not_helpful": 0,
            "total": 0,
            "helpful_rate": None,
            "accepted": 0,
            "suppressed": 0,
            "suppressed_dedupe": 0,
            "suppressed_rate_limited": 0,
        }
        assert payload["domain_trends"] == []
        assert payload["top_recurring_failures"] == []
        assert payload["top_quality_failure_categories"] == []
        assert payload["quality_failure_categories"] == {
            "counts": {},
            "top": [],
            "total_classified_failures": 0,
            "total_failure_events": 0,
        }
        assert payload["recent_signal_slices"] == {"mitigation": [], "degrade": []}


class TestSmsDashboardApi:
    @pytest.mark.asyncio
    async def test_sms_settings_get_requires_user_id(self):
        req = _fake_request(query={})
        resp = await mod.api_sms_settings_handler(req)
        assert resp.content_type == "application/json"
        assert "needs_user_id" in resp.text

    @pytest.mark.asyncio
    async def test_sms_settings_post_updates_phone(self, monkeypatch, tmp_path):
        import sms_ux

        monkeypatch.setattr(sms_ux, "sms_prefs", sms_ux.SMSPrefsStore(tmp_path / "sms_prefs.json"))
        req = _fake_request(
            method="POST",
            json_payload={"user_id": 12345, "phone_number": "+15551234567"},
        )

        resp = await mod.api_sms_settings_handler(req)

        assert resp.content_type == "application/json"
        assert "ok" in resp.text
        assert "+15551234567" in resp.text

    @pytest.mark.asyncio
    async def test_sms_status_and_history_returns_data(self, monkeypatch, tmp_path):
        import sms_ux

        monkeypatch.setattr(sms_ux, "sms_prefs", sms_ux.SMSPrefsStore(tmp_path / "sms_prefs.json"))
        prefs = sms_ux.UserSMSPrefs(
            user_id=333,
            phone_number="+15550001111",
            is_verified=True,
            recent_sends=[
                {
                    "sent_at": 1_700_000_000.0,
                    "provider": "twilio",
                    "sid": "SM123",
                    "status": "queued",
                    "preview": "hello",
                    "to": "+15550001111",
                }
            ],
        )
        await sms_ux.sms_prefs.update(prefs)

        status_req = _fake_request(query={"user_id": "333"})
        status_resp = await mod.api_sms_status_handler(status_req)
        assert status_resp.content_type == "application/json"
        assert "configured" in status_resp.text
        assert "true" in status_resp.text.lower()

        history_req = _fake_request(query={"user_id": "333", "limit": "5"})
        history_resp = await mod.api_sms_history_handler(history_req)
        assert history_resp.content_type == "application/json"
        assert "SM123" in history_resp.text


class TestChannelMemoryInspectorApi:
    @pytest.mark.asyncio
    async def test_inspect_requires_channel_id(self):
        req = _fake_request(query={})
        resp = await mod.api_channel_memory_inspect_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert "channel_id" in payload["error"]

    @pytest.mark.asyncio
    async def test_inspect_returns_scoped_summary(self):
        req = _fake_request(query={"channel_id": "123", "thread_id": "456", "limit": "3", "include_anchor": "1"})
        vector_store_mock = MagicMock(
            get_scoped_memory_summary=AsyncMock(
                return_value={
                    "scope": {"channel_id": "123", "thread_id": "456"},
                    "collections": {"memories": {"count": 1, "latest": [{"id": "mem_1"}]}},
                    "total_count": 1,
                    "anchor": {"present": False},
                    "alerts": {"count": 1, "items": [{"category": "scope_guard_block", "message": "blocked"}]},
                    "compaction": {"count": 2, "items": [{"collection": "memories", "pruned_count": 5}]},
                }
            )
        )
        with patch.dict("sys.modules", {"vector_store": vector_store_mock}):
            resp = await mod.api_channel_memory_inspect_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["scope"]["channel_id"] == "123"
        assert payload["warnings"]["scoped_recall_alerts"] == 1
        assert payload["warnings"]["recent_compactions"] == 2
        vector_store_mock.get_scoped_memory_summary.assert_awaited_once_with(
            channel_id="123",
            thread_id="456",
            latest_limit=3,
            include_anchor=True,
        )

    @pytest.mark.asyncio
    async def test_action_clear_runs_clear_and_audit(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "clear",
                "channel_id": "123",
                "thread_id": "456",
                "actor": "dashboard-ui",
                "confirm": True,
            },
        )
        vector_store_mock = MagicMock(
            clear_scoped_memory=AsyncMock(
                return_value={
                    "scope": {"channel_id": "123", "thread_id": "456"},
                    "deleted": {"memories": 2, "conversations": 1, "research": 0},
                    "total_deleted": 3,
                }
            )
        )
        audit_mock = MagicMock(audit_log=MagicMock())

        with patch.dict("sys.modules", {"vector_store": vector_store_mock, "audit": audit_mock}):
            resp = await mod.api_channel_memory_action_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["clear"]["total_deleted"] == 3
        vector_store_mock.clear_scoped_memory.assert_awaited_once_with(channel_id="123", thread_id="456")
        assert audit_mock.audit_log.call_count == 1

    @pytest.mark.asyncio
    async def test_action_clear_requires_confirmation_preview(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "clear",
                "channel_id": "123",
                "thread_id": "456",
                "actor": "dashboard-ui",
            },
        )
        vector_store_mock = MagicMock(
            get_scoped_memory_summary=AsyncMock(
                return_value={
                    "scope": {"channel_id": "123", "thread_id": "456"},
                    "collections": {"memories": {"count": 2, "latest": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}},
                    "total_count": 2,
                    "anchor": {"present": True},
                    "alerts": {"count": 0, "items": []},
                }
            )
        )
        with patch.dict("sys.modules", {"vector_store": vector_store_mock}):
            resp = await mod.api_channel_memory_action_handler(req)

        assert resp.status == 409
        payload = json.loads(resp.text)
        assert payload["requires_confirmation"] is True
        assert payload["preview"]["total_entries"] == 2
        assert payload["preview"]["collections"]["memories"]["count"] == 2
        assert len(payload["preview"]["collections"]["memories"]["latest"]) == 2
        vector_store_mock.get_scoped_memory_summary.assert_awaited_once_with(
            channel_id="123",
            thread_id="456",
            latest_limit=5,
            include_anchor=True,
        )

    @pytest.mark.asyncio
    async def test_action_retrain_runs_dream_cycle(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "retrain",
                "channel_id": "123",
                "actor": "dashboard-ui",
            },
        )

        class _FakeCycle:
            def __init__(self):
                self.run = AsyncMock(return_value="dream report")

        dream_cycle_mock = MagicMock(DreamCycle=_FakeCycle)
        audit_mock = MagicMock(audit_log=MagicMock())
        with patch.dict("sys.modules", {"dream_cycle": dream_cycle_mock, "audit": audit_mock}):
            resp = await mod.api_channel_memory_action_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["retrain"]["triggered"] is True
        assert payload["scope"]["thread_id"] is None
        assert audit_mock.audit_log.call_count == 1

    @pytest.mark.asyncio
    async def test_action_rejects_invalid_scope_id(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "clear",
                "channel_id": "chan-1",
            },
        )
        resp = await mod.api_channel_memory_action_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert "numeric" in payload["error"]


class TestRunsApi:
    @pytest.mark.asyncio
    async def test_runs_endpoint_exposes_explainability_payload(self):
        req = _fake_request(query={"hours": "24", "limit": "5"})
        fake_entries = [
            {
                "ts": 1_710_000_000.0,
                "trace_id": "trace123abc",
                "user_id": 111,
                "question": "summarize",
                "model_used": "gemini-2.5-pro",
                "success": True,
                "latency_ms": 321,
                "scope_mode": "thread",
                "lock_mode": "prior_report",
                "anchor_id": "report_42",
                "anchor_age_seconds": 75,
                "effective_profile": {"tone": "direct"},
            }
        ]
        with patch.dict("sys.modules", {"error_tracker": MagicMock(get_recent_outcomes=MagicMock(return_value=fake_entries))}):
            resp = await mod.api_runs_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert "runs" in payload
        assert len(payload["runs"]) == 1
        run = payload["runs"][0]
        assert run["scope_mode"] == "thread"
        assert run["lock_mode"] == "prior_report"
        assert run["trace_id"] == "trace123abc"
        assert run["anchor_id"] == "report_42"
        assert run["anchor_age_seconds"] == 75
        assert run["effective_profile_values"] == {"tone": "direct"}
        assert run["profile_values"] == {"tone": "direct"}
        assert run["explainability"]["trace_id"] == "trace123abc"
        assert run["explainability"]["scope_mode"] == "thread"
        assert run["explainability"]["lock_mode"] == "prior_report"
        assert run["explainability"]["anchor_id"] == "report_42"
        assert run["explainability"]["anchor_age_seconds"] == 75
        assert run["explainability"]["effective_profile_values"] == {"tone": "direct"}
        assert payload["filters"]["status"] == ["success"]
        assert payload["filters"]["models"] == ["gemini-2.5-pro"]
        assert payload["filters"]["users"] == ["111"]


class TestQualityEvalApi:
    @pytest.mark.asyncio
    async def test_quality_eval_endpoint_returns_latest_history_and_trend(self):
        req = _fake_request(query={"history": "5"})
        latest = {
            "scorecard_id": 10,
            "timestamp": 1_710_000_123.0,
            "sample_size": 42,
            "summary": {"pass": 12, "fail": 3, "rate": 0.8},
            "metrics": {
                "channel_leakage_prevention": {"pass": 4, "fail": 1, "sample": 5, "rate": 0.8},
                "followup_anchor_correctness": {"pass": 8, "fail": 2, "sample": 10, "rate": 0.8},
            },
        }
        history = [
            latest,
            {
                "scorecard_id": 9,
                "timestamp": 1_709_000_000.0,
                "sample_size": 30,
                "summary": {"pass": 9, "fail": 3, "rate": 0.75},
                "metrics": {
                    "channel_leakage_prevention": {"pass": 3, "fail": 1, "sample": 4, "rate": 0.75},
                    "followup_anchor_correctness": {"pass": 6, "fail": 2, "sample": 8, "rate": 0.75},
                },
            },
        ]
        runtime_state_mock = MagicMock(
            ensure_quality_eval_scorecard=MagicMock(return_value=latest),
            create_quality_eval_scorecard=MagicMock(return_value=latest),
            list_quality_eval_scorecards=MagicMock(return_value=history),
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock}):
            resp = await mod.api_quality_eval_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["latest"]["scorecard_id"] == 10
        assert len(payload["history"]) == 2
        assert "summary" in payload["trend"]
        assert "metrics" in payload["trend"]
        assert "channel_leakage_prevention" in payload["trend"]["metrics"]
        assert len(payload["trend"]["metrics"]["channel_leakage_prevention"]) == 2
        assert "calibration" in payload
        assert payload["calibration"]["advisory_only"] is True

    @pytest.mark.asyncio
    async def test_quality_eval_endpoint_exposes_calibration_shape(self):
        req = _fake_request(query={"history": "2", "calibration": "1"})
        latest = {
            "scorecard_id": 11,
            "timestamp": 1_710_000_999.0,
            "sample_size": 12,
            "summary": {"pass": 4, "fail": 1, "rate": 0.8},
            "metrics": {"channel_leakage_prevention": {"pass": 4, "fail": 1, "sample": 5, "rate": 0.8}},
        }
        runtime_state_mock = MagicMock(
            ensure_quality_eval_scorecard=MagicMock(return_value=latest),
            create_quality_eval_scorecard=MagicMock(return_value=latest),
            list_quality_eval_scorecards=MagicMock(return_value=[latest]),
        )
        offline_quality_eval_mock = MagicMock(
            load_replay_fixtures=MagicMock(return_value=[{"id": "case-1"}]),
            load_baseline_report=MagicMock(return_value={"summary": {"coverage_proxy": 0.9}}),
            run_quality_eval=MagicMock(
                return_value={
                    "pass": True,
                    "summary": {"coverage_proxy": 0.92, "warning_rate": 0.2, "max_latency_bucket": "slow"},
                    "calibration": {
                        "advisory_only": True,
                        "auto_apply": False,
                        "drift": {"baseline_available": True, "status": "stable", "metrics": {}},
                        "recommendations": {"advisory_only": True, "auto_apply": False, "proposals": []},
                    },
                }
            ),
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock, "offline_quality_eval": offline_quality_eval_mock}):
            resp = await mod.api_quality_eval_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["calibration"]["available"] is True
        assert payload["calibration"]["advisory_only"] is True
        assert payload["calibration"]["auto_apply"] is False
        assert "drift" in payload["calibration"]
        assert "severity" in payload["calibration"]["drift"]
        assert "recommendations" in payload["calibration"]


class TestChannelProfileAssistantApi:
    @pytest.mark.asyncio
    async def test_recommendations_requires_channel_id(self):
        req = _fake_request(query={})
        resp = await mod.api_channel_profile_recommendations_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert "channel_id" in payload["error"]

    @pytest.mark.asyncio
    async def test_recommendations_returns_scope_payload(self):
        req = _fake_request(query={"channel_id": "123", "thread_id": "456"})
        runtime_state_mock = MagicMock(
            refresh_channel_profile_recommendations=MagicMock(return_value=[]),
            list_channel_profile_recommendations=MagicMock(
                return_value=[
                    {
                        "recommendation_id": 9,
                        "channel_id": 123,
                        "thread_id": 456,
                        "profile_field": "table_style",
                        "recommended_value": "copy-safe",
                        "reason": "copy usage",
                        "confidence": 0.8,
                        "status": "suggested",
                    }
                ]
            ),
            get_channel_profile=MagicMock(return_value={"table_style": "discord"}),
            get_channel_profile_usage_signals=MagicMock(return_value={"recap_copy_export": 3}),
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock}):
            resp = await mod.api_channel_profile_recommendations_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["scope"]["channel_id"] == "123"
        assert payload["recommendations"][0]["profile_field"] == "table_style"
        runtime_state_mock.refresh_channel_profile_recommendations.assert_called_once_with(123, thread_id=456)

    @pytest.mark.asyncio
    async def test_recommendation_action_runs_update(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "recommendation_id": 44,
                "action": "approve",
                "actor": "dashboard-ui",
            },
        )
        runtime_state_mock = MagicMock(
            update_channel_profile_recommendation=MagicMock(
                return_value={
                    "recommendation_id": 44,
                    "channel_id": 123,
                    "thread_id": None,
                    "profile_field": "table_style",
                    "recommended_value": "copy-safe",
                    "status": "approved",
                }
            )
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock, "audit": MagicMock(audit_log=MagicMock())}):
            resp = await mod.api_channel_profile_recommendation_action_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["recommendation"]["status"] == "approved"
