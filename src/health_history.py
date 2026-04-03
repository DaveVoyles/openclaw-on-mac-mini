"""Track and query service health check history."""
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("openclaw")

DB_PATH = Path("/app/data/health_history.db")


@dataclass
class HealthEntry:
    service: str
    status: str  # "ok", "degraded", "down"
    message: str
    timestamp: float


class HealthHistory:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                timestamp REAL NOT NULL
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_ts ON health_checks(service, timestamp)"
        )
        self.db.commit()

    def record(self, service: str, status: str, message: str = ""):
        self.db.execute(
            "INSERT INTO health_checks (service, status, message, timestamp) VALUES (?, ?, ?, ?)",
            (service, status, message, time.time()),
        )
        self.db.commit()

    def get_trend(self, service: str, days: int = 7) -> dict:
        cutoff = time.time() - (days * 86400)
        rows = self.db.execute(
            "SELECT status, COUNT(*) FROM health_checks WHERE service=? AND timestamp>? GROUP BY status",
            (service, cutoff),
        ).fetchall()
        total = sum(r[1] for r in rows)
        status_counts = {r[0]: r[1] for r in rows}

        # Get recent incidents
        incidents = self.db.execute(
            "SELECT status, message, timestamp FROM health_checks "
            "WHERE service=? AND status!='ok' AND timestamp>? "
            "ORDER BY timestamp DESC LIMIT 10",
            (service, cutoff),
        ).fetchall()

        uptime_pct = (status_counts.get("ok", 0) / total * 100) if total > 0 else 0

        return {
            "service": service,
            "days": days,
            "total_checks": total,
            "uptime_pct": round(uptime_pct, 1),
            "status_counts": status_counts,
            "recent_incidents": incidents,
            "sparkline": self._sparkline(service, days),
        }

    def _sparkline(self, service: str, days: int) -> str:
        """Generate a simple text sparkline of health over time."""
        cutoff = time.time() - (days * 86400)
        bucket_size = (days * 86400) / 24  # 24 buckets
        rows = self.db.execute(
            "SELECT timestamp, status FROM health_checks WHERE service=? AND timestamp>? ORDER BY timestamp",
            (service, cutoff),
        ).fetchall()

        buckets = ["\u2593"] * 24  # default = no data
        for ts, status in rows:
            idx = min(23, int((ts - cutoff) / bucket_size))
            if status == "ok":
                buckets[idx] = "\u2588"
            elif status == "degraded":
                buckets[idx] = "\u2592"
            else:
                buckets[idx] = "\u2591"
        return "".join(buckets)


def _get_instance() -> HealthHistory:
    """Lazy singleton — avoids creating the DB at import time."""
    global _instance
    if _instance is None:
        _instance = HealthHistory()
    return _instance


_instance: HealthHistory | None = None
health_history = property(lambda self: _get_instance())  # backward compat


def record(service: str, status: str, message: str = ""):
    """Module-level convenience wrapper."""
    _get_instance().record(service, status, message)


def get_trend(service: str, days: int = 7) -> dict:
    """Module-level convenience wrapper."""
    return _get_instance().get_trend(service, days)
