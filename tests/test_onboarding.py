"""
Tests for onboarding system.
"""

import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from onboarding import (
    OnboardingManager,
    TutorialStep,
    UserProgress,
    get_onboarding_manager,
)


@pytest.fixture
def onboarding_manager(tmp_path):
    """Create onboarding manager with temp directory."""
    return OnboardingManager(data_dir=tmp_path)


def test_onboarding_manager_singleton():
    """Test that get_onboarding_manager returns singleton instance."""
    manager1 = get_onboarding_manager()
    manager2 = get_onboarding_manager()
    assert manager1 is manager2


def test_is_new_user(onboarding_manager):
    """Test checking if user is new."""
    assert onboarding_manager.is_new_user("user123")
    
    # Start onboarding
    onboarding_manager.start_onboarding("user123")
    
    assert not onboarding_manager.is_new_user("user123")


def test_start_onboarding(onboarding_manager):
    """Test starting onboarding for a new user."""
    progress = onboarding_manager.start_onboarding("user123")
    
    assert progress.user_id == "user123"
    assert progress.current_step == TutorialStep.WELCOME
    assert len(progress.completed_steps) == 0
    assert not progress.skipped
    assert progress.completed_at is None


def test_start_onboarding_existing_user(onboarding_manager):
    """Test starting onboarding for existing user returns existing progress."""
    progress1 = onboarding_manager.start_onboarding("user123")
    progress2 = onboarding_manager.start_onboarding("user123")
    
    assert progress1 is progress2


def test_skip_tutorial(onboarding_manager):
    """Test skipping the tutorial."""
    onboarding_manager.start_onboarding("user123")
    onboarding_manager.skip_tutorial("user123")
    
    progress = onboarding_manager.get_progress("user123")
    
    assert progress.skipped
    assert progress.completed_at is not None


def test_skip_tutorial_new_user(onboarding_manager):
    """Test skipping tutorial for new user."""
    onboarding_manager.skip_tutorial("user123")
    
    progress = onboarding_manager.get_progress("user123")
    
    assert progress is not None
    assert progress.skipped


def test_restart_tutorial(onboarding_manager):
    """Test restarting the tutorial."""
    # Start and complete a step
    onboarding_manager.start_onboarding("user123")
    onboarding_manager.complete_step("user123", TutorialStep.WELCOME)
    
    # Restart
    progress = onboarding_manager.restart_tutorial("user123")
    
    assert progress.current_step == TutorialStep.WELCOME
    assert len(progress.completed_steps) == 0
    assert not progress.skipped


def test_complete_step(onboarding_manager):
    """Test completing a tutorial step."""
    onboarding_manager.start_onboarding("user123")
    onboarding_manager.complete_step("user123", TutorialStep.WELCOME)
    
    progress = onboarding_manager.get_progress("user123")
    
    assert TutorialStep.WELCOME.value in progress.completed_steps
    assert progress.current_step == TutorialStep.BASIC_COMMANDS


def test_complete_all_steps(onboarding_manager):
    """Test completing all tutorial steps."""
    onboarding_manager.start_onboarding("user123")
    
    # Complete all steps
    for step in TutorialStep:
        onboarding_manager.complete_step("user123", step)
    
    progress = onboarding_manager.get_progress("user123")
    
    assert len(progress.completed_steps) == len(TutorialStep)
    assert progress.completed_at is not None


def test_get_progress_nonexistent_user(onboarding_manager):
    """Test getting progress for nonexistent user."""
    progress = onboarding_manager.get_progress("nonexistent")
    assert progress is None


def test_persistence(tmp_path):
    """Test that progress is persisted to disk."""
    # Create manager and start onboarding
    manager1 = OnboardingManager(data_dir=tmp_path)
    manager1.start_onboarding("user123")
    manager1.complete_step("user123", TutorialStep.WELCOME)
    
    # Create new manager with same data dir
    manager2 = OnboardingManager(data_dir=tmp_path)
    
    # Progress should be loaded
    progress = manager2.get_progress("user123")
    assert progress is not None
    assert TutorialStep.WELCOME.value in progress.completed_steps


