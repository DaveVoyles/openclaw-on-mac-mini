"""Atomic JSON registry for Phase 3 host-bridge sessions.

Persisted to ``<DATA_DIR>/host_bridge/sessions.json`` (gitignored). The file is
loaded on container start to detect crashed sessions and saved after every
state transition so a restart can surface them via ``/copilot-sessions``.

Writes are atomic: temp file in the same directory + ``os.replace``. The
registry uses an asyncio lock to serialise concurrent writers within a single
process; cross-process safety is not required (one container).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _default_registry_path() -> Path:
    base = os.getenv("OPENCLAW_HOST_BRIDGE_REGISTRY") or os.getenv("AUDIT_DIR")
    if base:
        return Path(base) / "host_bridge" / "sessions.json"
    return Path(__file__).resolve().parent.parent / "data" / "host_bridge" / "sessions.json"


@dataclass
class SessionRecord:
    session_id: str
    slack_user: str
    slack_channel: str
    slack_thread_ts: str
    started_at: float  # unix epoch seconds
    last_activity: float  # unix epoch seconds
    cwd: str
    host_pid: int | None = None
    status: str = "active"  # active | idle | ended | crashed
    transcript_path: str | None = None
    turns: int = 0  # number of user turns recorded
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionRecord":
        # Be permissive: drop unknown keys, fill missing optional ones.
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in allowed}
        clean.setdefault("meta", {})
        clean.setdefault("turns", 0)
        clean.setdefault("status", "active")
        return cls(**clean)


class Registry:
    """In-memory cache + atomic disk-backed dict of :class:`SessionRecord`."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_registry_path()
        self._lock = asyncio.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("registry: cannot create dir %s: %s", self._path.parent, exc)

    def _load_sync(self) -> dict[str, SessionRecord]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("registry: load failed (%s); starting empty", exc)
            return {}
        out: dict[str, SessionRecord] = {}
        for sid, rec in (raw or {}).items():
            if not isinstance(rec, dict):
                continue
            try:
                out[sid] = SessionRecord.from_dict({**rec, "session_id": sid})
            except (TypeError, ValueError) as exc:
                log.warning("registry: skipping malformed entry %s: %s", sid, exc)
        return out

    def _save_sync(self) -> None:
        self._ensure_dir()
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {sid: rec.to_dict() for sid, rec in self._sessions.items()}
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("registry: save failed: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def load(self) -> None:
        """Load registry from disk. Marks any ``active``/``idle`` rows as ``crashed``."""
        async with self._lock:
            self._sessions = self._load_sync()
            # Container just started -> nothing can still be "active". Mark them
            # as crashed so /copilot-sessions can surface them.
            mutated = False
            for rec in self._sessions.values():
                if rec.status in ("active", "idle"):
                    rec.status = "crashed"
                    mutated = True
            if mutated:
                self._save_sync()
            self._loaded = True

    async def save(self) -> None:
        async with self._lock:
            self._save_sync()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    async def add(self, rec: SessionRecord) -> None:
        async with self._lock:
            self._sessions[rec.session_id] = rec
            self._save_sync()

    async def update(self, session_id: str, **fields: Any) -> SessionRecord | None:
        async with self._lock:
            rec = self._sessions.get(session_id)
            if rec is None:
                return None
            for k, v in fields.items():
                if hasattr(rec, k):
                    setattr(rec, k, v)
            self._save_sync()
            return rec

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._save_sync()

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def list_for_user(self, slack_user: str) -> list[SessionRecord]:
        return [r for r in self._sessions.values() if r.slack_user == slack_user]

    def find_by_thread(self, channel: str, thread_ts: str) -> SessionRecord | None:
        for rec in self._sessions.values():
            if rec.slack_channel == channel and rec.slack_thread_ts == thread_ts:
                return rec
        return None

    def all(self) -> list[SessionRecord]:
        return list(self._sessions.values())


__all__ = ["Registry", "SessionRecord"]
