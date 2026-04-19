"""Tests for Wave 29: expanded Copilot model picker and context limits."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm.context_limits import get_model_context_window
from model_router import copilot_model_for_message

# ---------------------------------------------------------------------------
# copilot_model_for_message tests
# ---------------------------------------------------------------------------

class TestCopilotModelForMessage:
    def test_code_query_returns_claude_default(self):
        result = copilot_model_for_message("write a python function to parse JSON")
        assert result == "claude-sonnet-4.5"

    def test_code_query_respects_anthropic_model_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-opus-4"}):
            result = copilot_model_for_message("write a python function")
            assert result == "claude-opus-4"

    def test_reasoning_query_returns_o1_mini_default(self):
        result = copilot_model_for_message("prove this theorem using induction")
        assert result == "o1-mini"

    def test_reasoning_query_respects_copilot_reasoning_model_env(self):
        with patch.dict(os.environ, {"COPILOT_REASONING_MODEL": "o1"}):
            result = copilot_model_for_message("prove this theorem using induction")
            assert result == "o1"

    def test_math_query_returns_o1_mini(self):
        result = copilot_model_for_message("calculate the derivative of x squared")
        assert result == "o1-mini"

    def test_step_by_step_query_returns_o1_mini(self):
        result = copilot_model_for_message("explain step by step how to solve this")
        assert result == "o1-mini"

    def test_algorithm_complexity_returns_o1_mini(self):
        result = copilot_model_for_message("what is the complexity O(n) of this algorithm")
        assert result == "o1-mini"

    def test_simple_query_returns_gpt4o_default(self):
        result = copilot_model_for_message("what is the weather like today")
        assert result == "gpt-4o"

    def test_empty_message_returns_gpt4o(self):
        result = copilot_model_for_message("")
        assert result == "gpt-4o"

    def test_none_message_returns_gpt4o(self):
        result = copilot_model_for_message(None)
        assert result == "gpt-4o"

    def test_code_wins_over_reasoning(self):
        """Code pattern takes priority over reasoning pattern."""
        result = copilot_model_for_message("write a python function to solve this equation")
        assert result == "claude-sonnet-4.5"

    def test_openai_model_env_respected_for_default(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4.1"}):
            result = copilot_model_for_message("what is the capital of France")
            assert result == "gpt-4.1"

    def test_statistics_query_returns_o1_mini(self):
        result = copilot_model_for_message("explain statistics and probability distributions")
        assert result == "o1-mini"

    def test_logical_reasoning_returns_o1_mini(self):
        result = copilot_model_for_message("use logical reasoning to deduce the answer")
        assert result == "o1-mini"

    def test_debug_code_query_returns_claude(self):
        result = copilot_model_for_message("debug this python script")
        assert result == "claude-sonnet-4.5"


# ---------------------------------------------------------------------------
# context_limits tests
# ---------------------------------------------------------------------------

class TestContextLimits:
    def test_gpt4o_context_window(self):
        assert get_model_context_window("gpt-4o") == 128_000

    def test_gpt4o_mini_context_window(self):
        assert get_model_context_window("gpt-4o-mini") == 128_000

    def test_claude_sonnet_45_context_window(self):
        assert get_model_context_window("claude-sonnet-4.5") == 200_000

    def test_o1_mini_context_window(self):
        assert get_model_context_window("o1-mini") == 200_000

    def test_gpt41_context_window(self):
        assert get_model_context_window("gpt-4.1") == 1_047_576

    def test_gpt41_mini_context_window(self):
        assert get_model_context_window("gpt-4.1-mini") == 1_047_576

    def test_o1_context_window(self):
        assert get_model_context_window("o1") == 200_000

    def test_o3_mini_context_window(self):
        assert get_model_context_window("o3-mini") == 200_000

    def test_claude_opus_4_context_window(self):
        assert get_model_context_window("claude-opus-4") == 200_000

    def test_claude_sonnet_4_context_window(self):
        assert get_model_context_window("claude-sonnet-4") == 200_000

    def test_w29_model_picker_unknown_model_returns_none(self):
        assert get_model_context_window("unknown-model-xyz") is None

    def test_w29_model_picker_none_returns_none(self):
        assert get_model_context_window(None) is None
