from http.cookies import SimpleCookie

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

import dashboard.auth as auth


@pytest.fixture(autouse=True)
def _dashboard_auth_config(monkeypatch):
    monkeypatch.setattr(auth.cfg, "dashboard_username", "admin")
    monkeypatch.setattr(auth.cfg, "dashboard_password", "swordfish")
    monkeypatch.setattr(auth.cfg, "dashboard_session_secret", "test-session-secret")
    monkeypatch.setattr(auth.cfg, "dashboard_api_token", "api-token")
    auth._FAILED_ATTEMPTS.clear()
    auth._WARNED_FALLBACK_SECRET = False
    yield
    auth._FAILED_ATTEMPTS.clear()


async def _client(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _cookie_from_response(response) -> str:
    jar = SimpleCookie()
    jar.load(response.headers["Set-Cookie"])
    return jar[auth.COOKIE_NAME].value


def test_hash_and_verify_round_trip():
    hashed = auth.hash_password("correct horse battery staple")

    assert hashed.startswith("pbkdf2$")
    assert auth.verify_password("correct horse battery staple", hashed)
    assert not auth.verify_password("wrong", hashed)


def test_plaintext_password_fallback():
    assert auth.verify_password("swordfish", "swordfish")
    assert not auth.verify_password("swordfish", "other")


def test_session_cookie_valid_expired_and_tampered():
    cookie = auth.make_session_cookie("admin", ttl_seconds=60)

    assert auth.verify_session_cookie(cookie) == "admin"
    assert auth.verify_session_cookie(auth.make_session_cookie("admin", ttl_seconds=-1)) is None
    assert auth.verify_session_cookie(cookie + "tampered") is None


@pytest.mark.asyncio
async def test_valid_login_sets_secure_session_cookie_and_protected_page_loads():
    async def protected(_request):
        return web.Response(text="dashboard")

    app = web.Application()
    app.router.add_post("/api/login", auth.login_api_handler)
    app.router.add_get("/dashboard", auth.require_session(protected))
    client = await _client(app)
    try:
        response = await client.post("/api/login", json={"username": "admin", "password": "swordfish"})
        assert response.status == 200
        assert await response.json() == {"ok": True}

        set_cookie = response.headers["Set-Cookie"]
        assert f"{auth.COOKIE_NAME}=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "SameSite=Strict" in set_cookie

        cookie_value = _cookie_from_response(response)
        page = await client.get("/dashboard", headers={"Cookie": f"{auth.COOKIE_NAME}={cookie_value}"})
        assert page.status == 200
        assert await page.text() == "dashboard"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_username_is_case_insensitive_and_trimmed():
    app = web.Application()
    app.router.add_post("/api/login", auth.login_api_handler)
    client = await _client(app)
    try:
        # Configured username is "admin"; varied casing and surrounding
        # whitespace must all authenticate (password stays exact).
        for supplied in ("ADMIN", "Admin", "  admin  "):
            auth._FAILED_ATTEMPTS.clear()
            response = await client.post("/api/login", json={"username": supplied, "password": "swordfish"})
            assert response.status == 200, supplied
            assert await response.json() == {"ok": True}

        # Password remains case-sensitive and exact.
        auth._FAILED_ATTEMPTS.clear()
        bad = await client.post("/api/login", json={"username": "ADMIN", "password": "SWORDFISH"})
        assert bad.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_wrong_password_returns_401_and_rate_limits():
    app = web.Application()
    app.router.add_post("/api/login", auth.login_api_handler)
    client = await _client(app)
    try:
        for _ in range(5):
            response = await client.post("/api/login", json={"username": "admin", "password": "wrong"})
            assert response.status == 401

        response = await client.post("/api/login", json={"username": "admin", "password": "wrong"})
        assert response.status == 429
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rate_limit_not_bypassed_by_spoofed_forwarded_for():
    app = web.Application()
    app.router.add_post("/api/login", auth.login_api_handler)
    client = await _client(app)
    try:
        # Rotate a fresh X-Forwarded-For on every request; throttling must still
        # trigger because it keys on the targeted account, not the client header.
        for i in range(5):
            response = await client.post(
                "/api/login",
                json={"username": "admin", "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            )
            assert response.status == 401

        response = await client.post(
            "/api/login",
            json={"username": "admin", "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.250"},
        )
        assert response.status == 429
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_require_session_redirects_unauthenticated_page_request():
    async def protected(_request):
        return web.Response(text="dashboard")

    app = web.Application()
    app.router.add_get("/dashboard", auth.require_session(protected))
    client = await _client(app)
    try:
        response = await client.get("/dashboard?tab=home", allow_redirects=False)

        assert response.status == 302
        assert response.headers["Location"].startswith("/login?from=")
        assert "dashboard" in response.headers["Location"]
    finally:
        await client.close()


def test_require_action_auth_rejects_unauthenticated_request():
    request = make_mocked_request("POST", "/api/docker/action")

    response = auth.require_action_auth(request)

    assert response is not None
    assert response.status == 401


@pytest.mark.asyncio
async def test_require_action_auth_allows_valid_cookie_and_api_token():
    async def action_handler(request):
        auth_error = auth.require_action_auth(request)
        if auth_error is not None:
            return auth_error
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/api/docker/action", action_handler)
    client = await _client(app)
    try:
        cookie_value = auth.make_session_cookie("admin", ttl_seconds=60)
        response = await client.post(
            "/api/docker/action",
            headers={"Cookie": f"{auth.COOKIE_NAME}={cookie_value}"},
        )
        assert response.status == 200

        response = await client.post(
            "/api/docker/action",
            headers={"X-API-Token": "api-token"},
        )
        assert response.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auth_disabled_fail_open(monkeypatch):
    monkeypatch.setattr(auth.cfg, "dashboard_username", "")
    monkeypatch.setattr(auth.cfg, "dashboard_password", "")

    async def protected(_request):
        return web.Response(text="dashboard")

    app = web.Application()
    app.router.add_get("/dashboard", auth.require_session(protected))
    app.router.add_post("/api/login", auth.login_api_handler)
    client = await _client(app)
    try:
        page = await client.get("/dashboard")
        assert page.status == 200

        login = await client.post("/api/login", json={})
        assert login.status == 200
        assert await login.json() == {"ok": True}

        request = make_mocked_request("POST", "/api/docker/action")
        assert auth.require_action_auth(request) is None
    finally:
        await client.close()
