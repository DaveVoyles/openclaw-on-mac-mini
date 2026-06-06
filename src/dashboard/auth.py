"""Authentication helpers for the OpenClaw dashboard."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from aiohttp import web

from config import cfg

COOKIE_NAME = "oc_session"
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
HASH_ITERATIONS = 260_000
# key -> (attempts, locked_until, last_seen)
_FAILED_ATTEMPTS: dict[str, tuple[int, float, float]] = {}
_MAX_FAILED_ATTEMPTS = 5
_LOCK_SECONDS = 60
_MAX_TRACKED_KEYS = 2048
_WARNED_FALLBACK_SECRET = False

log = logging.getLogger(__name__)


def hash_password(plain: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, HASH_ITERATIONS)
    return "pbkdf2${}${}${}".format(
        HASH_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(supplied: str, stored: str) -> bool:
    """Verify a supplied password against plaintext or PBKDF2 storage."""
    if stored.startswith("pbkdf2$"):
        try:
            _, iterations_raw, salt_raw, digest_raw = stored.split("$", 3)
            iterations = int(iterations_raw)
            salt = base64.b64decode(salt_raw.encode("ascii"), validate=True)
            expected = base64.b64decode(digest_raw.encode("ascii"), validate=True)
        except (binascii.Error, ValueError, TypeError):
            return False
        if iterations <= 0:
            return False
        try:
            actual = hashlib.pbkdf2_hmac("sha256", supplied.encode("utf-8"), salt, iterations)
        except ValueError:
            return False
        return hmac.compare_digest(actual, expected)

    return hmac.compare_digest(supplied.encode("utf-8"), stored.encode("utf-8"))


def _auth_disabled() -> bool:
    return not cfg.dashboard_username and not cfg.dashboard_password


def _session_secret() -> bytes:
    global _WARNED_FALLBACK_SECRET

    secret = getattr(cfg, "dashboard_session_secret", "")
    if secret:
        return secret.encode("utf-8")

    if not _WARNED_FALLBACK_SECRET:
        log.warning("DASHBOARD_SESSION_SECRET is not set; using derived dashboard session secret")
        _WARNED_FALLBACK_SECRET = True
    material = "|".join(
        [
            "openclaw-dashboard-session",
            cfg.dashboard_username,
            cfg.dashboard_password,
            cfg.dashboard_api_token,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).digest()


def _b64_username(username: str) -> str:
    return base64.urlsafe_b64encode(username.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64_username(value: str) -> str | None:
    try:
        padded = value + ("=" * (-len(value) % 4))
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def make_session_cookie(username: str, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS) -> str:
    """Create a signed dashboard session cookie value."""
    expiry = str(int(time.time()) + ttl_seconds)
    signed = f"{username}|{expiry}"
    signature = hmac.new(_session_secret(), signed.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{_b64_username(username)}|{expiry}|{signature}"


def verify_session_cookie(value: str | None) -> str | None:
    """Return the session username when a cookie is valid and unexpired."""
    if not value:
        return None

    try:
        username_raw, expiry_raw, supplied_signature = value.split("|", 2)
        expiry = int(expiry_raw)
    except (ValueError, TypeError):
        return None

    if expiry < int(time.time()):
        return None

    username = _unb64_username(username_raw)
    if username is None:
        return None

    signed = f"{username}|{expiry_raw}"
    expected_signature = hmac.new(_session_secret(), signed.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    return username


def _rate_key(request: web.Request, username: str) -> str:
    """Derive a brute-force tracking key for a login attempt.

    ``X-Forwarded-For`` is client-controlled and must never be trusted for
    security decisions, so we key throttling on the targeted account (the
    realistic attack surface) and fall back to the real peer address only when
    no username was supplied.
    """
    if username:
        return "user:" + username.lower()
    return "ip:" + (request.remote or "unknown")


def _purge_attempts(now: float) -> None:
    """Evict expired entries and bound the tracking table size."""
    stale = [
        key
        for key, (_attempts, locked_until, last_seen) in _FAILED_ATTEMPTS.items()
        if (locked_until and locked_until <= now) or (now - last_seen > _LOCK_SECONDS)
    ]
    for key in stale:
        _FAILED_ATTEMPTS.pop(key, None)
    if len(_FAILED_ATTEMPTS) > _MAX_TRACKED_KEYS:
        oldest = sorted(_FAILED_ATTEMPTS, key=lambda k: _FAILED_ATTEMPTS[k][2])
        for key in oldest[: len(_FAILED_ATTEMPTS) - _MAX_TRACKED_KEYS]:
            _FAILED_ATTEMPTS.pop(key, None)


def _rate_limited(key: str) -> bool:
    attempts, locked_until, _last_seen = _FAILED_ATTEMPTS.get(key, (0, 0.0, 0.0))
    return attempts >= _MAX_FAILED_ATTEMPTS and locked_until > time.time()


def _record_failed_attempt(key: str) -> None:
    now = time.time()
    _purge_attempts(now)
    attempts, locked_until, _last_seen = _FAILED_ATTEMPTS.get(key, (0, 0.0, 0.0))
    if locked_until and locked_until <= now:
        attempts = 0
    attempts += 1
    locked_until = now + _LOCK_SECONDS if attempts >= _MAX_FAILED_ATTEMPTS else 0.0
    _FAILED_ATTEMPTS[key] = (attempts, locked_until, now)


async def login_api_handler(request: web.Request) -> web.Response:
    """Authenticate dashboard credentials and set a signed session cookie."""
    if _auth_disabled():
        return web.json_response({"ok": True})

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    supplied_username = str(payload.get("username", ""))
    supplied_password = str(payload.get("password", ""))

    rate_key = _rate_key(request, supplied_username)
    if _rate_limited(rate_key):
        return web.json_response({"message": "Too many failed login attempts"}, status=429)

    username_ok = hmac.compare_digest(
        supplied_username.strip().casefold().encode("utf-8"),
        cfg.dashboard_username.strip().casefold().encode("utf-8"),
    )
    password_ok = verify_password(supplied_password, cfg.dashboard_password)
    if not (username_ok and password_ok):
        _record_failed_attempt(rate_key)
        return web.json_response({"message": "Invalid username or password"}, status=401)

    _FAILED_ATTEMPTS.pop(rate_key, None)
    response = web.json_response({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        make_session_cookie(cfg.dashboard_username),
        max_age=DEFAULT_SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/",
    )
    return response


async def logout_handler(request: web.Request) -> web.StreamResponse:
    """Clear the dashboard session cookie and return to login."""
    response = web.Response(status=302, headers={"Location": "/login"})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


def require_session(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
    """Require a valid dashboard session for page routes."""

    async def _wrapped(request: web.Request) -> web.StreamResponse:
        if _auth_disabled():
            return await handler(request)
        if verify_session_cookie(request.cookies.get(COOKIE_NAME)):
            return await handler(request)
        return web.Response(
            status=302,
            headers={"Location": "/login?" + urlencode({"from": request.rel_url.path_qs})},
        )

    return _wrapped


def _valid_api_token(request: web.Request) -> bool:
    expected = cfg.dashboard_api_token
    if not expected:
        return False

    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
        if hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return True

    for header in ("X-API-Token", "X-OpenClaw-Token"):
        supplied = request.headers.get(header, "").strip()
        if supplied and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return True

    query_token = request.rel_url.query.get("api_key", "").strip()
    return bool(query_token) and hmac.compare_digest(query_token.encode("utf-8"), expected.encode("utf-8"))


def require_action_auth(request: web.Request) -> web.Response | None:
    """Authorize mutating dashboard API/tool calls."""
    if _auth_disabled():
        return None
    if verify_session_cookie(request.cookies.get(COOKIE_NAME)):
        return None
    if _valid_api_token(request):
        return None
    return web.json_response({"message": "Authentication required"}, status=401)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m dashboard.auth <password>", file=sys.stderr)
        raise SystemExit(2)
    print(hash_password(sys.argv[1]))
