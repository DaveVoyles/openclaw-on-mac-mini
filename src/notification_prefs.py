"""Per-user notification preferences with JSON file persistence."""

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

PREFS_FILE = Path(os.getenv("NOTIFICATION_PREFS_PATH", "/app/data/notification_prefs.json"))

# Separate file for extended per-user preferences (e.g. timezone)
_USER_PREFS_FILE = Path(os.getenv("USER_PREFS_PATH", "data/user_prefs.json"))

SEVERITY_LEVELS = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class UserNotifPrefs:
    user_id: int
    enabled: bool = True
    dm_alerts: bool = False
    muted_until: float = 0.0  # Unix timestamp; 0 = not muted
    severity_filter: str = "all"  # all | warning | critical
    blocked_services: list[str] = field(default_factory=list)


class NotificationPrefsStore:
    """Thread-safe, JSON-backed store for per-user notification preferences."""

    def __init__(self, path: Path | None = None):
        self._path = path or PREFS_FILE
        self._prefs: dict[int, UserNotifPrefs] = {}
        self._lock = asyncio.Lock()
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for uid_str, prefs_dict in data.items():
                    self._prefs[int(uid_str)] = UserNotifPrefs(**prefs_dict)
            except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
                log.warning("Failed to load notification prefs: %s", e)

    async def _save(self):
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {str(uid): asdict(p) for uid, p in self._prefs.items()}
            self._path.write_text(json.dumps(data, indent=2))

    # -- public API ----------------------------------------------------------

    def get(self, user_id: int) -> UserNotifPrefs:
        if user_id not in self._prefs:
            self._prefs[user_id] = UserNotifPrefs(user_id=user_id)
        return self._prefs[user_id]

    async def update(self, prefs: UserNotifPrefs):
        self._prefs[prefs.user_id] = prefs
        await self._save()

    def should_notify(self, user_id: int, service: str = "", severity: str = "info") -> bool:
        """Return True if *user_id* should receive this notification."""
        prefs = self.get(user_id)
        if not prefs.enabled:
            return False
        if prefs.muted_until and prefs.muted_until > time.time():
            return False
        if service and service.lower() in [s.lower() for s in prefs.blocked_services]:
            return False
        if prefs.severity_filter != "all":
            filter_level = SEVERITY_LEVELS.get(prefs.severity_filter, 0)
            msg_level = SEVERITY_LEVELS.get(severity, 0)
            if msg_level < filter_level:
                return False
        return True


# Module-level singleton
notif_prefs = NotificationPrefsStore()


# ---------------------------------------------------------------------------
# W13-4 — Timezone support for per-user briefing scheduling
# ---------------------------------------------------------------------------


def _load_user_prefs() -> dict:
    if _USER_PREFS_FILE.exists():
        try:
            return json.loads(_USER_PREFS_FILE.read_text())
        except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
            log.warning("Failed to load user_prefs.json: %s", exc)
    return {}


def _save_user_prefs(data: dict) -> None:
    try:
        _USER_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USER_PREFS_FILE.write_text(json.dumps(data, indent=2))
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Failed to save user_prefs.json: %s", exc)


def get_user_timezone(user_id: int) -> str:
    """Return the IANA timezone string for *user_id*, defaulting to 'UTC'."""
    data = _load_user_prefs()
    return str(data.get(str(user_id), {}).get("timezone", "UTC"))


def set_user_timezone(user_id: int, tz: str) -> None:
    """Persist an IANA timezone string for *user_id* to data/user_prefs.json."""
    import zoneinfo

    # Validate the timezone before saving
    try:
        zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError) as exc:
        raise ValueError(f"Invalid timezone: {tz!r}") from exc

    data = _load_user_prefs()
    uid_str = str(user_id)
    if uid_str not in data:
        data[uid_str] = {}
    data[uid_str]["timezone"] = tz
    _save_user_prefs(data)
    log.info("Set timezone for user %d to %s", user_id, tz)
