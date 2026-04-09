"""
Pytest configuration for OpenClaw tests.
Adds the project root to sys.path so all source modules are importable.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord

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
