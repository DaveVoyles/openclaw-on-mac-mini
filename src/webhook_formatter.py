"""Webhook payload formatters kept for test and import compatibility."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

try:
    import discord
except ModuleNotFoundError:  # pragma: no cover - lightweight test environments
    class _Color:
        @staticmethod
        def blurple() -> int:
            return 0x5865F2

        @staticmethod
        def yellow() -> int:
            return 0xFEE75C

        @staticmethod
        def green() -> int:
            return 0x57F287

        @staticmethod
        def red() -> int:
            return 0xED4245

    discord = SimpleNamespace(Color=_Color)


ColorValue = Any


def format_arr(payload: dict) -> tuple[str, str, ColorValue]:
    """Format Sonarr / Radarr / Lidarr webhook → (title, description, color)."""
    source = "Sonarr"
    if payload.get("movie"):
        source = "Radarr"
    elif payload.get("artist"):
        source = "Lidarr"

    event = payload.get("eventType", "Event")
    series = payload.get("series", {})
    movie = payload.get("movie", {})
    name = series.get("title") or movie.get("title") or payload.get("artist", {}).get("name", "Unknown")
    ep = payload.get("episodes", [{}])[0] if payload.get("episodes") else {}
    ep_title = ep.get("title", "")
    ep_num = f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}" if ep else ""

    lines: list[str] = []
    lines.append(f"**Event**: {event}")
    lines.append(f"**Title**: {name}" + (f" — {ep_num} {ep_title}" if ep_title else ""))
    if payload.get("isUpgrade"):
        lines.append("⬆️ Quality upgrade")

    title = f"🔔 Webhook: {source}"
    color = discord.Color.blurple()
    if event == "Grab":
        color = discord.Color.yellow()
    elif event == "Download":
        color = discord.Color.green()
    elif event in ("EpisodeFileDelete", "MovieFileDelete"):
        color = discord.Color.red()
        title = f"🗑️ {source}: File Deleted"

    description = "\n".join(lines) or "*(no details)*"
    return title, description, color


def format_sonarr(payload: dict) -> tuple[str, str, ColorValue]:
    """Format Sonarr webhook → (title, description, color)."""
    event = payload.get("eventType", "")
    if event == "Download":
        series = payload.get("series", {}).get("title", "Unknown")
        ep = payload.get("episodes", [{}])[0] if payload.get("episodes") else {}
        season = ep.get("seasonNumber", 0)
        episode = ep.get("episodeNumber", 0)
        ep_title = ep.get("title", "")
        desc = f"📺 **New episode downloaded**: {series} S{season:02d}E{episode:02d}"
        if ep_title:
            desc += f" — {ep_title}"
        if payload.get("isUpgrade"):
            desc += "\n⬆️ Quality upgrade"
        return "📺 Sonarr: Episode Downloaded", desc, discord.Color.green()
    return format_arr(payload)


def format_radarr(payload: dict) -> tuple[str, str, ColorValue]:
    """Format Radarr webhook → (title, description, color)."""
    event = payload.get("eventType", "")
    if event == "Download":
        movie = payload.get("movie", {})
        title = movie.get("title", "Unknown")
        year = movie.get("year", "")
        desc = f"🎬 **New movie downloaded**: {title}"
        if year:
            desc += f" ({year})"
        if payload.get("isUpgrade"):
            desc += "\n⬆️ Quality upgrade"
        return "🎬 Radarr: Movie Downloaded", desc, discord.Color.green()
    return format_arr(payload)


def format_lidarr(payload: dict) -> tuple[str, str, ColorValue]:
    """Format Lidarr webhook → (title, description, color)."""
    return format_arr(payload)


def format_plex(payload: dict) -> tuple[str, str, ColorValue]:
    """Format Plex webhook → (title, description, color)."""
    event = payload.get("event", payload.get("type", "Event"))
    meta = payload.get("Metadata", {})
    p_title = meta.get("title", "Unknown")
    p_type = meta.get("type", "")
    user = payload.get("Account", {}).get("title", "")

    if "play" in event.lower():
        desc = f"▶️ **Now playing**: {p_title}"
        if user:
            desc += f" by {user}"
        return "▶️ Plex: Now Playing", desc, discord.Color.green()

    lines: list[str] = []
    lines.append(f"**Event**: {event}")
    lines.append(f"**{'Episode' if p_type == 'episode' else 'Title'}**: {p_title}")
    if user:
        lines.append(f"**User**: {user}")

    title = "🔔 Webhook: Plex"
    color = discord.Color.blurple()
    description = "\n".join(lines) or "*(no details)*"
    return title, description, color


def format_qbittorrent(payload: dict) -> tuple[str, str, ColorValue]:
    """Format qBittorrent webhook → (title, description, color)."""
    name = payload.get("name", payload.get("hash", "Unknown"))
    category = payload.get("category", "")

    lines: list[str] = []
    lines.append(f"**Torrent**: {name}")
    if category:
        lines.append(f"**Category**: {category}")

    color = discord.Color.green()
    title = "✅ qBittorrent: Download Complete"
    description = "\n".join(lines) or "*(no details)*"
    return title, description, color


def format_generic(source: str, payload: dict) -> tuple[str, str, ColorValue]:
    """Generic fallback — show top-level keys."""
    lines: list[str] = []
    for key, value in list(payload.items())[:8]:
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"**{key}**: {value}")

    title = f"🔔 Webhook: {source.capitalize()}"
    color = discord.Color.blurple()
    description = "\n".join(lines) or "*(no details)*"
    return title, description, color


FORMATTERS: dict[str, object] = {
    "sonarr": format_sonarr,
    "radarr": format_radarr,
    "lidarr": format_lidarr,
    "plex": format_plex,
    "qbittorrent": format_qbittorrent,
}
