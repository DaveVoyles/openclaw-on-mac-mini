#!/usr/bin/env python3
"""
OpenClaw Post-Deploy Smoke Test Suite

Runs after every docker compose build to verify the bot is healthy.
Tests infrastructure endpoints, Discord connectivity, and actual /ask queries.

Usage:
    python3 scripts/post_deploy_test.py           # full suite
    python3 scripts/post_deploy_test.py --quick    # skip LLM test (faster)

Requires: DISCORD_BOT_TOKEN in .env (or environment)
Zero external dependencies — uses only stdlib.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Load .env if available (override existing env vars for testing)
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if value:  # Only set non-empty values
                os.environ[key] = value

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
HEALTH_URL = os.getenv("HEALTH_URL", "http://localhost:8765")
TEST_CHANNEL_ID = os.getenv("TEST_CHANNEL_ID", os.getenv("ALERT_CHANNEL_ID", ""))

# Host-side overrides: .env has container-internal URLs (host.docker.internal)
# but this script runs on the host, so we use localhost
OLLAMA_HOST_URL = "http://localhost:11434"
COPILOT_PROXY_HOST_URL = os.getenv("COPILOT_PROXY_URL", "").replace("host.docker.internal", "localhost")

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


class TestResult:
    def __init__(self, name: str, status: str, detail: str = "", latency_ms: int = 0):
        self.name = name
        self.status = status
        self.detail = detail
        self.latency_ms = latency_ms

    def __str__(self):
        icon = PASS if self.status == "pass" else FAIL if self.status == "fail" else WARN
        lat = f" ({self.latency_ms}ms)" if self.latency_ms else ""
        det = f" — {self.detail}" if self.detail else ""
        return f"  {icon} {self.name}{lat}{det}"


def _http_get(url: str, headers: dict = None, timeout: int = 10) -> tuple:
    """Simple HTTP GET. Returns (status_code, body_str, latency_ms)."""
    hdrs = {"User-Agent": "OpenClaw-SmokeTest/1.0"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            lat = int((time.monotonic() - t0) * 1000)
            return resp.status, body, lat
    except urllib.error.HTTPError as e:
        lat = int((time.monotonic() - t0) * 1000)
        return e.code, e.read().decode(), lat
    except Exception as e:
        lat = int((time.monotonic() - t0) * 1000)
        return 0, str(e), lat


def _http_post(url: str, data: dict, headers: dict = None, timeout: int = 10) -> tuple:
    """Simple HTTP POST with JSON body. Returns (status_code, body_str, latency_ms)."""
    body = json.dumps(data).encode()
    hdrs = {"Content-Type": "application/json", "User-Agent": "OpenClaw-SmokeTest/1.0"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode()
            lat = int((time.monotonic() - t0) * 1000)
            return resp.status, resp_body, lat
    except urllib.error.HTTPError as e:
        lat = int((time.monotonic() - t0) * 1000)
        return e.code, e.read().decode(), lat
    except Exception as e:
        lat = int((time.monotonic() - t0) * 1000)
        return 0, str(e), lat


def test_health_endpoint() -> TestResult:
    """Test /health returns 200 with valid JSON."""
    status, body, lat = _http_get(f"{HEALTH_URL}/health")
    if status != 200:
        return TestResult("Health endpoint", "fail", f"HTTP {status}", lat)
    try:
        data = json.loads(body)
        uptime = int(data.get("uptime_seconds", 0))
        return TestResult("Health endpoint", "pass", f"up {uptime}s", lat)
    except Exception:
        return TestResult("Health endpoint", "fail", "Invalid JSON", lat)


def test_smoke_endpoint() -> TestResult:
    """Test /smoke returns subsystem checks."""
    status, body, lat = _http_get(f"{HEALTH_URL}/smoke", timeout=30)
    if status not in (200, 503):
        return TestResult("Smoke check", "fail", f"HTTP {status}", lat)
    try:
        data = json.loads(body)
        checks = data.get("checks", {})
        passed = sum(1 for v in checks.values() if v.get("status") == "pass")
        total = len(checks)
        failed_names = [k for k, v in checks.items() if v.get("status") != "pass"]
        detail = f"{passed}/{total} subsystems"
        if failed_names:
            detail += f" (failed: {', '.join(failed_names)})"
        s = "pass" if passed == total else "warn" if passed > total // 2 else "fail"
        return TestResult("Smoke check", s, detail, lat)
    except Exception as e:
        return TestResult("Smoke check", "fail", str(e), lat)


def test_dashboard() -> TestResult:
    """Test /dashboard returns HTML."""
    status, body, lat = _http_get(f"{HEALTH_URL}/dashboard")
    if status != 200:
        return TestResult("Dashboard", "fail", f"HTTP {status}", lat)
    if "<html" in body.lower():
        return TestResult("Dashboard", "pass", f"{len(body)} bytes", lat)
    return TestResult("Dashboard", "fail", "Not HTML", lat)


def test_api_dashboard() -> TestResult:
    """Test /api/dashboard returns valid JSON."""
    status, body, lat = _http_get(f"{HEALTH_URL}/api/dashboard")
    if status != 200:
        return TestResult("Dashboard API", "fail", f"HTTP {status}", lat)
    try:
        data = json.loads(body)
        return TestResult("Dashboard API", "pass",
                          f"{len(data)} fields, skills={data.get('skill_count', '?')}", lat)
    except Exception:
        return TestResult("Dashboard API", "fail", "Invalid JSON", lat)


def test_ollama() -> TestResult:
    """Test Ollama is reachable."""
    status, body, lat = _http_get(f"{OLLAMA_HOST_URL}/api/tags", timeout=5)
    if status != 200:
        return TestResult("Ollama", "fail", f"HTTP {status}", lat)
    try:
        data = json.loads(body)
        models = [m["name"] for m in data.get("models", [])]
        return TestResult("Ollama", "pass", ", ".join(models[:3]), lat)
    except Exception:
        return TestResult("Ollama", "pass", "reachable", lat)


def test_discord_bot() -> TestResult:
    """Test the bot is connected to Discord."""
    if not BOT_TOKEN:
        return TestResult("Discord bot", "warn", "No DISCORD_BOT_TOKEN")
    status, body, lat = _http_get(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {BOT_TOKEN}"},
    )
    if status != 200:
        return TestResult("Discord bot", "fail", f"HTTP {status}", lat)
    try:
        data = json.loads(body)
        return TestResult("Discord bot", "pass", data.get("username", "?"), lat)
    except Exception:
        return TestResult("Discord bot", "pass", "connected", lat)


def test_copilot_proxy() -> TestResult:
    """Test Copilot proxy is reachable (if configured)."""
    proxy_url = COPILOT_PROXY_HOST_URL
    if not proxy_url:
        return TestResult("Copilot proxy", "warn", "Not configured")
    token = os.getenv("COPILOT_PROXY_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    status, body, lat = _http_get(f"{proxy_url.rstrip('/')}/models", headers=headers, timeout=10)
    if status == 200:
        try:
            count = len(json.loads(body).get("data", []))
            return TestResult("Copilot proxy", "pass", f"{count} models", lat)
        except Exception:
            return TestResult("Copilot proxy", "pass", "reachable", lat)
    return TestResult("Copilot proxy", "fail", f"HTTP {status}", lat)


def test_llm_responds() -> TestResult:
    """Test the LLM pipeline by calling chat() inside the container."""
    try:
        t0 = time.monotonic()
        result = subprocess.run(
            [
                "docker", "exec", "openclaw", "python3", "-c",
                "import asyncio; "
                "from llm import chat; "
                "r = asyncio.run(chat('Say exactly: DEPLOY_OK', model_preference='gemini')); "
                "print('REPLY:', r[0][:200]); "
                "print('MODEL:', r[2])"
            ],
            capture_output=True, text=True, timeout=60,
        )
        lat = int((time.monotonic() - t0) * 1000)

        output = result.stdout.strip()
        if "DEPLOY_OK" in output:
            model = ""
            for line in output.split("\n"):
                if line.startswith("MODEL:"):
                    model = line.split(":", 1)[1].strip()
            return TestResult("/ask LLM pipeline", "pass", f"via {model}", lat)
        elif result.returncode != 0:
            err = (result.stderr or result.stdout)[:100]
            return TestResult("/ask LLM pipeline", "fail", err, lat)
        else:
            return TestResult("/ask LLM pipeline", "warn",
                              f"Responded but no DEPLOY_OK: {output[:80]}", lat)
    except subprocess.TimeoutExpired:
        return TestResult("/ask LLM pipeline", "fail", "Timed out (60s)")
    except Exception as e:
        return TestResult("/ask LLM pipeline", "fail", str(e))


def test_discord_send_receive() -> TestResult:
    """Send a message to Discord and verify the bot can see the channel."""
    if not BOT_TOKEN or not TEST_CHANNEL_ID:
        return TestResult("Discord channel", "warn", "No BOT_TOKEN or TEST_CHANNEL_ID")
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}

    # Send test message
    status, body, lat = _http_post(
        f"https://discord.com/api/v10/channels/{TEST_CHANNEL_ID}/messages",
        {"content": "🧪 Post-deploy smoke test — verifying bot connectivity."},
        headers=headers,
    )
    if status != 200:
        return TestResult("Discord channel", "fail", f"Can't send: HTTP {status}", lat)

    # Clean up — delete the test message
    try:
        msg_id = json.loads(body).get("id")
        if msg_id:
            req = urllib.request.Request(
                f"https://discord.com/api/v10/channels/{TEST_CHANNEL_ID}/messages/{msg_id}",
                headers=headers, method="DELETE",
            )
            urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

    return TestResult("Discord channel", "pass", "send verified", lat)


def main():
    quick = "--quick" in sys.argv

    print()
    print("🧪 OpenClaw Post-Deploy Smoke Tests")
    print("=" * 50)
    if quick:
        print("   (quick mode — skipping LLM/Discord tests)")
    print()

    # Infrastructure tests
    results = [
        test_health_endpoint(),
        test_smoke_endpoint(),
        test_dashboard(),
        test_api_dashboard(),
        test_ollama(),
        test_discord_bot(),
        test_copilot_proxy(),
    ]

    if not quick:
        results.append(test_discord_send_receive())
        results.append(test_llm_responds())

    passed = sum(1 for r in results if r.status == "pass")
    warned = sum(1 for r in results if r.status == "warn")
    failed = sum(1 for r in results if r.status == "fail")

    for r in results:
        print(r)

    print()
    print(f"{'=' * 50}")
    print(f"  {PASS} {passed} passed  {WARN} {warned} warned  {FAIL} {failed} failed")
    print()

    if failed > 0:
        print("❌ DEPLOY VERIFICATION FAILED")
        sys.exit(1)
    elif warned > 0:
        print("⚠️ Deploy OK with warnings")
        sys.exit(0)
    else:
        print("✅ Deploy verified — all systems nominal")
        sys.exit(0)


if __name__ == "__main__":
    main()
