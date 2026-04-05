"""Time and duration utility functions for OpenClaw."""

import re


def parse_duration(duration_str: str) -> int:
    """
    Parse duration string to seconds.

    Supports formats:
    - "5m" -> 300 seconds
    - "2h" -> 7200 seconds
    - "1d" -> 86400 seconds
    - "30s" -> 30 seconds
    - "1h 30m" -> 5400 seconds
    - "2d 3h 15m" -> 183900 seconds

    Args:
        duration_str: Duration string (e.g., "5m", "2h", "1d")

    Returns:
        Duration in seconds

    Raises:
        ValueError: If duration string is invalid

    Examples:
        >>> parse_duration("5m")
        300
        >>> parse_duration("2h")
        7200
        >>> parse_duration("1d 12h")
        129600
    """
    duration_str = duration_str.strip().lower()

    if not duration_str:
        raise ValueError("Duration string cannot be empty")

    # Pattern: number followed by unit (s/m/h/d)
    pattern = r"(\d+)\s*([smhd])"
    matches = re.findall(pattern, duration_str)

    if not matches:
        raise ValueError(f"Invalid duration format: {duration_str}")

    total_seconds = 0
    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }

    for value_str, unit in matches:
        value = int(value_str)
        total_seconds += value * units[unit]

    return total_seconds


def format_duration(seconds: int, precision: int = 2) -> str:
    """
    Format seconds to human-readable duration.

    Args:
        seconds: Duration in seconds
        precision: Number of units to show (default: 2)

    Returns:
        Human-readable duration string

    Examples:
        >>> format_duration(300)
        '5 minutes'
        >>> format_duration(7200)
        '2 hours'
        >>> format_duration(183900)
        '2 days 3 hours'
        >>> format_duration(90)
        '1 minute 30 seconds'
    """
    if seconds == 0:
        return "0 seconds"

    if seconds < 0:
        return f"-{format_duration(-seconds, precision)}"

    units = [
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    ]

    parts = []
    remaining = seconds

    for unit_name, unit_seconds in units:
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            remaining %= unit_seconds

            # Pluralize if needed
            plural = "s" if count != 1 else ""
            parts.append(f"{count} {unit_name}{plural}")

            if len(parts) >= precision:
                break

    return " ".join(parts)


def format_duration_short(seconds: int) -> str:
    """
    Format seconds to short duration string (e.g., "5m", "2h 30m").

    Args:
        seconds: Duration in seconds

    Returns:
        Short duration string

    Examples:
        >>> format_duration_short(300)
        '5m'
        >>> format_duration_short(7200)
        '2h'
        >>> format_duration_short(5400)
        '1h 30m'
    """
    if seconds == 0:
        return "0s"

    if seconds < 0:
        return f"-{format_duration_short(-seconds)}"

    units = [
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
        ("s", 1),
    ]

    parts = []
    remaining = seconds

    for unit_symbol, unit_seconds in units:
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            remaining %= unit_seconds
            parts.append(f"{count}{unit_symbol}")

    return " ".join(parts[:2])  # Show max 2 units


def seconds_until_hour(hour: int, minute: int = 0) -> int:
    """
    Calculate seconds until a specific time today (or tomorrow if past).

    Args:
        hour: Target hour (0-23)
        minute: Target minute (0-59, default: 0)

    Returns:
        Seconds until target time

    Examples:
        >>> # If current time is 14:30 and target is 15:00
        >>> # seconds_until_hour(15, 0)  # Returns 1800 (30 minutes)
    """
    from datetime import datetime, timedelta

    if not 0 <= hour <= 23:
        raise ValueError(f"Hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"Minute must be 0-59, got {minute}")

    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If target time has passed today, use tomorrow
    if target <= now:
        target += timedelta(days=1)

    delta = target - now
    return int(delta.total_seconds())


def relative_time(seconds: float) -> str:
    """
    Convert seconds elapsed into a short human-readable string.

    Args:
        seconds: Seconds elapsed

    Returns:
        Relative time string (e.g., "3h ago", "just now")

    Examples:
        >>> relative_time(30)
        'just now'
        >>> relative_time(300)
        '5m ago'
        >>> relative_time(7200)
        '2h ago'
    """
    if seconds < 60:
        return "just now"

    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes}m ago"

    hours = int(minutes / 60)
    if hours < 24:
        return f"{hours}h ago"

    days = int(hours / 24)
    return f"{days}d ago"
