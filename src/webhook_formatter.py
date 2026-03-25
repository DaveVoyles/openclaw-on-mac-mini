"""Webhook payload formatters for arr/plex/qbittorrent notifications."""

import discord


def format_arr(payload: dict) -> tuple[str, str, discord.Color]:
    """Format Sonarr / Radarr / Lidarr webhook → (title, description, color)."""
    source = "Sonarr"
    if payload.get("movie"):
        source = "Radarr"
    elif payload.get("artist"):
        source = "Lidarr"

    event = payload.get("eventType", "Event")
    series = payload.get("series", {})
    movie = payload.get("movie", {})
    name = (
        series.get("title")
        or movie.get("title")
        or payload.get("artist", {}).get("name", "Unknown")
    )
    ep = payload.get("episodes", [{}])[0] if payload.get("episodes") else {}
    ep_title = ep.get("title", "")
    ep_num = (
        f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
        if ep
        else ""
    )

    lines: list[str] = []
    lines.append(f"**Event**: {event}")
    lines.append(
        f"**Title**: {name}" + (f" — {ep_num} {ep_title}" if ep_title else "")
    )
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


def format_sonarr(payload: dict) -> tuple[str, str, discord.Color]:
    """Format Sonarr webhook → (title, description, color)."""
    return format_arr(payload)


def format_radarr(payload: dict) -> tuple[str, str, discord.Color]:
    """Format Radarr webhook → (title, description, color)."""
    return format_arr(payload)


def format_lidarr(payload: dict) -> tuple[str, str, discord.Color]:
    """Format Lidarr webhook → (title, description, color)."""
    return format_arr(payload)


def format_plex(payload: dict) -> tuple[str, str, discord.Color]:
    """Format Plex webhook → (title, description, color)."""
    event = payload.get("event", payload.get("type", "Event"))
    meta = payload.get("Metadata", {})
    p_title = meta.get("title", "Unknown")
    p_type = meta.get("type", "")
    user = payload.get("Account", {}).get("title", "")

    lines: list[str] = []
    lines.append(f"**Event**: {event}")
    lines.append(
        f"**{'Episode' if p_type == 'episode' else 'Title'}**: {p_title}"
    )
    if user:
        lines.append(f"**User**: {user}")

    title = f"🔔 Webhook: Plex"
    color = discord.Color.blurple()
    if "play" in event.lower():
        color = discord.Color.green()
        title = "▶️ Plex: Now Playing"

    description = "\n".join(lines) or "*(no details)*"
    return title, description, color


def format_qbittorrent(payload: dict) -> tuple[str, str, discord.Color]:
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


def format_generic(source: str, payload: dict) -> tuple[str, str, discord.Color]:
    """Generic fallback — show top-level keys."""
    lines: list[str] = []
    for k, v in list(payload.items())[:8]:
        if isinstance(v, (str, int, float, bool)):
            lines.append(f"**{k}**: {v}")

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
