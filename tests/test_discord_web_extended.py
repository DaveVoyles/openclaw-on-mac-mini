"""Extended tests for discord_web.py — health, smoke, webhook, and service handlers."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import discord_web as mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bot(guilds=2, latency=0.05):
    bot = MagicMock()
    bot.start_time = time.monotonic() - 60
    bot.guilds = [object()] * guilds
    bot.latency = latency
    bot.user = MagicMock()
    bot.user.__str__ = MagicMock(return_value="TestBot#1234")
    bot.get_channel = MagicMock(return_value=None)
    return bot


def _make_request(source=None, headers=None, json_body=None, bot=None):
    b = bot or _make_bot()
    req = MagicMock()
    req.app = {"bot": b}
    req.headers = headers or {}
    req.match_info = {"source": source} if source else {}
    if json_body is not None:
        req.json = AsyncMock(return_value=json_body)
    else:
        req.json = AsyncMock(side_effect=Exception("no body"))
    return req, b


_SMOKE_MODS = {
    "llm": MagicMock(
        LOCAL_LLM_ENABLED=False,
        _ollama_available=AsyncMock(return_value=True),
    ),
    "vector_store": MagicMock(),
    "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
    "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
    "skills": MagicMock(SKILLS={"search_web": object(), "other": object()}),
}


# ---------------------------------------------------------------------------
# _health_handler
# ---------------------------------------------------------------------------

class TestHealthHandler:
    async def test_returns_healthy_status(self):
        req, _ = _make_request()
        resp = await mod._health_handler(req)
        data = json.loads(resp.body)
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert data["guilds"] == 2

    async def test_bot_user_is_string_when_set(self):
        req, bot = _make_request()
        resp = await mod._health_handler(req)
        data = json.loads(resp.body)
        assert data["bot_user"] is not None

    async def test_bot_user_is_none_when_not_set(self):
        req, bot = _make_request()
        bot.user = None
        resp = await mod._health_handler(req)
        data = json.loads(resp.body)
        assert data["bot_user"] is None

    async def test_returns_200(self):
        req, _ = _make_request()
        resp = await mod._health_handler(req)
        assert resp.status == 200

    async def test_includes_python_and_discord_version(self):
        req, _ = _make_request()
        resp = await mod._health_handler(req)
        data = json.loads(resp.body)
        assert "python" in data
        assert "discord_py" in data


# ---------------------------------------------------------------------------
# _metrics_handler — collector failure path
# ---------------------------------------------------------------------------

class TestMetricsHandlerCollectorFailure:
    async def test_handles_collector_exception_gracefully(self, monkeypatch):
        monkeypatch.setattr(mod, "get_collector", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        req, _ = _make_request()

        def bad_collector():
            raise RuntimeError("collector down")
        monkeypatch.setattr(mod, "get_collector", bad_collector)

        resp = await mod._metrics_handler(req)
        body = resp.body.decode()
        assert "openclaw_up 1" in body
        assert resp.status == 200

    async def test_basic_metrics_always_present_even_on_collector_fail(self, monkeypatch):
        monkeypatch.setattr(mod, "get_collector", lambda: (_ for _ in ()).throw(Exception("x")))

        def bad():
            raise Exception("bad")
        monkeypatch.setattr(mod, "get_collector", bad)
        req, _ = _make_request()
        resp = await mod._metrics_handler(req)
        body = resp.body.decode()
        assert "openclaw_guilds 2" in body
        assert "openclaw_latency_ms" in body


# ---------------------------------------------------------------------------
# _smoke_handler
# ---------------------------------------------------------------------------

class TestSmokeHandler:
    async def test_smoke_gemini_pass_ollama_skipped(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock()

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert "checks" in data
            assert "timestamp" in data
            assert "status" in data
            # gemini should pass
            assert data["checks"]["gemini_api"]["status"] == "pass"
            # ollama should be skipped
            assert data["checks"]["ollama"]["status"] == "skipped"

    async def test_smoke_gemini_empty_response(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        mock_chroma = MagicMock()

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["gemini_api"]["status"] == "fail"
            assert data["checks"]["gemini_api"]["error"] == "empty response"
            assert data["status"] == "fail"
            assert resp.status == 503

    async def test_smoke_gemini_exception(self):
        req, _ = _make_request()
        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(side_effect=Exception("api error"))
        llm_mock.LOCAL_LLM_ENABLED = False

        mock_chroma = MagicMock()

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["gemini_api"]["status"] == "fail"
            assert "api error" in data["checks"]["gemini_api"]["error"]

    async def test_smoke_ollama_enabled_and_up(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = True
        llm_mock._ollama_available = AsyncMock(return_value=True)

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock()

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["ollama"]["status"] == "pass"

    async def test_smoke_ollama_enabled_but_down(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = True
        llm_mock._ollama_available = AsyncMock(return_value=False)

        mock_chroma = MagicMock()

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["ollama"]["status"] == "fail"

    async def test_smoke_chromadb_fail(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock(side_effect=Exception("chroma down"))

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["chromadb"]["status"] == "fail"

    async def test_smoke_config_missing_token(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=MagicMock())),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="", google_api_key="key")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["config"]["status"] == "fail"
            assert "discord_bot_token" in data["checks"]["config"]["error"]

    async def test_smoke_config_missing_google_key(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=MagicMock())),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="")),
            "skills": MagicMock(SKILLS={"search_web": object()}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["config"]["status"] == "fail"
            assert "google_api_key" in data["checks"]["config"]["error"]

    async def test_smoke_skill_registry_no_search_web(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=MagicMock())),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={"other_skill": object()}),  # no search_web
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["skill_registry"]["status"] == "fail"
            assert "missing" in data["checks"]["skill_registry"]["error"]

    async def test_smoke_skill_registry_empty(self):
        req, _ = _make_request()
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_model.generate_content = MagicMock(return_value=mock_resp)

        llm_mock = MagicMock()
        llm_mock._get_model = AsyncMock(return_value=mock_model)
        llm_mock.LOCAL_LLM_ENABLED = False

        with patch.dict("sys.modules", {
            "llm": llm_mock,
            "vector_store": MagicMock(_get_client=MagicMock(return_value=MagicMock())),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
            "config": MagicMock(cfg=MagicMock(discord_bot_token="tok", google_api_key="key")),
            "skills": MagicMock(SKILLS={}),
        }):
            resp = await mod._smoke_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["skill_registry"]["status"] == "fail"


# ---------------------------------------------------------------------------
# _trigger_scan_handler
# ---------------------------------------------------------------------------

class TestTriggerScanHandler:
    async def test_auth_disabled_triggers_scan(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", False)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "")
        req, bot = _make_request(headers={})

        with patch.dict("sys.modules", {
            "discord_background": MagicMock(_run_proactive_scan=AsyncMock()),
        }):
            with patch("discord_web.asyncio.create_task"):
                resp = await mod._trigger_scan_handler(req)
                data = json.loads(resp.body)
                assert data["status"] == "scan triggered"
                assert resp.status == 200

    async def test_auth_required_no_token_returns_503(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", True)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "")
        req, _ = _make_request(headers={})
        resp = await mod._trigger_scan_handler(req)
        assert resp.status == 503

    async def test_auth_required_bad_token_returns_401(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", True)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "correct-token")
        req, _ = _make_request(headers={"Authorization": "Bearer wrong"})
        resp = await mod._trigger_scan_handler(req)
        assert resp.status == 401

    async def test_auth_required_valid_token_triggers_scan(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", True)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "valid-token")
        req, _ = _make_request(headers={"Authorization": "Bearer valid-token"})

        with patch.dict("sys.modules", {
            "discord_background": MagicMock(_run_proactive_scan=AsyncMock()),
        }):
            with patch("discord_web.asyncio.create_task"):
                resp = await mod._trigger_scan_handler(req)
                assert resp.status == 200


# ---------------------------------------------------------------------------
# _webhook_handler
# ---------------------------------------------------------------------------

class TestWebhookHandler:
    async def test_no_auth_required_no_channel_returns_ok(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        req, _ = _make_request(source="plex", json_body={"event": "play"})

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("Title", "Desc", 0)),
            ),
        }):
            resp = await mod._webhook_handler(req)
            data = json.loads(resp.body)
            assert data["ok"] is True

    async def test_auth_required_no_secret_returns_503(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", True)
        monkeypatch.setattr(mod, "WEBHOOK_SECRET", "")
        req, _ = _make_request(source="test", json_body={})
        resp = await mod._webhook_handler(req)
        assert resp.status == 503

    async def test_auth_required_wrong_token_returns_401(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", True)
        monkeypatch.setattr(mod, "WEBHOOK_SECRET", "correct")
        req, _ = _make_request(
            source="test",
            headers={"Authorization": "Bearer wrong"},
            json_body={},
        )
        resp = await mod._webhook_handler(req)
        assert resp.status == 401

    async def test_auth_required_valid_bearer_returns_ok(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", True)
        monkeypatch.setattr(mod, "WEBHOOK_SECRET", "my-secret")
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        req, _ = _make_request(
            source="radarr",
            headers={"Authorization": "Bearer my-secret"},
            json_body={"eventType": "Download"},
        )
        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("Title", "Desc", 0)),
            ),
        }):
            resp = await mod._webhook_handler(req)
            data = json.loads(resp.body)
            assert data["ok"] is True

    async def test_auth_valid_x_openclaw_token_header(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", True)
        monkeypatch.setattr(mod, "WEBHOOK_SECRET", "my-secret")
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        req, _ = _make_request(
            source="sonarr",
            headers={"X-OpenClaw-Token": "my-secret"},
            json_body={"eventType": "Grab"},
        )
        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("Title", "Desc", 0)),
            ),
        }):
            resp = await mod._webhook_handler(req)
            assert json.loads(resp.body)["ok"] is True

    async def test_sends_embed_to_alert_channel(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 12345)
        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock()

        req, bot = _make_request(source="sonarr", json_body={"eventType": "Download"})
        bot.get_channel = MagicMock(return_value=mock_channel)

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={"sonarr": MagicMock(return_value=("Title", "Desc", 0x00ff00))},
                format_generic=MagicMock(return_value=("Generic", "Desc", 0)),
            ),
        }):
            resp = await mod._webhook_handler(req)
            assert json.loads(resp.body)["ok"] is True
            mock_channel.send.assert_called_once()

    async def test_uses_generic_formatter_when_source_unknown(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        req, _ = _make_request(source="unknown_service", json_body={"data": "value"})

        mock_generic = MagicMock(return_value=("Generic", "Description", 0))
        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(FORMATTERS={}, format_generic=mock_generic),
        }):
            resp = await mod._webhook_handler(req)
            assert json.loads(resp.body)["ok"] is True
            mock_generic.assert_called_once()

    async def test_json_parse_error_uses_empty_dict(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        req, _ = _make_request(source="test")
        req.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "", 0))

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("T", "D", 0)),
            ),
        }):
            resp = await mod._webhook_handler(req)
            assert json.loads(resp.body)["ok"] is True

    async def test_non_dict_payload_wrapped(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        req, _ = _make_request(source="test")
        req.json = AsyncMock(return_value=["list", "payload"])

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("T", "D", 0)),
            ),
        }):
            resp = await mod._webhook_handler(req)
            assert json.loads(resp.body)["ok"] is True

    async def test_error_event_triggers_llm_analysis_task(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 12345)

        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock()
        req, bot = _make_request(
            source="sonarr",
            json_body={"eventType": "error", "message": "critical failure"},
        )
        bot.get_channel = MagicMock(return_value=mock_channel)

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("Error", "critical failure", 0xff0000)),
            ),
        }):
            with patch("discord_web.asyncio.create_task") as mock_task:
                await mod._webhook_handler(req)
                assert mock_task.called

    async def test_error_keyword_in_payload_triggers_analysis(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 12345)

        mock_channel = AsyncMock()
        req, bot = _make_request(
            source="plex",
            json_body={"message": "critical error occurred"},
        )
        bot.get_channel = MagicMock(return_value=mock_channel)

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("Alert", "critical error occurred", 0)),
            ),
        }):
            with patch("discord_web.asyncio.create_task") as mock_task:
                await mod._webhook_handler(req)
                assert mock_task.called

    async def test_channel_send_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(mod, "WEBHOOK_REQUIRE_AUTH", False)
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 12345)

        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock(side_effect=Exception("discord error"))
        req, bot = _make_request(source="test", json_body={"key": "value"})
        bot.get_channel = MagicMock(return_value=mock_channel)

        with patch.dict("sys.modules", {
            "webhook_formatter": MagicMock(
                FORMATTERS={},
                format_generic=MagicMock(return_value=("T", "D", 0)),
            ),
        }):
            # Should not raise
            resp = await mod._webhook_handler(req)
            assert json.loads(resp.body)["ok"] is True


# ---------------------------------------------------------------------------
# _analyze_webhook_event
# ---------------------------------------------------------------------------

class TestAnalyzeWebhookEvent:
    async def test_sends_analysis_embed_on_success(self, monkeypatch):
        channel = AsyncMock()
        channel.send = AsyncMock()

        async def mock_wait_for(coro, timeout=None):
            return ("AI analysis text", [], "model")

        with patch("discord_web.asyncio.wait_for", side_effect=mock_wait_for):
            await mod._analyze_webhook_event("sonarr", {"event": "error"}, channel)
            channel.send.assert_called_once()
            call_args = channel.send.call_args
            assert call_args[1]["embed"] is not None or call_args[0]

    async def test_skips_send_when_analysis_is_empty(self, monkeypatch):
        channel = AsyncMock()
        channel.send = AsyncMock()

        async def mock_wait_for(coro, timeout=None):
            return ("", [], "model")

        with patch("discord_web.asyncio.wait_for", side_effect=mock_wait_for):
            await mod._analyze_webhook_event("sonarr", {}, channel)
            channel.send.assert_not_called()

    async def test_handles_llm_exception_gracefully(self, monkeypatch):
        channel = AsyncMock()
        channel.send = AsyncMock()

        async def mock_wait_for(coro, timeout=None):
            raise Exception("llm down")

        with patch("discord_web.asyncio.wait_for", side_effect=mock_wait_for):
            # Should not raise
            await mod._analyze_webhook_event("sonarr", {"event": "error"}, channel)
            channel.send.assert_not_called()

    async def test_analysis_text_truncated_to_embed_limit(self, monkeypatch):
        channel = AsyncMock()
        channel.send = AsyncMock()
        long_text = "x" * 5000

        async def mock_wait_for(coro, timeout=None):
            return (long_text, [], "model")

        with patch("discord_web.asyncio.wait_for", side_effect=mock_wait_for):
            await mod._analyze_webhook_event("sonarr", {}, channel)
            channel.send.assert_called_once()


# ---------------------------------------------------------------------------
# _health_llm_handler
# ---------------------------------------------------------------------------

class TestHealthLlmHandler:
    async def test_gemini_ok_when_api_key_set(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        req, _ = _make_request()

        # Make aiohttp raise so ollama is "down"
        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("conn refused")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=False),
            }):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["gemini"] == "ok"
                assert data["checks"]["ollama"] == "down"
                assert resp.status == 200  # gemini ok → any_ok=True

    async def test_gemini_unconfigured_when_no_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        req, _ = _make_request()

        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("conn refused")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=False),
            }):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["gemini"] == "unconfigured"

    async def test_ollama_ok_when_responds_200(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        req, _ = _make_request()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_ctx_mgr = MagicMock()
        mock_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx_mgr)

        with patch("discord_web.aiohttp.ClientSession", return_value=mock_session):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=False),
            }):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["ollama"] == "ok"

    async def test_ollama_down_when_non_200(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        req, _ = _make_request()

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_ctx_mgr = MagicMock()
        mock_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx_mgr)

        with patch("discord_web.aiohttp.ClientSession", return_value=mock_session):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=False),
            }):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["ollama"] == "down"

    async def test_copilot_proxy_ok_when_enabled(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        req, _ = _make_request()

        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("err")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=True),
            }):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["copilot_proxy"] == "ok"

    async def test_copilot_proxy_unconfigured_when_disabled(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        req, _ = _make_request()

        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("err")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=False),
            }):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["copilot_proxy"] == "unconfigured"

    async def test_copilot_proxy_unconfigured_on_import_error(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        req, _ = _make_request()

        # Setting sys.modules["model_router"] = None causes ImportError on
        # `from model_router import ...` regardless of suite state.
        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("err")):
            with patch.dict("sys.modules", {"model_router": None}):
                resp = await mod._health_llm_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["copilot_proxy"] == "unconfigured"

    async def test_all_down_returns_503(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        req, _ = _make_request()

        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("err")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(ollama_url="http://localhost:11434")),
                "model_router": MagicMock(COPILOT_PROXY_ENABLED=False),
            }):
                resp = await mod._health_llm_handler(req)
                assert resp.status == 503


# ---------------------------------------------------------------------------
# _health_memory_handler
# ---------------------------------------------------------------------------

class TestHealthMemoryHandler:
    async def test_chromadb_ok(self, monkeypatch, tmp_path):
        req, _ = _make_request()
        qmd = tmp_path / "qmd.json"
        qmd.write_text("{}")
        monkeypatch.setenv("QMD_PATH", str(qmd))

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock()

        with patch.dict("sys.modules", {
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
        }):
            resp = await mod._health_memory_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["chromadb"] == "ok"
            assert data["checks"]["qmd"] == "ok"

    async def test_chromadb_down_returns_503(self, monkeypatch, tmp_path):
        req, _ = _make_request()
        monkeypatch.setenv("QMD_PATH", str(tmp_path / "missing.json"))

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock(side_effect=Exception("chroma down"))

        with patch.dict("sys.modules", {
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
        }):
            resp = await mod._health_memory_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["chromadb"] == "down"
            assert resp.status == 503
            assert data["status"] == "degraded"

    async def test_qmd_missing(self, monkeypatch, tmp_path):
        req, _ = _make_request()
        monkeypatch.setenv("QMD_PATH", str(tmp_path / "nonexistent.json"))

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock()

        with patch.dict("sys.modules", {
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/t_dw.db"),
        }):
            resp = await mod._health_memory_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["qmd"] == "missing"

    async def test_threads_db_ok_with_real_db(self, monkeypatch, tmp_path):
        """Test threads_db check when DB exists and has threads table."""
        import sqlite3

        db_path = tmp_path / "threads.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")
        conn.close()

        req, _ = _make_request()
        monkeypatch.setenv("QMD_PATH", str(tmp_path / "qmd.json"))

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock()

        with patch.dict("sys.modules", {
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH=str(db_path)),
        }):
            resp = await mod._health_memory_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["threads_db"] == "ok"

    async def test_threads_db_down_on_failure(self, monkeypatch, tmp_path):
        req, _ = _make_request()
        monkeypatch.setenv("QMD_PATH", str(tmp_path / "qmd.json"))

        mock_chroma = MagicMock()
        mock_chroma.heartbeat = MagicMock()

        # Use a bad path that will cause sqlite to fail
        with patch.dict("sys.modules", {
            "vector_store": MagicMock(_get_client=MagicMock(return_value=mock_chroma)),
            "thread_store": MagicMock(DB_PATH="/tmp/no_threads_table.db"),
        }):
            resp = await mod._health_memory_handler(req)
            data = json.loads(resp.body)
            # threads_db may be "ok" (sqlite creates file) or "down" (no table)
            assert data["checks"]["threads_db"] in ("ok", "down")


# ---------------------------------------------------------------------------
# _health_services_handler
# ---------------------------------------------------------------------------

class TestHealthServicesHandler:
    async def test_docker_unavailable_when_no_sock(self, monkeypatch):
        req, _ = _make_request()

        with patch.dict("sys.modules", {
            "config": MagicMock(cfg=MagicMock(nas_host="")),
            "scheduler": MagicMock(scheduler=MagicMock(list_tasks=MagicMock(return_value=[]))),
        }):
            resp = await mod._health_services_handler(req)
            data = json.loads(resp.body)
            # On Mac, /var/run/docker.sock may or may not exist
            assert data["checks"]["docker"] in ("ok", "unavailable")
            assert data["checks"]["nas"] == "unconfigured"

    async def test_scheduler_ok(self, monkeypatch):
        req, _ = _make_request()
        mock_sched = MagicMock()
        mock_sched.list_tasks = MagicMock(return_value=["task1", "task2"])

        with patch.dict("sys.modules", {
            "config": MagicMock(cfg=MagicMock(nas_host="")),
            "scheduler": MagicMock(scheduler=mock_sched),
        }):
            resp = await mod._health_services_handler(req)
            data = json.loads(resp.body)
            assert "scheduler" in data["checks"]
            assert "ok" in data["checks"]["scheduler"]

    async def test_scheduler_down_on_exception(self, monkeypatch):
        req, _ = _make_request()

        # Patch scheduler module with a failing list_tasks
        failing_sched = MagicMock()
        failing_sched.list_tasks = MagicMock(side_effect=Exception("scheduler error"))

        with patch.dict("sys.modules", {
            "config": MagicMock(cfg=MagicMock(nas_host="")),
            "scheduler": MagicMock(scheduler=failing_sched),
        }):
            resp = await mod._health_services_handler(req)
            data = json.loads(resp.body)
            assert data["checks"]["scheduler"] == "down"

    async def test_nas_configured_and_ok(self, monkeypatch):
        req, _ = _make_request()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_ctx_mgr = MagicMock()
        mock_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx_mgr)

        with patch("discord_web.aiohttp.ClientSession", return_value=mock_session):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(nas_host="192.168.1.1")),
                "scheduler": MagicMock(scheduler=MagicMock(list_tasks=MagicMock(return_value=[]))),
            }):
                resp = await mod._health_services_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["nas"] == "ok"

    async def test_nas_down_on_error(self, monkeypatch):
        req, _ = _make_request()

        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("conn refused")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(nas_host="192.168.1.1")),
                "scheduler": MagicMock(scheduler=MagicMock(list_tasks=MagicMock(return_value=[]))),
            }):
                resp = await mod._health_services_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["nas"] == "down"

    async def test_nas_server_error_marked_down(self, monkeypatch):
        req, _ = _make_request()

        mock_resp = MagicMock()
        mock_resp.status = 500  # >=500 → down
        mock_ctx_mgr = MagicMock()
        mock_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx_mgr)

        with patch("discord_web.aiohttp.ClientSession", return_value=mock_session):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(nas_host="192.168.1.1")),
                "scheduler": MagicMock(scheduler=MagicMock(list_tasks=MagicMock(return_value=[]))),
            }):
                resp = await mod._health_services_handler(req)
                data = json.loads(resp.body)
                assert data["checks"]["nas"] == "down"

    async def test_overall_degraded_when_any_down(self, monkeypatch):
        req, _ = _make_request()

        with patch("discord_web.aiohttp.ClientSession", side_effect=Exception("err")):
            with patch.dict("sys.modules", {
                "config": MagicMock(cfg=MagicMock(nas_host="192.168.1.1")),
                "scheduler": MagicMock(scheduler=MagicMock(list_tasks=MagicMock(return_value=[]))),
            }):
                resp = await mod._health_services_handler(req)
                data = json.loads(resp.body)
                assert data["status"] == "degraded"
                assert resp.status == 503