def test_persistence_file_format(tmp_path):
    """Test the format of the persisted file."""
    manager = OnboardingManager(data_dir=tmp_path)
    manager.start_onboarding("user123")
    
    # Check file exists and is valid JSON
    progress_file = tmp_path / "onboarding_progress.json"
    assert progress_file.exists()
    
    with open(progress_file, "r") as f:
        data = json.load(f)
    
    assert "user123" in data
    assert data["user123"]["user_id"] == "user123"
    assert "started_at" in data["user123"]


@pytest.mark.asyncio
async def test_send_welcome_message(onboarding_manager):
    """Test sending welcome message."""
    mock_user = Mock()
    mock_user.id = 12345
    
    mock_channel = AsyncMock()
    
    await onboarding_manager.send_welcome_message(mock_user, mock_channel)
    
    # Should have sent a message
    mock_channel.send.assert_called_once()
    
    # Should have started onboarding
    progress = onboarding_manager.get_progress("12345")
    assert progress is not None


@pytest.mark.asyncio
async def test_send_step_message(onboarding_manager):
    """Test sending step message."""
    mock_user = Mock()
    mock_channel = AsyncMock()
    
    embed = await onboarding_manager.send_step_message(
        mock_user, mock_channel, TutorialStep.WELCOME
    )
    
    # Should have sent a message
    mock_channel.send.assert_called_once()
    
    # Check embed content
    assert embed.title is not None
    assert "Welcome" in embed.title


def test_get_step_content(onboarding_manager):
    """Test getting content for tutorial steps."""
    # Test all steps have content
    for step in TutorialStep:
        content = onboarding_manager._get_step_content(step)
        
        assert "title" in content
        assert "description" in content
        assert content["title"]
        assert content["description"]


def test_step_content_structure(onboarding_manager):
    """Test step content has proper structure."""
    content = onboarding_manager._get_step_content(TutorialStep.BASIC_COMMANDS)
    
    assert "title" in content
    assert "description" in content
    assert "example" in content
    assert "tips" in content


def test_tutorial_step_progression(onboarding_manager):
    """Test that steps progress in order."""
    onboarding_manager.start_onboarding("user123")
    
    steps = list(TutorialStep)
    
    for i, step in enumerate(steps[:-1]):
        progress = onboarding_manager.get_progress("user123")
        assert progress.current_step == step
        
        onboarding_manager.complete_step("user123", step)
        
        progress = onboarding_manager.get_progress("user123")
        assert progress.current_step == steps[i + 1]


def test_multiple_users(onboarding_manager):
    """Test managing multiple users simultaneously."""
    # Start onboarding for multiple users
    for i in range(5):
        onboarding_manager.start_onboarding(f"user{i}")
    
    # Complete different steps for each
    for i in range(5):
        for j in range(i + 1):
            if j < len(TutorialStep):
                onboarding_manager.complete_step(f"user{i}", list(TutorialStep)[j])
    
    # Verify each user's progress
    for i in range(5):
        progress = onboarding_manager.get_progress(f"user{i}")
        assert len(progress.completed_steps) == i + 1


@pytest.mark.asyncio
async def test_send_step_message_progress_indicator(onboarding_manager):
    """Test that step message includes progress indicator."""
    mock_user = Mock()
    mock_channel = AsyncMock()
    
    embed = await onboarding_manager.send_step_message(
        mock_user, mock_channel, TutorialStep.BASIC_COMMANDS
    )
    
    # Footer should contain step number
    assert embed.footer is not None
    assert "Step" in embed.footer.text
    assert "/" in embed.footer.text


def test_complete_step_for_new_user(onboarding_manager):
    """Test completing a step for a user who hasn't started onboarding."""
    # This should auto-start onboarding
    onboarding_manager.complete_step("user123", TutorialStep.WELCOME)
    
    progress = onboarding_manager.get_progress("user123")
    assert progress is not None
    assert TutorialStep.WELCOME.value in progress.completed_steps
