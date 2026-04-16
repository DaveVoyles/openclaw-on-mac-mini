"""Tests for Wave 29: Copilot tool routing opt-in."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import model_routing_policy
from model_routing_policy import (
    build_provider_capability_registry,
    select_tool_route,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _registry(*, copilot=True, openai=False, anthropic=False, ollama=False):
    return build_provider_capability_registry(
        has_openai_key=openai,
        has_anthropic_key=anthropic,
        copilot_available=copilot,
        ollama_alive=ollama,
    )


def _tool_route(*, copilot=True, openai=False, anthropic=False, ollama=False):
    return select_tool_route(
        has_openai_key=openai,
        has_anthropic_key=anthropic,
        copilot_available=copilot,
        ollama_alive=ollama,
    )


# ---------------------------------------------------------------------------
# 1. COPILOT_TOOLS_ENABLED=false → copilot supports_native_tools is False
# ---------------------------------------------------------------------------

def test_copilot_tools_disabled_by_default():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", False):
        reg = _registry(copilot=True)
    assert reg["copilot"].supports_native_tools is False


# ---------------------------------------------------------------------------
# 2. COPILOT_TOOLS_ENABLED=true → copilot supports_native_tools is True
# ---------------------------------------------------------------------------

def test_copilot_tools_enabled_sets_native_tools_true():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        reg = _registry(copilot=True)
    assert reg["copilot"].supports_native_tools is True


# ---------------------------------------------------------------------------
# 3. select_tool_route with tools disabled + copilot available → gemini
# ---------------------------------------------------------------------------

def test_select_tool_route_disabled_returns_gemini():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", False):
        decision = _tool_route(copilot=True)
    assert decision.provider == "gemini"


# ---------------------------------------------------------------------------
# 4. select_tool_route with tools enabled + copilot available → copilot
# ---------------------------------------------------------------------------

def test_select_tool_route_enabled_returns_copilot():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        decision = _tool_route(copilot=True)
    assert decision.provider == "copilot"


# ---------------------------------------------------------------------------
# 5. select_tool_route with tools enabled + copilot unavailable → gemini
# ---------------------------------------------------------------------------

def test_select_tool_route_enabled_copilot_unavailable_falls_back_to_gemini():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        decision = _tool_route(copilot=False)
    assert decision.provider == "gemini"


# ---------------------------------------------------------------------------
# 6. build_provider_capability_registry copilot available + tools enabled
# ---------------------------------------------------------------------------

def test_registry_copilot_available_tools_enabled():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        reg = _registry(copilot=True)
    cap = reg["copilot"]
    assert cap.available is True
    assert cap.supports_native_tools is True


# ---------------------------------------------------------------------------
# 7. build_provider_capability_registry copilot available + tools disabled
# ---------------------------------------------------------------------------

def test_registry_copilot_available_tools_disabled():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", False):
        reg = _registry(copilot=True)
    cap = reg["copilot"]
    assert cap.available is True
    assert cap.supports_native_tools is False


# ---------------------------------------------------------------------------
# 8. Copilot unavailable + tools enabled → supports_native_tools still False
# ---------------------------------------------------------------------------

def test_registry_copilot_unavailable_tools_enabled_native_tools_false():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        reg = _registry(copilot=False)
    cap = reg["copilot"]
    assert cap.available is False
    assert cap.supports_native_tools is False


# ---------------------------------------------------------------------------
# 9. Tool route reason string mentions copilot when enabled
# ---------------------------------------------------------------------------

def test_tool_route_reason_mentions_copilot_when_enabled():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        decision = _tool_route(copilot=True)
    assert "copilot" in decision.reason.lower()


# ---------------------------------------------------------------------------
# 10. Tool route reason mentions gemini when disabled
# ---------------------------------------------------------------------------

def test_tool_route_reason_mentions_gemini_when_disabled():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", False):
        decision = _tool_route(copilot=True)
    assert "gemini" in decision.reason.lower()


# ---------------------------------------------------------------------------
# 11. Tools enabled + copilot unavailable, openai key present → openai
# ---------------------------------------------------------------------------

def test_select_tool_route_enabled_copilot_unavailable_openai_available():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        decision = _tool_route(copilot=False, openai=True)
    assert decision.provider == "openai"


# ---------------------------------------------------------------------------
# 12. Tools disabled, order: gemini beats anthropic/openai/copilot
# ---------------------------------------------------------------------------

def test_select_tool_route_disabled_gemini_wins_over_openai():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", False):
        decision = _tool_route(copilot=True, openai=True, anthropic=True)
    assert decision.provider == "gemini"


# ---------------------------------------------------------------------------
# 13. COPILOT_TOOLS_ENABLED env var parsing from string "true"
# ---------------------------------------------------------------------------

def test_copilot_tools_enabled_constant_type():
    # Verify it's a bool regardless of env value
    assert isinstance(model_routing_policy.COPILOT_TOOLS_ENABLED, bool)


# ---------------------------------------------------------------------------
# 14. Gemini is always available in registry regardless of flags
# ---------------------------------------------------------------------------

def test_gemini_always_available_in_registry():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", True):
        reg = _registry(copilot=False, openai=False, anthropic=False, ollama=False)
    assert reg["gemini"].available is True
    assert reg["gemini"].supports_native_tools is True


# ---------------------------------------------------------------------------
# 15. Default fallback reason when no provider available (edge case)
# ---------------------------------------------------------------------------

def test_tool_route_default_fallback_reason():
    with patch.object(model_routing_policy, "COPILOT_TOOLS_ENABLED", False):
        # Gemini is always available, so it should always be selected.
        # Patch registry to simulate no native-tool providers.
        with patch.object(
            model_routing_policy,
            "build_provider_capability_registry",
            return_value={},
        ):
            decision = select_tool_route(
                has_openai_key=False,
                has_anthropic_key=False,
                copilot_available=False,
                ollama_alive=False,
            )
    assert decision.provider == "gemini"
    assert "defaulted" in decision.reason.lower()
