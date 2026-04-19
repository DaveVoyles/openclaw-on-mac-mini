"""Tests for model_routing_policy.select_multimodal_route and image analysis wiring."""

from unittest.mock import AsyncMock, patch

import pytest

from model_routing_policy import MultimodalRouteDecision, select_multimodal_route

# ---------------------------------------------------------------------------
# select_multimodal_route policy
# ---------------------------------------------------------------------------


class TestSelectMultimodalRoute:
    def test_multimodal_route_prefers_copilot_when_available(self):
        result = select_multimodal_route(copilot_available=True, has_openai_key=False)
        assert isinstance(result, MultimodalRouteDecision)
        assert result.provider == "copilot"
        assert "GPT-4o" in result.reason

    def test_falls_back_to_openai_when_copilot_unavailable_with_key(self):
        result = select_multimodal_route(copilot_available=False, has_openai_key=True)
        assert result.provider == "openai"

    def test_falls_back_to_gemini_when_no_alternatives(self):
        result = select_multimodal_route(copilot_available=False, has_openai_key=False)
        assert result.provider == "gemini"
        assert "Gemini" in result.reason

    def test_copilot_beats_openai_key(self):
        result = select_multimodal_route(copilot_available=True, has_openai_key=True)
        assert result.provider == "copilot"

    def test_multimodal_route_returns_frozen_dataclass(self):
        result = select_multimodal_route(copilot_available=True, has_openai_key=False)
        with pytest.raises((AttributeError, TypeError)):
            result.provider = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Capability registry flags
# ---------------------------------------------------------------------------


class TestCapabilityRegistryMultimodal:
    def test_copilot_supports_multimodal_when_available(self):
        from model_routing_policy import build_provider_capability_registry

        registry = build_provider_capability_registry(
            has_openai_key=False,
            has_anthropic_key=False,
            copilot_available=True,
            ollama_alive=False,
        )
        assert registry["copilot"].supports_multimodal is True

    def test_copilot_not_multimodal_when_unavailable(self):
        from model_routing_policy import build_provider_capability_registry

        registry = build_provider_capability_registry(
            has_openai_key=False,
            has_anthropic_key=False,
            copilot_available=False,
            ollama_alive=False,
        )
        assert registry["copilot"].supports_multimodal is False

    def test_openai_supports_multimodal_with_key(self):
        from model_routing_policy import build_provider_capability_registry

        registry = build_provider_capability_registry(
            has_openai_key=True,
            has_anthropic_key=False,
            copilot_available=False,
            ollama_alive=False,
        )
        assert registry["openai"].supports_multimodal is True


# ---------------------------------------------------------------------------
# analyze_image wiring
# ---------------------------------------------------------------------------


class TestAnalyzeImageRouting:
    _image = b"\x89PNG\r\n\x1a\n"  # minimal PNG-like bytes
    _mime = "image/png"

    @pytest.mark.asyncio
    async def test_routes_to_copilot_vision_when_available(self):
        from llm.response import analyze_image

        with (
            patch("llm.response.select_multimodal_route") as mock_route,
            patch("llm.providers.COPILOT_PROXY_ENABLED", True),
            patch("llm.providers.chat_openai_vision", AsyncMock(return_value="Copilot desc")),
            patch("llm.response._needs_tools", return_value=False),
        ):
            mock_route.return_value = MultimodalRouteDecision(provider="copilot", reason="test")
            result = await analyze_image(self._image, self._mime, "describe it")

        assert result == "Copilot desc"

    @pytest.mark.asyncio
    async def test_multimodal_route_falls_back_to_gemini_when_copilot_returns_none(self):
        from unittest.mock import MagicMock

        from llm.response import analyze_image

        fake_response = MagicMock()
        fake_response.text = "Gemini desc"

        with (
            patch("llm.response.select_multimodal_route") as mock_route,
            patch("llm.providers.chat_openai_vision", AsyncMock(return_value=None)),
            patch("llm.response.GOOGLE_API_KEY", "fake-key"),
            patch("llm.response._client") as mock_client,
            patch("llm.response.asyncio") as mock_asyncio,
            patch("llm.response._needs_tools", return_value=False),
        ):
            mock_asyncio.to_thread = AsyncMock(return_value=fake_response)
            mock_route.return_value = MultimodalRouteDecision(provider="copilot", reason="test")
            result = await analyze_image(self._image, self._mime, "describe it")

        assert "Gemini desc" in result

    @pytest.mark.asyncio
    async def test_rejects_unsupported_mime(self):
        from llm.response import analyze_image

        result = await analyze_image(self._image, "application/pdf", "describe it")
        assert "Unsupported" in result
