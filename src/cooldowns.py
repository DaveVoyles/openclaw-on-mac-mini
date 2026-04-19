"""Per-user command cooldown tracking."""

import time
from collections import defaultdict

_cooldowns: dict[str, dict[int, float]] = defaultdict(dict)  # command -> user_id -> last_used_ts


def check_cooldown(command: str, user_id: int, cooldown_seconds: float) -> float:
    """Return 0.0 if user can run the command, or remaining cooldown seconds if they can't."""
    now = time.monotonic()
    last = _cooldowns[command].get(user_id, 0.0)
    elapsed = now - last
    if elapsed < cooldown_seconds:
        return cooldown_seconds - elapsed
    _cooldowns[command][user_id] = now
    return 0.0


def reset_cooldown(command: str, user_id: int) -> None:
    """Test helper — remove a user's cooldown entry for a command."""
    _cooldowns[command].pop(user_id, None)
