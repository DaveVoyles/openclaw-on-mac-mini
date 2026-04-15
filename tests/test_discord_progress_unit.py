"""Unit tests for discord_progress.ProgressTracker."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_discord_stub():
    """Build a minimal discord stub with the attributes ProgressTracker needs."""
    import types
    stub = types.SimpleNamespace()
    stub.Embed = MagicMock(return_value=MagicMock())
    stub.Color = MagicMock()
    stub.Color.purple = MagicMock(return_value=0x800080)
    return stub


@pytest.fixture
def mock_interaction():
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return interaction


@pytest.fixture(autouse=True)
def patch_discord(monkeypatch):
    """Patch discord in discord_progress so Embed/Color are available."""
    import discord_progress
    monkeypatch.setattr(discord_progress, "discord", _make_discord_stub())


@pytest.mark.asyncio
async def test_progress_tracker_start(mock_interaction):
    from discord_progress import ProgressTracker
    tracker = ProgressTracker(mock_interaction, title="Test")
    await tracker.start()
    mock_interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_progress_tracker_update(mock_interaction):
    from discord_progress import ProgressTracker
    tracker = ProgressTracker(mock_interaction, title="Test")
    await tracker.start()
    await tracker.update("Step 1…")
    assert "Step 1…" in tracker._lines


@pytest.mark.asyncio
async def test_progress_tracker_done(mock_interaction):
    from discord_progress import ProgressTracker
    tracker = ProgressTracker(mock_interaction, title="Test")
    await tracker.start()
    await tracker.done("All finished")
    assert any("Done" in line or "finished" in line for line in tracker._lines)


@pytest.mark.asyncio
async def test_progress_tracker_handles_edit_failure(mock_interaction):
    """Silent edit failure should be logged, not raised."""
    from discord_progress import ProgressTracker
    tracker = ProgressTracker(mock_interaction, title="Test")
    await tracker.start()
    # Simulate edit failure
    tracker._message = MagicMock(edit=AsyncMock(side_effect=Exception("Discord error")))
    # Should not raise
    await tracker.update("Failing update")

