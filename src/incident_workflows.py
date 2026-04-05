"""Incident workflow persistence and postmortem helpers."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_INCIDENT_DB_PATH = Path(os.getenv("INCIDENT_DB_PATH", "/memory/incidents.db"))

INCIDENT_STATUSES = ("open", "investigating", "monitoring", "resolved")
INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")
ALLOWED_STATUS_TRANSITIONS = {
    "open": {"investigating", "monitoring", "resolved"},
    "investigating": {"monitoring", "resolved"},
    "monitoring": {"investigating", "resolved"},
    "resolved": set(),
}


def parse_action_items(raw: str) -> list[str]:
    """Parse action items from newline/semicolon separated text."""
    if not raw.strip():
        return []
    chunks = re.split(r"[\n;]+", raw)
    parsed: list[str] = []
    for chunk in chunks:
        cleaned = re.sub(r"^\s*[-*\d.)]+\s*", "", chunk).strip()
        if cleaned:
            parsed.append(cleaned[:300])
    return parsed[:20]


class IncidentStore:
    """SQLite persistence for incident room lifecycle and timeline events."""

    def __init__(self, db_path: Path = DEFAULT_INCIDENT_DB_PATH):
        self.db_path = db_path
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            fallback = Path("data/incidents.db")
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = fallback
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                description TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                action_items_json TEXT NOT NULL DEFAULT '[]',
                postmortem_notes TEXT NOT NULL DEFAULT '',
                channel_id INTEGER,
                channel_name TEXT,
                thread_id INTEGER,
                thread_name TEXT,
                created_by INTEGER,
                created_by_name TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                resolved_at REAL
            );

            CREATE TABLE IF NOT EXISTS incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL,
                actor_id INTEGER,
                actor_name TEXT,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
            CREATE INDEX IF NOT EXISTS idx_incidents_updated_at ON incidents(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_incident_events_incident_id ON incident_events(incident_id, created_at DESC);
            """
        )
        self.conn.commit()

    def create_incident(
        self,
        *,
        title: str,
        severity: str,
        description: str,
        channel_id: int | None,
        channel_name: str | None,
        thread_id: int | None,
        thread_name: str | None,
        created_by: int | None,
        created_by_name: str | None,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        sev = severity.strip().lower()
        if sev not in INCIDENT_SEVERITIES:
            raise ValueError(f"Invalid severity: {severity}")
        now = created_at if created_at is not None else time.time()
        cur = self.conn.execute(
            """
            INSERT INTO incidents (
                title, severity, status, description, channel_id, channel_name,
                thread_id, thread_name, created_by, created_by_name, created_at, updated_at
            ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip()[:180],
                sev,
                description.strip()[:2000],
                channel_id,
                (channel_name or "")[:120],
                thread_id,
                (thread_name or "")[:120],
                created_by,
                (created_by_name or "")[:120],
                now,
                now,
            ),
        )
        self.conn.commit()
        incident_id = int(cur.lastrowid)
        self._record_event(
            incident_id=incident_id,
            event_type="created",
            status="open",
            note=description.strip()[:2000],
            actor_id=created_by,
            actor_name=created_by_name,
            created_at=now,
        )
        return self.get_incident(incident_id) or {}

    def set_context(
        self,
        incident_id: int,
        *,
        channel_id: int | None,
        channel_name: str | None,
        thread_id: int | None,
        thread_name: str | None,
    ) -> dict[str, Any] | None:
        now = time.time()
        cur = self.conn.execute(
            """
            UPDATE incidents
            SET channel_id = ?, channel_name = ?, thread_id = ?, thread_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (channel_id, (channel_name or "")[:120], thread_id, (thread_name or "")[:120], now, incident_id),
        )
        self.conn.commit()
        if cur.rowcount <= 0:
            return None
        return self.get_incident(incident_id)

    def transition_status(
        self,
        incident_id: int,
        *,
        new_status: str,
        note: str = "",
        actor_id: int | None,
        actor_name: str | None,
        changed_at: float | None = None,
    ) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return None
        current = str(row["status"])
        target = new_status.strip().lower()
        if target not in INCIDENT_STATUSES:
            raise ValueError(f"Invalid status: {new_status}")
        if current == "resolved":
            raise ValueError("Incident is already resolved")
        if target != current and target not in ALLOWED_STATUS_TRANSITIONS[current]:
            raise ValueError(f"Invalid status transition: {current} -> {target}")

        now = changed_at if changed_at is not None else time.time()
        self.conn.execute(
            "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
            (target, now, incident_id),
        )
        self.conn.commit()
        self._record_event(
            incident_id=incident_id,
            event_type="status_update",
            status=target,
            note=note.strip()[:2000],
            actor_id=actor_id,
            actor_name=actor_name,
            created_at=now,
        )
        return self.get_incident(incident_id)

    def resolve_incident(
        self,
        incident_id: int,
        *,
        summary: str,
        action_items: str | list[str],
        postmortem_notes: str,
        actor_id: int | None,
        actor_name: str | None,
        resolved_at: float | None = None,
    ) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return None
        if str(row["status"]) == "resolved":
            return None

        parsed_actions = (
            parse_action_items(action_items) if isinstance(action_items, str) else [str(i).strip()[:300] for i in action_items if str(i).strip()]
        )
        now = resolved_at if resolved_at is not None else time.time()
        self.conn.execute(
            """
            UPDATE incidents
            SET status = 'resolved',
                summary = ?,
                action_items_json = ?,
                postmortem_notes = ?,
                updated_at = ?,
                resolved_at = ?
            WHERE id = ?
            """,
            (
                summary.strip()[:2500],
                json.dumps(parsed_actions[:20]),
                postmortem_notes.strip()[:4000],
                now,
                now,
                incident_id,
            ),
        )
        self.conn.commit()
        self._record_event(
            incident_id=incident_id,
            event_type="resolved",
            status="resolved",
            note=summary.strip()[:2000],
            actor_id=actor_id,
            actor_name=actor_name,
            created_at=now,
        )
        if parsed_actions or postmortem_notes.strip():
            detail = {
                "action_items": parsed_actions[:20],
                "postmortem_notes": postmortem_notes.strip()[:2000],
            }
            self._record_event(
                incident_id=incident_id,
                event_type="postmortem",
                status="resolved",
                note=json.dumps(detail),
                actor_id=actor_id,
                actor_name=actor_name,
                created_at=now,
            )
        return self.get_incident(incident_id)

    def get_incident(self, incident_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_incident(row)

    def list_recent(self, limit: int = 20, include_resolved: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM incidents"
        params: list[Any] = []
        if not include_resolved:
            sql += " WHERE status != 'resolved'"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_incident(row) for row in rows]

    def get_timeline(self, incident_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT incident_id, event_type, status, note, actor_id, actor_name, created_at
            FROM incident_events
            WHERE incident_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (incident_id, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows]

    def _record_event(
        self,
        *,
        incident_id: int,
        event_type: str,
        status: str,
        note: str,
        actor_id: int | None,
        actor_name: str | None,
        created_at: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO incident_events (incident_id, event_type, status, note, actor_id, actor_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                event_type[:64],
                status[:32],
                note[:4000],
                actor_id,
                (actor_name or "")[:120],
                created_at,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> dict[str, Any]:
        incident = dict(row)
        try:
            incident["action_items"] = json.loads(incident.pop("action_items_json"))
        except (TypeError, json.JSONDecodeError):
            incident["action_items"] = []
        return incident


incident_store = IncidentStore()
