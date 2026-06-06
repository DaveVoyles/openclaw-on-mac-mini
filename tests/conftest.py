"""
Pytest configuration for OpenClaw tests.
Adds the project root to sys.path so all source modules are importable.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import discord
except ModuleNotFoundError:  # pragma: no cover - exercised in lightweight CLI test environments

    class _DiscordInteraction:
        pass

    discord = SimpleNamespace(Interaction=_DiscordInteraction)

# Make sure the project root and src/ are on the path for all test modules
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _patch_memory_dirs(tmp_path, monkeypatch):
    """Redirect /memory paths to a temp dir so tests never touch the real FS."""
    import memory
    import memory_conversation
    import memory_helpers
    import memory_preferences
    import memory_session

    mem_dir = tmp_path / "memory"
    # Patch the thin hub (backward compat) and each sub-module that owns the constants
    for mod in (memory, memory_helpers):
        monkeypatch.setattr(mod, "MEMORY_DIR", mem_dir)
        monkeypatch.setattr(mod, "THREADS_DIR", mem_dir / "threads")
        monkeypatch.setattr(mod, "SUMMARIES_DIR", mem_dir / "summaries")
    # memory_conversation imports THREADS_DIR directly from memory_helpers
    monkeypatch.setattr(memory_conversation, "THREADS_DIR", mem_dir / "threads")
    # memory_thread_persistence also references THREADS_DIR from memory_helpers
    import memory_thread_persistence

    monkeypatch.setattr(memory_thread_persistence, "THREADS_DIR", mem_dir / "threads")
    # memory_session imports SUMMARIES_DIR/MEMORY_DIR from memory_helpers
    monkeypatch.setattr(memory_session, "MEMORY_DIR", mem_dir)
    monkeypatch.setattr(memory_session, "SUMMARIES_DIR", mem_dir / "summaries")
    monkeypatch.setattr(memory_session, "HANDOVER_DIR", mem_dir / "handovers")
    # memory_preferences imports MEMORY_DIR from memory_helpers
    monkeypatch.setattr(memory_preferences, "MEMORY_DIR", mem_dir)
    monkeypatch.setattr(memory_preferences, "_PREFS_DIR", mem_dir / "preferences")


@pytest.fixture
def mock_llm():
    """Standard LLM mock returning (text, history, model_name) tuple."""
    m = AsyncMock(return_value=("Test response", [], "test-model"))
    return m


@pytest.fixture
def mock_discord_interaction():
    """Mock Discord interaction for testing slash commands."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 12345
    interaction.user.name = "TestUser"
    interaction.user.display_name = "TestUser"
    interaction.channel_id = 67890
    interaction.channel = MagicMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Reset module-level caches between tests to prevent state leakage."""
    yield
    # Clean up any module caches that might leak between tests
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(("llm", "memory", "spending", "scheduler")):
            mod = sys.modules[mod_name]
            # Reset known cache attributes
            for attr in ("_model", "_thinking_model", "_system_prompt_cache", "_tool_cache"):
                if hasattr(mod, attr):
                    try:
                        if isinstance(getattr(mod, attr), dict):
                            getattr(mod, attr).clear()
                        else:
                            setattr(mod, attr, None)
                    except Exception as e:
                        import warnings

                        warnings.warn(f"Failed to clear cache {attr} on {mod_name}: {e}")

    # Also clear cached lazy-loaded exports from llm.__init__ to avoid stale cache
    # This ensures __getattr__ is called again for lazy-loaded attributes
    if "llm" in sys.modules:
        try:
            llm_mod = sys.modules["llm"]
            # Only remove attributes that are in _LAZY_EXPORTS to avoid breaking the module
            from llm import _LAZY_EXPORTS

            for attr_name in _LAZY_EXPORTS.keys():
                llm_mod.__dict__.pop(attr_name, None)
        except Exception:
            pass  # If cleanup fails, continue


# Disabled for pytest-asyncio compatibility with pytest>=8
# @pytest.fixture(autouse=True, scope="function")
# async def _cleanup_http_sessions():
#     """Close all aiohttp sessions after each test to prevent 'Unclosed client session' warnings."""
#     yield
#     # Close all SessionManager instances
#     try:
#         from http_session import close_all
#         await close_all()
#     except Exception:
#         pass  # Ignore if module not imported yet


@pytest.fixture(autouse=True)
def _mock_llm_model_init(monkeypatch):
    """Mock LLM model initialization to avoid requiring API keys in tests."""

    # Prevent model initialization from requiring API keys
    def mock_get_model():
        """Mock _get_model to return a MagicMock instead of real Gemini model."""
        return MagicMock()

    def mock_init_gemini(*args, **kwargs):
        """Mock Gemini model init."""
        return MagicMock()

    # Patch before llm module imports
    try:
        import llm_client

        monkeypatch.setattr(llm_client, "_init_gemini_model", mock_init_gemini)
    except Exception:
        pass  # Module not imported yet, that's fine


@pytest.fixture(autouse=True, scope="session")
def _trigger_lazy_llm_loads():
    """Trigger lazy loading of llm submodules to populate sys.modules for tests."""
    try:
        import llm

        # Access lazy-loaded submodules via __getattr__ to populate sys.modules
        _ = llm.chat
        _ = llm.chat_stream
    except Exception:
        pass  # If skills import fails, that's OK; tests will handle their own imports


@pytest.fixture(autouse=True, scope="function")
def _ensure_event_loop_for_slack_bot():
    """Ensure event loop is available for tests that import slack_bot.

    slack_bot.py calls asyncio.ensure_future() at import time to register
    async handlers. In pytest-xdist workers, we need to ensure an event loop
    is set up before slack_bot is imported for the first time.
    """
    import asyncio

    try:
        # Try to get current loop, if it doesn't exist create one
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Event loop is closed")
        except RuntimeError:
            # No event loop or it's closed, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except Exception:
        pass  # If anything fails, just continue

    yield


@pytest.fixture(autouse=True)
def reset_emergency_stop():
    """Reset the global emergency-stop flag before and after each test.

    Consolidated from test_approval_store_unit.py, test_approvals.py, and
    test_approvals_extended.py. The flag is module-level state in approval_store;
    resetting it here prevents cross-test bleed for the entire suite.
    """
    from approval_store import set_emergency_stop

    set_emergency_stop(False)
    yield
    set_emergency_stop(False)


@pytest.fixture
def sched(tmp_path):
    """Fresh TaskScheduler backed by a temp file (no global state).

    Consolidated from test_scheduler.py and test_scheduler_coverage.py.
    Patches SCHEDULE_FILE so tests never write to /memory.
    """
    import scheduler as scheduler_module
    from scheduler import TaskScheduler

    temp_file = tmp_path / "schedules.json"
    with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
        yield TaskScheduler()


@pytest.fixture(autouse=True, scope="function")
def _isolate_environ_and_modules():
    """Isolate environment variables and sys.modules state per test.

    This fixture ensures that:
    1. Each test starts with a clean GOOGLE_OAUTH environment
    2. sys.modules modifications by calendar_skills/slack_bot don't pollute other tests
    3. State between parallel workers doesn't interfere

    Addresses flaky tests in:
    - test_slack_bot.py::TestCalendarCommand::test_slack_bot_has_calendar_handler
    - test_model_selection.py::TestChatModelPreference::test_chat_local_preference_success
    """
    import os

    # Save initial GOOGLE_OAUTH state (these are the keys manipulated by calendar tests)
    oauth_keys = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ]
    saved_oauth = {k: os.environ.get(k) for k in oauth_keys}
    saved_modules = frozenset(sys.modules.keys())

    yield

    # Restore only the GOOGLE_OAUTH keys to prevent calendar_skills state pollution
    for k in oauth_keys:
        if saved_oauth[k] is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = saved_oauth[k]

    # Remove only skill module root names that were newly imported during the test.
    # Only remove the top-level module, not submodules or core packages.
    # This is conservative to avoid breaking lazy loading or module relationships.
    skill_modules = {"calendar_skills", "slack_bot", "dropbox_sync"}
    modules_to_clean = []
    for mod_name in list(sys.modules.keys()):
        if mod_name not in saved_modules:
            # Only remove the exact skill module names, not submodules
            if mod_name in skill_modules:
                modules_to_clean.append(mod_name)

    for mod_name in modules_to_clean:
        try:
            del sys.modules[mod_name]
        except KeyError:
            pass  # Already removed


@pytest.fixture(autouse=True, scope="function")
def _isolate_metrics_collector():
    """Reset metrics collector singleton state before and after tests.

    The metrics_collector module uses a module-level singleton (_collector).
    In parallel test execution, this singleton can retain state from previous tests.
    This fixture ensures each test gets a clean collector state.

    Addresses intermittent failures in:
    - test_metrics_collector.py::test_record_command
    """
    try:
        import metrics_collector

        # Reset singleton BEFORE test runs
        metrics_collector._collector = None
    except (ImportError, AttributeError):
        pass

    yield

    # Clean up after test as well
    try:
        import metrics_collector

        metrics_collector._collector = None
    except ImportError:
        pass


@pytest.fixture(autouse=True, scope="function")
def _isolate_trace_context():
    """Reset trace context state before and after tests.

    The trace_context module uses contextvars.ContextVar for storing trace state.
    In parallel pytest-xdist workers, context vars should be isolated, but explicit
    cleanup between tests ensures no state leakage.

    Addresses intermittent failures in:
    - test_trace_context.py::TestTraceContext::test_trace_context_sets_and_clears
    """
    try:
        import trace_context

        # Reset context BEFORE test runs
        if hasattr(trace_context, "_current_trace"):
            trace_context._current_trace.set(None)
    except (ImportError, AttributeError):
        pass

    yield

    # Reset trace context after test completes as well
    try:
        import trace_context

        if hasattr(trace_context, "_current_trace"):
            trace_context._current_trace.set(None)
    except ImportError:
        pass
