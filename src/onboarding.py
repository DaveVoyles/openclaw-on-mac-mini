"""
User Onboarding System for OpenClaw.

Provides:
- Welcome messages for new users
- Interactive tutorial (5-7 steps)
- Feature discovery
- Progress tracking
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import discord
from discord import Embed

logger = logging.getLogger(__name__)


class TutorialStep(Enum):
    """Tutorial step definitions."""

    WELCOME = "welcome"
    BASIC_COMMANDS = "basic_commands"
    SCHEDULED_TASKS = "scheduled_tasks"
    API_INTEGRATIONS = "api_integrations"
    DASHBOARD_ACCESS = "dashboard_access"
    ADVANCED_FEATURES = "advanced_features"
    COMMUNITY_RESOURCES = "community_resources"


@dataclass
class UserProgress:
    """Track user onboarding progress."""

    user_id: str
    started_at: datetime
    current_step: TutorialStep = TutorialStep.WELCOME
    completed_steps: list[str] = field(default_factory=list)
    skipped: bool = False
    completed_at: datetime | None = None


class OnboardingManager:
    """Manage user onboarding and tutorials."""

    def __init__(self, data_dir: Path = Path("data")):
        self.data_dir = data_dir
        self.data_dir.mkdir(exist_ok=True)
        self.progress_file = self.data_dir / "onboarding_progress.json"
        self._user_progress: dict[str, UserProgress] = {}
        self._load_progress()

    def _load_progress(self):
        """Load user progress from disk."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, "r") as f:
                    data = json.load(f)
                    for user_id, progress_data in data.items():
                        self._user_progress[user_id] = UserProgress(
                            user_id=progress_data["user_id"],
                            started_at=datetime.fromisoformat(progress_data["started_at"]),
                            current_step=TutorialStep(progress_data["current_step"]),
                            completed_steps=progress_data["completed_steps"],
                            skipped=progress_data.get("skipped", False),
                            completed_at=datetime.fromisoformat(progress_data["completed_at"])
                            if progress_data.get("completed_at")
                            else None,
                        )
            except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError) as e:
                logger.error(f"Error loading onboarding progress: {e}")

    def _save_progress(self):
        """Save user progress to disk."""
        data = {}
        for user_id, progress in self._user_progress.items():
            data[user_id] = {
                "user_id": progress.user_id,
                "started_at": progress.started_at.isoformat(),
                "current_step": progress.current_step.value,
                "completed_steps": progress.completed_steps,
                "skipped": progress.skipped,
                "completed_at": progress.completed_at.isoformat()
                if progress.completed_at
                else None,
            }

        with open(self.progress_file, "w") as f:
            json.dump(data, f, indent=2)

    def is_new_user(self, user_id: str) -> bool:
        """Check if user is new (no onboarding started)."""
        return user_id not in self._user_progress

    def start_onboarding(self, user_id: str) -> UserProgress:
        """Start onboarding for a user."""
        if user_id in self._user_progress:
            return self._user_progress[user_id]

        progress = UserProgress(
            user_id=user_id,
            started_at=datetime.now(),
        )
        self._user_progress[user_id] = progress
        self._save_progress()

        logger.info(f"Started onboarding for user {user_id}")
        return progress

    def skip_tutorial(self, user_id: str):
        """Skip tutorial for a user."""
        if user_id not in self._user_progress:
            self.start_onboarding(user_id)

        progress = self._user_progress[user_id]
        progress.skipped = True
        progress.completed_at = datetime.now()
        self._save_progress()

        logger.info(f"User {user_id} skipped tutorial")

    def restart_tutorial(self, user_id: str) -> UserProgress:
        """Restart tutorial for a user."""
        progress = UserProgress(
            user_id=user_id,
            started_at=datetime.now(),
        )
        self._user_progress[user_id] = progress
        self._save_progress()

        logger.info(f"Restarted tutorial for user {user_id}")
        return progress

    def complete_step(self, user_id: str, step: TutorialStep):
        """Mark a tutorial step as completed."""
        if user_id not in self._user_progress:
            self.start_onboarding(user_id)

        progress = self._user_progress[user_id]

        if step.value not in progress.completed_steps:
            progress.completed_steps.append(step.value)

        # Move to next step
        steps = list(TutorialStep)
        current_index = steps.index(step)

        if current_index < len(steps) - 1:
            progress.current_step = steps[current_index + 1]
        else:
            # Tutorial completed
            progress.completed_at = datetime.now()

        self._save_progress()

    def get_progress(self, user_id: str) -> UserProgress | None:
        """Get user's onboarding progress."""
        return self._user_progress.get(user_id)

    async def send_welcome_message(
        self, user: discord.User, channel: discord.TextChannel
    ):
        """Send welcome message to a new user."""
        embed = Embed(
            title="🎉 Welcome to OpenClaw!",
            description=(
                "I'm your autonomous AI assistant for home automation, "
                "system management, and productivity!\n\n"
                "Let me show you around with a quick tutorial."
            ),
            color=0x667EEA,
        )

        embed.add_field(
            name="What I Can Do",
            value=(
                "🤖 Natural language AI conversations\n"
                "📅 Schedule tasks and reminders\n"
                "🌐 Browse web and analyze content\n"
                "📊 Monitor systems and services\n"
                "📧 Send emails and notifications\n"
                "🔧 Manage Docker containers and NAS\n"
                "...and much more!"
            ),
            inline=False,
        )

        embed.add_field(
            name="Getting Started",
            value=(
                "Use `/tutorial start` to begin the interactive tutorial\n"
                "Or use `/help` to see all available commands\n"
                "Or skip with `/tutorial skip`"
            ),
            inline=False,
        )

        embed.set_footer(text="Type /tutorial start to begin!")

        await channel.send(embed=embed)

        # Start onboarding
        self.start_onboarding(str(user.id))

    async def send_step_message(
        self, user: discord.User, channel: discord.TextChannel, step: TutorialStep
    ) -> Embed:
        """Send tutorial step message."""
        step_content = self._get_step_content(step)

        embed = Embed(
            title=f"📚 Tutorial: {step_content['title']}",
            description=step_content["description"],
            color=0x667EEA,
        )

        if "example" in step_content:
            embed.add_field(
                name="Try It Out",
                value=step_content["example"],
                inline=False,
            )

        if "tips" in step_content:
            embed.add_field(
                name="💡 Tips",
                value=step_content["tips"],
                inline=False,
            )

        # Progress indicator
        steps = list(TutorialStep)
        current_index = steps.index(step) + 1
        total_steps = len(steps)

        embed.set_footer(
            text=f"Step {current_index}/{total_steps} • Type 'next' to continue or '/tutorial skip' to exit"
        )

        await channel.send(embed=embed)
        return embed

    def _get_step_content(self, step: TutorialStep) -> dict[str, str]:
        """Get content for a tutorial step."""
        content_map = {
            TutorialStep.WELCOME: {
                "title": "Welcome & Overview",
                "description": (
                    "OpenClaw is your AI-powered assistant that lives in Discord.\n\n"
                    "I can help you with:\n"
                    "• Answering questions using AI\n"
                    "• Automating tasks and workflows\n"
                    "• Monitoring systems and services\n"
                    "• Managing your home automation\n\n"
                    "Let's explore the basics!"
                ),
            },
            TutorialStep.BASIC_COMMANDS: {
                "title": "Basic Commands",
                "description": (
                    "Here are the most important commands to get started:"
                ),
                "example": (
                    "`/ask question:What is the weather?` - Ask me anything\n"
                    "`/help` - See all available commands\n"
                    "`/analyze-file` - Analyze documents, images, or PDFs\n"
                    "`/browse url:https://example.com` - Browse and summarize web pages"
                ),
                "tips": (
                    "💡 I support conversations! Ask follow-up questions.\n"
                    "💡 I can process images, PDFs, and text files.\n"
                    "💡 Use natural language - no need for exact syntax!"
                ),
            },
            TutorialStep.SCHEDULED_TASKS: {
                "title": "Scheduled Tasks & Reminders",
                "description": (
                    "Automate recurring tasks with the scheduler:"
                ),
                "example": (
                    "`/schedule-task` - Create a new scheduled task\n"
                    "`/list-tasks` - View all scheduled tasks\n"
                    "`/set-reminder` - Set a one-time reminder"
                ),
                "tips": (
                    "💡 Use cron syntax for complex schedules\n"
                    "💡 Tasks can run AI prompts, send emails, and more\n"
                    "💡 Perfect for daily digests and weekly reports"
                ),
            },
            TutorialStep.API_INTEGRATIONS: {
                "title": "API Integrations",
                "description": (
                    "I integrate with many external services:"
                ),
                "example": (
                    "🌤️ Weather forecasts\n"
                    "📧 Email sending (Gmail, SMTP)\n"
                    "🐳 Docker container management\n"
                    "💾 NAS file operations\n"
                    "📱 SMS notifications\n"
                    "🎬 Media server control (Overseerr)"
                ),
                "tips": (
                    "💡 Configure API keys in your .env file\n"
                    "💡 Use `/api-status` to check integration health\n"
                    "💡 See API_SETUP_COMPLETE.md for setup guides"
                ),
            },
            TutorialStep.DASHBOARD_ACCESS: {
                "title": "Web Dashboard",
                "description": (
                    "Access the web dashboard for advanced features:"
                ),
                "example": (
                    "📊 Real-time metrics and analytics\n"
                    "📝 Conversation history browser\n"
                    "⚙️ Configuration management\n"
                    "🔍 Search across all conversations\n\n"
                    "Access at: http://localhost:8080/dashboard"
                ),
                "tips": (
                    "💡 Dashboard updates in real-time\n"
                    "💡 Export conversation history as markdown\n"
                    "💡 Monitor system health and performance"
                ),
            },
            TutorialStep.ADVANCED_FEATURES: {
                "title": "Advanced Features",
                "description": (
                    "Power user features for automation:"
                ),
                "example": (
                    "🧠 Long-term memory with semantic search\n"
                    "📊 Goal and habit tracking\n"
                    "💰 Expense tracking and budgets\n"
                    "🎯 Decision workflows (if-this-then-that)\n"
                    "🚨 Incident management and alerts\n"
                    "🔄 Agent loops for autonomous tasks"
                ),
                "tips": (
                    "💡 Use `/fact` to teach me about your preferences\n"
                    "💡 Set up agent loops for complex automations\n"
                    "💡 Configure workflows in decision_workflows.py"
                ),
            },
            TutorialStep.COMMUNITY_RESOURCES: {
                "title": "Community & Resources",
                "description": (
                    "You've completed the tutorial! 🎉\n\n"
                    "Here are some resources to continue learning:"
                ),
                "example": (
                    "📖 README.md - Full documentation\n"
                    "🎯 IMPLEMENTATION_SUMMARY.md - Feature guide\n"
                    "🔧 API_SETUP_COMPLETE.md - API integration guides\n"
                    "❓ /help - Interactive command reference\n"
                    "💬 Ask me anything - I'm here to help!"
                ),
                "tips": (
                    "💡 Explore the examples/ directory for inspiration\n"
                    "💡 Check out the skills/ directory for custom skills\n"
                    "💡 Join our community for tips and tricks"
                ),
            },
        }

        return content_map.get(step, {})


# Global onboarding manager
_manager_instance: OnboardingManager | None = None


def get_onboarding_manager() -> OnboardingManager:
    """Get or create the global onboarding manager."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = OnboardingManager()
    return _manager_instance
