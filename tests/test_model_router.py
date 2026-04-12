"""Tests for model_router.py — query classification and routing logic."""


from types import SimpleNamespace

import model_router
from model_router import ModelRoute, classify_query


class TestClassifyQueryRouting:
    """Test that classify_query routes to the correct model backend."""

    def test_simple_chat_routes_to_ollama(self):
        route = classify_query("hello how are you", ollama_alive=True)
        assert route.model_type == "ollama"
        assert "simple" in route.reason.lower() or "conversational" in route.reason.lower()

    def test_simple_chat_falls_back_when_ollama_down(self):
        route = classify_query("hello how are you", ollama_alive=False)
        assert route.model_type == "gemini"
        assert "ollama down" in route.reason.lower()

    def test_tool_requiring_routes_to_gemini(self):
        route = classify_query("check the weather", needs_tools=True)
        assert route.model_type == "gemini"
        assert "tool" in route.reason.lower()

    def test_image_attachment_routes_to_gemini(self):
        route = classify_query("what is in this picture", has_image=True)
        assert route.model_type == "gemini"
        assert "multimodal" in route.reason.lower() or "image" in route.reason.lower()

    def test_explicit_local_preference_uses_ollama(self):
        route = classify_query("anything", model_preference="local", ollama_alive=True)
        assert route.model_type == "ollama"
        assert "preference" in route.reason.lower()

    def test_explicit_local_preference_falls_back_when_down(self):
        route = classify_query("anything", model_preference="local", ollama_alive=False)
        assert route.model_type == "gemini"
        assert "fallback" in route.reason.lower()

    def test_explicit_gemini_preference(self):
        route = classify_query("anything", model_preference="gemini")
        assert route.model_type == "gemini"
        assert "preference" in route.reason.lower()

    def test_explicit_openai_preference_with_key(self):
        route = classify_query("anything", model_preference="openai", has_openai_key=True)
        assert route.model_type == "openai"

    def test_explicit_anthropic_preference_with_key(self):
        route = classify_query("anything", model_preference="anthropic", has_anthropic_key=True)
        assert route.model_type == "anthropic"

    def test_explicit_copilot_preference_uses_copilot_when_available(self):
        route = classify_query("anything", model_preference="copilot", copilot_available=True)
        assert route.model_type == "copilot"

    def test_code_query_prefers_anthropic(self):
        route = classify_query(
            "fix the python code bug in my function",
            has_anthropic_key=True,
        )
        assert route.model_type == "anthropic"
        assert "code" in route.reason.lower()

    def test_code_query_falls_to_gemini_without_keys(self):
        route = classify_query("debug the python code error")
        assert route.model_type == "gemini"
        assert "code" in route.reason.lower()

    def test_analysis_routes_to_gemini(self):
        route = classify_query("analyze the data in this report and summarize findings")
        assert route.model_type == "gemini"
        assert "analysis" in route.reason.lower() or "research" in route.reason.lower()

    def test_simple_chat_prefers_copilot_when_available(self):
        route = classify_query("hello how are you", copilot_available=True, ollama_alive=True)
        assert route.model_type == "copilot"
        assert "copilot" in route.reason.lower()

    def test_balanced_profile_prefers_ollama_for_simple_chat(self):
        route = classify_query(
            "hello how are you",
            copilot_available=True,
            ollama_alive=True,
            routing_profile="balanced",
        )
        assert route.model_type == "ollama"
        assert "balanced" in route.reason.lower()

    def test_gemini_first_profile_routes_non_tool_chat_to_gemini(self):
        route = classify_query(
            "analyze this report for me",
            copilot_available=True,
            routing_profile="gemini-first",
        )
        assert route.model_type == "gemini"
        assert "gemini-first" in route.reason.lower()

    def test_analysis_prefers_copilot_when_available(self):
        route = classify_query(
            "analyze the data in this report and summarize findings",
            copilot_available=True,
        )
        assert route.model_type == "copilot"
        assert "copilot" in route.reason.lower()

    def test_tool_query_still_prefers_gemini_when_copilot_available(self):
        route = classify_query("check the weather", needs_tools=True, copilot_available=True)
        assert route.model_type == "gemini"

    def test_tool_query_uses_capability_selector(self, monkeypatch):
        monkeypatch.setattr(
            model_router,
            "select_tool_route",
            lambda **_kwargs: SimpleNamespace(
                provider="gemini",
                reason="requires tool/function calling; selected native-tool provider: gemini",
            ),
        )

        route = classify_query("check the weather", needs_tools=True, copilot_available=True)

        assert route.model_type == "gemini"
        assert "selected native-tool provider" in route.reason


class TestModelRoute:
    def test_repr(self):
        r = ModelRoute("gemini", "test reason")
        assert "gemini" in repr(r)
        assert "test reason" in repr(r)
