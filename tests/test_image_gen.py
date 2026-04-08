"""Tests for image_gen.py — availability check, generate_image dimension clamping, error paths."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock heavy config deps before importing
_image_gen_mocks = {
    "config": MagicMock(
        TIMEOUT_FAST=10,
        cfg=MagicMock(sd_url="http://host.docker.internal:7860", sd_timeout=120),
    ),
    "http_session": MagicMock(SessionManager=MagicMock(return_value=MagicMock(get=AsyncMock()))),
}
for _name, _mock in _image_gen_mocks.items():
    sys.modules.setdefault(_name, _mock)

import image_gen as ig  # noqa: E402


@pytest.fixture
def mock_session():
    """Return a mock aiohttp session."""
    session = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def patch_get_session(mock_session):
    with patch("image_gen._get_session", return_value=mock_session):
        yield mock_session


class TestIsAvailable:
    @pytest.mark.asyncio
    async def test_returns_true_when_health_200(self, mock_session):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_session.get = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))
        result = await ig.is_available()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_health_500(self, mock_session):
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_session.get = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))
        result = await ig.is_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, mock_session):
        mock_session.get = MagicMock(side_effect=Exception("connection refused"))
        result = await ig.is_available()
        assert result is False


class TestGenerateImageDimensionClamping:
    """Test dimension validation without making real HTTP calls."""

    @pytest.mark.asyncio
    async def test_small_dimensions_clamped_to_256(self, mock_session):
        """Dimensions below 256 should be clamped to 256, rounded to 64."""
        captured = {}

        def capture_post(url, json=None, timeout=None):
            captured["payload"] = json
            resp = AsyncMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=b"x" * 2000)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=resp)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_session.post = capture_post

        await ig.generate_image("test prompt", width=10, height=10)
        assert captured["payload"]["width"] == 256
        assert captured["payload"]["height"] == 256

    @pytest.mark.asyncio
    async def test_large_dimensions_clamped_to_1536(self, mock_session):
        """Dimensions above 1536 should be clamped."""
        captured = {}

        def capture_post(url, json=None, timeout=None):
            captured["payload"] = json
            resp = AsyncMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=b"x" * 2000)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=resp)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_session.post = capture_post

        await ig.generate_image("test prompt", width=9999, height=8888)
        assert captured["payload"]["width"] <= 1536
        assert captured["payload"]["height"] <= 1536

    @pytest.mark.asyncio
    async def test_dimensions_rounded_to_64(self, mock_session):
        """Dimensions should be rounded down to nearest 64."""
        captured = {}

        def capture_post(url, json=None, timeout=None):
            captured["payload"] = json
            resp = AsyncMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=b"x" * 2000)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=resp)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_session.post = capture_post

        await ig.generate_image("test", width=700, height=900)
        assert captured["payload"]["width"] % 64 == 0
        assert captured["payload"]["height"] % 64 == 0
        # 700//64=10, 10*64=640
        assert captured["payload"]["width"] == 640
        assert captured["payload"]["height"] == 896  # 900//64=14, 14*64=896


class TestGenerateImageErrors:
    @pytest.mark.asyncio
    async def test_http_non_200_returns_none_with_message(self, mock_session):
        mock_resp = AsyncMock()
        mock_resp.status = 503
        mock_resp.text = AsyncMock(return_value="Service unavailable")
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))

        img_bytes, msg = await ig.generate_image("a prompt")
        assert img_bytes is None
        assert "503" in msg

    @pytest.mark.asyncio
    async def test_timeout_returns_none_with_timeout_message(self, mock_session):
        import aiohttp
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())

        img_bytes, msg = await ig.generate_image("a prompt")
        assert img_bytes is None
        assert "timed out" in msg.lower()

    @pytest.mark.asyncio
    async def test_client_error_returns_none_with_message(self, mock_session):
        import aiohttp
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("refused"))

        img_bytes, msg = await ig.generate_image("a prompt")
        assert img_bytes is None
        assert "reach" in msg.lower() or "SD service" in msg

    @pytest.mark.asyncio
    async def test_suspiciously_small_output_returns_none(self, mock_session):
        """Response with fewer than 1000 bytes signals a bad image."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"tiny")
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))

        img_bytes, msg = await ig.generate_image("a prompt")
        assert img_bytes is None
        assert "suspiciously small" in msg

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none_with_message(self, mock_session):
        mock_session.post = MagicMock(side_effect=ValueError("unexpected"))

        img_bytes, msg = await ig.generate_image("a prompt")
        assert img_bytes is None
        assert "failed" in msg.lower()


class TestGenerateImageSuccess:
    @pytest.mark.asyncio
    async def test_success_returns_bytes_and_ok(self, mock_session):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"x" * 5000)  # > 1000 bytes
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))

        img_bytes, msg = await ig.generate_image("beautiful sunset")
        assert img_bytes == b"x" * 5000
        assert msg == "ok"

    @pytest.mark.asyncio
    async def test_default_negative_prompt_used_when_none_provided(self, mock_session):
        captured = {}

        def capture_post(url, json=None, timeout=None):
            captured["payload"] = json
            resp = AsyncMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=b"x" * 2000)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=resp)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_session.post = capture_post

        await ig.generate_image("test", negative_prompt="")
        assert "blurry" in captured["payload"]["negative_prompt"]

    @pytest.mark.asyncio
    async def test_custom_params_passed_through(self, mock_session):
        captured = {}

        def capture_post(url, json=None, timeout=None):
            captured["payload"] = json
            resp = AsyncMock()
            resp.status = 200
            resp.read = AsyncMock(return_value=b"x" * 2000)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=resp)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_session.post = capture_post

        await ig.generate_image(
            "cyberpunk city",
            negative_prompt="blurry",
            width=512,
            height=512,
            steps=30,
            seed=42,
        )
        assert captured["payload"]["prompt"] == "cyberpunk city"
        assert captured["payload"]["steps"] == 30
        assert captured["payload"]["seed"] == 42
        assert captured["payload"]["negative_prompt"] == "blurry"
