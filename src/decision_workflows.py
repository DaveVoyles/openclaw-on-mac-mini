"""Decision workflow utilities: weighted voting, logs, and role-aware summaries."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DECISION_DB_PATH = Path(os.getenv("DECISION_DB_PATH", "/memory/decisions.db"))


@dataclass(slots=True)
class DecisionVote:
    """One user's vote for an option."""

    user_id: int
    user_name: str
    option_index: int
    roles: list[str]


def parse_role_weights(raw: str) -> dict[str, float]:
    """Parse `Role:Weight,Role2:Weight` into a normalized map."""
    if not raw.strip():
        return {}

    parsed: dict[str, float] = {}
    for token in raw.split(","):
        if ":" not in token:
            continue
        role, weight_text = token.split(":", 1)
        role_key = role.strip().lower()
        if not role_key:
            continue
        try:
            weight = float(weight_text.strip())
        except ValueError:
            continue
        if weight > 0:
            parsed[role_key] = weight
    return parsed


def _weight_for_roles(roles: list[str], role_weights: dict[str, float]) -> tuple[float, str | None]:
    if not role_weights:
        return 1.0, None

    chosen = 1.0
    chosen_role: str | None = None
    for role in roles:
        w = role_weights.get(role.lower())
        if w is not None and w > chosen:
            chosen = w
            chosen_role = role
    return chosen, chosen_role


def compute_weighted_outcome(
    question: str,
    options: list[str],
    votes: list[DecisionVote],
    role_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute weighted poll outcome and participant metadata."""
    role_weights = role_weights or {}
    weighted_totals = [0.0 for _ in options]
    raw_totals = [0 for _ in options]
    participants: list[dict[str, Any]] = []
    seen_users: set[int] = set()

    for vote in votes:
        if vote.user_id in seen_users:
            continue
        if vote.option_index < 0 or vote.option_index >= len(options):
            continue

        seen_users.add(vote.user_id)
        weight, matched_role = _weight_for_roles(vote.roles, role_weights)
        weighted_totals[vote.option_index] += weight
        raw_totals[vote.option_index] += 1
        participants.append(
            {
                "user_id": vote.user_id,
                "user_name": vote.user_name,
                "option_index": vote.option_index,
                "option": options[vote.option_index],
                "roles": vote.roles,
                "applied_weight": round(weight, 3),
                "matched_role": matched_role,
            }
        )

    winner_index = 0
    for idx in range(1, len(options)):
        if weighted_totals[idx] > weighted_totals[winner_index]:
            winner_index = idx
        elif weighted_totals[idx] == weighted_totals[winner_index] and raw_totals[idx] > raw_totals[winner_index]:
            winner_index = idx

    return {
        "question": question,
        "options": options,
        "role_weights": role_weights,
        "weighted_totals": [round(v, 3) for v in weighted_totals],
        "raw_totals": raw_totals,
        "winner_index": winner_index,
        "winner_option": options[winner_index],
        "winner_weighted_score": round(weighted_totals[winner_index], 3),
        "participants": participants,
        "participant_count": len(participants),
    }


class DecisionStore:
    """SQLite persistence for decision outcomes."""

    def __init__(self, db_path: Path = DEFAULT_DECISION_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                weighted_totals_json TEXT NOT NULL,
                raw_totals_json TEXT NOT NULL,
                winner_option TEXT NOT NULL,
                winner_weighted_score REAL NOT NULL,
                participants_json TEXT NOT NULL,
                role_weights_json TEXT NOT NULL,
                channel_id INTEGER,
                channel_name TEXT,
                thread_id INTEGER,
                thread_name TEXT,
                poll_message_id INTEGER,
                created_by INTEGER,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_decisions_channel_id ON decisions(channel_id);
        """)
        self.conn.commit()

    def log_decision(
        self,
        outcome: dict[str, Any],
        *,
        channel_id: int | None,
        channel_name: str | None,
        thread_id: int | None,
        thread_name: str | None,
        poll_message_id: int | None,
        created_by: int | None,
        created_at: float | None = None,
    ) -> int:
        now = created_at if created_at is not None else time.time()
        cur = self.conn.execute(
            """
            INSERT INTO decisions (
                question, options_json, weighted_totals_json, raw_totals_json,
                winner_option, winner_weighted_score, participants_json, role_weights_json,
                channel_id, channel_name, thread_id, thread_name, poll_message_id, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome["question"],
                json.dumps(outcome["options"]),
                json.dumps(outcome["weighted_totals"]),
                json.dumps(outcome["raw_totals"]),
                outcome["winner_option"],
                outcome["winner_weighted_score"],
                json.dumps(outcome["participants"]),
                json.dumps(outcome["role_weights"]),
                channel_id,
                channel_name,
                thread_id,
                thread_name,
                poll_message_id,
                created_by,
                now,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_decision(self, decision_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_decision(row)

    def list_recent(self, limit: int = 10, channel_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM decisions"
        params: list[Any] = []
        if channel_id is not None:
            sql += " WHERE channel_id = ?"
            params.append(channel_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def _row_to_decision(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["options"] = json.loads(d.pop("options_json"))
        d["weighted_totals"] = json.loads(d.pop("weighted_totals_json"))
        d["raw_totals"] = json.loads(d.pop("raw_totals_json"))
        d["participants"] = json.loads(d.pop("participants_json"))
        d["role_weights"] = json.loads(d.pop("role_weights_json"))
        return d


def role_aware_summary(decision: dict[str, Any], audience: str = "general") -> str:
    """Render a concise summary with audience-specific emphasis."""
    audience_key = audience.strip().lower()
    option_lines = ", ".join(
        f"{name} (w={weighted}, v={raw})"
        for name, weighted, raw in zip(decision["options"], decision["weighted_totals"], decision["raw_totals"])
    )
    base = (
        f"Decision #{decision['id']}: {decision['question']}\n"
        f"Winner: {decision['winner_option']} (weighted {decision['winner_weighted_score']})\n"
        f"Breakdown: {option_lines}\n"
        f"Participants: {len(decision['participants'])}"
    )
    if audience_key == "pm":
        return base + "\nPM focus: prioritize delivery impact, owner alignment, and timeline risk."
    if audience_key == "eng":
        return base + "\nEng focus: convert winner into scoped tasks, dependencies, and implementation trade-offs."
    if audience_key == "qa":
        return base + "\nQA focus: derive acceptance criteria, regression scope, and validation checkpoints."
    return base + "\nGeneral focus: communicate outcome and next actions to the team."
