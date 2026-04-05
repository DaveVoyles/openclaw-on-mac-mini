"""
Digest Skills — LLM-callable functions for personalized user digests.

Provides skills for configuring and generating personalized daily/weekly digests
tailored to individual user interests.
"""

import logging
from typing import Any

from runtime_state import get_current_user_id

log = logging.getLogger("openclaw.digest_skills")


async def configure_digest(preferences_dict: dict[str, Any]) -> str:
    """Configure personalized digest preferences for the calling user.

    Sets up topics, stocks, teams, keywords, schedule, and delivery options.

    Args:
        preferences_dict: Dictionary of preferences to set. Supported keys:
            - topics: List of topics to follow (e.g., ["AI", "space exploration"])
            - stocks: List of stock tickers to watch (e.g., ["TSLA", "NVDA"])
            - teams: List of sports teams to follow (e.g., ["Lakers", "Patriots"])
            - keywords: List of keywords to match (e.g., ["OpenAI", "SpaceX"])
            - exclude: List of topics to exclude (e.g., ["celebrity gossip"])
            - schedule: "daily", "weekly", "custom", or "manual"
            - delivery_time: Time in HH:MM format (e.g., "08:00")
            - delivery_day: Day for weekly digests (e.g., "Monday")
            - timezone: User timezone (e.g., "America/New_York")
            - format: "concise", "detailed", or "bullets"
            - max_items: Max items per section (default 10)
            - channels: List of delivery targets (e.g., ["dm", "123456789"])
            - enabled: Boolean to enable/disable digest

    Returns:
        Confirmation message with configured preferences

    Examples:
        >>> await configure_digest({
        ...     "topics": ["AI", "robotics"],
        ...     "stocks": ["TSLA", "NVDA"],
        ...     "schedule": "daily",
        ...     "delivery_time": "08:00"
        ... })
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        manager.save_preferences(user_id, preferences_dict)

        prefs = manager.get_preferences(user_id)

        # Format confirmation
        summary_parts = ["✅ Digest configured successfully!\n"]

        if prefs.get("topics"):
            summary_parts.append(f"📚 Topics ({len(prefs['topics'])}): {', '.join(prefs['topics'][:5])}")
        if prefs.get("stocks"):
            summary_parts.append(f"📈 Stocks ({len(prefs['stocks'])}): {', '.join(prefs['stocks'][:5])}")
        if prefs.get("teams"):
            summary_parts.append(f"🏀 Teams ({len(prefs['teams'])}): {', '.join(prefs['teams'][:5])}")
        if prefs.get("keywords"):
            summary_parts.append(f"🔍 Keywords ({len(prefs['keywords'])}): {', '.join(prefs['keywords'][:5])}")
        if prefs.get("exclude"):
            summary_parts.append(f"🚫 Excluding ({len(prefs['exclude'])}): {', '.join(prefs['exclude'][:3])}")

        summary_parts.append(
            f"\n⏰ Schedule: {prefs.get('schedule', 'daily')} at {prefs.get('delivery_time', '08:00')} "
            f"{prefs.get('timezone', 'UTC')}"
        )
        summary_parts.append(f"📝 Format: {prefs.get('format', 'concise')}")
        summary_parts.append(f"📊 Max items per section: {prefs.get('max_items', 10)}")

        summary_parts.append("\n💡 Test your digest with `!my_digest` or `!digest_preview`")

        return "\n".join(summary_parts)

    except Exception as exc:
        log.error("Failed to configure digest: %s", exc)
        return f"❌ Failed to configure digest: {exc}"


async def get_my_digest() -> str:
    """Generate and return a personalized digest for the calling user.

    Creates a digest based on the user's configured preferences including
    topics, stocks, teams, and keywords. Returns immediately with current data.

    Returns:
        Formatted personalized digest in markdown

    Examples:
        >>> await get_my_digest()
        # Returns digest with news, stocks, sports based on user preferences
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        digest = await manager.generate_digest(user_id, preview=False)

        return digest

    except Exception as exc:
        log.error("Failed to generate digest: %s", exc)
        return f"❌ Failed to generate digest: {exc}"


