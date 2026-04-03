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
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS disk_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mount_point TEXT NOT NULL,
                total_gb REAL NOT NULL,
                used_gb REAL NOT NULL,
                free_gb REAL NOT NULL,
                percent_used REAL NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        self.db.commit()

    def record(self, service: str, status: str, message: str = ""):
        self.db.execute(
            "INSERT INTO health_checks (service, status, message, timestamp) VALUES (?, ?, ?, ?)",
            (service, status, message, time.time()),
        )
        self.db.commit()

    def record_disk(
        self,
        mount_point: str,
        total_gb: float,
        used_gb: float,
        free_gb: float,
        percent_used: float,
    ):
        """Record a disk usage snapshot."""
        self.db.execute(
            "INSERT INTO disk_usage (mount_point, total_gb, used_gb, free_gb, percent_used, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (mount_point, total_gb, used_gb, free_gb, percent_used, time.time()),
        )
        self.db.commit()

    def predict_full(self, mount_point: str, days_lookback: int = 30) -> dict:
        """Predict days until disk is full using linear regression."""
        cutoff = time.time() - (days_lookback * 86400)
        rows = self.db.execute(
            "SELECT timestamp, used_gb, total_gb FROM disk_usage "
            "WHERE mount_point=? AND timestamp>? ORDER BY timestamp",
            (mount_point, cutoff),
        ).fetchall()

        if len(rows) < 2:
            return {"mount_point": mount_point, "prediction": "insufficient data", "days_until_full": None}

        n = len(rows)
        t0 = rows[0][0]
        xs = [(r[0] - t0) / 86400 for r in rows]  # days since first reading
        ys = [r[1] for r in rows]  # used_gb
        total_gb = rows[-1][2]

        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x2 = sum(x * x for x in xs)

        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            return {"mount_point": mount_point, "prediction": "stable", "days_until_full": None}

        slope = (n * sum_xy - sum_x * sum_y) / denom  # GB per day
        current_used = ys[-1]
        remaining_gb = total_gb - current_used

        if slope <= 0:
            return {
                "mount_point": mount_point,
                "prediction": "stable or decreasing",
                "days_until_full": None,
                "growth_rate_gb_day": round(slope, 3),
            }

        days_until_full = remaining_gb / slope
        return {
            "mount_point": mount_point,
            "current_used_gb": round(current_used, 1),
            "total_gb": round(total_gb, 1),
            "percent_used": round(current_used / total_gb * 100, 1),
            "growth_rate_gb_day": round(slope, 2),
            "days_until_full": round(days_until_full),
            "prediction": f"~{round(days_until_full)} days at current rate",
        }

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


def record_disk(mount_point: str, total_gb: float, used_gb: float, free_gb: float, percent_used: float):
    """Module-level convenience wrapper."""
    _get_instance().record_disk(mount_point, total_gb, used_gb, free_gb, percent_used)


def predict_full(mount_point: str, days_lookback: int = 30) -> dict:
    """Module-level convenience wrapper."""
    return _get_instance().predict_full(mount_point, days_lookback)


def get_trend(service: str, days: int = 7) -> dict:
    """Module-level convenience wrapper."""
    return _get_instance().get_trend(service, days)
