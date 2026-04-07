"""Security-focused tests for discord_web auth helpers."""

from unittest.mock import MagicMock

import discord_web as mod


def _request_with_headers(headers: dict[str, str]) -> MagicMock:
    req = MagicMock()
    req.headers = headers
    return req


class TestBearerAuthHelpers:
    def test_is_authorized_bearer_accepts_authorization_header(self):
        req = _request_with_headers({"Authorization": "Bearer secret-token"})
        assert mod._is_authorized_bearer(req, "secret-token")

    def test_is_authorized_bearer_accepts_x_openclaw_token_header(self):
        req = _request_with_headers({"X-OpenClaw-Token": "secret-token"})
        assert mod._is_authorized_bearer(req, "secret-token")

    def test_is_authorized_bearer_rejects_wrong_token(self):
        req = _request_with_headers({"Authorization": "Bearer wrong-token"})
        assert not mod._is_authorized_bearer(req, "secret-token")


class TestApiActionAuthGuard:
    def test_guard_returns_none_when_auth_disabled(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", False)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "")
        req = _request_with_headers({})
        assert mod._require_api_action_auth(req) is None

    def test_guard_returns_503_when_required_but_missing_token(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", True)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "")
        req = _request_with_headers({})
        resp = mod._require_api_action_auth(req)
        assert resp is not None
        assert resp.status == 503

    def test_guard_returns_401_on_bad_token(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", True)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "secret-token")
        req = _request_with_headers({"Authorization": "Bearer nope"})
        resp = mod._require_api_action_auth(req)
        assert resp is not None
        assert resp.status == 401

    def test_guard_returns_none_on_valid_token(self, monkeypatch):
        monkeypatch.setattr(mod, "API_ACTION_AUTH_REQUIRED", True)
        monkeypatch.setattr(mod, "API_ACTION_TOKEN", "secret-token")
        req = _request_with_headers({"Authorization": "Bearer secret-token"})
        assert mod._require_api_action_auth(req) is None