async def update_digest_preferences(key: str, value: Any) -> str:
    """Update a specific digest preference for the calling user.

    Allows updating individual preference fields without replacing all settings.

    Args:
        key: Preference key to update (e.g., "schedule", "delivery_time", "format")
        value: New value for the preference

    Returns:
        Confirmation message

    Examples:
        >>> await update_digest_preferences("schedule", "weekly")
        >>> await update_digest_preferences("delivery_time", "09:00")
        >>> await update_digest_preferences("format", "detailed")
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        manager.update_preference(user_id, key, value)

        return f"✅ Updated {key} to: {value}\n\nTest with `!digest_preview`"

    except Exception as exc:
        log.error("Failed to update digest preference: %s", exc)
        return f"❌ Failed to update preference: {exc}"


async def preview_digest() -> str:
    """Preview what the next scheduled digest will contain.

    Generates a digest with current data and user preferences, marked as preview.
    Use this to test your configuration before the scheduled delivery.

    Returns:
        Preview digest in markdown with preview header

    Examples:
        >>> await preview_digest()
        # Returns preview of digest with current data
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        digest = await manager.generate_digest(user_id, preview=True)

        return digest

    except Exception as exc:
        log.error("Failed to preview digest: %s", exc)
        return f"❌ Failed to preview digest: {exc}"


async def add_digest_topic(topic: str) -> str:
    """Add a topic to the user's digest preferences.

    Args:
        topic: Topic to add (e.g., "AI", "space exploration", "electric vehicles")

    Returns:
        Confirmation message

    Examples:
        >>> await add_digest_topic("artificial intelligence")
        >>> await add_digest_topic("climate change")
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        manager.add_to_list(user_id, "topics", topic)

        prefs = manager.get_preferences(user_id)
        topics_count = len(prefs.get("topics", []))

        return f"✅ Added topic: **{topic}**\n\nYou're now following {topics_count} topic(s)"

    except Exception as exc:
        log.error("Failed to add digest topic: %s", exc)
        return f"❌ Failed to add topic: {exc}"


async def add_digest_stock(ticker: str) -> str:
    """Add a stock ticker to the user's digest watchlist.

    Args:
        ticker: Stock ticker symbol (e.g., "TSLA", "NVDA", "AAPL")

    Returns:
        Confirmation message

    Examples:
        >>> await add_digest_stock("TSLA")
        >>> await add_digest_stock("NVDA")
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        ticker_upper = ticker.strip().upper()

        manager = get_digest_manager()
        manager.add_to_list(user_id, "stocks", ticker_upper)

        prefs = manager.get_preferences(user_id)
        stocks_count = len(prefs.get("stocks", []))

        return f"✅ Added stock: **{ticker_upper}**\n\nYou're now watching {stocks_count} stock(s)"

    except Exception as exc:
        log.error("Failed to add digest stock: %s", exc)
        return f"❌ Failed to add stock: {exc}"


async def add_digest_team(team: str) -> str:
    """Add a sports team to the user's digest preferences.

    Args:
        team: Team name (e.g., "Lakers", "Patriots", "Yankees")

    Returns:
        Confirmation message

    Examples:
        >>> await add_digest_team("Lakers")
        >>> await add_digest_team("New England Patriots")
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        manager.add_to_list(user_id, "teams", team)

        prefs = manager.get_preferences(user_id)
        teams_count = len(prefs.get("teams", []))

        return f"✅ Added team: **{team}**\n\nYou're now following {teams_count} team(s)"

    except Exception as exc:
        log.error("Failed to add digest team: %s", exc)
        return f"❌ Failed to add team: {exc}"


async def remove_digest_topic(topic: str) -> str:
    """Remove a topic from the user's digest preferences.

    Args:
        topic: Topic to remove

    Returns:
        Confirmation message
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        manager.remove_from_list(user_id, "topics", topic)

        return f"✅ Removed topic: **{topic}**"

    except Exception as exc:
        log.error("Failed to remove digest topic: %s", exc)
        return f"❌ Failed to remove topic: {exc}"


