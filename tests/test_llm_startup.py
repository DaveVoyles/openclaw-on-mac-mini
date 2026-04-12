"""
Tests for llm.startup — scan_providers() and _log_availability_summary().

Covers:
1. Happy path — all 4 providers available
2. Copilot disabled via COPILOT_PROXY_ENABLED=False
3. Ollama raises an exception → ollama=False (swallowed)
4. Partial availability — only OPENAI_API_KEY set
5. Log format — _log_availability_summary emits ✅/❌ per provider

Import strategy: stub llm package + llm.providers + model_routing_policy in
sys.modules, then load startup.py via importlib.util.spec_from_file_location so
llm/__init__.py (which needs google-genai) is never executed.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Path to startup.py source
# ---------------------------------------------------------------------------
_STARTUP_PATH = str(Path(__file__).parent.parent / "src" / "llm" / "startup.py")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _build_stubs(copilot_enabled: bool = True, ollama_result=True):
    """Return (providers_stub, mrp_stub) with fresh AsyncMocks."""
    providers = ModuleType("llm.providers")
    providers.COPILOT_PROXY_ENABLED = copilot_enabled
    if isinstance(ollama_result, Exception):
        providers.check_proxy_health = AsyncMock(return_value=True)
    else:
        providers.check_proxy_health = AsyncMock(return_value=True)

    mrp = ModuleType("model_routing_policy")
    if isinstance(ollama_result, Exception):
        mrp.is_ollama_alive = AsyncMock(side_effect=ollama_result)
    else:
        mrp.is_ollama_alive = AsyncMock(return_value=ollama_result)

    return providers, mrp


def _load_startup(providers_stub, mrp_stub):
    """Install stubs and load startup.py fresh via spec loader."""
    # Build a minimal llm package that exposes providers as an attribute
    llm_pkg = ModuleType("llm")
    llm_pkg.__path__ = [str(Path(_STARTUP_PATH).parent)]
    llm_pkg.__package__ = "llm"
    llm_pkg.providers = providers_stub

    sys.modules["llm"] = llm_pkg
    sys.modules["llm.providers"] = providers_stub
    sys.modules["model_routing_policy"] = mrp_stub
    sys.modules.pop("llm.startup", None)

    spec = importlib.util.spec_from_file_location("llm.startup", _STARTUP_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["llm.startup"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanProvidersHappyPath:
    """All 4 providers available."""

    async def test_all_providers_available(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=True, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["copilot"]["available"] is True
        assert result["ollama"]["available"] is True
        assert result["openai"]["available"] is True
        assert result["anthropic"]["available"] is True

    async def test_returns_dict_type(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=True, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert isinstance(result, dict)
        assert set(result.keys()) == {"copilot", "ollama", "openai", "anthropic"}

    async def test_result_shape_has_available_and_latency(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=True, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        for provider in ("copilot", "ollama", "openai", "anthropic"):
            assert "available" in result[provider], f"Missing 'available' for {provider}"
            assert "latency_ms" in result[provider], f"Missing 'latency_ms' for {provider}"

    async def test_pinged_providers_have_latency_when_available(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=True, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        # Network-pinged providers report a real latency when reachable
        assert isinstance(result["copilot"]["latency_ms"], float)
        assert result["copilot"]["latency_ms"] >= 0
        assert isinstance(result["ollama"]["latency_ms"], float)
        assert result["ollama"]["latency_ms"] >= 0

    async def test_key_checked_providers_have_none_latency(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=True, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        # Key-checked providers have no latency measurement
        assert result["openai"]["latency_ms"] is None
        assert result["anthropic"]["latency_ms"] is None


class TestCopilotDisabled:
    """COPILOT_PROXY_ENABLED=False → copilot=False regardless of ping result."""

    async def test_copilot_false_when_disabled(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=False, ollama_result=True)
        # Even though check_proxy_health returns True, copilot must be False
        providers.check_proxy_health = AsyncMock(return_value=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["copilot"]["available"] is False

    async def test_copilot_latency_none_when_disabled(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=False, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["copilot"]["latency_ms"] is None

    async def test_other_providers_unaffected(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=False, ollama_result=True)
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["ollama"]["available"] is True
        assert result["openai"]["available"] is True
        assert result["anthropic"]["available"] is True

    async def test_proxy_health_not_called_when_disabled(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(copilot_enabled=False, ollama_result=True)
        startup = _load_startup(providers, mrp)

        await startup.scan_providers()

        providers.check_proxy_health.assert_not_called()


class TestOllamaException:
    """is_ollama_alive raises → ollama=False (exception swallowed by asyncio.gather)."""

    async def test_ollama_false_on_exception(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(
            copilot_enabled=True,
            ollama_result=ConnectionRefusedError("Ollama down"),
        )
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["ollama"]["available"] is False

    async def test_ollama_latency_none_on_exception(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(
            copilot_enabled=True,
            ollama_result=ConnectionRefusedError("Ollama down"),
        )
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["ollama"]["latency_ms"] is None

    async def test_scan_does_not_raise(self, monkeypatch):
        """scan_providers must not propagate the Ollama exception."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(
            copilot_enabled=True,
            ollama_result=RuntimeError("Unexpected Ollama error"),
        )
        startup = _load_startup(providers, mrp)

        # Must not raise
        result = await startup.scan_providers()
        assert isinstance(result, dict)

    async def test_other_providers_unaffected_by_ollama_error(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs(
            copilot_enabled=True,
            ollama_result=ConnectionRefusedError("Ollama down"),
        )
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["copilot"]["available"] is True
        assert result["openai"]["available"] is True
        assert result["anthropic"]["available"] is True


class TestPartialAvailability:
    """Env-var key detection: only present keys count as True."""

    async def test_openai_true_anthropic_false(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        providers, mrp = _build_stubs()
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["openai"]["available"] is True
        assert result["anthropic"]["available"] is False

    async def test_no_keys_both_false(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        providers, mrp = _build_stubs()
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["openai"]["available"] is False
        assert result["anthropic"]["available"] is False

    async def test_only_anthropic_key_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

        providers, mrp = _build_stubs()
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["openai"]["available"] is False
        assert result["anthropic"]["available"] is True

    async def test_empty_string_key_is_falsy(self, monkeypatch):
        """An empty string env var must count as unavailable."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        providers, mrp = _build_stubs()
        startup = _load_startup(providers, mrp)

        result = await startup.scan_providers()

        assert result["openai"]["available"] is False


class TestLogFormat:
    """_log_availability_summary must emit ✅/❌ for every provider."""

    def _startup(self):
        providers, mrp = _build_stubs()
        return _load_startup(providers, mrp)

    def _status(self, copilot=True, ollama=False, openai=True, anthropic=False):
        """Build a status dict in the new shape."""
        return {
            "copilot": {"available": copilot, "latency_ms": 42.0 if copilot else None},
            "ollama": {"available": ollama, "latency_ms": 11.0 if ollama else None},
            "openai": {"available": openai, "latency_ms": None},
            "anthropic": {"available": anthropic, "latency_ms": None},
        }

    def test_available_provider_uses_checkmark(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(self._status(copilot=True, openai=True))

        assert mock_info.called
        log_output = mock_info.call_args[0][0]
        assert "✅" in log_output
        assert "copilot" in log_output
        assert "openai" in log_output

    def test_unavailable_provider_uses_cross(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(
                self._status(copilot=False, ollama=False, openai=False, anthropic=False)
            )

        log_output = mock_info.call_args[0][0]
        assert "❌" in log_output
        assert "✅" not in log_output

    def test_all_available_no_cross(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(
                self._status(copilot=True, ollama=True, openai=True, anthropic=True)
            )

        log_output = mock_info.call_args[0][0]
        assert "✅" in log_output
        assert "❌" not in log_output

    def test_all_providers_appear_in_log(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(self._status())

        log_output = mock_info.call_args[0][0]
        for provider in ("copilot", "ollama", "openai", "anthropic"):
            assert provider in log_output, f"Expected '{provider}' in log"

    def test_header_line_present(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(
                {"copilot": {"available": True, "latency_ms": None}}
            )

        log_output = mock_info.call_args[0][0]
        assert "Provider Availability" in log_output

    def test_latency_ms_included_in_log_when_available(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(
                {"copilot": {"available": True, "latency_ms": 142.0}}
            )

        log_output = mock_info.call_args[0][0]
        assert "142ms" in log_output

    def test_pinged_unavailable_shows_timeout(self):
        startup = self._startup()

        with patch("logging.Logger.info") as mock_info:
            startup._log_availability_summary(
                {"copilot": {"available": False, "latency_ms": None}}
            )

        log_output = mock_info.call_args[0][0]
        assert "timeout" in log_output

    async def test_scan_providers_triggers_log(self, monkeypatch):
        """scan_providers() must call _log_availability_summary exactly once."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        providers, mrp = _build_stubs()
        startup = _load_startup(providers, mrp)

        with patch.object(startup, "_log_availability_summary") as mock_log:
            await startup.scan_providers()

        mock_log.assert_called_once()
        call_arg = mock_log.call_args[0][0]
        assert isinstance(call_arg, dict)
        assert "copilot" in call_arg
        assert "available" in call_arg["copilot"]
