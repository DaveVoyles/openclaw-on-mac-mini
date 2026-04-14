"""Session and workspace helpers for the terminal-first OpenClaw CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MAX_CONTEXT_CHARS = 12_000
MAX_FILE_PREVIEW_CHARS = 4_000
MAX_DIR_ENTRIES = 200
MAX_EVENT_SUMMARY_CHARS = 180
WATCH_INTERVENTION_LIMIT = 20
ROUTED_ACTION_CHECKPOINT_LIMIT = 5
ROUTED_ACTION_CHECKPOINT_MAX_FILE_BYTES = 256_000
TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass
class SessionSummary:
    """Persisted CLI session metadata."""

    session_id: str
    title: str
    cwd: str
    files: list[str] = field(default_factory=list)
    plan_id: str = ""
    task_id: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    last_command: str = ""
    last_summary: str = ""
    command_count: int = 0
    file_edit_count: int = 0
    output_count: int = 0
    automation_mode: str = ""
    automation_status: str = ""
    watch_interval_seconds: int = 0
    checkpoint_count: int = 0
    last_checkpoint_at: str = ""
    repl_auto_route: bool = True
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cli_data_root(*, platform_name: str | None = None) -> Path:
    """Return the writable per-user data root for the standalone CLI."""
    override = str(os.getenv("OPENCLAW_CLI_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    current_platform = platform_name or os.sys.platform
    if current_platform.startswith("win"):
        return Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "OpenClaw"
    if current_platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenClaw"
    return Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "openclaw"


def sessions_root() -> Path:
    return cli_data_root() / "sessions"


def atomic_write(path: Path, data: str) -> None:
    """Write text atomically without depending on the full app package tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _normalize_session_id(session_id: str) -> str:
    """Normalize session ids to a safe filesystem token."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(session_id or "").strip())
    safe_id = re.sub(r"-{2,}", "-", safe_id).strip("-")
    if not safe_id:
        raise ValueError("session_id is required")
    return safe_id[:160]


def _session_dir(session_id: str) -> Path:
    return sessions_root() / _normalize_session_id(session_id)


def _metadata_path(session_id: str) -> Path:
    return _session_dir(session_id) / "metadata.json"


def _events_path(session_id: str) -> Path:
    return _session_dir(session_id) / "events.jsonl"


def _outputs_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "outputs"


def _watch_state_path(session_id: str) -> Path:
    return _session_dir(session_id) / "watch_state.json"


def _routed_action_checkpoints_path(session_id: str) -> Path:
    return _session_dir(session_id) / "routed_action_checkpoints.json"


def _normalize_watch_interventions(state: dict[str, Any]) -> dict[str, Any]:
    state["interventions"] = [
        item for item in list(state.get("interventions") or []) if isinstance(item, dict)
    ][-WATCH_INTERVENTION_LIMIT:]
    state["force_run_once"] = bool(state.get("force_run_once"))
    state["stop_requested"] = bool(state.get("stop_requested"))
    state["stop_requested_at"] = str(state.get("stop_requested_at", "") or "")
    state["last_intervention_at"] = str(state.get("last_intervention_at", "") or "")
    return state


def _slugify(value: str, *, default: str = "session") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:40] or default


def _short_summary(text: str, *, limit: int = MAX_EVENT_SUMMARY_CHARS) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _ensure_session_layout(session_id: str) -> Path:
    session_dir = _session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    _outputs_dir(session_id).mkdir(parents=True, exist_ok=True)
    _events_path(session_id).touch(exist_ok=True)
    return session_dir


def create_session(
    *,
    title: str = "",
    cwd: str | os.PathLike[str] | None = None,
    files: list[str] | None = None,
    plan_id: str = "",
    task_id: str = "",
) -> SessionSummary:
    """Create and persist a new CLI session."""
    label = title or (files[0] if files else cwd or "session")
    session_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}-{_slugify(str(label), default='session')}"
    summary = SessionSummary(
        session_id=session_id,
        title=(title or f"OpenClaw session for {label}").strip()[:120],
        cwd=str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd().resolve()),
        files=sorted({str(Path(path).expanduser()) for path in (files or [])}),
        plan_id=str(plan_id or "").strip(),
        task_id=str(task_id or "").strip(),
    )
    _ensure_session_layout(session_id)
    save_session(summary)
    return summary


def save_session(summary: SessionSummary) -> None:
    """Persist session metadata."""
    _ensure_session_layout(summary.session_id)
    summary.updated_at = _now_iso()
    atomic_write(_metadata_path(summary.session_id), json.dumps(asdict(summary), indent=2, sort_keys=True))


def update_session(session_id: str, **fields: Any) -> SessionSummary:
    """Update selected session metadata fields and persist them."""
    summary = require_session(session_id)
    for key, value in fields.items():
        if hasattr(summary, key):
            setattr(summary, key, value)
    save_session(summary)
    return summary


def load_session(session_id: str) -> SessionSummary | None:
    """Load persisted session metadata when present."""
    try:
        path = _metadata_path(session_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return SessionSummary(**payload)
    except TypeError:
        return None


def require_session(session_id: str) -> SessionSummary:
    session = load_session(session_id)
    if session is None:
        raise FileNotFoundError(f"Session '{session_id}' was not found.")
    return session


def list_sessions(*, limit: int = 20) -> list[SessionSummary]:
    """Return recent CLI sessions ordered by newest activity first."""
    summaries: list[SessionSummary] = []
    root = sessions_root()
    if not root.exists():
        return summaries
    for metadata_path in root.glob("*/metadata.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            summaries.append(SessionSummary(**payload))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    summaries.sort(key=lambda item: (item.updated_at, item.created_at, item.session_id), reverse=True)
    return summaries[: max(1, limit)]


def append_event(
    session_id: str,
    *,
    kind: str,
    content: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a structured event to a session and refresh summary metadata."""
    summary = require_session(session_id)
    payload = {
        "created_at": _now_iso(),
        "kind": str(kind or "").strip() or "event",
        "content": content,
        "metadata": metadata or {},
    }
    _ensure_session_layout(session_id)
    with _events_path(session_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    summary.last_command = payload["kind"]
    summary.last_summary = _short_summary(
        str((metadata or {}).get("summary") or content or payload["kind"])
    )
    if payload["kind"] in {"prompt", "chat", "analyze", "research", "write", "exec", "edit", "plan"}:
        summary.command_count += 1
    if payload["kind"] == "edit" and (metadata or {}).get("changed"):
        summary.file_edit_count += 1
    if payload["kind"] == "checkpoint":
        summary.checkpoint_count += 1
        summary.last_checkpoint_at = payload["created_at"]
    maybe_plan_id = str((metadata or {}).get("plan_id") or "").strip()
    maybe_task_id = str((metadata or {}).get("task_id") or "").strip()
    if maybe_plan_id:
        summary.plan_id = maybe_plan_id
    if maybe_task_id:
        summary.task_id = maybe_task_id
    maybe_files = (metadata or {}).get("files")
    if isinstance(maybe_files, list):
        summary.files = sorted({*summary.files, *(str(item) for item in maybe_files if item)})
    save_session(summary)


def load_events(session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load structured session events."""
    try:
        path = _events_path(session_id)
    except ValueError:
        return []
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit is not None and limit > 0:
        return events[-limit:]
    return events


_DECISION_KINDS = {"route", "plan", "approval", "checkpoint", "exec", "edit"}


def get_last_decision_event(session_id: str) -> dict[str, Any] | None:
    """Return the most recent decision event for a session.

    A decision event is any event whose ``kind`` is one of: ``route``,
    ``plan``, ``approval``, ``checkpoint``, ``exec``, or ``edit``.  This is
    used by the ``/why`` command to explain what the CLI last decided to do.

    Returns ``None`` when no matching event exists.
    """
    for event in reversed(load_events(session_id)):
        if str(event.get("kind") or "").strip().lower() in _DECISION_KINDS:
            return event
    return None


def _normalize_collaboration_actor(actor: Any, *, default: str) -> str:
    label = " ".join(str(actor or "").strip().split())
    return label[:80] or default


def _normalize_collaboration_tags(raw_tags: Any) -> list[str]:
    if isinstance(raw_tags, str):
        values = raw_tags.split()
    elif isinstance(raw_tags, list):
        values = [str(item or "") for item in raw_tags]
    else:
        values = []
    tags: list[str] = []
    for value in values:
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
        if cleaned and cleaned not in tags:
            tags.append(cleaned[:40])
    return tags


def build_collaboration_snapshot(session_id: str, *, limit: int = 5) -> dict[str, Any]:
    """Summarize collaboration-oriented session state using local artifacts only."""
    summary = require_session(session_id)
    events = load_events(session_id)
    watch_state = load_watch_state(session_id) or {}
    recent_handoffs = [
        item for item in list_handoffs(limit=max(limit * 4, 20))
        if str(item.get("source_session_id") or "") == session_id
    ]

    actors: dict[str, dict[str, Any]] = {}
    notes: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    def _record_actor(name: str, timestamp: str) -> None:
        entry = actors.get(name)
        if entry is None:
            actors[name] = {"name": name, "event_count": 1, "last_at": timestamp}
            return
        entry["event_count"] = int(entry.get("event_count") or 0) + 1
        if timestamp and timestamp > str(entry.get("last_at") or ""):
            entry["last_at"] = timestamp

    for event in events:
        kind = str(event.get("kind") or "").strip().lower()
        meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        timestamp = str(event.get("created_at") or event.get("timestamp") or "").strip()
        content = str(event.get("content") or "").strip()
        summary_text = str(meta.get("summary") or content or kind).strip()

        if kind == "collab":
            actor = _normalize_collaboration_actor(meta.get("actor"), default="operator")
            tags = _normalize_collaboration_tags(meta.get("tags"))
            entry = {
                "timestamp": timestamp,
                "actor": actor,
                "summary": summary_text,
                "content": content,
                "tags": tags,
                "source": "manual",
            }
            _record_actor(actor, timestamp)
            if str(meta.get("collab_kind") or "note").strip().lower() == "decision":
                decisions.append(entry)
            else:
                notes.append(entry)
            continue

        if kind in _DECISION_KINDS:
            actor = _normalize_collaboration_actor(meta.get("actor"), default="OpenClaw")
            tags = [kind]
            route_tag = str(meta.get("slash_command") or "").strip().lstrip("/")
            risk_tag = str(meta.get("risk_level") or "").strip().lower()
            if route_tag:
                tags.append(route_tag)
            if risk_tag:
                tags.append(risk_tag)
            _record_actor(actor, timestamp)
            decisions.append(
                {
                    "timestamp": timestamp,
                    "actor": actor,
                    "summary": summary_text,
                    "content": content,
                    "tags": _normalize_collaboration_tags(tags),
                    "source": "system",
                }
            )

    for intervention in list(watch_state.get("interventions") or []):
        if not isinstance(intervention, dict):
            continue
        actor = _normalize_collaboration_actor(intervention.get("actor"), default="dashboard")
        _record_actor(actor, str(intervention.get("created_at") or ""))

    actor_items = sorted(
        actors.values(),
        key=lambda item: (int(item.get("event_count") or 0), str(item.get("last_at") or ""), item.get("name", "")),
        reverse=True,
    )
    return {
        "session": {
            "session_id": summary.session_id,
            "title": summary.title,
            "cwd": summary.cwd,
            "plan_id": summary.plan_id,
            "task_id": summary.task_id,
            "tags": list(summary.tags),
            "last_summary": summary.last_summary,
        },
        "actors": actor_items[:limit],
        "recent_notes": notes[-limit:][::-1],
        "recent_decisions": decisions[-limit:][::-1],
        "recent_outputs": list_saved_outputs(session_id, limit=min(limit, 3)),
        "latest_handoff": recent_handoffs[0] if recent_handoffs else None,
        "share": {
            "resume_command": f"openclaw --session {summary.session_id}",
            "inspect_command": f"openclaw session show {summary.session_id}",
            "share_command": f"openclaw session share {summary.session_id}",
        },
    }


def load_conversation_history(session_id: str, *, limit_turns: int = 10) -> list[dict[str, str]]:
    """Rebuild ask/chat history from recorded session events."""
    history: list[dict[str, str]] = []
    for event in load_events(session_id):
        kind = str(event.get("kind") or "").strip().lower()
        content = str(event.get("content") or "").strip()
        if kind == "chat" and content == "/clear":
            history.clear()
            continue
        if not content:
            continue
        if kind in {"prompt", "user"}:
            history.append({"role": "user", "content": content})
        elif kind in {"response", "assistant"}:
            history.append({"role": "assistant", "content": content})
    if limit_turns <= 0:
        return history
    return history[-(limit_turns * 2) :]


def save_output(
    session_id: str,
    name: str,
    content: str,
    *,
    command: str = "",
    model: str = "",
) -> Path:
    """Persist a text output artifact beneath the session directory.

    Optional keyword arguments ``command`` and ``model`` are recorded in a
    companion ``.provenance.json`` sidecar file written alongside the output
    file.  If writing the sidecar fails for any reason, the failure is
    silently ignored so that ``save_output`` always succeeds.
    """
    summary = require_session(session_id)
    outputs_dir = _outputs_dir(session_id)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    original_name = str(name or "output.txt").strip()
    stem = _slugify(Path(original_name).stem or "output", default="output")
    suffix = Path(original_name).suffix or ".txt"
    target = outputs_dir / f"{stem}{suffix}"
    index = 2
    while target.exists():
        target = outputs_dir / f"{stem}-{index}{suffix}"
        index += 1
    atomic_write(target, str(content or ""))
    try:
        provenance = {
            "session_id": session_id,
            "name": original_name,
            "command": str(command or ""),
            "model": str(model or ""),
            "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        sidecar = target.with_suffix(".provenance.json")
        atomic_write(sidecar, json.dumps(provenance, indent=2, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass
    summary.output_count += 1
    summary.last_summary = f"saved output {target.name}"
    save_session(summary)
    return target


def load_output_provenance(session_id: str, output_path: Path) -> dict[str, Any]:
    """Read and return the provenance sidecar for a saved output file.

    Returns an empty dict when the sidecar is missing, unreadable, or
    contains invalid JSON.  ``session_id`` is accepted for API consistency
    but the sidecar is located solely from ``output_path``.
    """
    try:
        sidecar = Path(output_path).with_suffix(".provenance.json")
        return dict(json.loads(sidecar.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return {}


def list_saved_outputs(session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return saved session outputs ordered newest-first."""
    outputs_dir = _outputs_dir(session_id)
    outputs: list[tuple[Path, os.stat_result]] = []
    if outputs_dir.exists():
        for path in outputs_dir.iterdir():
            if not path.is_file():
                continue
            if path.name.endswith(".provenance.json"):
                continue  # skip sidecar files
            try:
                stat = path.stat()
            except OSError:
                continue
            outputs.append((path, stat))
    outputs.sort(key=lambda item: (-item[1].st_mtime_ns, item[0].name))
    items = [
        {
            "name": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for path, stat in outputs
    ]
    if limit > 0:
        return items[:limit]
    return items


def load_saved_output_preview(
    session_id: str,
    selector: str,
    *,
    max_chars: int = MAX_FILE_PREVIEW_CHARS,
) -> dict[str, Any] | None:
    """Resolve a saved output by 1-based index or filename and return a bounded preview."""
    token = str(selector or "").strip()
    if not token:
        return None
    outputs = list_saved_outputs(session_id, limit=0)
    chosen: dict[str, Any] | None = None
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(outputs):
            chosen = outputs[index - 1]
    else:
        chosen = next((item for item in outputs if str(item.get("name") or "") == token), None)
    if chosen is None:
        return None
    path = Path(str(chosen.get("path") or ""))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        preview = f"[unable to read output: {exc}]"
        return {**chosen, "preview": preview, "truncated": False}
    preview = text[:max_chars]
    truncated = len(text) > max_chars
    if truncated:
        preview += "\n...[truncated]..."
    return {**chosen, "preview": preview, "truncated": truncated}


def export_session(session_id: str) -> dict[str, Any]:
    """Return a fully expanded JSON-safe session export."""
    summary = require_session(session_id)
    return {
        "session": asdict(summary),
        "events": load_events(session_id),
        "outputs": list_saved_outputs(session_id, limit=0),
        "watch_state": load_watch_state(session_id),
        "routed_action_checkpoints": list_routed_action_checkpoints(session_id, limit=0),
        "collaboration": build_collaboration_snapshot(session_id, limit=10),
    }


def save_watch_state(session_id: str, state: dict[str, Any]) -> None:
    """Persist watch-mode state for a CLI session."""
    _ensure_session_layout(session_id)
    atomic_write(
        _watch_state_path(session_id),
        json.dumps(_normalize_watch_interventions(dict(state)), indent=2, sort_keys=True),
    )


def load_watch_state(session_id: str) -> dict[str, Any] | None:
    """Load persisted watch-mode state when present."""
    try:
        path = _watch_state_path(session_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _normalize_watch_interventions(payload) if isinstance(payload, dict) else None


def queue_watch_intervention(
    session_id: str,
    *,
    action: str,
    actor: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Append a watch control request for dashboard-driven interventions."""
    require_session(session_id)
    state = load_watch_state(session_id)
    if state is None:
        raise FileNotFoundError(f"Watch state for session '{session_id}' was not found.")

    normalized_action = str(action or "").strip().lower().replace("_", "-")
    if normalized_action in {"stop", "interrupt"}:
        normalized_action = "graceful-stop"
    elif normalized_action in {"checkpoint", "force"}:
        normalized_action = "force-checkpoint"
    if normalized_action not in {"graceful-stop", "force-checkpoint"}:
        raise ValueError(f"Unsupported watch intervention: {action}")

    interventions = list(state.get("interventions") or [])
    existing = next(
        (
            item
            for item in reversed(interventions)
            if str(item.get("action") or "") == normalized_action
            and str(item.get("status") or "") == "pending"
        ),
        None,
    )
    if existing is not None:
        return existing

    created_at = _now_iso()
    request = {
        "request_id": uuid.uuid4().hex[:10],
        "action": normalized_action,
        "status": "pending",
        "actor": str(actor or "dashboard").strip()[:120] or "dashboard",
        "reason": str(reason or "").strip()[:240],
        "created_at": created_at,
    }
    interventions.append(request)
    state["interventions"] = interventions[-WATCH_INTERVENTION_LIMIT:]
    state["last_intervention_at"] = created_at
    if normalized_action == "graceful-stop":
        state["stop_requested"] = True
        state["stop_requested_at"] = created_at
    elif normalized_action == "force-checkpoint":
        state["force_run_once"] = True
    save_watch_state(session_id, state)
    return request


def _load_routed_action_checkpoint_store(session_id: str) -> list[dict[str, Any]]:
    try:
        path = _routed_action_checkpoints_path(session_id)
    except ValueError:
        return []
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def _save_routed_action_checkpoint_store(session_id: str, entries: list[dict[str, Any]]) -> None:
    _ensure_session_layout(session_id)
    atomic_write(
        _routed_action_checkpoints_path(session_id),
        json.dumps(entries[-ROUTED_ACTION_CHECKPOINT_LIMIT:], indent=2, sort_keys=True),
    )


def _capture_checkpoint_file_snapshot(path_text: str) -> dict[str, Any]:
    target = Path(path_text).expanduser().resolve()
    snapshot: dict[str, Any] = {
        "path": str(target),
        "existed": target.exists(),
        "recoverable": False,
        "reason": "",
        "content": "",
    }
    if not target.exists():
        snapshot["recoverable"] = True
        return snapshot
    if not target.is_file():
        snapshot["reason"] = "target is not a regular file"
        return snapshot
    if not _is_text_file(target):
        snapshot["reason"] = "target is not a text file"
        return snapshot
    try:
        stat = target.stat()
    except OSError as exc:
        snapshot["reason"] = f"unable to stat file: {exc}"
        return snapshot
    if stat.st_size > ROUTED_ACTION_CHECKPOINT_MAX_FILE_BYTES:
        snapshot["reason"] = (
            f"file exceeds {ROUTED_ACTION_CHECKPOINT_MAX_FILE_BYTES} bytes"
        )
        return snapshot
    try:
        snapshot["content"] = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        snapshot["reason"] = f"unable to read file: {exc}"
        return snapshot
    snapshot["recoverable"] = True
    return snapshot


def list_routed_action_checkpoints(
    session_id: str,
    *,
    limit: int = ROUTED_ACTION_CHECKPOINT_LIMIT,
) -> list[dict[str, Any]]:
    """Return retained routed-action checkpoints newest-first."""
    entries = list(reversed(_load_routed_action_checkpoint_store(session_id)))
    if limit > 0:
        return entries[:limit]
    return entries


def create_routed_action_checkpoint(
    session_id: str,
    *,
    action_kind: str,
    target: str,
    detail: str = "",
    cwd: str = "",
    route_metadata: dict[str, Any] | None = None,
    file_paths: list[str] | None = None,
    workspace_signature: str = "",
) -> dict[str, Any]:
    """Capture bounded rollback metadata for a routed multi-step action."""
    require_session(session_id)
    entries = _load_routed_action_checkpoint_store(session_id)
    metadata = dict(route_metadata or {})
    file_snapshots = [
        _capture_checkpoint_file_snapshot(path_text)
        for path_text in list(dict.fromkeys(str(path) for path in (file_paths or []) if path))
    ]
    rollback_supported = (
        str(action_kind or "").strip().lower() == "edit"
        and bool(file_snapshots)
        and all(bool(snapshot.get("recoverable")) for snapshot in file_snapshots)
    )
    rollback_reason = ""
    if not rollback_supported:
        rollback_reason = "Automatic rollback is only supported for text file edits."
        for snapshot in file_snapshots:
            reason = str(snapshot.get("reason") or "").strip()
            if reason:
                rollback_reason = reason
                break

    checkpoint_id = uuid.uuid4().hex[:10]
    step_index = int(metadata.get("step_index") or 0)
    step_total = int(metadata.get("step_total") or 0)
    checkpoint = {
        "checkpoint_id": checkpoint_id,
        "created_at": _now_iso(),
        "route_source": str(metadata.get("source") or "").strip(),
        "route_prompt": str(metadata.get("prompt") or "").strip(),
        "step_index": step_index,
        "step_total": step_total,
        "step_kind": str(metadata.get("step_kind") or action_kind).strip(),
        "action_kind": str(action_kind or "").strip(),
        "target": str(target or "").strip(),
        "detail": str(detail or "").strip(),
        "cwd": str(cwd or "").strip(),
        "workspace_signature": str(workspace_signature or "").strip(),
        "file_snapshots": file_snapshots,
        "rollback_supported": rollback_supported,
        "rollback_reason": rollback_reason,
        "rollback_status": "available" if rollback_supported else "manual-only",
        "restored_at": "",
    }
    entries.append(checkpoint)
    _save_routed_action_checkpoint_store(session_id, entries)

    step_label = (
        f"step {step_index}/{step_total}"
        if step_index > 0 and step_total > 0
        else "routed step"
    )
    summary = f"{checkpoint['action_kind']} checkpoint {checkpoint_id} captured for {step_label}"
    if rollback_supported:
        summary += " (rollback ready)"
    append_event(
        session_id,
        kind="checkpoint",
        content=checkpoint["target"],
        metadata={
            "summary": summary,
            "checkpoint_id": checkpoint_id,
            "action_kind": checkpoint["action_kind"],
            "detail": checkpoint["detail"],
            "rollback_supported": rollback_supported,
            "rollback_reason": rollback_reason,
            "workspace_signature": checkpoint["workspace_signature"],
            "step_index": step_index,
            "step_total": step_total,
            "files": [snapshot["path"] for snapshot in file_snapshots],
        },
    )
    return checkpoint


def restore_last_routed_action_checkpoint(session_id: str) -> dict[str, Any] | None:
    """Restore the newest retained routed-action checkpoint when possible."""
    require_session(session_id)
    entries = _load_routed_action_checkpoint_store(session_id)
    if not entries:
        return None

    checkpoint = dict(entries[-1])
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
    action_kind = str(checkpoint.get("action_kind") or "").strip() or "action"
    target = str(checkpoint.get("target") or "").strip()

    if str(checkpoint.get("rollback_status") or "") == "rolled_back":
        append_event(
            session_id,
            kind="rollback",
            content=target,
            metadata={
                "summary": f"rollback skipped for checkpoint {checkpoint_id}; already restored",
                "checkpoint_id": checkpoint_id,
                "status": "already_rolled_back",
            },
        )
        return {
            "status": "already_rolled_back",
            "checkpoint": checkpoint,
            "restored_files": [],
            "reason": "Checkpoint already rolled back.",
        }

    if not bool(checkpoint.get("rollback_supported")):
        reason = str(checkpoint.get("rollback_reason") or "").strip() or (
            "Automatic rollback is unavailable for this action."
        )
        append_event(
            session_id,
            kind="rollback",
            content=target,
            metadata={
                "summary": f"rollback unavailable for checkpoint {checkpoint_id}: {reason}",
                "checkpoint_id": checkpoint_id,
                "status": "unsupported",
                "reason": reason,
            },
        )
        return {
            "status": "unsupported",
            "checkpoint": checkpoint,
            "restored_files": [],
            "reason": reason,
        }

    restored_files: list[str] = []
    try:
        for snapshot in checkpoint.get("file_snapshots") or []:
            path = Path(str(snapshot.get("path") or "")).expanduser().resolve()
            existed_before = bool(snapshot.get("existed"))
            if existed_before:
                atomic_write(path, str(snapshot.get("content") or ""))
            elif path.exists():
                if not path.is_file():
                    raise IsADirectoryError(f"Rollback target is not a file: {path}")
                path.unlink()
            restored_files.append(str(path))
    except OSError as exc:
        append_event(
            session_id,
            kind="rollback",
            content=target,
            metadata={
                "summary": f"rollback failed for checkpoint {checkpoint_id}: {exc}",
                "checkpoint_id": checkpoint_id,
                "status": "failed",
                "reason": str(exc),
            },
        )
        return {
            "status": "failed",
            "checkpoint": checkpoint,
            "restored_files": restored_files,
            "reason": str(exc),
        }

    checkpoint["rollback_status"] = "rolled_back"
    checkpoint["restored_at"] = _now_iso()
    entries[-1] = checkpoint
    _save_routed_action_checkpoint_store(session_id, entries)
    append_event(
        session_id,
        kind="rollback",
        content=target,
        metadata={
            "summary": f"rolled back {action_kind} checkpoint {checkpoint_id}",
            "checkpoint_id": checkpoint_id,
            "status": "restored",
            "files": restored_files,
        },
    )
    return {
        "status": "restored",
        "checkpoint": checkpoint,
        "restored_files": restored_files,
        "reason": "",
    }


def _handoffs_root() -> Path:
    """Return the directory where handoff manifests are stored."""
    return cli_data_root() / "handoffs"


def create_handoff(session_id: str, *, note: str = "", pin_outputs: list[str] | None = None) -> str:
    """Snapshot the current session state into a portable handoff manifest."""
    summary = load_session(session_id)
    if summary is None:
        raise ValueError(f"Session not found: {session_id!r}")

    handoff_id = (
        "handoff_"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_"
        + session_id[:8]
    )

    manifest: dict[str, Any] = {
        "id": handoff_id,
        "created_at": _now_iso(),
        "source_session_id": session_id,
        "session_title": summary.title,
        "cwd": summary.cwd,
        "tracked_files": summary.files,
        "plan_id": summary.plan_id,
        "task_id": summary.task_id,
        "tags": summary.tags,
        "last_summary": summary.last_summary,
        "note": note,
        "pinned_outputs": pin_outputs or [],
        "recent_history": load_conversation_history(session_id, limit_turns=10),
        "outputs_snapshot": list_saved_outputs(session_id, limit=20),
        "collaboration": build_collaboration_snapshot(session_id, limit=10),
    }

    atomic_write(_handoffs_root() / f"{handoff_id}.json", json.dumps(manifest, indent=2, sort_keys=True))
    return handoff_id


def list_handoffs(*, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent handoff manifests sorted by creation time descending."""
    root = _handoffs_root()
    if not root.exists():
        return []

    entries: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return entries[:limit]


def load_handoff(name_or_id: str) -> dict[str, Any] | None:
    """Load a handoff manifest by exact name or ID prefix."""
    root = _handoffs_root()
    if not root.exists():
        return None

    exact = root / f"{name_or_id}.json"
    if exact.exists():
        try:
            return json.loads(exact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    for path in root.glob("*.json"):
        if path.name.startswith(name_or_id):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None

    return None


def apply_handoff(manifest: dict[str, Any], target_session_id: str) -> dict[str, Any]:
    """Merge a handoff manifest's context into a target session."""
    if not isinstance(manifest, dict) or "source_session_id" not in manifest:
        raise ValueError("manifest must be a dict with a 'source_session_id' key")

    result: dict[str, Any] = {"restored": [], "missing": [], "warnings": []}

    available_tracked_files: list[str] = []
    for f in manifest.get("tracked_files") or []:
        if Path(f).exists():
            result["restored"].append(f"tracked_file:{f}")
            available_tracked_files.append(f)
        else:
            result["missing"].append(f"tracked_file:{f}")

    cwd = manifest.get("cwd")
    if cwd and not Path(cwd).exists():
        result["warnings"].append(f"cwd:{cwd}")

    if target_session_id:
        existing = load_session(target_session_id)
        existing_files: list[str] = list(existing.files) if existing and existing.files else []
        merged_files = list(set(existing_files + available_tracked_files))
        existing_tags: list[str] = list(existing.tags) if existing and existing.tags else []
        handoff_tag = "handoff:" + manifest.get("source_session_id", "")[:8]
        if handoff_tag not in existing_tags:
            existing_tags.append(handoff_tag)
        update_session(
            target_session_id,
            last_summary=manifest.get("last_summary", ""),
            files=merged_files,
            tags=existing_tags,
        )

    return result


def build_workspace_signature(
    *,
    cwd: str | os.PathLike[str] | None = None,
    targets: list[str] | None = None,
    max_entries: int = MAX_DIR_ENTRIES,
) -> str:
    """Build a stable hash of the workspace snapshot for watch-mode skips."""
    base = Path(cwd or Path.cwd()).expanduser().resolve()
    lines: list[str] = [f"cwd:{base}"]

    def _stat_line(path: Path) -> str:
        try:
            stat = path.stat()
            return f"{path}|{int(stat.st_mtime_ns)}|{int(stat.st_size)}|{'d' if path.is_dir() else 'f'}"
        except OSError:
            return f"{path}|missing"

    lines.append(_stat_line(base))

    def _append_directory_children(directory: Path) -> None:
        overflow_count = 0
        overflow_digest = hashlib.sha256()
        try:
            for index, child in enumerate(sorted(directory.iterdir(), key=lambda item: item.name.lower())):
                stat_line = _stat_line(child)
                if index < max_entries:
                    lines.append(stat_line)
                    continue
                overflow_count += 1
                overflow_digest.update(stat_line.encode("utf-8"))
                overflow_digest.update(b"\n")
        except OSError:
            lines.append(f"{directory}|unreadable")
            return
        if overflow_count:
            lines.append(f"{directory}|overflow|{overflow_count}|{overflow_digest.hexdigest()[:16]}")

    if base.exists() and base.is_dir():
        _append_directory_children(base)

    for raw_target in targets or []:
        candidate = Path(raw_target).expanduser()
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        lines.append(_stat_line(candidate))
        if candidate.exists() and candidate.is_dir():
            _append_directory_children(candidate)

    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return digest[:16]


def extract_prompt_targets(
    prompt_parts: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split `@path` references from prompt parts."""
    base = Path(cwd or Path.cwd()).expanduser()
    clean_parts: list[str] = []
    targets: list[str] = []
    for part in prompt_parts:
        if part.startswith("@") and len(part) > 1:
            target = (base / part[1:]).expanduser()
            targets.append(str(target))
            continue
        clean_parts.append(part)
    return clean_parts, targets


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        sample = path.read_bytes()[:512]
    except OSError:
        return False
    return b"\x00" not in sample


def _render_directory_preview(path: Path, *, max_entries: int = MAX_DIR_ENTRIES) -> str:
    entries: list[str] = []
    if not path.exists():
        return f"- missing directory: {path}"
    try:
        for index, child in enumerate(sorted(path.iterdir(), key=lambda item: item.name.lower())):
            if index >= max_entries:
                entries.append("  ...")
                break
            suffix = "/" if child.is_dir() else ""
            entries.append(f"  - {child.name}{suffix}")
    except OSError as exc:
        return f"- unable to inspect directory {path}: {exc}"
    return f"- directory: {path}\n" + "\n".join(entries)


def _render_file_preview(path: Path, *, max_chars: int = MAX_FILE_PREVIEW_CHARS) -> str:
    if not path.exists():
        return f"- missing file: {path}"
    if not _is_text_file(path):
        return f"- binary or unsupported file: {path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"- unable to read file {path}: {exc}"
    preview = text[:max_chars]
    if len(text) > max_chars:
        preview += "\n...[truncated]..."
    return f"- file: {path}\n```text\n{preview}\n```"


def collect_workspace_context(
    *,
    cwd: str | os.PathLike[str] | None = None,
    targets: list[str] | None = None,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> tuple[list[str], str]:
    """Build a text context bundle from a working directory and explicit targets."""
    base = Path(cwd or Path.cwd()).expanduser().resolve()
    normalized_targets: list[str] = []
    chunks: list[str] = [f"Working directory: {base}"]

    if base.exists():
        chunks.append(_render_directory_preview(base))
    else:
        chunks.append(f"Working directory does not exist: {base}")

    seen: set[str] = set()
    for raw_target in targets or []:
        candidate = Path(raw_target).expanduser()
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        target_str = str(candidate)
        if target_str in seen:
            continue
        seen.add(target_str)
        normalized_targets.append(target_str)
        if candidate.is_dir():
            chunks.append(_render_directory_preview(candidate))
        else:
            chunks.append(_render_file_preview(candidate))

    rendered = "\n\n".join(chunks)
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 15].rstrip() + "\n...[truncated]..."
    return normalized_targets, rendered


def recent_output_context(session_id: str, *, max_chars: int = 4_000) -> str:
    """Return a compact context block from the newest saved output artifacts."""
    outputs_dir = _outputs_dir(session_id)
    if not outputs_dir.exists():
        return ""
    sections: list[str] = []
    remaining = max_chars
    for path in sorted(outputs_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if remaining <= 0 or not path.is_file():
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = text[: min(len(text), remaining, 1_500)]
        section = f"Output: {path.name}\n{snippet}"
        sections.append(section)
        remaining -= len(section)
    return "\n\n".join(sections)
