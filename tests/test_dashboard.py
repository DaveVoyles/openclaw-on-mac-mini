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


class TestGuideHandler:
    async def test_returns_html(self):
        req = _fake_request()
        resp = await mod.guide_handler(req)
        assert resp.content_type == "text/html"
        assert len(resp.text) > 100


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
                }
            )
        )
        with patch.dict("sys.modules", {"vector_store": vector_store_mock}):
            resp = await mod.api_channel_memory_inspect_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["scope"]["channel_id"] == "123"
        assert payload["warnings"]["scoped_recall_alerts"] == 1
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