async def remove_digest_stock(ticker: str) -> str:
    """Remove a stock ticker from the user's digest watchlist.

    Args:
        ticker: Stock ticker to remove

    Returns:
        Confirmation message
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        ticker_upper = ticker.strip().upper()

        manager = get_digest_manager()
        manager.remove_from_list(user_id, "stocks", ticker_upper)

        return f"✅ Removed stock: **{ticker_upper}**"

    except Exception as exc:
        log.error("Failed to remove digest stock: %s", exc)
        return f"❌ Failed to remove stock: {exc}"


async def remove_digest_team(team: str) -> str:
    """Remove a sports team from the user's digest preferences.

    Args:
        team: Team name to remove

    Returns:
        Confirmation message
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        manager.remove_from_list(user_id, "teams", team)

        return f"✅ Removed team: **{team}**"

    except Exception as exc:
        log.error("Failed to remove digest team: %s", exc)
        return f"❌ Failed to remove team: {exc}"


async def get_digest_config() -> str:
    """Get the current digest configuration for the calling user.

    Returns:
        Formatted display of all current digest preferences

    Examples:
        >>> await get_digest_config()
        # Returns current topics, stocks, teams, schedule, etc.
    """
    try:
        from digest_manager import get_digest_manager

        user_id = get_current_user_id()
        if not user_id:
            return "❌ Could not determine current user ID"

        manager = get_digest_manager()
        prefs = manager.get_preferences(user_id)

        lines = ["📋 **Your Digest Configuration**\n"]

        # Topics
        topics = prefs.get("topics", [])
        if topics:
            lines.append(f"📚 **Topics ({len(topics)}):** {', '.join(topics)}")
        else:
            lines.append("📚 **Topics:** None configured")

        # Stocks
        stocks = prefs.get("stocks", [])
        if stocks:
            lines.append(f"📈 **Stocks ({len(stocks)}):** {', '.join(stocks)}")
        else:
            lines.append("📈 **Stocks:** None configured")

        # Teams
        teams = prefs.get("teams", [])
        if teams:
            lines.append(f"🏀 **Teams ({len(teams)}):** {', '.join(teams)}")
        else:
            lines.append("🏀 **Teams:** None configured")

        # Keywords
        keywords = prefs.get("keywords", [])
        if keywords:
            lines.append(f"🔍 **Keywords ({len(keywords)}):** {', '.join(keywords)}")

        # Exclusions
        exclude = prefs.get("exclude", [])
        if exclude:
            lines.append(f"🚫 **Excluding ({len(exclude)}):** {', '.join(exclude)}")

        # Schedule
        lines.append(
            f"\n⏰ **Schedule:** {prefs.get('schedule', 'daily')} at "
            f"{prefs.get('delivery_time', '08:00')} {prefs.get('timezone', 'UTC')}"
        )

        if prefs.get("schedule") == "weekly":
            lines.append(f"📅 **Delivery Day:** {prefs.get('delivery_day', 'Monday')}")

        # Format options
        lines.append(f"📝 **Format:** {prefs.get('format', 'concise')}")
        lines.append(f"📊 **Max items:** {prefs.get('max_items', 10)} per section")

        # Status
        enabled = prefs.get("enabled", True)
        status = "✅ Enabled" if enabled else "⏸️ Disabled"
        lines.append(f"\n**Status:** {status}")

        # Help
        lines.append("\n💡 **Quick actions:**")
        lines.append("• `!digest_topics add <topic>` - Add a topic")
        lines.append("• `!digest_stocks add <ticker>` - Add a stock")
        lines.append("• `!my_digest` - Get digest now")
        lines.append("• `!digest_preview` - Preview next digest")

        return "\n".join(lines)

    except Exception as exc:
        log.error("Failed to get digest config: %s", exc)
        return f"❌ Failed to get configuration: {exc}"


# ---------------------------------------------------------------------------
# Skill declarations
# ---------------------------------------------------------------------------

DIGEST_SKILLS = {
    "configure_digest": configure_digest,
    "get_my_digest": get_my_digest,
    "update_digest_preferences": update_digest_preferences,
    "preview_digest": preview_digest,
    "add_digest_topic": add_digest_topic,
    "add_digest_stock": add_digest_stock,
    "add_digest_team": add_digest_team,
    "remove_digest_topic": remove_digest_topic,
    "remove_digest_stock": remove_digest_stock,
    "remove_digest_team": remove_digest_team,
    "get_digest_config": get_digest_config,
}
