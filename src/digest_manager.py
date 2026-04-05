"""
Personalized User Digest Manager — Store preferences & generate custom digests.

Manages per-user digest preferences and generates personalized content from
news, sports, stocks, and other sources based on user interests.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils import atomic_write

log = logging.getLogger("openclaw.digest_manager")

# ---------------------------------------------------------------------------
# Schema & paths
# ---------------------------------------------------------------------------

DIGEST_PREFS_DIR = Path("/memory/preferences/digests")

def _ensure_digest_dir() -> None:
    """Ensure digest preferences directory exists."""
    try:
        DIGEST_PREFS_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        # Directory may not be writable in test environments
        pass


DEFAULT_DIGEST_PREFERENCES: dict[str, Any] = {
    "user_id": "",
    "topics": [],
    "stocks": [],
    "teams": [],
    "keywords": [],
    "exclude": [],
    "schedule": "daily",  # daily, weekly, custom, manual
    "delivery_time": "08:00",
    "delivery_day": "Monday",  # for weekly
    "timezone": "UTC",
    "format": "concise",  # concise, detailed, bullets
    "max_items": 10,
    "channels": ["dm"],  # dm, channel_id
    "enabled": True,
    "created_at": "",
    "updated_at": "",
}


# ---------------------------------------------------------------------------
# DigestManager class
# ---------------------------------------------------------------------------


class DigestManager:
    """Manages user digest preferences and generation."""

    def __init__(self) -> None:
        """Initialize the digest manager."""
        pass

    @staticmethod
    def _get_user_pref_path(user_id: str) -> Path:
        """Get the preference file path for a user."""
        safe_id = re.sub(r"[^\w\-]", "_", str(user_id))
        return DIGEST_PREFS_DIR / f"{safe_id}.json"

    def save_preferences(self, user_id: str, preferences: dict[str, Any]) -> None:
        """Save digest preferences for a user.

        Args:
            user_id: Discord user ID
            preferences: User preference dictionary
        """
        if not user_id:
            raise ValueError("user_id is required")

        _ensure_digest_dir()
        path = self._get_user_pref_path(user_id)

        # Merge with defaults
        prefs = {**DEFAULT_DIGEST_PREFERENCES, **preferences}
        prefs["user_id"] = str(user_id)
        prefs["updated_at"] = datetime.now(timezone.utc).isoformat()

        if not prefs.get("created_at"):
            prefs["created_at"] = prefs["updated_at"]

        atomic_write(path, json.dumps(prefs, indent=2))
        log.info("Digest preferences saved for user %s", user_id)

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        """Get digest preferences for a user.

        Args:
            user_id: Discord user ID

        Returns:
            User preferences dict, or defaults if not found
        """
        if not user_id:
            return {**DEFAULT_DIGEST_PREFERENCES}

        path = self._get_user_pref_path(user_id)

        try:
            prefs = json.loads(path.read_text())
            # Ensure all default keys exist
            return {**DEFAULT_DIGEST_PREFERENCES, **prefs}
        except FileNotFoundError:
            log.debug("No digest preferences found for user %s", user_id)
            return {**DEFAULT_DIGEST_PREFERENCES, "user_id": str(user_id)}
        except Exception as exc:
            log.warning("Failed to load digest preferences for %s: %s", user_id, exc)
            return {**DEFAULT_DIGEST_PREFERENCES, "user_id": str(user_id)}

    def update_preference(self, user_id: str, key: str, value: Any) -> None:
        """Update a single preference field for a user.

        Args:
            user_id: Discord user ID
            key: Preference key to update
            value: New value
        """
        prefs = self.get_preferences(user_id)

        if key not in DEFAULT_DIGEST_PREFERENCES:
            log.warning("Unknown digest preference key: %s", key)
            # Allow it anyway for flexibility

        prefs[key] = value
        self.save_preferences(user_id, prefs)
        log.info("Updated digest preference %s for user %s", key, user_id)

    def add_to_list(self, user_id: str, key: str, value: str) -> None:
        """Add an item to a list preference (topics, stocks, teams, keywords).

        Args:
            user_id: Discord user ID
            key: List preference key (topics, stocks, teams, keywords, exclude)
            value: Item to add
        """
        prefs = self.get_preferences(user_id)

        if key not in {"topics", "stocks", "teams", "keywords", "exclude"}:
            raise ValueError(f"Invalid list preference key: {key}")

        if not isinstance(prefs[key], list):
            prefs[key] = []

        value_clean = value.strip()
        if value_clean and value_clean not in prefs[key]:
            prefs[key].append(value_clean)
            self.save_preferences(user_id, prefs)
            log.info("Added '%s' to %s for user %s", value_clean, key, user_id)

    def remove_from_list(self, user_id: str, key: str, value: str) -> None:
        """Remove an item from a list preference.

        Args:
            user_id: Discord user ID
            key: List preference key
            value: Item to remove
        """
        prefs = self.get_preferences(user_id)

        if key not in {"topics", "stocks", "teams", "keywords", "exclude"}:
            raise ValueError(f"Invalid list preference key: {key}")

        if isinstance(prefs[key], list) and value in prefs[key]:
            prefs[key].remove(value)
            self.save_preferences(user_id, prefs)
            log.info("Removed '%s' from %s for user %s", value, key, user_id)

    def list_all_users(self) -> list[str]:
        """Get list of all users with digest preferences.

        Returns:
            List of user IDs
        """
        try:
            return [
                p.stem for p in DIGEST_PREFS_DIR.glob("*.json")
                if p.is_file()
            ]
        except Exception as exc:
            log.error("Failed to list digest users: %s", exc)
            return []

    async def generate_digest(
        self,
        user_id: str,
        preview: bool = False,
    ) -> str:
        """Generate a personalized digest for a user.

        Args:
            user_id: Discord user ID
            preview: If True, adds preview header

        Returns:
            Formatted digest markdown
        """
        prefs = self.get_preferences(user_id)

        if not prefs.get("enabled") and not preview:
            return "⚠️ Your digest is currently disabled. Use !digest_enable to turn it back on."

        # Check if user has any preferences set
        has_prefs = any([
            prefs.get("topics"),
            prefs.get("stocks"),
            prefs.get("teams"),
            prefs.get("keywords"),
        ])

        if not has_prefs:
            return (
                "⚠️ You haven't configured any digest preferences yet!\n\n"
                "Use `!configure_digest` to set up your personalized digest, or try:\n"
                "- `!digest_topics add AI` - Add a topic\n"
                "- `!digest_stocks add TSLA` - Add a stock\n"
                "- `!digest_teams add Lakers` - Add a team"
            )

        sections: list[str] = []
        sources_count = 0

        # Header
        now = datetime.now(timezone.utc)
        schedule = prefs.get("schedule", "daily").upper()
        header = "🔍 DIGEST PREVIEW" if preview else f"📰 YOUR {schedule} DIGEST"
        sections.append(f"{header} - {now.strftime('%B %d, %Y')}\n")

        # Generate news section if topics/keywords configured
        if prefs.get("topics") or prefs.get("keywords"):
            news_section = await self._generate_news_section(prefs)
            if news_section:
                sections.append(news_section)
                sources_count += 1

        # Generate stocks section if stocks configured
        if prefs.get("stocks"):
            stocks_section = await self._generate_stocks_section(prefs)
            if stocks_section:
                sections.append(stocks_section)
                sources_count += 1

        # Generate sports section if teams configured
        if prefs.get("teams"):
            sports_section = await self._generate_sports_section(prefs)
            if sports_section:
                sections.append(sports_section)
                sources_count += 1

        # Footer
        sections.append("\n---")
        sections.append(
            f"Generated from {sources_count} sources filtered by your preferences\n"
            f"Configure: `!configure_digest` | Preview: `!digest_preview`"
        )

        if preview:
            sections.append("\n*This is a preview. Your actual digest will be delivered at "
                          f"{prefs.get('delivery_time', '08:00')} {prefs.get('timezone', 'UTC')}*")

        return "\n".join(sections)

    async def _generate_news_section(self, prefs: dict[str, Any]) -> str:
        """Generate news section based on topics and keywords.

        Args:
            prefs: User digest preferences

        Returns:
            Formatted news section markdown
        """
        topics = prefs.get("topics", [])
        keywords = prefs.get("keywords", [])
        exclude = prefs.get("exclude", [])
        max_items = prefs.get("max_items", 10)

        if not topics and not keywords:
            return ""

        try:
            from skills import news_skills

            # Collect articles from topics
            articles: list[dict[str, Any]] = []

            # Search for each topic/keyword
            search_terms = list(set(topics + keywords))[:5]  # Limit searches

            for term in search_terms:
                try:
                    result = await asyncio.wait_for(
                        news_skills.search_news(term, max_results=5),
                        timeout=15
                    )

                    # Parse articles from result (assuming it's markdown or structured)
                    # This is a simplified parser - adjust based on actual return format
                    if result and not result.startswith("❌"):
                        articles.append({
                            "title": term,
                            "content": result[:200],
                            "relevance": self._calculate_relevance(result, topics, keywords),
                        })
                except asyncio.TimeoutError:
                    log.warning("News search timed out for: %s", term)
                except Exception as exc:
                    log.warning("News search failed for %s: %s", term, exc)

            if not articles:
                return ""

            # Filter out excluded topics
            if exclude:
                articles = [
                    a for a in articles
                    if not any(ex.lower() in a["content"].lower() for ex in exclude)
                ]

            # Sort by relevance and take top items
            articles.sort(key=lambda x: x.get("relevance", 0), reverse=True)
            articles = articles[:max_items]

            # Format section
            section_parts = [f"\n🤖 NEWS & TOPICS ({len(articles)} articles)"]
            for article in articles:
                section_parts.append(f"• {article['title']}: {article['content'][:150]}...")

            return "\n".join(section_parts) + "\n"

        except ImportError:
            log.warning("News skills not available")
            return ""
        except Exception as exc:
            log.error("Failed to generate news section: %s", exc)
            return ""

    async def _generate_stocks_section(self, prefs: dict[str, Any]) -> str:
        """Generate stocks section based on watchlist.

        Args:
            prefs: User digest preferences

        Returns:
            Formatted stocks section markdown
        """
        stocks = prefs.get("stocks", [])

        if not stocks:
            return ""

        try:
            from skills import finance_skills

            stock_data: list[str] = []

            for symbol in stocks[:10]:  # Limit to 10 stocks
                try:
                    result = await asyncio.wait_for(
                        finance_skills.get_stock_quote(symbol),
                        timeout=10
                    )

                    if result and not result.startswith("❌"):
                        stock_data.append(f"• {result}")
                except asyncio.TimeoutError:
                    log.warning("Stock quote timed out for: %s", symbol)
                    stock_data.append(f"• {symbol}: (timeout)")
                except Exception as exc:
                    log.warning("Stock quote failed for %s: %s", symbol, exc)

            if not stock_data:
                return ""

            section_parts = [f"\n📈 YOUR STOCKS ({len(stock_data)} symbols)"]
            section_parts.extend(stock_data)

            return "\n".join(section_parts) + "\n"

        except ImportError:
            log.warning("Finance skills not available")
            return ""
        except Exception as exc:
            log.error("Failed to generate stocks section: %s", exc)
            return ""

    async def _generate_sports_section(self, prefs: dict[str, Any]) -> str:
        """Generate sports section based on teams.

        Args:
            prefs: User digest preferences

        Returns:
            Formatted sports section markdown
        """
        teams = prefs.get("teams", [])

        if not teams:
            return ""

        try:
            from skills import sports_skills

            team_updates: list[str] = []

            for team in teams[:5]:  # Limit to 5 teams
                try:
                    result = await asyncio.wait_for(
                        sports_skills.get_team_schedule(team, days=1),
                        timeout=10
                    )

                    if result and not result.startswith("❌"):
                        team_updates.append(f"• {team}: {result[:200]}")
                except asyncio.TimeoutError:
                    log.warning("Team schedule timed out for: %s", team)
                except Exception as exc:
                    log.warning("Team schedule failed for %s: %s", team, exc)

            if not team_updates:
                return ""

            section_parts = [f"\n🏀 SPORTS UPDATES ({len(team_updates)} teams)"]
            section_parts.extend(team_updates)

            return "\n".join(section_parts) + "\n"

        except ImportError:
            log.warning("Sports skills not available")
            return ""
        except Exception as exc:
            log.error("Failed to generate sports section: %s", exc)
            return ""

    @staticmethod
    def _calculate_relevance(
        content: str,
        topics: list[str],
        keywords: list[str],
    ) -> float:
        """Calculate relevance score for content based on user preferences.

        Scoring:
        - Exact topic match in title: 1.0
        - Keyword match in content: 0.7
        - Topic mentioned in content: 0.5
        - Base score: 0.3
        """
        content_lower = content.lower()
        score = 0.3  # Base score

        # Check for exact topic matches (worth more)
        for topic in topics:
            if topic.lower() in content_lower[:100]:  # Check first 100 chars (likely title)
                score += 1.0
                break

        # Check for keyword matches
        for keyword in keywords:
            if keyword.lower() in content_lower:
                score += 0.7

        # Check for topic mentions anywhere
        for topic in topics:
            if topic.lower() in content_lower:
                score += 0.5

        return min(score, 3.0)  # Cap at 3.0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_digest_manager: DigestManager | None = None


def get_digest_manager() -> DigestManager:
    """Get the global DigestManager instance."""
    global _digest_manager
    if _digest_manager is None:
        _digest_manager = DigestManager()
    return _digest_manager
