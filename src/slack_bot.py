"""
OpenClaw Slack Bot — Socket Mode integration.

Listens for @OpenClaw mentions and DMs via Slack Socket Mode (no public URL needed).
Routes messages through OpenClaw's ask orchestrator and replies in thread.

Required environment variables:
  SLACK_BOT_TOKEN    — xoxb-... (Bot User OAuth Token)
  SLACK_APP_TOKEN    — xapp-... (App-Level Token with connections:write scope)
  SLACK_ENABLED      — "true" to enable (default: false)

Setup:
  1. Create app at https://api.slack.com/apps
  2. Enable Socket Mode (Features > Socket Mode)
  3. Add Bot Token Scopes: app_mentions:read, channels:history, chat:write,
     im:history, im:read, im:write, reactions:read
  4. Subscribe to events: app_mention, message.im, reaction_added
  5. Enable slash command: /chat (any Request URL placeholder works in Socket Mode)
  6. Install app to workspace
  7. Copy Bot User OAuth Token (xoxb-...) to SLACK_BOT_TOKEN
  8. Copy App-Level Token (xapp-...) to SLACK_APP_TOKEN

Features:
  - @mention in channels → OpenClaw answer (in-thread)
  - DMs → OpenClaw answer
  - Thread context: follow-up messages in a thread carry prior Q&A as history
  - Model selector: append --model gemini|openai|anthropic|copilot|auto to any prompt
  - /chat slash command: native Slack slash command
  - 👍/👎 feedback: react to any bot response to log a rating

Wiring into src/bot.py (add inside setup_hook or on_ready):
  # Start Slack bot if configured
  if os.getenv("SLACK_ENABLED", "false").lower() == "true":
      try:
          from slack_bot import create_slack_handler
          _slack_handler = await create_slack_handler()
          if _slack_handler:
              asyncio.create_task(_slack_handler.start_async())
              log.info("Slack Socket Mode handler started")
      except Exception as e:
          log.warning("Slack bot failed to start: %s", e)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import aiohttp

import file_skills
from constants import ATTACHMENT_TEXT_MAX_CHARS
from document_skills import create_word
from http_session import SessionManager
from llm import analyze_image as llm_analyze_image
from trace_context import set_trace as _set_trace

log = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_HERMES_SESSION_RE = re.compile(r"^Session:\s*(\S+)", re.MULTILINE)
_HERMES_AUDIT_DIR = Path(__file__).parent.parent / "data" / "audit" / "host_bridge"


class _HermesStreamHandle:
    """Proc-like handle for an in-flight Hermes SSH stream.

    Hermes runs on the host over SSH (via ``host_bridge.run_hermes_stream``), so
    there is no local subprocess to track. This lightweight handle lets the
    existing concurrency guard, liveness checks, and /copilot-cancel / -end
    handlers keep working: ``terminate()``/``kill()`` request cancellation, and
    ``wait()`` resolves once the stream loop has finished.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._cancel_event = asyncio.Event()
        self._done = asyncio.Event()
        # Owner of a one-shot (/q, /resume) turn registered under a synthetic
        # cancel id, so /copilot-cancel can verify ownership before stopping it.
        self.slack_user: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def cancel_event(self) -> asyncio.Event:
        """Event the host bridge watches to hard-interrupt the remote turn."""
        return self._cancel_event

    def terminate(self) -> None:
        self._cancelled = True
        self._cancel_event.set()

    def kill(self) -> None:
        self._cancelled = True
        self._cancel_event.set()

    async def wait(self) -> int:
        await self._done.wait()
        return 0

    def finish(self) -> None:
        self._done.set()


_hermes_live_procs: dict[str, _HermesStreamHandle] = {}
_slack_app_client: Any | None = None


def _is_code_chunk(text: str) -> bool:
    """Return True if text looks like terminal/code output rather than prose."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    code_like = sum(
        1
        for line in lines
        if line.startswith(("$", ">", "    ", "\t", "```"))
        or line.startswith(("│", "┃", "╭", "╰", "├", "─"))
        or line.lstrip().startswith(("//", "#!", "/*"))
    )
    return (code_like / len(lines)) >= 0.35


async def _copilot_chunk_poster(record: Any, chunk: str) -> None:
    if not chunk or not chunk.strip():
        return

    client = _slack_app_client
    if client is None:
        return

    # Check if any line is a tool-progress indicator — post those as lightweight italic updates.
    try:
        from dashboard.api_handlers import _copilot_tool_label  # type: ignore[import]
    except ImportError:
        _copilot_tool_label = None  # type: ignore[assignment]

    lines = chunk.splitlines()
    tool_lines = []
    content_lines = []
    for line in lines:
        if _copilot_tool_label and _copilot_tool_label(line):
            tool_lines.append(_copilot_tool_label(line))
        else:
            content_lines.append(line)

    if tool_lines:
        try:
            await client.chat_postMessage(
                channel=record.slack_channel,
                thread_ts=record.slack_thread_ts,
                text="\n".join(f"_{tool}_" for tool in tool_lines),
                mrkdwn=True,
            )
        except Exception:  # noqa: BLE001
            pass

    body = "\n".join(content_lines).strip()
    if not body:
        return
    if len(body) > 3800:
        body = body[:3800] + "\n…[truncated]"
    try:
        if _is_code_chunk(body):
            text = f"```\n{body}\n```"
        else:
            text = body
        await client.chat_postMessage(
            channel=record.slack_channel,
            thread_ts=record.slack_thread_ts,
            text=text,
            mrkdwn=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("/copilot chunk post failed: %s", exc)


async def _ensure_session_manager() -> Any:
    """Lazy-init the host SessionManager exactly once per process."""
    try:
        from host_bridge import get_session_manager
    except ImportError:
        return None
    mgr = get_session_manager()
    if not getattr(mgr, "_started", False):
        await mgr.start(_copilot_chunk_poster)
    return mgr


def _is_hermes_session(record: Any) -> bool:
    meta = getattr(record, "meta", {}) or {}
    return meta.get("backend") == "hermes"


def _hermes_session_id(record: Any) -> str | None:
    meta = getattr(record, "meta", {}) or {}
    session_id = meta.get("hermes_session_id")
    return str(session_id).strip() if session_id else None


def _extract_hermes_session_id(text: str) -> str | None:
    if not text:
        return None
    match = _HERMES_SESSION_RE.search(text)
    return match.group(1).strip() if match else None


def _session_is_live(mgr: Any, session_id: str) -> bool:
    return session_id in _hermes_live_procs or (mgr is not None and mgr.is_live(session_id))


def _append_copilot_transcript(path: str | None, text: str) -> None:
    if not path or not text:
        return
    try:
        transcript_path = Path(path)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        log.warning("/copilot transcript append failed: %s", exc)


async def _run_hermes_turn(record: Any, prompt: str) -> str | None:
    if record.session_id in _hermes_live_procs:
        return "a Hermes turn is already running for this session"

    mgr = await _ensure_session_manager()
    if mgr is None:
        return "host_bridge module unavailable"

    prior_hermes_session_id = _hermes_session_id(record)
    turn_num = int(getattr(record, "turns", 0)) + 1
    if turn_num > 1 and not prior_hermes_session_id:
        await mgr.registry.update(record.session_id, status="crashed", last_activity=time.time())
        record.status = "crashed"
        record.last_activity = time.time()
        return "Hermes session ID is missing for this thread; start a new /copilot or /hermes session."

    now = time.time()
    await mgr.registry.update(record.session_id, last_activity=now, turns=turn_num, status="active")
    record.turns = turn_num
    record.last_activity = now
    record.status = "active"
    _append_copilot_transcript(
        record.transcript_path,
        f"\n>>> USER TURN {turn_num} ({record.slack_user})\n{prompt}\n",
    )

    # Hermes runs on the host over SSH (the binary is not present inside the
    # container). Stream output back through the host bridge.
    from host_bridge import run_hermes_stream

    handle = _HermesStreamHandle()
    _hermes_live_procs[record.session_id] = handle

    stdout_parts: list[str] = []
    stderr_text = ""
    return_code = 0
    try:
        async for event in run_hermes_stream(
            prompt=prompt,
            slack_user_id=record.slack_user,
            hermes_session_id=prior_hermes_session_id,
            cwd=record.cwd or None,
            cancel_event=handle.cancel_event,
        ):
            if handle.cancelled:
                stderr_text = "cancelled"
                return_code = 1
                break
            etype = event.get("type")
            if etype == "chunk":
                text = event.get("text", "")
                if text:
                    stdout_parts.append(text)
                    _append_copilot_transcript(record.transcript_path, text)
                    await _copilot_chunk_poster(record, text)
            elif etype == "error":
                stderr_text = event.get("error") or "hermes error"
                return_code = 1
            elif etype == "done":
                exit_code = event.get("exit_code")
                if not event.get("success"):
                    return_code = exit_code if isinstance(exit_code, int) and exit_code != 0 else 1
                    if not stderr_text:
                        stderr_text = event.get("error") or f"Hermes exited with status {return_code}"
    except Exception as exc:  # noqa: BLE001
        await mgr.registry.update(record.session_id, status="crashed", last_activity=time.time())
        record.status = "crashed"
        return f"Hermes failed to start: {type(exc).__name__}: {exc}"
    finally:
        handle.finish()
        _hermes_live_procs.pop(record.session_id, None)

    stdout_text = "".join(stdout_parts)

    if stderr_text:
        _append_copilot_transcript(record.transcript_path, f"\n[stderr]\n{stderr_text}\n")

    finished_at = time.time()
    hermes_session_id = prior_hermes_session_id or _extract_hermes_session_id(stdout_text)
    final_status = "idle" if return_code == 0 else "crashed"
    update_fields: dict[str, Any] = {"status": final_status, "last_activity": finished_at}
    if hermes_session_id and hermes_session_id != prior_hermes_session_id:
        meta = dict(getattr(record, "meta", {}) or {})
        meta["backend"] = "hermes"
        meta["hermes_session_id"] = hermes_session_id
        update_fields["meta"] = meta
        record.meta = meta
    elif return_code == 0 and turn_num == 1 and not hermes_session_id:
        final_status = "crashed"
        update_fields["status"] = final_status
    await mgr.registry.update(record.session_id, **update_fields)
    record.status = final_status
    record.last_activity = finished_at

    if return_code != 0:
        return stderr_text.strip() or f"Hermes exited with status {return_code}"
    if not hermes_session_id:
        return "Hermes completed, but did not emit a session ID; thread replies cannot resume."
    if not stdout_text.strip():
        client = _slack_app_client
        if client is not None:
            try:
                await client.chat_postMessage(
                    channel=record.slack_channel,
                    thread_ts=record.slack_thread_ts,
                    text="_Hermes returned no output._",
                )
            except Exception:  # noqa: BLE001
                pass
    return None


async def _send_ntfy(title: str, message: str, priority: str = "default") -> None:
    """Send a push notification via ntfy.sh if configured."""
    import aiohttp

    ntfy_url = os.environ.get("NTFY_URL", "")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    if not ntfy_url or not ntfy_topic:
        return
    try:
        url = f"{ntfy_url.rstrip('/')}/{ntfy_topic}"
        async with aiohttp.ClientSession() as session:
            await session.post(
                url,
                data=message.encode(),
                headers={
                    "Title": title,
                    "Priority": priority,
                },
            )
    except Exception:
        pass


def _decode_overseerr_api_key(raw_key: str) -> str:
    import base64

    try:
        return base64.b64decode((raw_key or "").strip()).decode()
    except Exception:
        return (raw_key or "").strip()


async def _plex_get_text(plex_url: str, path: str) -> tuple[int, str]:
    headers: dict[str, str] = {}
    plex_token = (os.getenv("PLEX_TOKEN", "") or "").strip()
    if plex_token:
        headers["X-Plex-Token"] = plex_token
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(f"{plex_url.rstrip('/')}{path}", headers=headers) as resp:
            return resp.status, await resp.text()


async def _overseerr_request(
    method: str,
    overseerr_url: str,
    api_key: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | list[Any] | str]:
    headers = {"X-Api-Key": api_key}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.request(
            method,
            f"{overseerr_url.rstrip('/')}{path}",
            headers=headers,
            params=params,
            json=payload,
        ) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "json" in content_type:
                return resp.status, await resp.json(content_type=None)
            return resp.status, await resp.text()


async def _openclaw_local_json(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_s: int = 15,
) -> tuple[int, dict[str, Any] | list[Any] | str]:
    base_url = (os.getenv("OPENCLAW_LOCAL_API_BASE", "http://localhost:8765") or "http://localhost:8765").rstrip("/")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
        async with session.request(method, f"{base_url}{path}", params=params, json=payload) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "json" in content_type:
                return resp.status, await resp.json(content_type=None)
            return resp.status, await resp.text()


def _wol_machine_registry() -> dict[str, dict[str, str | None]]:
    machines: dict[str, dict[str, str | None]] = {}
    if os.environ.get("WOL_MACBOOK_PRO_MAC"):
        machines["mbp"] = {
            "label": "MacBook Pro",
            "mac": os.environ["WOL_MACBOOK_PRO_MAC"],
            "ip": "192.168.1.131",
        }
    if os.environ.get("WOL_MACBOOK_PRO2_MAC"):
        machines["mbp2"] = {
            "label": "MacBook Pro 2",
            "mac": os.environ["WOL_MACBOOK_PRO2_MAC"],
            "ip": "192.168.1.136",
        }
    if not machines and os.environ.get("WOL_MACBOOK_MAC"):
        machines["mbp"] = {
            "label": "MacBook",
            "mac": os.environ["WOL_MACBOOK_MAC"],
            "ip": None,
        }
    return machines


def _send_wol_magic_packet(mac: str, broadcast_ip: str) -> None:
    import socket

    mac_clean = re.sub(r"[:\-]", "", mac).upper()
    if len(mac_clean) != 12:
        raise ValueError(f"Invalid MAC address format: {mac}")
    magic = bytes.fromhex("FF" * 6 + mac_clean * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, (broadcast_ip, 9))
        sock.sendto(magic, ("255.255.255.255", 9))


def _parse_plex_xml(xml_text: str) -> Any | None:
    import xml.etree.ElementTree as ET

    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def _overseerr_title(result: dict[str, Any]) -> str:
    return str(result.get("title") or result.get("name") or result.get("originalTitle") or "Untitled").strip()


def _pick_overseerr_result(results: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    query_norm = query.strip().casefold()
    for result in results:
        candidates = [result.get("title"), result.get("name"), result.get("originalTitle")]
        if any(str(candidate).strip().casefold() == query_norm for candidate in candidates if candidate):
            return result
    return results[0] if results else None


async def _plex_status_summary(plex_url: str) -> str:
    try:
        status, body = await _plex_get_text(plex_url, "/identity")
    except Exception as exc:  # noqa: BLE001
        return f"❌ Could not reach Plex at `{plex_url}`: {exc}"

    if status != 200:
        return f"⚠️ Plex responded with HTTP {status} from `{plex_url}`."

    root = _parse_plex_xml(body)
    if root is None:
        return f"✅ Plex responded from `{plex_url}`, but the identity payload could not be parsed."

    version = root.attrib.get("version") or "unknown"
    machine_id = root.attrib.get("machineIdentifier") or "unknown"
    claimed = "claimed" if root.attrib.get("claimed") == "1" else "not claimed"
    return (
        "✅ *Plex server is online*\n"
        f"• URL: `{plex_url}`\n"
        f"• Version: `{version}`\n"
        f"• Machine ID: `{machine_id}`\n"
        f"• State: `{claimed}`"
    )


async def _plex_recent_summary(plex_url: str) -> str:
    try:
        sessions_status, sessions_body = await _plex_get_text(plex_url, "/status/sessions")
    except Exception as exc:  # noqa: BLE001
        return f"❌ Could not fetch Plex sessions from `{plex_url}`: {exc}"

    if sessions_status == 200:
        root = _parse_plex_xml(sessions_body)
        if root is not None:
            active_items: list[str] = []
            for video in root.findall("Video")[:5]:
                title = video.attrib.get("title") or "Untitled"
                if video.attrib.get("type") == "episode" and video.attrib.get("grandparentTitle"):
                    title = f"{video.attrib.get('grandparentTitle')} — {title}"
                user = "Unknown"
                user_el = video.find("User")
                if user_el is not None:
                    user = user_el.attrib.get("title") or user_el.attrib.get("name") or user
                active_items.append(f"• {title} — {user}")
            if active_items:
                return "📺 *Active Plex sessions*\n" + "\n".join(active_items)

    try:
        recent_status, recent_body = await _plex_get_text(
            plex_url,
            "/library/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size=5",
        )
    except Exception as exc:  # noqa: BLE001
        return f"❌ Could not fetch Plex recent additions from `{plex_url}`: {exc}"

    if recent_status != 200:
        return (
            "⚠️ Could not fetch Plex recent activity. "
            f"Sessions HTTP {sessions_status}; recentlyAdded HTTP {recent_status}."
        )

    root = _parse_plex_xml(recent_body)
    if root is None:
        return "⚠️ Plex returned recent activity, but the XML payload could not be parsed."

    items: list[str] = []
    for media in list(root)[:5]:
        title = media.attrib.get("title") or media.attrib.get("grandparentTitle") or "Untitled"
        if media.attrib.get("type") == "episode" and media.attrib.get("grandparentTitle") and media.attrib.get("title"):
            title = f"{media.attrib.get('grandparentTitle')} — {media.attrib.get('title')}"
        year = (media.attrib.get("year") or "").strip()
        suffix = f" ({year})" if year else ""
        items.append(f"• {title}{suffix}")

    if not items:
        return "ℹ️ Plex is online, but there are no active sessions or recent additions to show."

    return "🆕 *Recent Plex additions*\n" + "\n".join(items)


# ---------------------------------------------------------------------------
# Per-user preferences (persisted to data/slack_user_prefs.json)
# ---------------------------------------------------------------------------
# Stored as: {"U123ABC": {"simple": true}, ...}
# Loaded once at startup; written on every change.
# ---------------------------------------------------------------------------

_PREFS_PATH: Path = Path(__file__).parent.parent / "data" / "slack_user_prefs.json"
_user_prefs: dict[str, dict] = {}

_PERSONAS_PATH: Path = Path(__file__).parent.parent / "data" / "slack_user_personas.json"
_personas: dict[str, dict] = {}


def _load_prefs() -> None:
    global _user_prefs
    try:
        if _PREFS_PATH.exists():
            _user_prefs = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load user prefs from %s: %s", _PREFS_PATH, exc)
        _user_prefs = {}


def _save_prefs() -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(_user_prefs, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save user prefs to %s: %s", _PREFS_PATH, exc)


def _get_user_simple(user_id: str) -> bool:
    """Return True if the user has enabled persistent simple mode."""
    return bool((_user_prefs.get(user_id) or {}).get("simple", False))


def _set_user_simple(user_id: str, value: bool) -> None:
    """Set (or clear) persistent simple mode for *user_id* and write to disk."""
    if user_id not in _user_prefs:
        _user_prefs[user_id] = {}
    _user_prefs[user_id]["simple"] = value
    _save_prefs()


def _load_personas() -> None:
    global _personas
    try:
        if _PERSONAS_PATH.exists():
            _personas = json.loads(_PERSONAS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load personas from %s: %s", _PERSONAS_PATH, exc)
        _personas = {}


def _save_personas() -> None:
    try:
        _PERSONAS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSONAS_PATH.write_text(json.dumps(_personas, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save personas to %s: %s", _PERSONAS_PATH, exc)


async def _get_user_name(user_id: str, client: Any) -> str:
    """Return the user's preferred display name, resolving from Slack API if needed."""
    stored = (_personas.get(user_id) or {}).get("name")
    if stored:
        return stored
    try:
        result = await client.users_info(user=user_id)
        profile = (result.get("user") or {}).get("profile") or {}
        name = profile.get("display_name") or profile.get("real_name") or ""
        name = name.strip().split()[0] if name.strip() else ""  # first name only
        if name:
            if user_id not in _personas:
                _personas[user_id] = {}
            _personas[user_id]["name"] = name
            _save_personas()
            return name
    except Exception:
        pass
    return "there"


# Load prefs and personas at import time so they are ready before any handler fires.
_load_prefs()
_load_personas()

# ---------------------------------------------------------------------------
# Per-user email credentials (IMAP / Gmail App Password)
# ---------------------------------------------------------------------------
# Stored as: {"U123ABC": {"user": "chuck@gmail.com", "password": "xxxx xxxx xxxx xxxx"}}
# Allows each Slack user to connect their own Gmail. Never committed — local data/ only.
# ---------------------------------------------------------------------------

_USER_EMAIL_CREDS_PATH: Path = Path(__file__).parent.parent / "data" / "user_email_creds.json"
_user_email_creds: dict[str, dict] = {}


def _load_user_email_creds() -> None:
    global _user_email_creds
    try:
        if _USER_EMAIL_CREDS_PATH.exists():
            _user_email_creds = json.loads(_USER_EMAIL_CREDS_PATH.read_text(encoding="utf-8"))
        else:
            _user_email_creds = {}
    except Exception as exc:
        log.warning("Failed to load user email creds: %s", exc)
        _user_email_creds = {}


def _save_user_email_creds() -> None:
    try:
        _USER_EMAIL_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USER_EMAIL_CREDS_PATH.write_text(json.dumps(_user_email_creds, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save user email creds: %s", exc)


# ---------------------------------------------------------------------------
# Per-user Dropbox tokens
# ---------------------------------------------------------------------------
# Stored as: {"U123ABC": {"token": "sl.xxx", "watch_path": "/OpenClaw"}}
# ---------------------------------------------------------------------------

_USER_DROPBOX_PATH: Path = Path(__file__).parent.parent / "data" / "user_dropbox_tokens.json"
_user_dropbox_tokens: dict[str, dict] = {}


def _load_user_dropbox_tokens() -> None:
    global _user_dropbox_tokens
    try:
        if _USER_DROPBOX_PATH.exists():
            _user_dropbox_tokens = json.loads(_USER_DROPBOX_PATH.read_text(encoding="utf-8"))
        else:
            _user_dropbox_tokens = {}
    except Exception as exc:
        log.warning("Failed to load user Dropbox tokens: %s", exc)
        _user_dropbox_tokens = {}


def _save_user_dropbox_tokens() -> None:
    try:
        _USER_DROPBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USER_DROPBOX_PATH.write_text(json.dumps(_user_dropbox_tokens, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save user Dropbox tokens: %s", exc)


_load_user_email_creds()
_load_user_dropbox_tokens()

# ---------------------------------------------------------------------------
# Per-user file history (persisted to data/slack_file_history.json)
# ---------------------------------------------------------------------------
# Stored as: {"U123": [{"name": "doc.docx", "size": 1234, "sha256": "...",
#   "last_used_ts": 1234567890.0, "mimetype": "..."}, ...]}
# Newest entry first; capped at _FILE_HISTORY_MAX per user.
# ---------------------------------------------------------------------------

_FILE_HISTORY_PATH: Path = Path(__file__).parent.parent / "data" / "slack_file_history.json"
_file_history: dict[str, list[dict]] = {}
_FILE_HISTORY_MAX = 20


def _load_file_history() -> None:
    global _file_history
    try:
        if _FILE_HISTORY_PATH.exists():
            _file_history = json.loads(_FILE_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load file history: %s", exc)
        _file_history = {}


def _save_file_history() -> None:
    try:
        _FILE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FILE_HISTORY_PATH.write_text(json.dumps(_file_history, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save file history: %s", exc)


def _record_file_history(user_id: str, file_obj: dict, file_bytes: bytes | None = None) -> None:
    """Add or update a file in the user's history. Keeps newest _FILE_HISTORY_MAX."""
    import hashlib
    import time as _time

    name = file_obj.get("name", "")
    size = file_obj.get("size", 0)
    sha256 = ""
    if file_bytes:
        sha256 = hashlib.sha256(file_bytes).hexdigest()[:16]
    entry = {
        "name": name,
        "size": size,
        "sha256": sha256,
        "last_used_ts": _time.time(),
        "mimetype": file_obj.get("mimetype", ""),
    }
    history = _file_history.setdefault(user_id, [])
    history[:] = [h for h in history if h.get("name") != name]
    history.insert(0, entry)
    history[:] = history[:_FILE_HISTORY_MAX]
    _save_file_history()


_load_file_history()

# ---------------------------------------------------------------------------
# Onboarding state
# ---------------------------------------------------------------------------
_onboarded_users: set[str] = set()  # users who have received onboarding or used the bot


def _match_question_to_history(user_id: str, question: str) -> dict | None:
    """Return the best-matching file from history for this question, or None.

    Matches on filename keywords (minus extension). Returns the most recently
    used match if any keyword appears in the question text (case-insensitive).
    """
    history = _file_history.get(user_id, [])
    if not history:
        return None
    question_lower = question.lower()
    for entry in history:  # history is newest-first
        name = entry.get("name", "")
        stem = Path(name).stem.lower().replace("_", " ").replace("-", " ")
        keywords = [w for w in stem.split() if len(w) > 3]
        if any(kw in question_lower for kw in keywords):
            return entry
    return None


async def _check_new_user_onboarding(user_id: str, client: Any) -> None:
    """Send a welcome DM to a new user after a delay if they haven't interacted."""
    delay = int(os.environ.get("OPENCLAW_ONBOARDING_DELAY_SECS", "60"))
    if user_id in _onboarded_users:
        return
    _onboarded_users.add(user_id)
    await asyncio.sleep(delay)
    # After delay, check if they've actually used the bot (prefs exist or history exists)
    if _user_prefs.get(user_id) or _file_history.get(user_id):
        return  # they've already used it, skip
    name = await _get_user_name(user_id, client)
    try:
        await client.chat_postMessage(
            channel=user_id,
            text=f"Hi {name}! " + _WELCOME_MESSAGE,
        )
        log.info("Sent onboarding DM to new user %s", user_id)
    except Exception as exc:
        log.debug("Onboarding DM failed for %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# File attachment support
# NOTE: The Slack app requires the `files:read` Bot Token Scope to download
# private file URLs. Add files:read in the Slack app manifest if not present.
# ---------------------------------------------------------------------------

_slack_dl_sessions = SessionManager(timeout=30, name="slack_attachments")


# ---------------------------------------------------------------------------

_RE_RECOVERY_BLOCKQUOTE = re.compile(
    r"\n{1,2}> ℹ️ \*\*Recovery note:\*\*\n(?:> [^\n]*\n?)*",
    re.MULTILINE,
)
_RE_RECOVERY_PLAIN = re.compile(
    r"\n{1,2}[ℹ️:information_source:]\s*\*?\*?Recovery note\*?\*?:?[^\n]*\n(?:[^\n]*\n?){0,6}",
    re.MULTILINE,
)
_RE_VIA_TRAILER = re.compile(r"\n_via [^\n]+_[ \t]*(?=\n|$)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_RE_H = re.compile(r"^#{1,3} (.+)$", re.MULTILINE)
_RE_HR = re.compile(r"^[-─*]{3,}$", re.MULTILINE)


def _clean_for_slack(text: str) -> str:
    """Strip recovery notes / CLI footers and convert markdown to Slack mrkdwn."""
    # Remove recovery note blocks (blockquote and plain variants)
    text = _RE_RECOVERY_BLOCKQUOTE.sub("", text)
    text = _RE_RECOVERY_PLAIN.sub("", text)

    # Strip _via model_ attribution line
    text = _RE_VIA_TRAILER.sub("", text)

    # Convert markdown links [text](url) → <url|text>
    text = _RE_MD_LINK.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)

    # Convert **bold** → *bold* (Slack mrkdwn)
    text = _RE_BOLD.sub(lambda m: f"*{m.group(1)}*", text)

    # Demote ATX headers (#, ##, ###) to bold lines
    text = _RE_H.sub(lambda m: f"*{m.group(1)}*", text)

    # Remove horizontal rules
    text = _RE_HR.sub("", text)

    return text.strip()


def _suggest_actions_for_file(filename: str, mimetype: str) -> str:
    """Return friendly action suggestions based on file type."""
    name_lower = filename.lower()
    mime_lower = mimetype.lower()

    if name_lower.endswith((".docx", ".doc")) or "word" in mime_lower or "wordprocessingml" in mime_lower:
        return (
            f"📄 I see you uploaded *{filename}*. What would you like?\n"
            "• Proofread and fix grammar\n"
            "• Make it more professional / formal\n"
            "• Summarize in bullet points\n"
            "• Rewrite a specific section\n\n"
            "_Just reply and tell me what you need!_"
        )
    if name_lower.endswith((".xlsx", ".xls", ".csv")) or "spreadsheet" in mime_lower or "excel" in mime_lower:
        return (
            f"📊 I see you uploaded *{filename}*. What would you like?\n"
            "• Summarize what this is tracking\n"
            "• Explain any formulas or columns\n"
            "• Find errors or unusual values\n"
            "• Create a summary paragraph\n\n"
            "_Just reply and tell me what you need!_"
        )
    if name_lower.endswith(".pdf") or mime_lower == "application/pdf":
        return (
            f"📑 I see you uploaded *{filename}*. What would you like?\n"
            "• Summarize the key points\n"
            "• Extract action items\n"
            "• Answer a specific question about it\n\n"
            "_Just reply and tell me what you need!_"
        )
    if mime_lower.startswith("image/"):
        return (
            f"🖼️ I see you uploaded *{filename}*. What would you like?\n"
            "• Describe what's in the image\n"
            "• Read any text visible in the photo\n"
            "• Answer a question about it\n\n"
            "_Just reply and tell me what you need!_"
        )
    return (
        f"📎 I see you uploaded *{filename}*. What would you like me to do with it?\n"
        "_Just tell me and I'll get started!_"
    )


# ------------------------------------------------------------------
# Model selector  --model <alias>
# ------------------------------------------------------------------
_MODEL_FLAG_RE = re.compile(r"--model\s+(\S+)", re.IGNORECASE)
_MODEL_ALIASES: dict[str, str] = {
    "auto": "auto",
    "gemini": "gemini",
    "openai": "openai",
    "gpt": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "copilot": "copilot",
}

_WELCOME_MESSAGE = (
    "👋 *Hi! I'm OpenClaw — your personal AI assistant, right here in Slack.*\n\n"
    "Here are some things you can ask me right now:\n"
    '• "What should I make for dinner tonight with chicken and vegetables?" 🍳\n'
    '• "Help me write a message to my doctor about rescheduling my appointment" ✍️\n'
    '• "What does this letter say?" *(then drop in a photo or PDF)* 📄\n'
    '• "Look at this spreadsheet and tell me what it shows" *(drop in an Excel file)* 📊\n'
    '• "What\'s a good birthday gift for someone who likes gardening?" 🎁\n'
    '• "Explain this in plain English" *(paste in any confusing text)* 🤔\n\n'
    "*You don't need any special commands — just talk to me like you'd talk to a friend!*\n"
    "Type `/help` anytime to see more examples."
)

_HELP_TEXT = """
*⚕ OpenClaw Commands*

*🤖 AI & Chat*
• `/chat <prompt>` — Ask OpenClaw a question in Slack
• `/hermes <prompt>` — Start a Hermes session
• `/h <prompt>` — Alias for `/hermes`
• `/q <prompt>` — Quick ephemeral Hermes answer
• `/resume [prompt]` — Resume your last Hermes session
• `/sessions [n|resume n]` — List, inspect, or resume Hermes sessions
• `/copilot <prompt>` — Start a threaded Copilot CLI session
• `/copilot-sessions` — List Copilot sessions
• `/copilot-attach <id>` — Attach to a Copilot session
• `/copilot-recap <id>` — Recap a Copilot session
• `/copilot-cancel <id>` — Cancel an active Copilot run
• `/copilot-end <id>` — End a Copilot session
• `/research <topic>` — Run the research pipeline
• `/simple on|off` — Toggle plain-language mode
• `/clear` — Reset your current session state
• `/nickname <name>` — Set the name OpenClaw uses for you

*📁 Files & Knowledge*
• `/files [query]` — Browse synced documents
• `/filesearch <query>` — Search your recent files
• `/batch <action>` — Process all uploaded files
• `/brief` — Show your recent files
• `/template [name]` — List or download templates
• `/mypins` — Show your saved notes
• `/metrics` — View 7-day workspace usage metrics
• `/mystats` — View your personal usage stats
• `/digest on|off` — Toggle digest delivery
• `/schedule <time|off>` — Set your preferred digest time

*📬 Google & Integrations*
• `/inbox` — List unread Gmail messages
• `/email <n|setup|forget|query>` — Read or search email
• `/today` — Show today’s calendar
• `/calendar [today|week]` — View upcoming calendar events
• `/drive ...` — Search or read Google Drive files
• `/contacts ...` — Search Google Contacts
• `/clawbox <connect|sync|list|status>` — Manage Dropbox access
• `/clawchan ...` — List or archive Slack channels

*🖥️ Host & Ops*
• `/status` — Quick system health snapshot
• `/uptime` — Show grouped Uptime Kuma service status
• `/health` — Detailed OpenClaw bot health card
• `/host <shortcut>` — Run a saved host-bridge shortcut
• `/incident ...` — Start, inspect, or resolve incidents
• `/wake mbp|mbp2` — Send a Wake-on-LAN packet
• `/tailscale` — Show current Tailscale device status
• `/nas df|ls <path>|free` — Browse NAS status and folders
• `/nas-share <path>` — Generate a NAS share link

*🎬 Media*
• `/watching` — See what Plex is playing right now
• `/media` — Active Plex streams, recent plays, recently added
• `/plex <status|recent|search|request>` — Check Plex / Overseerr
• `/request <title>` — Search Overseerr and request media
• `/arr` — View Sonarr/Radarr/Lidarr queues
• `/downloads` — View SABnzbd downloads
• `/qbt` — qBittorrent active torrents, speeds, free space
• `/upcoming` — Show Sonarr episodes airing soon

*⚙️ Utilities*
• `/morning` — Trigger your morning briefing DM
• `/news [topic]` — Show top headlines or search a topic
• `/notify <message>` — Send a push notification to your phone (prefix `high:` for urgent)
• `/adguard` — DNS stats: queries, block rate, top blocked domains
• `/grafana` — Grafana health status and dashboard links
• `/help` — Show this command list

_Tip: Most slash command replies are ephemeral, so only you can see them._
"""

_SIMPLE_FLAG_RE = re.compile(r"\s*--simple\b", re.IGNORECASE)
_SIMPLE_SYSTEM_PREFIX = (
    "Please respond in plain, simple language. Avoid jargon and technical terms. "
    "Use short sentences. Write as if explaining to someone who is not technical. "
)

_BROWSER_NAV_PATTERNS = re.compile(
    r"""(?ix)
    \b(?:go\s+to|navigate\s+to|visit|open|browse\s+to|
        extract\s+from|get\s+content\s+from|read\s+page\s+at|
        fetch\s+page\s+at|scrape)\s+
    (https?://[^\s>]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SCREENSHOT_PATTERNS = re.compile(
    r"""(?ix)
    (?:take\s+a?\s*|grab\s+a?\s*|capture\s+a?\s*|get\s+a?\s*)?
    screenshot\s+(?:of\s+|from\s+)?
    (https?://\S+|\S+\.\S+)
    |
    (?:screenshot|capture)\s+this\s+(?:page|site|url|website)?:?\s*
    (https?://\S+|\S+\.\S+)
    |
    show\s+me\s+(?:what\s+)?
    (https?://\S+|\S+\.\S+)
    \s*(?:looks?\s+like|appearance|screenshot)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ------------------------------------------------------------------
# Bot message registry for 👍/👎 feedback
# key: (channel, message_ts)  value: originating user_id
# ------------------------------------------------------------------
_bot_message_registry: dict[tuple[str, str], str] = {}

# Wave 8: retry cache for error recovery UX
_retry_cache: dict[str, str] = {}
_RETRY_CACHE_MAX: int = 50

# Populated once after the Slack client performs auth.test
_BOT_USER_ID: str = ""

# ---------------------------------------------------------------------------
# Observability — wave 4 tracking vars
# ---------------------------------------------------------------------------
_BOT_START_TIME: float = 0.0
_model_last_success: dict[str, float] = {}
_daily_query_count: int = 0
_error_window: list[float] = []
_last_alert_ts: float = 0.0
_ws_connection_count: int = 0  # incremented on every Socket Mode open; >1 means reconnect
_ws_last_reconnect_notify_ts: float = 0.0  # epoch of last reconnect DM (cooldown gate)
_WS_RECONNECT_NOTIFY_COOLDOWN = 3600.0  # seconds — suppress routine Slack 408 reconnect noise

# ---------------------------------------------------------------------------
# Feature flag check
# ---------------------------------------------------------------------------

SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
# Wave 14: User token for channel management (xoxp-...) — optional
SLACK_USER_TOKEN = os.getenv("SLACK_USER_TOKEN", "")

# Wave 4: upload endpoint + proactive alerts
OPENCLAW_UPLOAD_KEY = os.getenv("OPENCLAW_UPLOAD_KEY", "")
OPENCLAW_UPLOAD_PORT = int(os.getenv("OPENCLAW_UPLOAD_PORT", "8080"))
SLACK_NOTIFY_USER_ID = os.getenv("SLACK_NOTIFY_USER_ID", "")
_slack_client_ref: Any = None  # set at startup for use in upload handler


def _on_model_fallback(provider: str, failures: int) -> None:
    """Synchronous hook called by llm.providers when a circuit opens."""
    try:
        if not _slack_client_ref or not SLACK_NOTIFY_USER_ID:
            return
        loop = asyncio.get_event_loop()
        if loop.is_running():
            msg = (
                f"⚠️ Model fallback: *{provider}* circuit opened after {failures} failures. "
                "Routing to next provider in chain."
            )
            asyncio.ensure_future(_slack_client_ref.chat_postMessage(channel=SLACK_NOTIFY_USER_ID, text=msg))
            asyncio.ensure_future(
                _send_ntfy("⚠️ Model Fallback", f"Provider {provider} circuit opened", priority="high")
            )
    except Exception:
        pass


_AI_FILES_DIR = Path(os.getenv("AI_FILES_DIR", "/ai-files"))
_KNOWN_FILES_PATH = Path(__file__).parent.parent / "data" / "known_files.json"
_LAST_SYNC_PATH = Path(__file__).parent.parent / "data" / "last_sync.json"
_FILE_POLL_INTERVAL = int(os.getenv("OPENCLAW_FILE_POLL_INTERVAL", "60"))

# --- Wave 5: digest ---
# Use /tmp (bind-mounted rw from host) because /app/data is read-only at runtime.
_DIGEST_PREFS_PATH = Path("/tmp/digest_prefs.json")
_DIGEST_CHECK_INTERVAL: int = int(os.getenv("DIGEST_CHECK_INTERVAL", "3600"))  # check every hour
_DIGEST_LOOKBACK_HOURS: int = int(os.getenv("DIGEST_LOOKBACK_HOURS", "24"))  # show files modified in last N hours

# --- Missed-message catch-up ---
# Written to /tmp (bind-mounted rw from host) so it survives container restarts.
_LAST_SEEN_PATH = Path("/tmp/slack_last_seen.json")
_last_seen_ts: dict[str, str] = {}  # channel_id → last processed message ts

# --- /incident: incident-copilot bridge (mirrors Discord IncidentCog) ---
# Allowed users defaults to the proactive-alert owner when not explicitly set.
OPENCLAW_INCIDENT_ALLOWED_USERS = os.getenv("OPENCLAW_INCIDENT_ALLOWED_USERS", "")
# Cached action list per incident id; tuple of (created_at_monotonic, actions).
# In-memory only — incidents themselves are durable in incident_store SQLite.
_incident_actions_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_INCIDENT_CACHE_TTL_S = int(os.getenv("OPENCLAW_INCIDENT_CACHE_TTL_S", "1800"))
_INCIDENT_REPORT_TIMEOUT_S = int(os.getenv("OPENCLAW_INCIDENT_REPORT_TIMEOUT_S", "45"))


def _incident_allowed_user_ids() -> set[str]:
    """Resolve the set of Slack user IDs allowed to invoke /incident.

    Falls back to SLACK_NOTIFY_USER_ID (the proactive-alert owner) when
    OPENCLAW_INCIDENT_ALLOWED_USERS is unset. Returns empty set when neither
    is configured — handler will refuse all callers in that case.
    """
    raw = OPENCLAW_INCIDENT_ALLOWED_USERS or SLACK_NOTIFY_USER_ID
    return {part.strip() for part in raw.split(",") if part.strip()}


def _incident_cache_put(incident_id: int, actions: list[dict[str, Any]]) -> None:
    _incident_actions_cache[incident_id] = (time.monotonic(), list(actions))
    # Opportunistic cleanup so the dict can't grow unboundedly across long uptimes.
    cutoff = time.monotonic() - _INCIDENT_CACHE_TTL_S
    stale = [k for k, (ts, _) in _incident_actions_cache.items() if ts < cutoff]
    for k in stale:
        _incident_actions_cache.pop(k, None)


def _incident_cache_get(incident_id: int) -> list[dict[str, Any]] | None:
    entry = _incident_actions_cache.get(incident_id)
    if not entry:
        return None
    ts, actions = entry
    if time.monotonic() - ts > _INCIDENT_CACHE_TTL_S:
        _incident_actions_cache.pop(incident_id, None)
        return None
    return actions


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _incident_action_blocks(
    incident_id: int,
    summary: str,
    causes: list[str],
    actions: list[dict[str, Any]],
    model_used: str,
    error: str | None = None,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit blocks for an incident Copilot report."""
    blocks: list[dict[str, Any]] = []
    header_lines = [f"🚨 *Incident #{incident_id}* — Copilot report (model: `{model_used}`)"]
    if error:
        header_lines.append(f"⚠️ _{error}_")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(header_lines)}})
    if summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary[:2800]}"}})
    if causes:
        cause_text = "\n".join(f"• {c}" for c in causes[:5])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Suspected causes:*\n{cause_text}"}})
    if actions:
        action_lines = []
        buttons: list[dict[str, Any]] = []
        for idx, action in enumerate(actions[:3]):
            title = str(action.get("title", "Action"))[:120]
            desc = str(action.get("description", ""))[:200]
            risk = str(action.get("risk_level", "high")).upper()
            executable = bool(action.get("executable"))
            tag = "🟢 executable" if executable else "ℹ️ recommendation"
            action_lines.append(f"*{idx + 1}. {title}* — `{risk}` {tag}\n{desc}")
            if executable:
                buttons.append(
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"Run #{idx + 1}: {title[:60]}"},
                        "style": "primary" if risk in {"LOW", "MEDIUM"} else "danger",
                        "action_id": "incident_action_run",
                        "value": json.dumps({"id": incident_id, "idx": idx}),
                    }
                )
        if action_lines:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Proposed actions:*\n\n" + "\n\n".join(action_lines)},
                }
            )
        if buttons:
            blocks.append({"type": "actions", "elements": buttons})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"`/incident status {incident_id}` · `/incident timeline {incident_id}` · `/incident resolve {incident_id}`",
                }
            ],
        }
    )
    return blocks


async def _run_incident_start(*, client: Any, channel_id: str, user_id: str, title: str) -> None:
    """Create an incident, run the Copilot report, and post action buttons.

    Shared between the /incident slash command and tests. Returns nothing;
    user feedback is delivered via Slack messages.
    """
    from incident_copilot import generate_incident_report  # lazy
    from incident_workflows import incident_store  # lazy

    try:
        incident = incident_store.create_incident(
            title=title[:200],
            severity="high",
            description="",
            channel_id=None,
            channel_name=channel_id,
            thread_id=None,
            thread_name=None,
            created_by=None,
            created_by_name=user_id,
        )
    except Exception as exc:  # broad: surface to user
        log.warning("/incident start: create_incident failed: %s", exc)
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Failed to create incident: `{exc}`")
        return

    incident_id = int(incident.get("id", 0))
    stub_ts: str | None = None
    try:
        stub = await client.chat_postMessage(
            channel=channel_id,
            text=f"🚨 Incident #{incident_id} started — gathering telemetry and drafting Copilot actions…",
        )
        stub_ts = (stub or {}).get("ts")
    except Exception as exc:  # broad: stub failure must not abort the report
        log.warning("/incident start: stub chat_postMessage failed: %s", exc)

    error: str | None = None
    summary = ""
    causes: list[str] = []
    actions: list[dict[str, Any]] = []
    model_used = "unknown"
    try:
        report = await asyncio.wait_for(
            generate_incident_report(incident, requested_services=""),
            timeout=_INCIDENT_REPORT_TIMEOUT_S,
        )
        summary = str(report.get("summary", "")).strip()
        causes = [str(item) for item in report.get("suspected_causes", []) if str(item).strip()]
        actions = [item for item in report.get("actions", []) if isinstance(item, dict)]
        model_used = str(report.get("model_used", "unknown"))[:80]
    except asyncio.TimeoutError:
        error = "Incident Copilot timed out gathering telemetry."
        model_used = "timeout"
        log.warning("Incident Copilot timed out for incident #%s", incident_id)
    except Exception as exc:  # broad: intentional
        error = f"Incident Copilot was unavailable: {exc}"
        model_used = "error"
        log.warning("Incident Copilot failed for incident #%s: %s", incident_id, exc)

    # Persist Copilot output to the incident timeline (parity with Discord cog).
    try:
        incident_store.append_event(
            incident_id,
            event_type="copilot_summary",
            note=json.dumps({"summary": summary, "causes": causes, "model": model_used}),
            actor_id=None,
            actor_name=user_id,
        )
        incident_store.append_event(
            incident_id,
            event_type="copilot_actions",
            note=json.dumps(
                {
                    "actions": [
                        {k: a.get(k) for k in ("title", "command", "target", "risk_level", "executable")}
                        for a in actions[:3]
                    ]
                }
            ),
            actor_id=None,
            actor_name=user_id,
        )
    except Exception as exc:  # broad: telemetry failure must not break user flow
        log.warning("/incident start: append_event failed: %s", exc)

    _incident_cache_put(incident_id, actions)
    blocks = _incident_action_blocks(incident_id, summary, causes, actions, model_used, error=error)
    final_text = f"🚨 Incident #{incident_id}: {summary[:120] or title[:120]}"
    try:
        if stub_ts:
            await client.chat_update(channel=channel_id, ts=stub_ts, text=final_text, blocks=blocks)
        else:
            await client.chat_postMessage(channel=channel_id, text=final_text, blocks=blocks)
    except Exception as exc:  # broad: fall back to a plain post
        log.warning("/incident start: chat_update failed (%s); posting fresh message", exc)
        await client.chat_postMessage(channel=channel_id, text=final_text, blocks=blocks)


def _parse_action_button_value(raw: str) -> tuple[int, int] | None:
    """Parse the JSON `value` on an incident action button into (incident_id, idx)."""
    try:
        payload = json.loads(raw or "{}")
        return int(payload["id"]), int(payload["idx"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


_CASUAL_TIPS = [
    "\n\n💡 _Tip: You can just talk to me naturally — no commands needed!_",
    "\n\n💡 _Tip: Drop a photo or file into Slack anytime and ask me about it._",
    '\n\n💡 _Tip: Not happy with my answer? Just say "try again" or "make it shorter"._',
    "\n\n💡 _Tip: You can ask me anything — cooking, writing, explaining something — just like texting a friend._",
    "\n\n💡 _Tip: Type `/help` to see example questions you can ask me._",
]

_MONTHLY_TIP_MESSAGE = (
    "👋 *Monthly tip from OpenClaw!*\n\n"
    "Here are 3 things you might not have tried yet:\n\n"
    "1️⃣ *Drop in a photo of any document* — a letter, receipt, or handwritten note — and ask me to read it.\n"
    "2️⃣ *Ask me to write something for you* — a thank-you note, a message to a teacher, anything at all.\n"
    "3️⃣ *Reply in a thread* to keep our conversation going — I'll remember what we were talking about.\n\n"
    "_You'll get one of these friendly reminders once a month. Just ignore it if you already knew all this! 😊_"
)

_MONTHLY_TIP_INTERVAL: int = 30 * 24 * 3600  # 30 days in seconds


def _maybe_append_tip() -> str:
    """Return a casual tip string 1-in-12 calls, otherwise empty string."""
    if random.randint(1, 12) == 1:
        return random.choice(_CASUAL_TIPS)
    return ""


# --- Wave 5: templates ---
_DATA_DIR = Path(__file__).parent.parent / "data"
_TEMPLATES_DIR = _DATA_DIR / "templates"

# --- Wave 10: Dropbox sync ---
_DROPBOX_TOKEN: str | None = os.getenv("DROPBOX_APP_TOKEN")
_DROPBOX_FOLDER: str = os.getenv("DROPBOX_WATCH_FOLDER", "/Family AI")
_DROPBOX_NOTIFY_CHANNEL: str | None = os.getenv("DROPBOX_NOTIFY_CHANNEL")
_DROPBOX_CACHE_DIR: Path = _DATA_DIR / "dropbox_cache"
_DROPBOX_CURSOR_PATH: Path = _DATA_DIR / "dropbox_cursor.json"
_DROPBOX_VIRTUAL_USER: str = "dropbox_sync"

# --- Wave 12: Dropbox OAuth2 (per-user one-click connect) ---
_DROPBOX_APP_KEY: str = os.getenv("DROPBOX_APP_KEY", "")
_DROPBOX_APP_SECRET: str = os.getenv("DROPBOX_APP_SECRET", "")
# Public URL of this server (e.g. http://192.168.1.100:8080) — used as OAuth2 redirect base.
_OPENCLAW_PUBLIC_URL: str = os.getenv("OPENCLAW_PUBLIC_URL", "").rstrip("/")
# In-memory map of state_token → slack_user_id (ephemeral CSRF protection)
_dropbox_oauth_states: dict[str, str] = {}

# --- Wave 10 Yoda: Google Calendar OAuth ---
_GOOGLE_CLIENT_ID: str | None = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
_GOOGLE_CLIENT_SECRET: str | None = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
_GOOGLE_REFRESH_TOKEN: str | None = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
_google_token_cache: dict[str, Any] = {}  # {access_token, expires_at}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Wave 4: /upload HTTP endpoint helpers
# ---------------------------------------------------------------------------

_ALLOWED_UPLOAD_EXTENSIONS = {".docx", ".xlsx", ".pdf", ".txt", ".csv"}


async def _handle_upload(request: "aiohttp.web.Request") -> "aiohttp.web.Response":
    """Handle POST /upload — accept a file and write it to /ai-files/."""
    from aiohttp import web

    # Authenticate via shared secret header
    provided_key = request.headers.get("X-OpenClaw-Key", "")
    if OPENCLAW_UPLOAD_KEY and provided_key != OPENCLAW_UPLOAD_KEY:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        reader = await request.multipart()
    except Exception:
        return web.json_response({"error": "Expected multipart/form-data"}, status=400)

    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "Missing 'file' field"}, status=400)

    filename = field.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return web.json_response(
            {"error": f"Extension '{ext}' not allowed. Supported: {sorted(_ALLOWED_UPLOAD_EXTENSIONS)}"},
            status=415,
        )

    _AI_FILES_DIR.mkdir(parents=True, exist_ok=True)
    dest = _AI_FILES_DIR / Path(filename).name
    data = await field.read(decode=True)
    dest.write_bytes(data)
    log.info("Upload: wrote %d bytes to %s", len(data), dest)

    if _slack_client_ref and SLACK_NOTIFY_USER_ID:
        asyncio.create_task(_notify_upload_received(dest.name, len(data)))

    return web.json_response({"status": "ok", "filename": dest.name, "size": len(data)})


async def _handle_dropbox_oauth_callback(request: "aiohttp.web.Request") -> "aiohttp.web.Response":
    """Handle GET /dropbox/callback — complete the Dropbox OAuth2 flow."""
    from aiohttp import web

    _HTML_OK = (
        "<html><head><title>OpenClaw</title></head><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>✅ Dropbox connected!</h2>"
        "<p>You can close this window and return to Slack.</p>"
        "</body></html>"
    )
    _HTML_ERR = (
        "<html><head><title>OpenClaw</title></head><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>❌ Something went wrong</h2>"
        "<p>{msg}</p><p>Try <code>/dropbox connect</code> in Slack again.</p>"
        "</body></html>"
    )

    error = request.rel_url.query.get("error", "")
    if error:
        return web.Response(
            text=_HTML_ERR.format(msg=f"Dropbox declined: {error}"),
            content_type="text/html",
            status=400,
        )

    code = request.rel_url.query.get("code", "")
    state = request.rel_url.query.get("state", "")

    if not code or not state:
        return web.Response(
            text=_HTML_ERR.format(msg="Missing code or state parameter."),
            content_type="text/html",
            status=400,
        )

    user_id = _dropbox_oauth_states.pop(state, None)
    if not user_id:
        return web.Response(
            text=_HTML_ERR.format(msg="Session expired or invalid. Please try again."),
            content_type="text/html",
            status=400,
        )

    # Exchange authorization code for an access token
    redirect_uri = f"{_OPENCLAW_PUBLIC_URL}/dropbox/callback"
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                "https://api.dropboxapi.com/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=aiohttp.BasicAuth(_DROPBOX_APP_KEY, _DROPBOX_APP_SECRET),
                timeout=aiohttp.ClientTimeout(total=15),
            )
            token_data = await resp.json()
    except Exception as exc:
        log.error("Dropbox token exchange failed: %s", exc)
        return web.Response(
            text=_HTML_ERR.format(msg="Could not reach Dropbox to complete auth."),
            content_type="text/html",
            status=502,
        )

    access_token = token_data.get("access_token", "")
    if not access_token:
        err_desc = token_data.get("error_description", token_data.get("error", "unknown"))
        log.error("Dropbox token exchange error: %s", err_desc)
        return web.Response(
            text=_HTML_ERR.format(msg=f"Dropbox auth error: {err_desc}"),
            content_type="text/html",
            status=400,
        )

    # Persist per-user token (preserve existing watch_path if set)
    existing = _user_dropbox_tokens.get(user_id, {})
    _user_dropbox_tokens[user_id] = {
        "token": access_token,
        "watch_path": existing.get("watch_path", "/OpenClaw"),
    }
    _save_user_dropbox_tokens()
    log.info("Dropbox OAuth: stored token for Slack user %s", user_id)

    # DM the user in Slack to confirm
    if SLACK_BOT_TOKEN:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    "https://slack.com/api/chat.postMessage",
                    json={
                        "channel": user_id,
                        "text": (
                            "✅ *Your Dropbox is now connected!*\n\n"
                            "Try `/dropbox list` to see your files, or drop a file into your "
                            "*OpenClaw* Dropbox folder and OpenClaw will notice it automatically.\n\n"
                            "To disconnect later, type `/dropbox forget`."
                        ),
                    },
                    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as exc:
            log.warning("Failed to DM user after Dropbox connect: %s", exc)

    return web.Response(text=_HTML_OK, content_type="text/html")


async def _run_upload_server() -> None:
    """Start the aiohttp upload HTTP server on OPENCLAW_UPLOAD_PORT."""
    try:
        from aiohttp import web
    except ImportError:
        log.warning("aiohttp not available — upload server not started")
        return

    upload_app = web.Application()
    upload_app.router.add_post("/upload", _handle_upload)
    upload_app.router.add_get("/health", lambda _req: web.json_response({"status": "ok"}))
    upload_app.router.add_get("/dropbox/callback", _handle_dropbox_oauth_callback)

    runner = web.AppRunner(upload_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", OPENCLAW_UPLOAD_PORT)
    await site.start()
    log.info("Upload server listening on port %d", OPENCLAW_UPLOAD_PORT)


# ---------------------------------------------------------------------------
# Wave 4: Proactive file-alert helpers
# ---------------------------------------------------------------------------


def _load_known_files() -> set[str]:
    """Load the set of known filenames from disk."""
    try:
        if _KNOWN_FILES_PATH.exists():
            data = json.loads(_KNOWN_FILES_PATH.read_text(encoding="utf-8"))
            return set(data.get("files", []))
    except Exception as exc:
        log.warning("Could not load known_files.json: %s", exc)
    return set()


def _save_known_files(known: set[str]) -> None:
    try:
        _KNOWN_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _KNOWN_FILES_PATH.write_text(json.dumps({"files": sorted(known)}, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not save known_files.json: %s", exc)


def _human_time(ts: float) -> str:
    """Return a human-readable relative time string."""
    delta = time.time() - ts
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _load_digest_prefs() -> dict[str, dict]:
    """Load per-user digest preferences from disk."""
    try:
        if _DIGEST_PREFS_PATH.exists():
            return json.loads(_DIGEST_PREFS_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_digest_prefs(prefs: dict[str, dict]) -> None:
    """Persist per-user digest preferences to disk."""
    try:
        _DIGEST_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DIGEST_PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    except Exception as exc:
        log.warning("_save_digest_prefs: %s", exc)


def _load_last_seen_ts() -> dict[str, str]:
    """Load per-channel last-seen timestamps from /tmp (survives restarts via host bind-mount)."""
    try:
        if _LAST_SEEN_PATH.exists():
            return json.loads(_LAST_SEEN_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_last_seen_ts(data: dict[str, str]) -> None:
    try:
        _LAST_SEEN_PATH.write_text(json.dumps(data))
    except Exception as exc:
        log.warning("_save_last_seen_ts: %s", exc)


def _record_last_seen(channel: str, ts: str) -> None:
    """Update last-seen ts for a DM channel after processing a message."""
    global _last_seen_ts
    if ts and ts > _last_seen_ts.get(channel, "0"):
        _last_seen_ts[channel] = ts
        _save_last_seen_ts(_last_seen_ts)


async def _catchup_missed_dms(client: Any) -> int:
    """On startup, replay any DMs received while the bot was offline or reconnecting.

    Fetches IM history since the last recorded timestamp for each known channel
    and processes missed messages through the standard DM pipeline.

    Returns the number of messages replayed.
    """
    global _last_seen_ts
    _last_seen_ts = _load_last_seen_ts()

    if not _last_seen_ts:
        log.info("_catchup_missed_dms: no prior state — skipping catch-up on first run")
        return 0

    try:
        result = await client.conversations_list(types="im", limit=200)
        channels = result.get("channels") or []
    except Exception as exc:
        log.warning("_catchup_missed_dms: conversations.list failed: %s", exc)
        return 0

    caught_up = 0
    for ch in channels:
        ch_id = ch.get("id", "")
        oldest = _last_seen_ts.get(ch_id)
        if not ch_id or not oldest:
            continue
        try:
            hist = await client.conversations_history(channel=ch_id, oldest=oldest, limit=50, inclusive=False)
            messages = hist.get("messages") or []
        except Exception as exc:
            log.warning("_catchup_missed_dms: conversations.history failed for %s: %s", ch_id, exc)
            continue

        # Slack returns newest-first; reverse for chronological replay.
        for msg in reversed(messages):
            if msg.get("bot_id") or msg.get("user") == _BOT_USER_ID:
                continue
            if msg.get("subtype"):  # joins, leaves, file shares, etc.
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue

            user_id = msg.get("user", "unknown")
            msg_ts = msg.get("ts", "")
            log.info("_catchup_missed_dms: replaying missed DM from %s in %s (ts=%s)", user_id, ch_id, msg_ts)
            try:
                prompt, model_pref, use_simple = _parse_flags(text)
                use_simple = use_simple or _get_user_simple(user_id)

                await client.chat_postMessage(
                    channel=ch_id,
                    text="🔁 I was briefly offline and noticed I missed your message — catching up now!",
                )
                thinking_resp = await client.chat_postMessage(channel=ch_id, text="⏳ Thinking…")
                thinking_ts = thinking_resp.get("ts") if thinking_resp else None

                # Minimal say() shim so _send_answer can post replies to this channel.
                _ch = ch_id  # capture for closure

                async def _say(text: str = "", **kwargs: Any) -> Any:  # noqa: E731
                    return await client.chat_postMessage(channel=_ch, text=text, **kwargs)

                await _send_answer(
                    client=client,
                    say=_say,
                    channel=ch_id,
                    thread_ts=None,
                    thinking_ts=thinking_ts,
                    prompt=prompt,
                    user_id=user_id,
                    model_pref=model_pref,
                    simple=use_simple,
                )
                _record_last_seen(ch_id, msg_ts)
                caught_up += 1
            except Exception as exc:
                log.warning("_catchup_missed_dms: failed to replay message ts=%s: %s", msg_ts, exc)

    if caught_up:
        log.info("_catchup_missed_dms: replayed %d missed DM(s)", caught_up)
    else:
        log.info("_catchup_missed_dms: no missed DMs found")
    return caught_up


def _install_ws_reconnect_hook(handler: Any, slack_client: Any) -> None:
    """Monkey-patch connect_to_new_endpoint so every WS reconnect:
      1. logs the event,
      2. runs missed-DM catch-up, and
      3. DMs SLACK_NOTIFY_USER_ID to confirm the bot is back.

    The initial connection uses connect() directly and is NOT intercepted here,
    so this fires only on true reconnects (after a drop/408/timeout).
    """
    _orig = handler.client.connect_to_new_endpoint

    async def _notify_reconnect() -> None:
        global _ws_connection_count, _ws_last_reconnect_notify_ts
        _ws_connection_count += 1
        log.info("Slack WS reconnected (reconnect #%d) — running catch-up", _ws_connection_count)
        missed = await _catchup_missed_dms(slack_client)
        now = time.monotonic()
        # Always notify if messages were missed; only notify on plain reconnects every hour.
        should_notify = bool(missed) or (now - _ws_last_reconnect_notify_ts) > _WS_RECONNECT_NOTIFY_COOLDOWN
        if SLACK_NOTIFY_USER_ID and should_notify:
            try:
                msg = (
                    f"⚡ Slack WebSocket reconnected — caught up {missed} missed message(s)."
                    if missed
                    else "⚡ Slack WebSocket reconnected — no missed messages."
                )
                await slack_client.chat_postMessage(channel=SLACK_NOTIFY_USER_ID, text=msg)
                _ws_last_reconnect_notify_ts = now
            except Exception as exc:
                log.warning("WS reconnect notify failed: %s", exc)

    async def _patched(force: bool = False) -> None:
        await _orig(force=force)
        try:
            asyncio.get_event_loop().create_task(_notify_reconnect())
        except Exception as exc:
            log.warning("_install_ws_reconnect_hook: could not schedule reconnect task: %s", exc)

    handler.client.connect_to_new_endpoint = _patched


async def _digest_loop(client: Any) -> None:
    """Background task: DM enabled users with a digest of recent files."""
    log.info("Digest loop started (check every %ds, lookback %dh)", _DIGEST_CHECK_INTERVAL, _DIGEST_LOOKBACK_HOURS)
    while True:
        await asyncio.sleep(_DIGEST_CHECK_INTERVAL)
        try:
            prefs = _load_digest_prefs()
            now = time.time()
            cutoff = now - (_DIGEST_LOOKBACK_HOURS * 3600)
            recent: list[tuple[str, float]] = []
            if _AI_FILES_DIR.exists():
                for f in _AI_FILES_DIR.iterdir():
                    if f.is_file() and f.stat().st_mtime >= cutoff:
                        recent.append((f.name, f.stat().st_mtime))
            recent.sort(key=lambda x: x[1], reverse=True)

            for user_id, pref in prefs.items():
                if not pref.get("enabled"):
                    continue

                # Monthly tips nudge
                last_monthly_tip = pref.get("last_monthly_tip", 0)
                if now - last_monthly_tip >= _MONTHLY_TIP_INTERVAL:
                    try:
                        await client.chat_postMessage(channel=user_id, text=_MONTHLY_TIP_MESSAGE)
                        prefs[user_id]["last_monthly_tip"] = now
                        log.info("Sent monthly tip to %s", user_id)
                    except Exception as exc:
                        log.warning("_digest_loop: failed to send monthly tip to %s: %s", user_id, exc)

                last_sent = pref.get("last_sent", 0)
                if now - last_sent < (_DIGEST_LOOKBACK_HOURS * 3600 * 0.9):
                    continue
                if not recent:
                    continue
                lines = [f"• *{name}* — {_human_time(mtime)}" for name, mtime in recent[:10]]
                text = (
                    f"📊 *Your {_DIGEST_LOOKBACK_HOURS}h digest* — {len(recent)} file(s) updated\n\n"
                    + "\n".join(lines)
                    + "\n\n_Reply with a filename to work with it, or type `/digest off` to unsubscribe._"
                )
                try:
                    await client.chat_postMessage(channel=user_id, text=text)
                    prefs[user_id]["last_sent"] = now
                    asyncio.create_task(
                        _send_ntfy("📋 Evening Digest Ready", "Your daily digest has been posted to Slack")
                    )
                    log.info("Sent digest to %s (%d files)", user_id, len(recent))
                except Exception as exc:
                    log.warning("_digest_loop: failed to DM %s: %s", user_id, exc)
            _save_digest_prefs(prefs)
        except Exception as exc:
            log.warning("_digest_loop: error: %s", exc)


async def _file_alert_loop(client: Any) -> None:
    """Background task: poll /ai-files for new files and DM the notify user."""
    if not SLACK_NOTIFY_USER_ID:
        log.info("SLACK_NOTIFY_USER_ID not set — proactive file alerts disabled")
        return

    known = _load_known_files()

    while True:
        await asyncio.sleep(_FILE_POLL_INTERVAL)
        try:
            if not _AI_FILES_DIR.exists():
                continue

            current = {
                f.name
                for f in _AI_FILES_DIR.iterdir()
                if f.is_file() and f.suffix.lower() in _ALLOWED_UPLOAD_EXTENSIONS
            }
            new_files = current - known
            if new_files:
                for fname in sorted(new_files):
                    await _send_file_alert(client, fname)
                known = current
                _save_known_files(known)
        except Exception as exc:
            log.warning("file_alert_loop: error during poll: %s", exc)


async def _notify_upload_received(filename: str, size: int) -> None:
    """DM the notify user when a file is uploaded via the HTTP endpoint."""
    if size > 1024:
        size_human = f"{size // 1024} KB"
    else:
        size_human = f"{size} bytes"
    try:
        await _slack_client_ref.chat_postMessage(
            channel=SLACK_NOTIFY_USER_ID,
            text=(
                f"📥 *Got it!* I received `{filename}` ({size_human}). "
                'Just ask me what to do with it — for example: "summarize this" or "what\'s in this file?"'
            ),
        )
    except Exception as exc:
        log.debug("_notify_upload_received: failed to DM %s: %s", SLACK_NOTIFY_USER_ID, exc)


async def _send_file_alert(client: Any, filename: str) -> None:
    """DM the notify user about a newly detected file with Block Kit action buttons."""
    mimetype = _mimetype_for(filename)
    synthetic_id = f"aifiles::{filename}"
    synthetic_obj = {
        "id": synthetic_id,
        "name": filename,
        "mimetype": mimetype,
        "size": 0,
        "url_private": None,
        "ai_files_path": str(_AI_FILES_DIR / filename),
    }
    _register_file(synthetic_id, synthetic_obj)

    blocks = _build_file_blocks(
        filename=filename,
        description="📥 New file synced to OpenClaw — what would you like to do?",
        mimetype=mimetype,
        file_id=synthetic_id,
    )
    try:
        await client.chat_postMessage(
            channel=SLACK_NOTIFY_USER_ID,
            blocks=blocks,
            text=f"📄 New file synced: *{filename}*",
        )
        log.info("Sent file alert to %s for %s", SLACK_NOTIFY_USER_ID, filename)
    except Exception as exc:
        log.warning("_send_file_alert: failed to DM %s: %s", SLACK_NOTIFY_USER_ID, exc)


async def _process_slack_files(files: list[dict], token: str, question: str) -> str:
    """Download and incorporate Slack file attachments into *question*.

    Supports:
    - Images (mimetype image/*): analyzed via Gemini vision (llm_analyze_image)
    - Text/docs (text/*, application/pdf, application/vnd.*, etc.): decoded as
      UTF-8 and appended, capped at ATTACHMENT_TEXT_MAX_CHARS chars
    - Other: a note about unsupported type is appended

    Requires ``files:read`` scope on the Slack bot token.
    """
    for file in files:
        url = file.get("url_private_download") or file.get("url_private")
        if not url:
            continue

        filename = file.get("name", "unknown")
        mimetype = (file.get("mimetype") or "").lower()

        try:
            session = await _slack_dl_sessions.get()
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    log.warning("Failed to download Slack file %s: HTTP %d", filename, resp.status)
                    question += f"\n\n[Attachment: failed to download {filename}]"
                    continue

                data = await resp.read()

            if mimetype.startswith("image/"):
                # OCR intent: extract text verbatim instead of describing the image
                from ocr_skill import is_ocr_request, ocr_file  # lazy import

                if is_ocr_request(question):
                    ocr_text = await ocr_file(data, mimetype, hint="")
                    question = f"{question}\n\n[OCR result for {filename}]:\n{ocr_text}"
                else:
                    image_answer = await llm_analyze_image(data, mimetype, question)
                    question = f"{question}\n\n[Image attachment analysis: {image_answer}]"
            elif mimetype == "application/pdf":
                # OCR intent on PDF: extract text layer (or ask user to send as image)
                from ocr_skill import is_ocr_request, ocr_file  # lazy import

                if is_ocr_request(question):
                    ocr_text = await ocr_file(data, mimetype, hint="")
                    question = f"{question}\n\n[OCR result for {filename}]:\n{ocr_text}"
                else:
                    doc_text = data.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_MAX_CHARS]
                    question = (
                        f"{question}\n\n--- Attached Document: {filename} "
                        f"(first {ATTACHMENT_TEXT_MAX_CHARS} chars) ---\n"
                        f"{doc_text}\n"
                        f"--- End Document ---"
                    )
            elif (
                mimetype.startswith("text/")
                or mimetype in ("application/json",)
                or mimetype.startswith("application/vnd.")
            ):
                doc_text = data.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_MAX_CHARS]
                question = (
                    f"{question}\n\n--- Attached Document: {filename} "
                    f"(first {ATTACHMENT_TEXT_MAX_CHARS} chars) ---\n"
                    f"{doc_text}\n"
                    f"--- End Document ---"
                )
            elif mimetype.startswith("audio/"):
                question += (
                    f"\n\n[🎵 Audio file detected: {filename} — audio transcription is not yet supported. "
                    "Please describe what you need help with in text!]"
                )
            else:
                question += f"\n\n[Attachment: unsupported file type {filename} ({mimetype})]"

        except Exception as exc:
            log.warning("Failed to process Slack file %s: %s", filename, exc)
            question += f"\n\n[Attachment: error processing {filename}]"

    return question


def _parse_model_flag(text: str) -> tuple[str, str]:
    """Extract --model <alias> from *text*.

    Returns (cleaned_text, model_pref) where model_pref is a valid
    OpenClaw model preference string.
    """
    match = _MODEL_FLAG_RE.search(text)
    if not match:
        return text, "auto"
    alias = match.group(1).lower()
    model_pref = _MODEL_ALIASES.get(alias, "auto")
    clean = _MODEL_FLAG_RE.sub("", text).strip()
    return clean, model_pref


def _parse_flags(text: str) -> tuple[str, str, bool]:
    """Parse --simple and --model flags from *text*.

    Returns (cleaned_text, model_pref, use_simple).
    """
    use_simple = bool(_SIMPLE_FLAG_RE.search(text))
    cleaned = _SIMPLE_FLAG_RE.sub("", text).strip()
    cleaned, model_pref = _parse_model_flag(cleaned)
    return cleaned, model_pref, use_simple


def _register_bot_message(channel: str, ts: str, user_id: str) -> None:
    """Track a bot message so reactions can be matched back to the requester."""
    _bot_message_registry[(channel, ts)] = user_id
    # Prune to avoid unbounded growth (keep last 500 entries)
    if len(_bot_message_registry) > 500:
        oldest = next(iter(_bot_message_registry))
        del _bot_message_registry[oldest]


async def _build_thread_history(client: Any, channel: str, thread_ts: str) -> list[dict[str, str]]:
    """Fetch previous messages in *thread_ts* and return them as conversation history.

    The last message (the current prompt) is excluded — the caller supplies that
    as the ``prompt`` argument to ``_ask``.
    """
    global _BOT_USER_ID
    try:
        result = await client.conversations_replies(channel=channel, ts=thread_ts, limit=20)
        messages: list[dict] = result.get("messages", [])
        history: list[dict[str, str]] = []
        for msg in messages[:-1]:  # exclude the triggering message
            content = (msg.get("text") or "").strip()
            if not content or content == "⏳ Thinking…":
                continue
            is_bot = bool(msg.get("bot_id")) or (_BOT_USER_ID and msg.get("user") == _BOT_USER_ID)
            role = "assistant" if is_bot else "user"
            history.append({"role": role, "content": content})
        return history
    except Exception as exc:
        log.warning("Failed to fetch thread history for %s/%s: %s", channel, thread_ts, exc)
        return []


def _slack_is_configured() -> bool:
    if not SLACK_ENABLED:
        log.warning("Slack bot disabled: SLACK_ENABLED != 'true'")
        return False
    if not SLACK_BOT_TOKEN or not SLACK_BOT_TOKEN.startswith("xoxb-"):
        log.warning("Slack bot disabled: SLACK_BOT_TOKEN missing or invalid (expected xoxb-...)")
        return False
    if not SLACK_APP_TOKEN or not SLACK_APP_TOKEN.startswith("xapp-"):
        log.warning("Slack bot disabled: SLACK_APP_TOKEN missing or invalid (expected xapp-...)")
        return False
    return True


# ---------------------------------------------------------------------------
# File action registry + Block Kit helpers
# ---------------------------------------------------------------------------
# file_id → full file_obj dict from files_info (url_private, name, mimetype, …)
# Kept in memory to avoid re-calling files_info on every button click.
# Pruned to last 200 entries.
_file_registry: dict[str, dict] = {}

# Batch file grouping state: channel:ts → list of file events queued within grouping window
_pending_batch: dict[str, list[dict]] = {}
_batch_lock: asyncio.Lock | None = None  # initialized lazily

# Compare flow: user_id → file_id of Document A awaiting a second file
_compare_pending: dict[str, str] = {}

# Prompts sent to the LLM when a file action button is clicked
_FILE_ACTION_PROMPTS: dict[str, str] = {
    "file_proofread": (
        "Please proofread this document and correct any grammar, spelling, or punctuation "
        "errors. List each correction clearly."
    ),
    "file_summarize": "Please summarize the key points in a few bullet points.",
    "file_explain": (
        "Please explain what this document is about in plain, simple language. Assume the reader is non-technical."
    ),
    "file_errors": (
        "Please identify any errors, inconsistencies, unusual values, or potential problems "
        "in this document. Be specific."
    ),
    "file_research": (
        "Using web research to enhance your analysis. "
        "First identify key entities, terms, or claims in this document that would benefit from current information. "
        "Then provide a research-enhanced analysis incorporating both the document content and current facts."
    ),
    "file_describe": "Please describe what is in this image in detail.",
    "file_read_text": "Please read and transcribe all text visible in this image.",
    "file_chart": (
        "Analyze this spreadsheet data. Identify the best columns to visualize as a chart. "
        "Return a JSON object with these fields: "
        '{"chart_type": "bar|line|pie", "x_column": "column name", "y_columns": ["col1", "col2"], '
        '"title": "chart title", "description": "one-sentence description of what the chart shows"}. '
        "Return ONLY the JSON, no other text."
    ),
    "file_formula": (
        "Examine this spreadsheet carefully. List all the formulas you find and explain what each one does in plain English. "
        "For any formula that seems complex or hard to understand, suggest a simpler alternative. "
        "If any formulas appear to have errors or could cause problems, flag them clearly."
    ),
    "file_translate": (
        "Please translate this document into {language}. "
        "Preserve the original formatting and structure as much as possible. "
        "Return only the translated text."
    ),
    "file_compare": (
        "You are comparing two documents. Identify the key differences between them: "
        "structural changes, added/removed sections, significant wording changes, and any "
        "factual differences. Present as a clear summary with bullet points."
    ),
}


def _register_file(file_id: str, file_obj: dict, file_bytes: bytes | None = None) -> None:
    """Store *file_obj* (and optionally raw bytes) in the registry, pruning to 200 entries."""
    _file_registry[file_id] = {"file_obj": file_obj, "file_bytes": file_bytes}
    if len(_file_registry) > 200:
        oldest = next(iter(_file_registry))
        del _file_registry[oldest]


def _build_file_blocks(filename: str, description: str | None, mimetype: str, file_id: str) -> list[dict]:
    """Build Slack Block Kit blocks for a file upload suggestion message."""
    is_image = (mimetype or "").lower().startswith("image/")

    header = f"📎 I see you uploaded *{filename}*."
    if description:
        header = f"{header}\n_{description}_"
    header += "\n\nWhat would you like to do?"

    if is_image:
        buttons: list[dict] = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 Describe it", "emoji": True},
                "action_id": "file_describe",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📝 Read text in image", "emoji": True},
                "action_id": "file_read_text",
                "value": file_id,
            },
        ]
    elif (mimetype or "").lower() == "application/pdf" or filename.lower().endswith(".pdf"):
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📋 Summarize", "emoji": True},
                "action_id": "file_summarize",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📌 Key Points", "emoji": True},
                "action_id": "file_explain",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ Action Items", "emoji": True},
                "action_id": "file_errors",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🌍 Translate", "emoji": True},
                "action_id": "file_translate",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔀 Compare", "emoji": True},
                "action_id": "file_compare_start",
                "value": file_id,
            },
        ]
    elif (mimetype or "").lower().startswith("audio/"):
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🎵 Audio — transcription coming soon", "emoji": True},
                "action_id": "audio_unsupported",
                "value": file_id,
            },
        ]
    else:
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✏️ Proofread", "emoji": True},
                "action_id": "file_proofread",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📋 Summarize", "emoji": True},
                "action_id": "file_summarize",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "❓ Explain it", "emoji": True},
                "action_id": "file_explain",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 Find errors", "emoji": True},
                "action_id": "file_errors",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔬 Research", "emoji": True},
                "action_id": "file_research",
                "value": file_id,
            },
        ]

        # Add chart button only for spreadsheet files
        if filename.endswith((".xlsx", ".csv")) or mimetype in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/csv",
        ):
            buttons.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📊 Chart", "emoji": True},
                    "action_id": "file_chart",
                    "value": file_id,
                }
            )
            buttons.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📐 Formulas", "emoji": True},
                    "action_id": "file_formula",
                    "value": file_id,
                }
            )

        # Add translate button for all non-image document files
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🌍 Translate", "emoji": True},
                "action_id": "file_translate",
                "value": file_id,
            }
        )

        # Compare button for all non-image document files
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔀 Compare", "emoji": True},
                "action_id": "file_compare_start",
                "value": file_id,
            }
        )

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "actions", "elements": buttons},
    ]


async def _generate_chart(
    file_obj: dict,
    token: str,
    user_id: str,
) -> bytes | None:
    """Generate a chart PNG from an Excel/CSV file. Returns PNG bytes or None on failure."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import openpyxl
    except ImportError as exc:
        log.warning("_generate_chart: missing dependency: %s", exc)
        return None

    # Download file bytes
    file_bytes = None
    registry_entry = _file_registry.get(file_obj.get("id", "")) or {}
    if isinstance(registry_entry, dict) and "file_bytes" in registry_entry:
        file_bytes = registry_entry["file_bytes"]

    if not file_bytes:
        url = file_obj.get("url_private") or file_obj.get("ai_files_path")
        if not url:
            return None
        if file_obj.get("ai_files_path"):
            file_bytes = Path(file_obj["ai_files_path"]).read_bytes()
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    if resp.status != 200:
                        return None
                    file_bytes = await resp.read()

    if not file_bytes:
        return None

    # Parse Excel
    try:
        import io as _io

        wb = openpyxl.load_workbook(_io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            return None
        headers = [str(h or f"Col{i}") for i, h in enumerate(rows[0])]
        data_rows = rows[1:51]  # cap at 50 rows
    except Exception as exc:
        log.warning("_generate_chart: failed to parse Excel: %s", exc)
        return None

    # Get LLM chart spec
    try:
        sample = "\t".join(headers) + "\n" + "\n".join("\t".join(str(v or "") for v in r) for r in data_rows[:5])
        spec_prompt = (
            f"Spreadsheet columns: {headers}\nSample data:\n{sample}\n\n"
            'Return JSON: {"chart_type": "bar|line|pie", "x_column": "col", '
            '"y_columns": ["col"], "title": "...", "description": "..."}'
        )
        spec_json = await _ask(spec_prompt, user_id=user_id, simple=False, model_pref="gemini")
        import json as _json
        import re as _re

        json_match = _re.search(r"\{.*\}", spec_json, _re.DOTALL)
        spec = _json.loads(json_match.group() if json_match else spec_json)
    except Exception as exc:
        log.warning("_generate_chart: LLM spec failed: %s", exc)
        # Fallback: bar chart of first two numeric columns
        spec = {
            "chart_type": "bar",
            "x_column": headers[0],
            "y_columns": [headers[1]] if len(headers) > 1 else [headers[0]],
            "title": "Data Chart",
            "description": "",
        }

    # Build chart
    try:
        import io as _io2

        x_col = spec.get("x_column", headers[0])
        y_cols = spec.get("y_columns", [headers[1]] if len(headers) > 1 else [])
        chart_type = spec.get("chart_type", "bar")
        title = spec.get("title", "Chart")

        x_idx = headers.index(x_col) if x_col in headers else 0
        y_idxs = [headers.index(c) for c in y_cols if c in headers]
        if not y_idxs:
            y_idxs = [1] if len(headers) > 1 else [0]

        x_vals = [str(r[x_idx] or "") for r in data_rows]

        fig, ax = plt.subplots(figsize=(10, 6))
        for y_idx in y_idxs[:3]:  # cap at 3 series
            y_vals = []
            for r in data_rows:
                try:
                    y_vals.append(float(r[y_idx] or 0))
                except (TypeError, ValueError):
                    y_vals.append(0.0)
            label = headers[y_idx]
            if chart_type == "line":
                ax.plot(x_vals, y_vals, marker="o", label=label)
            elif chart_type == "pie" and len(y_idxs) == 1:
                ax.pie(y_vals, labels=x_vals, autopct="%1.1f%%")
                ax.set_title(title)
                break
            else:
                ax.bar(x_vals, y_vals, label=label)

        if chart_type != "pie":
            ax.set_title(title)
            ax.set_xlabel(x_col)
            if len(y_idxs) > 1:
                ax.legend()
            plt.xticks(rotation=45, ha="right")

        plt.tight_layout()
        buf = _io2.BytesIO()
        plt.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        log.warning("_generate_chart: chart render failed: %s", exc)
        return None


def _mimetype_for(filename: str) -> str:
    """Return a reasonable MIME type from a filename suffix."""
    suffix = Path(filename).suffix.lower()
    return {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }.get(suffix, "application/octet-stream")


def _route_model_for_file(filename: str, action: str) -> str:
    """Return the best model_pref for this file + action combination.

    Routing table (highest priority first):
    - .docx + proofread/summarize → gemini (long context)
    - .xlsx + analyze/chart → copilot (structured data)
    - any + research/search → perplexity-direct (web search)
    - default → auto
    """
    suffix = Path(filename).suffix.lower()
    action_lower = action.lower()

    if suffix == ".docx" and any(kw in action_lower for kw in ("proofread", "summarize")):
        return "gemini"
    if suffix == ".xlsx" and any(kw in action_lower for kw in ("analyze", "chart")):
        return "copilot"
    if any(kw in action_lower for kw in ("research", "search")):
        return "perplexity-direct"
    return "auto"


# ---------------------------------------------------------------------------
# Research request detection
# ---------------------------------------------------------------------------

_RESEARCH_KEYWORDS_RE = re.compile(
    r"\b(research|look\s+up|find\s+info|search\s+for)\b",
    re.IGNORECASE,
)


def _is_research_request(text: str) -> bool:
    """Return True if *text* contains a research-intent keyword."""
    return bool(_RESEARCH_KEYWORDS_RE.search(text))


# ---------------------------------------------------------------------------
# Batch upload detection
# ---------------------------------------------------------------------------


def _is_batch_upload(files: list) -> bool:
    """Return True if two or more files are present (batch mode)."""
    return len(files) >= 2


# ---------------------------------------------------------------------------
# Research pipeline (Perplexity → optional Gemini incorporation)
# ---------------------------------------------------------------------------


async def _run_research_pipeline(
    client: Any,
    channel: str,
    user: str,
    text: str,
    file_obj: dict | None = None,
) -> None:
    """Two-phase research pipeline: Perplexity search → optional Gemini incorporation.

    Phase 1: Call _ask() with perplexity-direct to get web research results.
    Phase 2: If a file is active, call _ask() with gemini to incorporate findings.
    Phase 3: Post combined answer.  If no file: post Perplexity results with a tip.
    """
    # Phase 1: Perplexity research
    perplexity_prompt = f"Research the following topic and provide a concise, cited summary with key facts:\n\n{text}"
    try:
        research_results = await _ask(perplexity_prompt, user, model_pref="perplexity-direct")
    except Exception as exc:
        log.warning("_run_research_pipeline: Perplexity phase failed: %s", exc)
        research_results = f"(Research unavailable: {exc})"

    # No active file — return Perplexity results with tip
    if file_obj is None:
        answer = f"🔍 *Research Results*\n\n{research_results}\n\n_Tip: share a Word doc to incorporate these findings_"
        try:
            await client.chat_postMessage(channel=channel, text=answer)
        except Exception as exc:
            log.warning("_run_research_pipeline: post failed: %s", exc)
        return

    # Interim acknowledgement
    try:
        await client.chat_postMessage(
            channel=channel,
            text="🔍 Found research results. Incorporating into your document...",
        )
    except Exception as exc:
        log.warning("_run_research_pipeline: interim post failed: %s", exc)

    # Phase 2: Gemini incorporates research into document context
    file_content_preview = ""
    try:
        file_content_preview = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, "")
    except Exception:
        pass

    gemini_prompt = (
        f"Research findings:\n{research_results}\n\n"
        f"Using the above research, help incorporate these findings into the document:\n"
        f"{file_content_preview}"
    )
    try:
        gemini_answer = await _ask(gemini_prompt, user, model_pref="gemini")
    except Exception as exc:
        log.warning("_run_research_pipeline: Gemini phase failed: %s", exc)
        gemini_answer = "(Could not incorporate findings into document)"

    # Phase 3: Post final combined answer
    final_text = f"🔍 *Research Summary*\n{research_results}\n\n📄 *Suggested document update*\n{gemini_answer}"
    try:
        await client.chat_postMessage(channel=channel, text=_clean_for_slack(final_text))
    except Exception as exc:
        log.warning("_run_research_pipeline: final post failed: %s", exc)


# ---------------------------------------------------------------------------
# Batch file processor
# ---------------------------------------------------------------------------


async def _process_batch(
    client: Any,
    channel: str,
    thread_ts: str,
    files: list,
    action: str,
    dispatch_fn=None,
) -> list[dict]:
    """Process *files* sequentially, posting progress updates in *thread_ts*.

    Args:
        dispatch_fn: Optional async callable(file_obj, action_id, user_id) -> str.
                     When None a no-op placeholder is used (useful in tests).
    """
    total = len(files)
    try:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"📦 Processing {total} files...",
        )
    except Exception as exc:
        log.warning("_process_batch: initial post failed: %s", exc)

    results: list[dict] = []

    for i, file_obj in enumerate(files, start=1):
        filename = file_obj.get("name", f"file_{i}")

        # Build progress snapshot
        progress_lines: list[str] = []
        for j, f in enumerate(files, start=1):
            fname = f.get("name", f"file_{j}")
            if j < i:
                progress_lines.append(f"✅ {j}/{total}: {fname} done")
            elif j == i:
                progress_lines.append(f"⏳ {j}/{total}: {fname}...")
            else:
                progress_lines.append(f"⬜ {j}/{total}: {fname}")

        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="\n".join(progress_lines),
            )
        except Exception as exc:
            log.warning("_process_batch: progress post failed for %s: %s", filename, exc)

        # Process the file
        try:
            if dispatch_fn is not None:
                result = await dispatch_fn(
                    file_obj=file_obj,
                    action_id=f"file_{action}",
                    user_id="batch",
                )
            else:
                result = f"Processed {filename}"
            results.append({"filename": filename, "status": "done", "result": result})
        except Exception as exc:
            log.error("_process_batch: error processing %s: %s", filename, exc)
            results.append({"filename": filename, "status": "error", "error": str(exc)})

        # Brief pause between files to respect rate limits
        if i < total:
            await asyncio.sleep(2)

    done_count = sum(1 for r in results if r["status"] == "done")
    summary = (
        f"✅ All {done_count} files processed!" if done_count == total else f"✅ {done_count}/{total} files processed."
    )
    try:
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=summary)
    except Exception as exc:
        log.warning("_process_batch: summary post failed: %s", exc)

    return results


async def _auto_brief_file(file_obj: dict, token: str) -> str | None:
    """Return a one-sentence description of the file, or None if unavailable.

    Downloads the first 800 chars of the file content and asks the LLM for a
    brief, plain-language description. Falls back to None on any error.
    Images are skipped (the vision model handles them differently).
    """
    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    if not url:
        return None

    filename = file_obj.get("name", "file")
    mimetype = (file_obj.get("mimetype") or "").lower()

    # Images: skip (vision analysis runs separately when the user picks an action)
    if mimetype.startswith("image/"):
        return None

    # Only try to extract text from supported types
    if not (
        mimetype.startswith("text/")
        or mimetype == "application/pdf"
        or mimetype == "application/json"
        or mimetype.startswith("application/vnd.")
    ):
        return None

    try:
        session = await _slack_dl_sessions.get()
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
    except Exception as exc:
        log.debug("_auto_brief_file: download failed for %s: %s", filename, exc)
        return None

    try:
        preview = data.decode("utf-8", errors="replace")[:800].strip()
    except Exception:
        return None

    if not preview:
        return None

    try:
        brief = await _ask(
            (
                f"In one short sentence (max 20 words), describe what this file is about. "
                f"Be specific.\n\nFile name: {filename}\n\nContent preview:\n{preview}"
            ),
            user_id="_auto_brief",
            simple=True,
        )
        return brief.strip().rstrip(".") if brief else None
    except Exception as exc:
        log.debug("_auto_brief_file: LLM failed for %s: %s", filename, exc)
        return None


async def _compare_documents(
    file_obj_a: dict,
    file_obj_b: dict,
    token: str,
    user_id: str,
    simple: bool = False,
) -> str:
    """Compare two documents semantically and return a diff summary."""
    content_a = await _process_slack_files([file_obj_a], token, "Return the full text content of this document.")
    content_b = await _process_slack_files([file_obj_b], token, "Return the full text content of this document.")
    compare_prompt = (
        "Compare these two documents and summarize the key differences:\n\n"
        f"--- Document A: {file_obj_a.get('name', 'Document A')} ---\n{content_a}\n\n"
        f"--- Document B: {file_obj_b.get('name', 'Document B')} ---\n{content_b}\n\n"
        "Focus on: structural changes, added/removed sections, significant wording differences, "
        "and any factual changes. Use bullet points."
    )
    return await _ask(compare_prompt, user_id=user_id, simple=simple, model_pref="gemini")


async def _two_phase_research(file_obj: dict, token: str, base_prompt: str) -> str:
    """Two-phase pipeline: Perplexity for web context + Gemini for doc integration.

    Falls back to single-phase if Perplexity routing is unavailable.
    """
    # Phase 1: get web research via Perplexity
    doc_content = await _process_slack_files(
        [file_obj],
        token,
        "Extract the main topics, entities, and key claims from this document in a brief list.",
    )
    research_prompt = f"Research current information about these topics from a document: {doc_content[:500]}"
    try:
        research_result = await _ask(research_prompt, user_id="system", simple=False, model_pref="perplexity-direct")
    except Exception as exc:
        log.warning("_two_phase_research: Perplexity phase failed, falling back: %s", exc)
        research_result = ""

    # Phase 2: Gemini synthesizes doc + research
    if research_result:
        combined_prompt = (
            f"{base_prompt}\n\n"
            f"--- Document ---\n{doc_content}\n--- End Document ---\n\n"
            f"--- Current Research ---\n{research_result}\n--- End Research ---\n\n"
            "Please provide an analysis that incorporates both the document content and the current research above."
        )
    else:
        combined_prompt = await _process_slack_files([file_obj], token, base_prompt)

    return combined_prompt


async def _ask(
    prompt: str,
    user_id: str,
    *,
    model_pref: str = "auto",
    history: list[dict] | None = None,
    simple: bool = False,
    client: Any = None,
) -> str:
    """Route a prompt through OpenClaw's agent ask pipeline."""
    global _daily_query_count, _error_window, _last_alert_ts
    from ask_executor import execute_agent_ask as _execute_agent_ask

    try:
        if simple:
            prompt = _SIMPLE_SYSTEM_PREFIX + prompt
        payload = await _execute_agent_ask(
            prompt=prompt,
            model_pref=model_pref,
            history=history or [],
            user_name=f"slack:{user_id}",
        )
        result = str(payload.get("response") or payload.get("text") or "(no response)").strip()
        _model_last_success[model_pref] = time.monotonic()
        _daily_query_count += 1
        return result
    except Exception as exc:  # broad: intentional
        log.error("_execute_agent_ask failed for slack user %s: %s", user_id, exc)
        now = time.monotonic()
        _error_window.append(now)
        _error_window[:] = [t for t in _error_window if now - t < 300]
        if len(_error_window) >= 3 and client is not None:
            await _alert_admin(client, f"model={model_pref} user={user_id} err={exc}")
        raise


# ---------------------------------------------------------------------------
# Progress streaming helpers
# ---------------------------------------------------------------------------

_PROGRESS_STEPS: list[str] = [
    "📖 Reading your document…",
    "🔍 Analyzing content…",
    "✍️ Writing response…",
    "⏳ Almost done…",
]


async def _edit_thinking_with_progress(
    client: Any,
    channel: str,
    ts: str,
    steps: list[str],
    interval_secs: float = 8.0,
) -> None:
    """Cycle through step messages on the thinking placeholder until cancelled."""
    for step in steps:
        await asyncio.sleep(interval_secs)
        try:
            await client.chat_update(channel=channel, ts=ts, text=step)
        except Exception:
            break


# ---------------------------------------------------------------------------
# Shared send-and-track helper
# ---------------------------------------------------------------------------


async def _send_answer(
    *,
    client: Any,
    say: Any,
    channel: str,
    thread_ts: str | None,
    thinking_ts: str | None,
    prompt: str,
    user_id: str,
    model_pref: str = "auto",
    history: list[dict] | None = None,
    simple: bool = False,
) -> str:
    """Ask OpenClaw, update the thinking placeholder, and register the reply for feedback."""
    t0 = time.monotonic()
    progress_task: asyncio.Task | None = None
    if thinking_ts:
        progress_task = asyncio.create_task(_edit_thinking_with_progress(client, channel, thinking_ts, _PROGRESS_STEPS))
    try:
        try:
            answer = await _ask(prompt, user_id, model_pref=model_pref, history=history, simple=simple)
            text = _clean_for_slack(answer) if answer else "(no response)"
            text += _maybe_append_tip()
            _log_query_metrics(
                user_id,
                action="message",
                model_used=model_pref or "auto",
                duration_ms=int((time.monotonic() - t0) * 1000),
                status="ok",
            )
        except Exception as exc:
            log.warning("_send_answer: _ask failed: %s", exc)
            text = "Hmm, something didn't work right — sorry about that! Try sending your message again. If it keeps happening, you can let Dave know."
            _log_query_metrics(
                user_id,
                action="message",
                model_used=model_pref or "auto",
                duration_ms=int((time.monotonic() - t0) * 1000),
                status="error",
            )
            # Post a Block Kit "Try again" button so the user has a recovery path
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
            _retry_cache[prompt_hash] = prompt
            if len(_retry_cache) > _RETRY_CACHE_MAX:
                oldest_key = next(iter(_retry_cache))
                del _retry_cache[oldest_key]
            try:
                retry_blocks = [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "⚠️ Something went wrong — want me to try again?"},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "🔁 Retry", "emoji": True},
                                "action_id": "retry_last_prompt",
                                "value": prompt_hash,
                            }
                        ],
                    },
                ]
                await say(blocks=retry_blocks, text="⚠️ Something went wrong — want me to try again?")
            except Exception as retry_exc:
                log.warning("_send_answer: failed to post retry button: %s", retry_exc)
    finally:
        if progress_task and not progress_task.done():
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

    sent_ts: str | None = None

    if thinking_ts:
        try:
            resp = await client.chat_update(channel=channel, ts=thinking_ts, text=text)
            sent_ts = (resp or {}).get("ts") or thinking_ts
        except Exception:
            pass

    if sent_ts is None:
        kwargs: dict[str, Any] = {"text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        resp2 = await say(**kwargs)
        sent_ts = (resp2 or {}).get("ts")

    if sent_ts:
        _register_bot_message(channel, sent_ts, user_id)

    return text


# ---------------------------------------------------------------------------
# Admin DM alerting
# ---------------------------------------------------------------------------


async def _alert_admin(client: Any, message: str) -> None:
    """DM the admin when error rate spikes."""
    global _last_alert_ts
    admin_user = os.environ.get("SLACK_ADMIN_USER_ID", "")
    if not admin_user:
        return
    now = time.monotonic()
    if now - _last_alert_ts < 300:
        return
    _last_alert_ts = now
    try:
        await client.chat_postMessage(channel=admin_user, text=f"⚠️ OpenClaw alert:\n{message}")
    except Exception as exc:
        log.warning("_alert_admin: failed to DM admin: %s", exc)


async def _handle_browser_nav_intent(
    client: Any,
    channel: str,
    raw_text: str,
    thread_ts: str | None = None,
) -> bool:
    """Detect browser navigation intent in raw_text; fetch page content and post result.

    Returns True if a navigation intent was detected (caller should return early).
    """
    m = _BROWSER_NAV_PATTERNS.search(raw_text)
    if not m:
        return False

    url = m.group(1)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from browser_skills import navigate_and_extract  # lazy import — lives in /app/skills

        content = await navigate_and_extract(url)
    except Exception as exc:
        log.warning("_handle_browser_nav_intent: failed for %s: %s", url, exc)
        kwargs: dict[str, Any] = {
            "channel": channel,
            "text": f"⚠️ Couldn't fetch {url} — site may be down or blocking bots.",
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await client.chat_postMessage(**kwargs)
        return True

    reply_kwargs: dict[str, Any] = {
        "channel": channel,
        "text": f"🌐 Content from <{url}|{url}>:\n```\n{content}\n```",
    }
    if thread_ts:
        reply_kwargs["thread_ts"] = thread_ts
    await client.chat_postMessage(**reply_kwargs)
    return True


async def _handle_screenshot_intent(
    client: Any,
    channel: str,
    raw_text: str,
    thread_ts: str | None = None,
) -> bool:
    """Detect screenshot intent in raw_text; capture and upload if found.

    Returns True if a screenshot intent was detected (caller should return early).
    """
    m = _SCREENSHOT_PATTERNS.search(raw_text)
    if not m:
        return False

    url = next((g for g in m.groups() if g), None)
    if not url:
        return False

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    safe_hostname = re.sub(r"[^a-zA-Z0-9._-]", "_", parsed.netloc or "page")

    try:
        from screenshot_skill import take_website_screenshot  # lazy import — lives in /app/skills

        png_bytes = await take_website_screenshot(url)
    except Exception as exc:
        log.warning("_handle_screenshot_intent: failed for %s: %s", url, exc)
        kwargs: dict[str, Any] = {
            "channel": channel,
            "text": f"⚠️ Couldn't screenshot {url} — site may be down or blocking bots. Try a different URL.",
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await client.chat_postMessage(**kwargs)
        return True

    upload_kwargs: dict[str, Any] = {
        "channel": channel,
        "filename": f"screenshot_{safe_hostname}.png",
        "content": png_bytes,
        "initial_comment": f"📸 Screenshot of {url}",
    }
    if thread_ts:
        upload_kwargs["thread_ts"] = thread_ts
    await client.files_upload_v2(**upload_kwargs)
    return True


# ---------------------------------------------------------------------------
# Slack app factory
# ---------------------------------------------------------------------------


async def _handle_batch_file(event: dict, client: Any, say: Any) -> None:
    """Group simultaneous file_shared events and process as a batch when multiple arrive.

    Uses a 0.5-second grouping window. If multiple files land with the same
    channel_id + event_ts key, they are processed sequentially with progress
    updates in thread. Single-file uploads are processed via the normal path
    (auto-brief + Block Kit buttons) with no behaviour change.
    """
    global _batch_lock
    if _batch_lock is None:
        _batch_lock = asyncio.Lock()

    channel: str = event.get("channel_id", "")
    # Use event_ts as the grouping key; fall back to ts when absent
    group_ts: str = event.get("event_ts") or event.get("ts", "")
    batch_key = f"{channel}:{group_ts}"

    async with _batch_lock:
        if batch_key not in _pending_batch:
            _pending_batch[batch_key] = []
        _pending_batch[batch_key].append(event)
        is_first = len(_pending_batch[batch_key]) == 1

    if not is_first:
        # Another coroutine is already managing this batch window; nothing to do.
        return

    # Wait briefly for any additional file_shared events that belong to the same message
    await asyncio.sleep(0.5)

    async with _batch_lock:
        batch_events = _pending_batch.pop(batch_key, [])

    if len(batch_events) <= 1:
        # Single file — delegate to the normal inline handling below
        await _process_single_file_shared(event, client, say)
        return

    # Batch path: multiple files in one message
    n = len(batch_events)
    try:
        header_resp = await client.chat_postMessage(
            channel=channel,
            text=f"📦 Processing {n} files…",
        )
        thread_ts = (header_resp or {}).get("ts", "")
    except Exception as exc:
        log.warning("_handle_batch_file: could not post batch header: %s", exc)
        thread_ts = ""

    for i, file_event in enumerate(batch_events, start=1):
        fid: str = file_event.get("file_id", "")
        try:
            file_info_resp = await client.files_info(file=fid)
            file_obj: dict = (file_info_resp or {}).get("file", {})
        except Exception as exc:
            log.warning("_handle_batch_file: files_info failed for %s: %s", fid, exc)
            continue

        if not file_obj:
            continue

        _register_file(fid, file_obj)
        filename = file_obj.get("name", f"file_{i}")
        mimetype = file_obj.get("mimetype") or ""

        # Per-file progress indicator
        if thread_ts:
            try:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"⏳ {i}/{n}: processing *{filename}*…",
                )
            except Exception as exc:
                log.warning("_handle_batch_file: progress post failed for %s: %s", filename, exc)

        description = await _auto_brief_file(file_obj, SLACK_BOT_TOKEN)
        blocks = _build_file_blocks(filename, description, mimetype, fid)
        fallback_text = (
            f"📎 *{filename}*" + (f"\n_{description}_" if description else "") + "\n\nWhat would you like to do?"
        )
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts or None,
                text=fallback_text,
                blocks=blocks,
            )
        except Exception as exc:
            log.warning("_handle_batch_file: Block Kit post failed for %s: %s", filename, exc)

    if thread_ts:
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"✅ All {n} files ready!",
            )
        except Exception as exc:
            log.warning("_handle_batch_file: summary post failed: %s", exc)


async def _process_single_file_shared(event: dict, client: Any, say: Any) -> None:
    """Handle a single file_shared event: auto-brief + Block Kit action buttons."""
    file_id: str = event.get("file_id", "")
    channel: str = event.get("channel_id", "")
    user_id: str = event.get("user_id", "")

    if not file_id or not channel:
        return

    try:
        file_info_resp = await client.files_info(file=file_id)
        file_obj: dict = (file_info_resp or {}).get("file", {})
    except Exception as exc:
        log.warning("file_shared: failed to fetch info for file %s: %s", file_id, exc)
        return

    if not file_obj:
        return

    _register_file(file_id, file_obj)
    filename = file_obj.get("name", "file")
    mimetype = file_obj.get("mimetype") or ""

    # Compare flow: if user has already selected Document A, treat this as Document B
    if user_id and user_id in _compare_pending:
        file_id_a = _compare_pending.pop(user_id)
        file_obj_a_entry = _file_registry.get(file_id_a) or {}
        if isinstance(file_obj_a_entry, dict) and "file_obj" in file_obj_a_entry:
            file_obj_a = file_obj_a_entry["file_obj"]
        else:
            file_obj_a = file_obj_a_entry or {}
        thinking_resp = await say(text="⏳ Comparing documents…")
        thinking_ts = (thinking_resp or {}).get("ts")
        use_simple = _get_user_simple(user_id)
        result = await _compare_documents(file_obj_a, file_obj, SLACK_BOT_TOKEN, user_id, simple=use_simple)
        text = _clean_for_slack(result)
        if thinking_ts:
            try:
                await client.chat_update(channel=channel, ts=thinking_ts, text=text)
            except Exception:
                await say(text=text)
        else:
            await say(text=text)
        return  # skip normal auto-brief for this file

    # Download file bytes now for later use in corrected-doc upload
    try:
        url = file_obj.get("url_private_download") or file_obj.get("url_private")
        if url:
            session = await _slack_dl_sessions.get()
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    file_bytes = await resp.read()
                    _register_file(file_id, file_obj, file_bytes)
                    if user_id:
                        _record_file_history(user_id, file_obj, file_bytes)
    except Exception as exc:
        log.debug("file_shared: could not pre-download bytes for %s: %s", file_id, exc)

    # Show a placeholder while we run the auto-brief
    try:
        placeholder = await client.chat_postMessage(channel=channel, text=f"📎 *{filename}* — reading…")
        placeholder_ts = (placeholder or {}).get("ts")
    except Exception:
        placeholder_ts = None

    # Auto-brief: 1-sentence description of the file (graceful fallback on error)
    description = await _auto_brief_file(file_obj, SLACK_BOT_TOKEN)

    blocks = _build_file_blocks(filename, description, mimetype, file_id)
    fallback_text = (
        f"📎 *{filename}*" + (f"\n_{description}_" if description else "") + "\n\nWhat would you like to do?"
    )

    try:
        if placeholder_ts:
            await client.chat_update(channel=channel, ts=placeholder_ts, text=fallback_text, blocks=blocks)
        else:
            await client.chat_postMessage(channel=channel, text=fallback_text, blocks=blocks)
    except Exception as exc:
        # Block Kit may fail if interactivity is not yet enabled in the manifest.
        log.warning("file_shared: Block Kit failed for %s (%s); using plain text", filename, exc)
        plain = _suggest_actions_for_file(filename, mimetype)
        if description:
            plain = f"_{description}_\n\n{plain}"
        try:
            if placeholder_ts:
                await client.chat_update(channel=channel, ts=placeholder_ts, text=plain)
            else:
                await client.chat_postMessage(channel=channel, text=plain)
        except Exception as exc2:
            log.warning("file_shared: plain fallback also failed for %s: %s", filename, exc2)


def _log_query_metrics(
    user_id: str,
    action: str,
    model_used: str,
    duration_ms: int,
    status: str,  # "ok" or "error"
) -> None:
    """Append one JSON line to logs/slack_metrics.jsonl. No PII stored."""
    import hashlib

    metrics_path = Path(os.environ.get("SLACK_METRICS_PATH", "logs/slack_metrics.jsonl"))
    try:
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "user_hash": hashlib.sha256(user_id.encode()).hexdigest()[:12],
            "action": action,
            "model": model_used,
            "duration_ms": duration_ms,
            "status": status,
        }
        with metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("_log_query_metrics: failed to write: %s", exc)


def _read_metrics_summary(path: Path | str) -> dict:
    """Read last 7 days from a metrics JSONL file and return a summary dict.

    Returns an empty dict (with ``no_data=True``) if the file doesn't exist or
    has no qualifying records.
    """
    path = Path(path)
    if not path.exists():
        return {"no_data": True}

    cutoff = time.time() - 7 * 24 * 3600
    total = 0
    errors = 0
    total_duration = 0
    action_counts: dict[str, int] = {}
    user_counts: dict[str, int] = {}

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ts", 0) < cutoff:
                continue
            total += 1
            if rec.get("status") == "error":
                errors += 1
            total_duration += rec.get("duration_ms", 0)
            action = rec.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
            user_hash = rec.get("user_hash", "")
            if user_hash:
                user_counts[user_hash] = user_counts.get(user_hash, 0) + 1

    if total == 0:
        return {"no_data": True}

    top_actions = sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "no_data": False,
        "total": total,
        "errors": errors,
        "avg_duration_ms": total_duration // total,
        "top_actions": top_actions,
        "top_users": [u for u, _ in top_users],
    }


_VALID_TIMES_RE = re.compile(
    r"^(?:([01]?\d|2[0-3]):([0-5]\d)|([01]?\d|2[0-3])([ap]m?)|off)$",
    re.IGNORECASE,
)


def _parse_schedule_time(text: str) -> int | None:
    """Parse a time string like '9am', '8:30', '14:00' into an hour (0-23). Returns None for 'off'."""
    text = text.strip().lower()
    if text == "off":
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        return int(m.group(1)) % 24
    m = re.match(r"^(\d{1,2})(am?|pm?)$", text)
    if m:
        hour = int(m.group(1))
        if "p" in m.group(2) and hour != 12:
            hour += 12
        elif "a" in m.group(2) and hour == 12:
            hour = 0
        return hour % 24
    return -1  # parse error


_VAGUE_PATTERNS: frozenset[str] = frozenset(
    [
        "help",
        "hi",
        "hello",
        "hey",
        "thanks",
        "ok",
        "okay",
        "this",
        "it",
        "stuff",
        "things",
        "something",
        "anything",
        "can you",
        "please",
        "yes",
        "no",
        "sure",
    ]
)


def _is_vague_question(text: str, has_files: bool = False) -> bool:
    """Return True if the message is too vague to answer well without clarification.

    A message is vague when it is short (< 6 words), has no file attachment to
    provide context, and the words used are all generic/filler terms.
    """
    if has_files:
        return False
    words = text.strip().lower().split()
    if len(words) == 0:
        return True
    if len(words) >= 6:
        return False
    # All words must be vague patterns (or punctuation) for it to be flagged
    clean_words = [w.strip("?!.,") for w in words if w.strip("?!.,")]
    return bool(clean_words) and all(w in _VAGUE_PATTERNS for w in clean_words)


# ---------------------------------------------------------------------------
# App Home tab
# ---------------------------------------------------------------------------


def _get_hermes_model() -> str:
    """Read the active Hermes model from ~/.hermes/config.yaml, fallback to env or default."""
    import os
    from pathlib import Path

    try:
        import yaml

        config_path = Path(os.path.expanduser("~/.hermes/config.yaml"))
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
            if isinstance(model_cfg, dict):
                return model_cfg.get("default") or os.getenv("HERMES_MODEL", "claude-sonnet-4.6")
    except Exception:
        pass
    return os.getenv("HERMES_MODEL", "claude-sonnet-4.6")


def _build_home_view(user_id: str, name: str) -> dict:
    """Build a Slack Block Kit Home tab view for the given user."""
    greeting_name = name if name and name != "there" else "there"
    hermes_model = _get_hermes_model()
    intro_text = f"*Hi {greeting_name}.* OpenClaw is your personal AI on Mac Mini M4 · Hermes · {hermes_model}"

    command_sections = [
        (
            "🤖 AI & Sessions",
            [
                "`/chat <prompt>` — Ask OpenClaw in Slack",
                "`/hermes <prompt>` — Start a Hermes thread",
                "`/h <prompt>` — Quick Hermes alias",
                "`/q <prompt>` — Quick ephemeral Hermes answer",
                "`/resume [prompt]` — Resume your last Hermes session",
                "`/sessions [n|resume n]` — Browse or resume Hermes sessions",
                "`/copilot <prompt>` — Start a threaded Copilot CLI session",
                "`/copilot-sessions` — List Copilot sessions",
                "`/copilot-attach <id>` — Attach to a Copilot session",
                "`/copilot-recap <id>` — Recap a Copilot session",
                "`/copilot-cancel <id>` — Cancel an active Copilot run",
                "`/copilot-end <id>` — End a Copilot session",
                "`/research <topic>` — Run the research pipeline",
            ],
        ),
        (
            "📁 Files & Workspace",
            [
                "`/files [query]` — Browse synced documents",
                "`/filesearch <query>` — Search your recent files",
                "`/batch <action>` — Process uploaded files",
                "`/brief` — Show your recent files",
                "`/template [name]` — List or download templates",
                "`/mypins` — Show your saved notes",
                "`/metrics` — View 7-day workspace usage metrics",
                "`/mystats` — View your personal usage stats",
                "`/digest on|off` — Toggle digest delivery",
                "`/schedule <time|off>` — Set your digest time",
            ],
        ),
        (
            "📬 Google & Integrations",
            [
                "`/inbox` — List unread Gmail messages",
                "`/email <n|setup|forget|query>` — Read or search email",
                "`/today` — Show today's calendar",
                "`/calendar [today|week]` — View calendar events",
                "`/clawbox <connect|sync|list|status>` — Manage Dropbox access",
                "`/clawchan ...` — List or archive Slack channels",
                "`/drive ...` — Search or read Google Drive files",
                "`/contacts ...` — Search Google Contacts",
            ],
        ),
        (
            "🖥️ Host & Ops",
            [
                "`/status` — Quick system health snapshot",
                "`/uptime` — Show grouped Uptime Kuma service status",
                "`/health` — Detailed OpenClaw bot health card",
                "`/host <shortcut>` — Run a saved host-bridge shortcut",
                "`/incident ...` — Start, inspect, or resolve incidents",
                "`/wake mbp|mbp2` — Wake a MacBook Pro on the LAN",
                "`/tailscale` — Show Tailscale device status",
                "`/nas df|ls <path>|free` — Browse NAS status and folders",
                "`/nas-share <path>` — Generate a NAS share link",
            ],
        ),
        (
            "🎬 Media",
            [
                "`/watching` — What's playing on Plex now",
                "`/media` — Active Plex streams, recent plays, recently added",
                "`/plex <status|recent|search|request>` — Check Plex / Overseerr",
                "`/request <title>` — Search Overseerr and request media",
                "`/arr` — View Sonarr/Radarr/Lidarr queues",
                "`/downloads` — View SABnzbd downloads",
                "`/qbt` — qBittorrent active torrents and speeds",
                "`/upcoming` — Show upcoming Sonarr episodes",
            ],
        ),
        (
            "⚙️ Preferences & Help",
            [
                "`/morning` — Trigger your morning briefing DM",
                "`/news [topic]` — Show top headlines or search a topic",
                "`/notify <message>` — Send a push notification via ntfy",
                "`/adguard` — DNS stats: queries, block rate, top blocked domains",
                "`/grafana` — Grafana health status and dashboard links",
                "`/help` — Full command reference",
                "`/simple on|off` — Toggle plain-language mode",
                "`/clear` — Reset your current session state",
                "`/nickname <name>` — Set the name OpenClaw uses for you",
            ],
        ),
    ]

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "⚕ OpenClaw", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": intro_text}},
        {"type": "divider"},
    ]

    for title, commands in command_sections:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}*\n" + "\n".join(commands)},
            }
        )

    recent = list(reversed(_file_history.get(user_id, [])))[:3]
    if recent:
        file_lines = []
        for f in recent:
            fname = f.get("name", "unknown")
            uploaded = f.get("uploaded_at", "")[:10] if f.get("uploaded_at") else ""
            file_lines.append(f"• *{fname}*" + (f" ({uploaded})" if uploaded else ""))
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*📁 Your recent files*\n" + "\n".join(file_lines)},
                },
            ]
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Dashboard: https://openclaw.davevoyles.synology.me/dashboard · 56 commands available · Type `/help` for the full list",
                }
            ],
        }
    )

    return {"type": "home", "blocks": blocks}


async def _post_clarification_prompt(client: Any, channel: str, user_id: str) -> None:
    """Post a friendly Block Kit clarification card to help the user get started."""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "👋 I want to make sure I help you well! What would you like to do?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📄 Ask about a file", "emoji": True},
                    "action_id": "clarify_file",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "💬 Ask me anything", "emoji": True},
                    "action_id": "clarify_question",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📝 Help me write something", "emoji": True},
                    "action_id": "clarify_write",
                },
            ],
        },
    ]
    try:
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="What would you like to do?",
            blocks=blocks,
        )
    except Exception as exc:
        log.warning("_post_clarification_prompt failed: %s", exc)


# ---------------------------------------------------------------------------
# Wave 10: Dropbox helpers
# ---------------------------------------------------------------------------


def _get_dropbox_client(token: str | None = None) -> Any | None:
    """Return a Dropbox client using *token* or the server-level DROPBOX_APP_TOKEN."""
    active_token = token or _DROPBOX_TOKEN
    if not active_token:
        return None
    try:
        import dropbox  # noqa: PLC0415

        return dropbox.Dropbox(active_token)
    except ImportError:
        return None


def _dropbox_list_folder(path: str, token: str | None = None) -> list[dict]:
    """List files in a Dropbox folder using *token* (or server token). Returns [] if not configured."""
    dbx = _get_dropbox_client(token=token)
    if dbx is None:
        return []
    try:
        result = dbx.files_list_folder(path)
        files = []
        for entry in result.entries:
            if hasattr(entry, "server_modified"):
                files.append(
                    {
                        "name": entry.name,
                        "size": getattr(entry, "size", 0),
                        "modified": entry.server_modified.strftime("%Y-%m-%d %H:%M"),
                        "id": entry.id,
                        "path": entry.path_lower,
                    }
                )
        return sorted(files, key=lambda f: f["modified"], reverse=True)
    except Exception:  # noqa: BLE001
        return []


async def _dropbox_sync_new_files(slack_client: Any) -> int:
    """Poll Dropbox for new files and sync them. Returns count of new files."""
    from datetime import datetime

    dbx = _get_dropbox_client()
    if dbx is None:
        return 0

    # Load cursor
    cursor: str | None = None
    if _DROPBOX_CURSOR_PATH.exists():
        try:
            cursor = json.loads(_DROPBOX_CURSOR_PATH.read_text()).get("cursor")
        except Exception:  # noqa: BLE001
            cursor = None

    try:
        if cursor:
            result = dbx.files_list_folder_continue(cursor)
        else:
            result = dbx.files_list_folder(_DROPBOX_FOLDER)
    except Exception:  # noqa: BLE001
        return 0

    new_count = 0
    _DROPBOX_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for entry in result.entries:
        if not hasattr(entry, "server_modified"):
            continue  # skip folders/deletes
        ext = Path(entry.name).suffix.lower()
        if ext not in {".docx", ".pdf", ".xlsx", ".txt", ".doc", ".csv"}:
            continue
        try:
            local_path = _DROPBOX_CACHE_DIR / entry.name
            dbx.files_download_to_file(str(local_path), entry.path_lower)
            if _DROPBOX_VIRTUAL_USER not in _file_history:
                _file_history[_DROPBOX_VIRTUAL_USER] = []
            _file_history[_DROPBOX_VIRTUAL_USER].append(
                {
                    "name": entry.name,
                    "uploaded_at": datetime.now().isoformat(),
                    "auto_brief": None,
                    "source": "dropbox",
                }
            )
            _save_file_history()
            new_count += 1
            if slack_client and _DROPBOX_NOTIFY_CHANNEL:
                await slack_client.chat_postMessage(
                    channel=_DROPBOX_NOTIFY_CHANNEL,
                    text=f"📦 New file from Dropbox: *{entry.name}* — ready to analyze!",
                )
        except Exception:  # noqa: BLE001
            continue

    try:
        _DROPBOX_CURSOR_PATH.write_text(json.dumps({"cursor": result.cursor}))
    except Exception:  # noqa: BLE001
        pass

    return new_count


# ---------------------------------------------------------------------------
# Wave 10 Yoda: Google Calendar helpers
# ---------------------------------------------------------------------------


async def _get_google_access_token() -> str | None:
    """Exchange refresh token for a short-lived access token. Cached for 55 min."""
    import time
    import urllib.parse
    import urllib.request

    if not (_GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET and _GOOGLE_REFRESH_TOKEN):
        return None
    now = time.time()
    if _google_token_cache.get("expires_at", 0) > now + 60:
        return _google_token_cache["access_token"]
    try:
        data = urllib.parse.urlencode(
            {
                "client_id": _GOOGLE_CLIENT_ID,
                "client_secret": _GOOGLE_CLIENT_SECRET,
                "refresh_token": _GOOGLE_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            }
        ).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        _google_token_cache["access_token"] = result["access_token"]
        _google_token_cache["expires_at"] = now + result.get("expires_in", 3600)
        return result["access_token"]
    except Exception:  # noqa: BLE001
        return None


async def _get_calendar_events(days_ahead: int = 0) -> list[dict]:
    """Fetch Google Calendar events for today or the next N days."""
    import urllib.parse
    import urllib.request

    token = await _get_google_access_token()
    if token is None:
        return []
    try:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = now.replace(hour=23, minute=59, second=59) + timedelta(days=days_ahead)
        params = urllib.parse.urlencode(
            {
                "calendarId": "primary",
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 20,
            }
        )
        req = urllib.request.Request(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        events = []
        for item in data.get("items", []):
            start = item.get("start", {})
            end = item.get("end", {})
            events.append(
                {
                    "summary": item.get("summary", "(no title)"),
                    "start": start.get("dateTime", start.get("date", "")),
                    "end": end.get("dateTime", end.get("date", "")),
                    "location": item.get("location", ""),
                }
            )
        return events
    except Exception:  # noqa: BLE001
        return []


def _format_calendar_events(events: list[dict], label: str = "today") -> str:
    """Format calendar events as plain text."""
    from datetime import datetime

    if not events:
        return f"📅 Nothing on the calendar {label}."
    lines = [f"📅 *Your schedule for {label}:*"]
    for ev in events:
        start_str = ev["start"]
        try:
            dt = datetime.fromisoformat(start_str)
            time_part = dt.strftime("%-I:%M %p")
        except (ValueError, TypeError):
            time_part = start_str
        loc = f"  ·  📍 {ev['location']}" if ev.get("location") else ""
        lines.append(f"• {time_part} — {ev['summary']}{loc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wave 10 Leia: Gmail helpers
# ---------------------------------------------------------------------------

_gmail_message_cache: list[dict] = []  # stores last /inbox result per session


async def _get_gmail_unread(max_results: int = 5) -> list[dict]:
    """Fetch last N unread emails from Gmail inbox (metadata only, no body)."""
    token = await _get_google_access_token()
    if token is None:
        return []
    try:
        import urllib.parse  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        params = urllib.parse.urlencode(
            {
                "labelIds": "INBOX,UNREAD",
                "maxResults": max_results,
            }
        )
        req = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        messages = data.get("messages", [])
        results = []
        for msg in messages:
            msg_id = msg["id"]
            meta_params = urllib.parse.urlencode(
                {
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                }
            )
            meta_req = urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?{meta_params}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(meta_req, timeout=10) as resp:
                meta = json.loads(resp.read())
            headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            results.append(
                {
                    "id": msg_id,
                    "subject": headers.get("Subject", "(no subject)"),
                    "from": headers.get("From", "Unknown"),
                    "date": headers.get("Date", ""),
                }
            )
        return results
    except Exception:  # noqa: BLE001
        return []


async def _get_gmail_body(message_id: str) -> str:
    """Fetch full email body text, truncated at 4000 chars."""
    token = await _get_google_access_token()
    if token is None:
        return "(Gmail not configured)"
    try:
        import base64  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        def _extract_text(payload: dict) -> str:
            if payload.get("mimeType") == "text/plain":
                encoded = payload.get("body", {}).get("data", "")
                if encoded:
                    return base64.urlsafe_b64decode(encoded + "==").decode("utf-8", errors="replace")
            for part in payload.get("parts", []):
                text = _extract_text(part)
                if text:
                    return text
            return ""

        body = _extract_text(data.get("payload", {}))
        if not body:
            body = "(empty email)"
        return body[:4000]
    except Exception:  # noqa: BLE001
        return "(Could not load email body)"


def _register_core_handlers(app: Any) -> None:
    """Register core event handlers: App Home, @mention, DM, /chat, reactions, and basic commands."""
    global _slack_app_client
    _slack_app_client = app.client

    # ------------------------------------------------------------------
    # Handler: App Home tab opened
    # ------------------------------------------------------------------

    @app.event("app_home_opened")
    async def handle_app_home_opened(event: dict[str, Any], client: Any) -> None:
        """Publish a personalized Home tab view when the user opens the App Home."""
        user_id: str = event.get("user", "")
        if not user_id:
            return
        name: str = (_personas.get(user_id) or {}).get("name", "there")
        view = _build_home_view(user_id, name)
        try:
            await client.views_publish(user_id=user_id, view=view)
        except Exception as exc:
            log.warning("handle_app_home_opened: failed to publish view for %s: %s", user_id, exc)

    # ------------------------------------------------------------------
    # Handler: @mention in a channel
    # ------------------------------------------------------------------

    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], say: Any, client: Any) -> None:
        user_id: str = event.get("user", "unknown")
        channel: str = event.get("channel", "")
        msg_ts: str = event.get("ts", "")
        thread_ts: str = event.get("thread_ts") or msg_ts
        raw_text: str = event.get("text", "")

        _set_trace(command="mention")

        asyncio.create_task(_check_new_user_onboarding(user_id, client))

        # Strip @mention token(s) and extract optional --model flag
        prompt_raw = _MENTION_RE.sub("", raw_text).strip()
        files: list[dict] = event.get("files", [])

        if not prompt_raw and not files:
            await say(
                text=_WELCOME_MESSAGE,
                thread_ts=thread_ts,
            )
            return

        prompt, model_pref, use_simple = _parse_flags(prompt_raw)
        use_simple = use_simple or _get_user_simple(user_id)

        # Screenshot intent — capture and upload before any LLM call
        if await _handle_screenshot_intent(client, channel, prompt_raw, thread_ts=thread_ts):
            return

        # Browser navigation intent — fetch page content before any LLM call
        if await _handle_browser_nav_intent(client, channel, prompt_raw, thread_ts=thread_ts):
            return
        if not event.get("files"):
            match = _match_question_to_history(user_id, prompt_raw)
            if match:
                filename = match.get("name", "")
                suggestion_msg = (
                    f"💡 Did you mean to use `{filename}`? Type `/files {filename}` to select it, or just ask away!"
                )
                await say(text=suggestion_msg, thread_ts=thread_ts)

        # Enrich prompt with any uploaded file content
        if files:
            prompt = await _process_slack_files(files, SLACK_BOT_TOKEN, prompt)

        # Build thread history when this is a reply in an existing thread
        history: list[dict] = []
        if event.get("thread_ts"):
            history = await _build_thread_history(client, channel, thread_ts)

        thinking_resp = await say(text="⏳ Thinking…", thread_ts=thread_ts)
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=thread_ts,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref=model_pref,
            history=history,
            simple=use_simple,
        )
        _onboarded_users.add(user_id)

    # ------------------------------------------------------------------
    # Handler: DMs (direct messages)
    # ------------------------------------------------------------------

    @app.event("message")
    async def handle_dm(event: dict[str, Any], say: Any, client: Any) -> None:
        # Ignore bot messages, edited messages, and non-DM channels
        if event.get("bot_id") or event.get("subtype"):
            return

        # --- Phase 3: route thread replies to active /copilot sessions ---
        # This runs BEFORE the channel-type guard so it works in both DMs and
        # channels. We only act when the thread matches a registered session.
        thread_ts_evt: str | None = event.get("thread_ts")
        if thread_ts_evt:
            try:
                from host_bridge import get_session_manager

                _mgr = get_session_manager()
                _rec = _mgr.find_by_thread(event.get("channel", ""), thread_ts_evt)
            except Exception:  # noqa: BLE001
                _mgr = None
                _rec = None
            if _rec is not None and (_is_hermes_session(_rec) or _session_is_live(_mgr, _rec.session_id)):
                _txt = (event.get("text") or "").strip()
                _uid = event.get("user", "")
                if not _txt:
                    return
                if _rec.slack_user != _uid:
                    try:
                        await client.chat_postEphemeral(
                            channel=event.get("channel", ""),
                            user=_uid,
                            text="🙅 this Copilot session belongs to someone else",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    return
                if _is_hermes_session(_rec) and _rec.status == "ended":
                    try:
                        await client.chat_postEphemeral(
                            channel=event.get("channel", ""),
                            user=_uid,
                            text="🙅 this Copilot session is already closed",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    return
                # React with ⏳ so the user knows it was accepted as a turn
                try:
                    await client.reactions_add(
                        name="hourglass_flowing_sand",
                        channel=event.get("channel", ""),
                        timestamp=event.get("ts", ""),
                    )
                except Exception:  # noqa: BLE001
                    pass
                if _is_hermes_session(_rec):
                    _err = await _run_hermes_turn(_rec, _txt)
                else:
                    _err = await _mgr.send_turn(_rec.session_id, _txt, slack_user=_uid)
                if _err:
                    try:
                        await client.chat_postMessage(
                            channel=event.get("channel", ""),
                            thread_ts=thread_ts_evt,
                            text=f"❌ {_err}",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return  # do NOT fall through to LLM chat for session turns

        if event.get("channel_type") != "im":
            return

        user_id: str = event.get("user", "unknown")
        channel: str = event.get("channel", "")
        raw_text: str = (event.get("text") or "").strip()
        files: list[dict] = event.get("files", [])

        # Record this message so catch-up logic knows the latest processed ts.
        _record_last_seen(channel, event.get("ts", ""))

        _set_trace(command="dm")
        asyncio.create_task(_check_new_user_onboarding(user_id, client))

        if not raw_text and not files:
            await say(text=_WELCOME_MESSAGE)
            return

        prompt, model_pref, use_simple = _parse_flags(raw_text)
        use_simple = use_simple or _get_user_simple(user_id)

        # Screenshot intent — capture and upload before any LLM call
        thread_ts_dm: str | None = event.get("thread_ts")
        if await _handle_screenshot_intent(client, channel, raw_text, thread_ts=thread_ts_dm):
            return

        # Browser navigation intent — fetch page content before any LLM call
        if await _handle_browser_nav_intent(client, channel, raw_text, thread_ts=thread_ts_dm):
            return

        # Clarification prompt for vague top-level DMs (not thread replies)
        has_files = bool(event.get("files"))
        if event.get("thread_ts") is None and _is_vague_question(raw_text, has_files=has_files):
            await _post_clarification_prompt(client, channel, user_id)
            return

        # Batch processing: multiple files in one message
        if _is_batch_upload(files):
            msg_ts = event.get("ts", "")
            await _process_batch(client, channel, msg_ts, files, "summarize")
            return

        # Research pipeline: Perplexity search + optional Gemini document incorporation
        if _is_research_request(prompt):
            active_file_id = (_user_prefs.get(user_id) or {}).get("active_file_id")
            file_obj_for_research: dict | None = None
            if active_file_id and active_file_id in _file_registry:
                reg_entry = _file_registry[active_file_id]
                if isinstance(reg_entry, dict) and "file_obj" in reg_entry:
                    file_obj_for_research = reg_entry["file_obj"]
            await _run_research_pipeline(client, channel, user_id, prompt, file_obj_for_research)
            return

        # Smart file suggestion — only when no file is attached
        if not event.get("files"):
            match = _match_question_to_history(user_id, prompt)
            if match:
                filename = match.get("name", "")
                suggestion_msg = (
                    f"💡 Did you mean to use `{filename}`? Type `/files {filename}` to select it, or just ask away!"
                )
                await say(text=suggestion_msg)

        # Enrich prompt with any uploaded file content
        if files:
            prompt = await _process_slack_files(files, SLACK_BOT_TOKEN, prompt)

        # Carry thread history for DM thread replies (same as handle_mention)
        thread_ts: str | None = event.get("thread_ts")
        history: list[dict] | None = None
        if thread_ts:
            history = await _build_thread_history(client, channel, thread_ts)

        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        response_text = await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=thread_ts,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref=model_pref,
            simple=use_simple,
            history=history,
        )
        if response_text:
            asyncio.create_task(_send_ntfy("OpenClaw Reply", f"Bot replied in DM: {response_text[:100]}"))
        _onboarded_users.add(user_id)

    # ------------------------------------------------------------------
    # Handler: /chat slash command
    # ------------------------------------------------------------------

    @app.command("/chat")
    async def handle_slash_ask(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()  # must acknowledge within 3 seconds

        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "").strip()

        if not raw_text:
            await say(text="Usage: `/chat your question here`\nNeed ideas? Type `/help` to see examples.")
            return

        prompt, model_pref, use_simple = _parse_flags(raw_text)
        use_simple = use_simple or _get_user_simple(user_id)

        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref=model_pref,
            simple=use_simple,
        )

    # ------------------------------------------------------------------
    # Handler: 👍/👎 reaction feedback
    # ------------------------------------------------------------------

    @app.event("reaction_added")
    async def handle_reaction(event: dict[str, Any], client: Any) -> None:
        emoji: str = event.get("reaction", "")
        if emoji not in ("thumbsup", "+1", "thumbsdown", "-1"):
            return

        item: dict = event.get("item", {})
        if item.get("type") != "message":
            return

        channel: str = item.get("channel", "")
        ts: str = item.get("ts", "")
        key = (channel, ts)

        if key not in _bot_message_registry:
            return

        rating = 1 if emoji in ("thumbsup", "+1") else -1
        original_user = _bot_message_registry[key]
        reacting_user = event.get("user", "unknown")
        log.info(
            "Slack feedback: rating=%+d message_ts=%s channel=%s original_user=%s reacting_user=%s",
            rating,
            ts,
            channel,
            original_user,
            reacting_user,
        )
        # Acknowledge with a quiet emoji so the user knows it registered
        try:
            ack_emoji = "white_check_mark" if rating > 0 else "noted"
            await client.reactions_add(channel=channel, timestamp=ts, name=ack_emoji)
        except Exception:
            pass  # reaction may already exist; not critical

    # ------------------------------------------------------------------
    # Handler: /help slash command — beginner-friendly guide
    # ------------------------------------------------------------------

    @app.command("/help")
    async def handle_slash_help(ack: Any, say: Any) -> None:
        await ack()
        try:
            await say(text=_HELP_TEXT)
        except Exception as e:
            log.warning("Slack send failed in /help: %s", e)

    # ------------------------------------------------------------------
    # Handler: /health slash command — bot health card
    # ------------------------------------------------------------------

    @app.command("/health")
    async def handle_slash_status(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "unknown")
        channel = body.get("channel_id", "")

        now = time.monotonic()
        uptime_secs = int(now - _BOT_START_TIME) if _BOT_START_TIME else 0
        hours, remainder = divmod(uptime_secs, 3600)
        minutes = remainder // 60
        uptime_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"

        version = os.environ.get("OPENCLAW_VERSION", "dev")
        lines: list[str] = [f"🤖 *OpenClaw Bot Status* (v{version})\n"]
        lines.append(f"⏱ Uptime: {uptime_str}  |  Queries today: {_daily_query_count}")

        # Mac Mini reachability
        mac_mini_ip = os.getenv("OPENCLAW_MAC_MINI_IP", "192.168.1.93")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as sess:
                async with sess.get(f"http://{mac_mini_ip}:8080/health") as resp:
                    lines.append("✅ Mac Mini: reachable" if resp.status == 200 else f"⚠️ Mac Mini: HTTP {resp.status}")
        except Exception:
            lines.append("❌ Mac Mini: unreachable")

        # /ai-files inventory
        try:
            if _AI_FILES_DIR.exists():
                files = [
                    f for f in _AI_FILES_DIR.iterdir() if f.is_file() and f.suffix.lower() in _ALLOWED_UPLOAD_EXTENSIONS
                ]
                lines.append(f"📁 Storage: {len(files)} file(s)")
            else:
                lines.append("📁 Storage: folder not found")
        except Exception:
            lines.append("📁 Storage: error reading")

        # Last sync
        try:
            if _LAST_SYNC_PATH.exists():
                sync_data = json.loads(_LAST_SYNC_PATH.read_text(encoding="utf-8"))
                sync_ts = sync_data.get("timestamp", "")
                sync_file = sync_data.get("last_file", "")
                lines.append(f"🔄 Last sync: {sync_ts}" + (f" ({sync_file})" if sync_file else ""))
            else:
                lines.append("🔄 Last sync: none recorded")
        except Exception:
            lines.append("🔄 Last sync: unknown")

        # Model health
        model_lines: list[str] = []
        for model, ts in sorted(_model_last_success.items()):
            ago = int(now - ts)
            ago_str = f"{ago}s ago" if ago < 60 else f"{ago // 60}m ago"
            model_lines.append(f"  • {model}: {ago_str}")
        lines.append("\n*Model health:*\n" + ("\n".join(model_lines) if model_lines else "  (none used yet)"))

        if SLACK_NOTIFY_USER_ID:
            lines.append(f"🔔 File alerts: enabled (<@{SLACK_NOTIFY_USER_ID}>)")

        status_text = "\n".join(lines)
        try:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=status_text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": status_text}}],
            )
        except Exception as exc:
            log.warning("handle_slash_status: failed to post ephemeral: %s", exc)

    @app.command("/news")
    async def handle_slash_news(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import aiohttp

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        api_key = os.environ.get("NEWSAPI_KEY", "")
        if not api_key:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="⚠️ NEWSAPI_KEY not configured.",
            )
            return

        topic = (body.get("text", "") or "").strip()

        try:
            params: dict[str, str] = {"country": "us", "pageSize": "8", "apiKey": api_key}
            if topic:
                params["q"] = topic
                params.pop("country", None)
                url = "https://newsapi.org/v2/everything"
                params["sortBy"] = "publishedAt"
                params["language"] = "en"
            else:
                url = "https://newsapi.org/v2/top-headlines"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    data = await resp.json()

            articles = data.get("articles", [])
            if not articles:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="📰 No headlines found.",
                )
                return

            header = f"📰 *Top Headlines{' — ' + topic if topic else ''}*\n"
            lines = [header]
            for article in articles[:8]:
                title = article.get("title", "?")
                source = article.get("source", {}).get("name", "")
                if source:
                    lines.append(f"• {title} _{source}_")
                else:
                    lines.append(f"• {title}")

            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="\n".join(lines),
            )
        except Exception as exc:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ News fetch failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Handler: /digest — per-user periodic file digest opt-in
    # ------------------------------------------------------------------

    @app.command("/digest")
    async def handle_slash_digest(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        arg: str = (body.get("text") or "").strip().lower()
        prefs = _load_digest_prefs()
        user_pref = prefs.setdefault(user_id, {"enabled": False, "last_sent": 0})

        if arg in ("on", "enable", "1", "yes", "daily"):
            user_pref["enabled"] = True
            _save_digest_prefs(prefs)
            try:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text=(
                        f"✅ *Digest enabled!* I'll DM you every {_DIGEST_LOOKBACK_HOURS} hours "
                        f"with a summary of recently synced files. "
                        f"Type `/digest off` to stop anytime."
                    ),
                )
            except Exception as exc:
                log.warning("handle_slash_digest on: %s", exc)
        elif arg in ("off", "disable", "0", "no"):
            user_pref["enabled"] = False
            _save_digest_prefs(prefs)
            try:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text="🔕 *Digest disabled.* You won't receive automatic file summaries. Type `/digest on` to re-enable.",
                )
            except Exception as exc:
                log.warning("handle_slash_digest off: %s", exc)
        else:
            enabled = user_pref.get("enabled", False)
            last_sent = user_pref.get("last_sent", 0)
            last_str = _human_time(last_sent) if last_sent else "never"
            status_emoji = "✅" if enabled else "🔕"
            try:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text=(
                        f"{status_emoji} *Digest status:* {'enabled' if enabled else 'disabled'}\n"
                        f"• Last sent: {last_str}\n"
                        f"• Lookback window: {_DIGEST_LOOKBACK_HOURS} hours\n\n"
                        f"Commands: `/digest on` · `/digest off`"
                    ),
                )
            except Exception as exc:
                log.warning("handle_slash_digest status: %s", exc)

    # ------------------------------------------------------------------
    # Handler: /simple — toggle persistent plain-language mode per user
    # ------------------------------------------------------------------

    @app.command("/simple")
    async def handle_slash_simple(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        arg: str = (body.get("text") or "").strip().lower()

        if arg in ("on", "enable", "1", "yes"):
            _set_user_simple(user_id, True)
            await say(
                text=(
                    "✅ *Simple mode on!* I'll always give you plain, easy-to-read answers — "
                    "no jargon, short sentences. You don't need to add anything to your messages.\n"
                    "Type `/simple off` any time to go back to normal."
                )
            )
        elif arg in ("off", "disable", "0", "no"):
            _set_user_simple(user_id, False)
            await say(
                text=(
                    "🔄 *Simple mode off.* Back to normal responses.\n"
                    "You can still add `--simple` to any individual message for a plain answer."
                )
            )
        else:
            status = "on ✅" if _get_user_simple(user_id) else "off"
            await say(
                text=(
                    f"Simple mode is currently *{status}*.\n\n"
                    "• `/simple on` — always get plain, easy-to-read answers\n"
                    "• `/simple off` — go back to normal\n\n"
                    "_Tip: turn it on once and forget about it!_"
                )
            )

    # ------------------------------------------------------------------
    # Handler: /research — Perplexity research pipeline slash command
    # ------------------------------------------------------------------

    @app.command("/research")
    async def handle_slash_research(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "").strip()

        if not raw_text:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="Usage: `/research climate change for my annual report`",
            )
            return

        try:
            await client.chat_postEphemeral(channel=channel, user=user_id, text="⏳ Starting research pipeline…")
        except Exception:
            pass

        active_file_id = (_user_prefs.get(user_id) or {}).get("active_file_id")
        file_obj_for_research: dict | None = None
        if active_file_id and active_file_id in _file_registry:
            reg_entry = _file_registry[active_file_id]
            if isinstance(reg_entry, dict) and "file_obj" in reg_entry:
                file_obj_for_research = reg_entry["file_obj"]

        await _run_research_pipeline(client, channel, user_id, raw_text, file_obj_for_research)

    # ------------------------------------------------------------------
    # Handler: /batch — batch process all registered files
    # ------------------------------------------------------------------

    @app.command("/batch")
    async def handle_slash_batch(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "summarize").strip()

        # Collect all registered files (registry stores {"file_obj": ..., ...})
        user_files: list[dict] = []
        for fid, fobj in _file_registry.items():
            reg_file_obj = fobj.get("file_obj") if isinstance(fobj, dict) and "file_obj" in fobj else fobj
            if reg_file_obj:
                user_files.append(reg_file_obj)

        if not user_files:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="⚠️ No files found. Upload your files first, then run `/batch`.",
            )
            return

        action = raw_text.split()[0] if raw_text else "summarize"
        try:
            resp = await client.chat_postMessage(
                channel=channel,
                text=f"📦 Starting batch {action} on {len(user_files)} file(s)...",
            )
        except Exception as e:
            log.warning("Slack send failed in /batch: %s", e)
            resp = {}
        thread_ts = (resp or {}).get("ts", "")

        await _process_batch(client, channel, thread_ts, user_files, action)


def _register_file_handlers(app: Any) -> None:
    """Register file event handlers: file_shared, Block Kit action buttons, /files command, compare/translate."""
    # ------------------------------------------------------------------
    # Handler: file_shared — auto-brief + Block Kit action buttons
    # ------------------------------------------------------------------

    @app.event("file_shared")
    async def handle_file_shared(event: dict[str, Any], client: Any, say: Any) -> None:
        await _handle_batch_file(event, client, say)

    # ------------------------------------------------------------------
    # Handlers: Block Kit file action buttons
    # Each button in the file suggestion message carries the file_id as
    # its value; the handler re-downloads the file and runs the LLM.
    # Requires interactivity enabled in the manifest: make slack-manifest
    # ------------------------------------------------------------------

    async def _return_corrected_doc(
        file_obj: dict,
        channel: str,
        user_id: str,
        corrected_text: str,
        client: Any,
    ) -> None:
        """Upload a corrected .docx back to Slack. Skips non-.docx files gracefully."""
        filename = file_obj.get("name", "document.docx")
        if not filename.lower().endswith(".docx"):
            try:
                await client.chat_postMessage(
                    channel=channel,
                    text="ℹ️ Corrected document return is only supported for .docx files.",
                )
            except Exception:
                pass
            return

        try:
            corrected_filename = "corrected_" + filename
            new_bytes = await create_word(title=corrected_filename, content=corrected_text)
            await client.files_upload_v2(
                channel=channel,
                filename=corrected_filename,
                content=new_bytes,
                initial_comment="✅ Here's your corrected document!",
            )
        except Exception as exc:
            log.warning("_return_corrected_doc: upload failed for %s: %s", filename, exc)

    async def _dispatch_file_action(action_id: str, ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        await ack()

        user_id: str = (body.get("user") or {}).get("id", "unknown")
        actions: list[dict] = body.get("actions", [{}])
        file_id: str = (actions[0] if actions else {}).get("value", "")
        channel: str = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")

        if not file_id or not channel:
            await say(text="⚠️ Couldn't identify the file. Please upload it again.")
            return

        file_obj = _file_registry.get(file_id)
        if not file_obj:
            await say(text="⚠️ I've lost track of that file — try uploading it again and I'll be ready.")
            return

        # Registry now stores {"file_obj": ..., "file_bytes": ...}
        if isinstance(file_obj, dict) and "file_obj" in file_obj:
            file_obj = file_obj["file_obj"]

        # file_chart: generate PNG chart from spreadsheet data
        if action_id == "file_chart":
            thinking_resp = await say(text="⏳ Generating chart…")
            thinking_ts = (thinking_resp or {}).get("ts")
            png_bytes = await _generate_chart(file_obj, SLACK_BOT_TOKEN, user_id)
            if png_bytes:
                try:
                    await client.files_upload_v2(
                        channel=channel,
                        content=png_bytes,
                        filename=f"chart_{file_obj.get('name', 'data')}.png",
                        title=f"Chart: {file_obj.get('name', 'data')}",
                    )
                    if thinking_ts:
                        await client.chat_delete(channel=channel, ts=thinking_ts)
                except Exception as exc:
                    log.warning("_dispatch_file_action: chart upload failed: %s", exc)
                    if thinking_ts:
                        await client.chat_update(
                            channel=channel,
                            ts=thinking_ts,
                            text="⚠️ Chart generated but upload failed.",
                        )
            else:
                msg = "📊 Chart generation requires `matplotlib` and `openpyxl`. Ask an admin to install them."
                if thinking_ts:
                    await client.chat_update(channel=channel, ts=thinking_ts, text=msg)
                else:
                    await say(text=msg)
            return  # don't fall through to normal dispatch

        prompt_text = _FILE_ACTION_PROMPTS.get(action_id, "Please analyze this file.")

        # Handle files referenced from /ai-files directly (no Slack download needed)
        if action_id == "file_research":
            prompt = await _two_phase_research(file_obj, SLACK_BOT_TOKEN, prompt_text)
        elif file_obj.get("ai_files_path"):
            file_content = await file_skills.read_local_file(file_obj["ai_files_path"])
            prompt = f"{prompt_text}\n\n--- File: {file_obj['name']} ---\n{file_content}\n--- End ---"
        else:
            prompt = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, prompt_text)

        use_simple = _get_user_simple(user_id)

        # Smart model routing based on file type + action
        filename_for_routing = file_obj.get("name", "")
        if action_id == "file_research":
            model_pref = "gemini"
        else:
            model_pref = _route_model_for_file(filename_for_routing, action_id)

        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            simple=use_simple,
            model_pref=model_pref,
        )

        # For proofread on .docx: also return a corrected document file
        if action_id == "file_proofread":
            filename = (file_obj.get("name") or "").lower()
            if filename.endswith(".docx"):
                try:
                    correction_prompt = await _process_slack_files(
                        [file_obj],
                        SLACK_BOT_TOKEN,
                        "Return ONLY the fully corrected version of this document as plain text. "
                        "Fix all spelling, grammar, and punctuation errors. "
                        "Preserve the same paragraph structure.",
                    )
                    corrected_text = await _ask(correction_prompt, user_id=user_id, simple=False)
                    await _return_corrected_doc(file_obj, channel, user_id, corrected_text, client)
                except Exception as exc:
                    log.warning("_dispatch_file_action: corrected doc failed for %s: %s", filename, exc)

    # ------------------------------------------------------------------
    # Handler: /files — browse and reference synced documents
    # ------------------------------------------------------------------

    @app.command("/files")
    async def handle_slash_files(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id") or "unknown"
        channel: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip()

        if text.lower() in ("recent", "history"):
            history = _file_history.get(user_id, [])
            if not history:
                await client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text="📂 No file history yet. Upload a file and it'll appear here next time.",
                )
                return
            import time as _time

            lines = [f"📋 *Your recent files (last {len(history)}):*"]
            for entry in history:
                name = entry.get("name", "?")
                size = entry.get("size", 0)
                ts = entry.get("last_used_ts", 0)
                ago = int(_time.time() - ts)
                if ago < 3600:
                    ago_str = f"{ago // 60}m ago"
                elif ago < 86400:
                    ago_str = f"{ago // 3600}h ago"
                else:
                    ago_str = f"{ago // 86400}d ago"
                lines.append(f"  • `{name}` ({size:,} bytes, {ago_str})")
            lines.append("\n_Tip: `/files <name>` to select a file from storage_")
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="\n".join(lines),
            )
            return

        if not text:
            # List mode — show all files in /ai-files
            listing = await file_skills.list_local_files("/ai-files")

            if "empty" in listing.lower() or "not found" in listing.lower():
                await client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text=("📂 No files yet! Drop a Word doc into your OpenClaw folder and it'll appear here."),
                )
                return

            lines = listing.splitlines()
            file_blocks: list[dict] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "📁 *Files in OpenClaw:*"}},
            ]
            for line in lines[1:21]:  # skip header line, cap at 20
                stripped = line.strip()
                if stripped:
                    file_blocks.append(
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"`{stripped}`"},
                        }
                    )
            file_blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "Tip: `/files budget.xlsx` to select a file"}],
                }
            )
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                blocks=file_blocks,
                text="Files in OpenClaw",
            )
            return

        # Reference mode — select a specific file from /ai-files
        target = Path("/ai-files") / text
        if not target.exists() or not target.is_file():
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=f"⚠️ File not found: `{text}`. Use `/files` to see available files.",
            )
            return

        stat = target.stat()
        synthetic_file_id = f"aifiles::{text}"
        synthetic_file_obj = {
            "id": synthetic_file_id,
            "name": text,
            "mimetype": _mimetype_for(text),
            "size": stat.st_size,
            "url_private": None,
            "ai_files_path": str(target),
        }
        _register_file(synthetic_file_id, synthetic_file_obj)

        if user_id not in _user_prefs:
            _user_prefs[user_id] = {}
        _user_prefs[user_id]["active_file_id"] = synthetic_file_id
        _save_prefs()

        blocks = _build_file_blocks(
            filename=text,
            description=f"From OpenClaw storage ({stat.st_size:,} bytes)",
            mimetype=synthetic_file_obj["mimetype"],
            file_id=synthetic_file_id,
        )
        await client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=f"Selected file: {text}",
        )

    # Register one handler per action_id using closures
    # Note: file_translate and file_compare are excluded from the generic dispatch loop because
    # they have their own flows registered separately below.
    _excluded_from_generic = {"file_translate", "file_compare"}
    for _action_id in [k for k in _FILE_ACTION_PROMPTS.keys() if k not in _excluded_from_generic]:

        def _make_handler(aid: str) -> Any:
            async def handler(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
                await _dispatch_file_action(aid, ack, body, client, say)

            handler.__name__ = f"handle_{aid}"
            return handler

        app.action(_action_id)(_make_handler(_action_id))


def _register_slash_commands(app: Any) -> None:
    """Register slash command handlers: /metrics, /brief, /mystats, /template, /mypins, /filesearch, /schedule, /clear, /nickname."""
    # ------------------------------------------------------------------
    # Handler: /metrics — usage summary for last 7 days
    # ------------------------------------------------------------------

    @app.command("/metrics")
    async def handle_slash_metrics(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        channel = body.get("channel_id", "")
        user_id = body.get("user_id", "unknown")

        metrics_path = Path(os.environ.get("SLACK_METRICS_PATH", "logs/slack_metrics.jsonl"))
        summary = _read_metrics_summary(metrics_path)

        if summary.get("no_data"):
            text = "📊 *OpenClaw Usage* — No metrics recorded yet."
        else:
            top_actions_lines = "\n".join(f"  • {action}: {count}" for action, count in summary["top_actions"])
            top_users_str = ", ".join(summary["top_users"]) or "—"
            text = (
                f"📊 *OpenClaw Usage (Last 7 Days)*\n"
                f"Total queries: {summary['total']}  |  "
                f"Errors: {summary['errors']}  |  "
                f"Avg response: {summary['avg_duration_ms']:,}ms\n\n"
                f"*Top actions:*\n{top_actions_lines}\n\n"
                f"*Top users (anonymized):* {top_users_str}"
            )

        try:
            await client.chat_postEphemeral(channel=channel, user=user_id, text=text)
        except Exception as exc:
            log.warning("handle_slash_metrics: failed to post ephemeral: %s", exc)

    # ------------------------------------------------------------------
    # Handler: /clear — reset session state for the calling user
    # ------------------------------------------------------------------

    @app.command("/brief")
    async def handle_slash_brief(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        entries = _file_history.get(user_id, [])
        if not entries:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=user_id,
                text="📂 You haven't uploaded any files yet. Drop a file here to get started!",
            )
            return

        import datetime

        recent = list(reversed(entries))[:5]
        lines = []
        for entry in recent:
            name = entry.get("name", "unknown")
            uploaded_at = entry.get("uploaded_at", "")
            if uploaded_at:
                try:
                    dt = datetime.datetime.fromisoformat(uploaded_at)
                    delta = datetime.datetime.now() - dt
                    days = delta.days
                    if days == 0:
                        when = "today"
                    elif days == 1:
                        when = "yesterday"
                    else:
                        when = f"{days} days ago"
                except Exception:
                    when = uploaded_at[:10]
            else:
                when = "recently"
            lines.append(f"• *{name}* — {when}")

        text = "*📂 Your recent files:*\n" + "\n".join(lines)
        text += "\n\n_Type `/files recent` to see the full list, or just upload a new file!_"
        await client.chat_postEphemeral(
            channel=body["channel_id"],
            user=user_id,
            text=text,
        )

    @app.command("/mystats")
    async def handle_slash_mystats(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import hashlib

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:12]

        metrics_path = Path(__file__).parent.parent / "logs" / "slack_metrics.jsonl"

        query_count = 0
        file_count = 0
        total_ms = 0
        error_count = 0
        action_counts: dict[str, int] = {}

        if metrics_path.exists():
            try:
                with open(metrics_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("user_id") != user_hash:
                            continue
                        query_count += 1
                        action = rec.get("action", "")
                        action_counts[action] = action_counts.get(action, 0) + 1
                        if "file" in action.lower():
                            file_count += 1
                        dur = rec.get("duration_ms", 0)
                        if dur:
                            total_ms += dur
                        if rec.get("status") == "error":
                            error_count += 1
            except Exception as exc:
                log.warning("mystats: error reading metrics: %s", exc)

        avg_ms = int(total_ms / query_count) if query_count > 0 else 0
        top_actions = sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{a} ({c})" for a, c in top_actions) if top_actions else "none yet"

        text = (
            f"*📊 Your OpenClaw Stats*\n\n"
            f"• Queries answered: *{query_count}*\n"
            f"• Files processed: *{file_count}*\n"
            f"• Average response time: *{avg_ms}ms*\n"
            f"• Errors: *{error_count}*\n"
            f"• Top actions: {top_str}\n\n"
            f"_Stats tracked since OpenClaw Wave 4. Your ID is anonymized._"
        )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
        )

    @app.command("/template")
    async def handle_slash_template(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        channel_id: str = body.get("channel_id", user_id)
        arg: str = (body.get("text") or "").strip().lower()

        _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        available: list[Path] = sorted(
            [f for f in _TEMPLATES_DIR.iterdir() if f.is_file() and f.suffix in {".xlsx", ".docx", ".pdf", ".txt"}]
            if _TEMPLATES_DIR.exists()
            else []
        )

        if not arg or arg == "list":
            if not available:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="📂 No templates available yet. Contact your OpenClaw admin to add templates to `data/templates/`.",
                )
                return
            names = "\n".join(f"• `{f.stem}` ({f.suffix[1:].upper()})" for f in available)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"📄 *Available templates ({len(available)})* — type `/template <name>` to download:\n\n" + names
                ),
            )
            return

        match: Path | None = None
        for tpl in available:
            if tpl.stem.lower() == arg:
                match = tpl
                break
        if not match:
            for tpl in available:
                if tpl.stem.lower().startswith(arg):
                    match = tpl
                    break

        if not match:
            names_str = ", ".join(f"`{f.stem}`" for f in available)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"❓ Template `{arg}` not found.\n"
                    f"Available: {names_str or 'none yet'}\n"
                    f"Type `/template list` to see all options."
                ),
            )
            return

        try:
            file_bytes = match.read_bytes()
            await client.files_upload_v2(
                channel=user_id,
                filename=match.name,
                content=file_bytes,
                initial_comment=f"📄 Here's your *{match.stem}* template! Fill in the highlighted areas and you're good to go.",
            )
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"✅ *{match.name}* sent to your DMs!",
            )
        except Exception as exc:
            log.warning("handle_slash_template: failed to upload %s: %s", match.name, exc)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"⚠️ Couldn't upload {match.name}. Please try again in a moment.",
            )

    @app.command("/mypins")
    async def handle_slash_saved(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")

        notes_path = _DATA_DIR / "slack_saved_notes.json"
        if not notes_path.exists():
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="You haven't saved any messages yet — react 🔖 to any bot response to save it!",
            )
            return

        try:
            all_notes: list[dict] = json.loads(notes_path.read_text())
        except Exception:
            all_notes = []

        user_notes = [n for n in all_notes if n.get("user_id") == user_id]
        if not user_notes:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="You haven't saved any messages yet — react 🔖 to any bot response to save it!",
            )
            return

        recent = list(reversed(user_notes))[:5]
        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": "🔖 Your Saved Notes", "emoji": True}},
        ]
        for note in recent:
            saved_at = note.get("saved_at", "")
            try:
                import datetime

                dt = datetime.datetime.fromisoformat(saved_at)
                delta = datetime.datetime.now() - dt
                days = delta.days
                when = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
            except Exception:
                when = saved_at[:10] if saved_at else "recently"
            preview = (note.get("text") or "")[:200].replace("\n", " ")
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{when}* — {preview}…"
                        if len(note.get("text", "")) > 200
                        else f"*{when}* — {preview}",
                    },
                }
            )
            blocks.append({"type": "divider"})

        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_Showing {len(recent)} of {len(user_notes)} saved notes_"}],
            }
        )

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            blocks=blocks,
            text="🔖 Your Saved Notes",
        )

    @app.command("/filesearch")
    async def handle_slash_search(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        keyword: str = (body.get("text") or "").strip().lower()

        if not keyword:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/search <keyword>` — e.g. `/search budget`",
            )
            return

        entries = _file_history.get(user_id, [])
        matches = [
            e
            for e in entries
            if keyword in (e.get("name") or "").lower() or keyword in (e.get("auto_brief") or "").lower()
        ]

        if not matches:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"No files matching *{keyword}* found — try `/brief` to see all your recent uploads.",
            )
            return

        lines = []
        for entry in list(reversed(matches))[:10]:
            name = entry.get("name", "unknown")
            uploaded_at = entry.get("uploaded_at", "")
            try:
                import datetime

                dt = datetime.datetime.fromisoformat(uploaded_at)
                delta = datetime.datetime.now() - dt
                days = delta.days
                when = "today" if days == 0 else "yesterday" if days == 1 else f"{days}d ago"
            except Exception:
                when = "recently"
            brief = entry.get("auto_brief", "")
            brief_str = f" — _{brief}_" if brief else ""
            lines.append(f"• *{name}* ({when}){brief_str}")

        text = f"🔍 Files matching *{keyword}*:\n" + "\n".join(lines)
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
        )

    @app.command("/schedule")
    async def handle_slash_schedule(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip()

        if not text:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/schedule <time>` — e.g. `/schedule 9am` or `/schedule 14:00` or `/schedule off`",
            )
            return

        parsed = _parse_schedule_time(text)
        if parsed == -1:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Couldn't parse *{text}* — try formats like `9am`, `8:30`, `14:00`, or `off`",
            )
            return

        prefs = _load_digest_prefs()
        if user_id not in prefs:
            prefs[user_id] = {"enabled": False}

        if parsed is None:
            prefs[user_id].pop("preferred_hour", None)
            _save_digest_prefs(prefs)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="✅ Digest schedule cleared — digest will use the default 24-hour interval.",
            )
        else:
            prefs[user_id]["preferred_hour"] = parsed
            _save_digest_prefs(prefs)
            if parsed == 0:
                hour_str = "12:00am"
            elif parsed < 12:
                hour_str = f"{parsed}:00am"
            elif parsed == 12:
                hour_str = "12:00pm"
            else:
                hour_str = f"{parsed - 12}:00pm"
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"✅ Digest scheduled for *{hour_str}* daily. Make sure `/digest on` is enabled!",
            )

    @app.command("/clear")
    async def handle_slash_clear(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        user_id = body.get("user_id", "unknown")
        _compare_pending.pop(user_id, None)
        if user_id in _user_prefs:
            _user_prefs[user_id].pop("active_file_id", None)
            _user_prefs[user_id].pop("translate_file_id", None)
            _save_prefs()
        await say(
            text="✅ *Session cleared!* Thread history and active file selections have been reset. Start fresh with your next message."
        )

    @app.command("/nickname")
    async def handle_slash_nickname(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        name: str = (body.get("text") or "").strip()
        if not name:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/nickname <your name>` — e.g. `/nickname Chuck`",
            )
            return
        if user_id not in _personas:
            _personas[user_id] = {}
        _personas[user_id]["name"] = name
        _save_personas()
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"✅ Got it! I'll call you *{name}* from now on. 👋",
        )


async def _send_morning_briefing(client: Any) -> str:
    """Send morning briefing DM to owner."""
    import os
    import shutil
    import sqlite3
    import time as _time

    notify_user = os.environ.get("SLACK_NOTIFY_USER_ID", "")
    if not notify_user:
        return "Morning briefing skipped: SLACK_NOTIFY_USER_ID not set"

    sections = ["*☀️ Good morning! Here's your daily briefing:*\n"]

    weather_loc = os.environ.get("WEATHER_DEFAULT_LOCATION", "")
    if weather_loc:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://wttr.in/{weather_loc}?format=3",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    weather = await resp.text()
            sections.append(f"🌤 *Weather:* {weather.strip()}")
        except Exception:
            pass

    try:
        nas_path = os.environ.get("NAS_BACKUP_PATH", "/Volumes/Misc")
        if os.path.exists(nas_path):
            usage = shutil.disk_usage(nas_path)
            pct = usage.used / usage.total * 100
            sections.append(f"💾 *NAS disk:* {pct:.1f}% used ({usage.free // 1_073_741_824:.0f} GB free)")
    except Exception:
        pass

    # Tailscale peers
    try:
        from host_bridge import _enabled as _host_bridge_enabled
        from host_bridge import run_shell as _run_shell_ts

        if _host_bridge_enabled():
            ts_output = await _run_shell_ts(command="tailscale status --json", slack_user_id="slack", timeout_s=8)
            ts_text = ts_output if isinstance(ts_output, str) else (getattr(ts_output, "stdout", "") or "")
            import json as _json

            ts_data = _json.loads(ts_text)
            peers = ts_data.get("Peer", {})
            online = sum(1 for p in peers.values() if p.get("Online", False))
            total_peers = len(peers)
            sections.append(f"🌐 *Tailscale:* {online}/{total_peers} peers online")
    except Exception:
        pass

    try:
        db_path = os.path.expanduser("~/.hermes/state.db")
        yesterday = _time.time() - 86400
        conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE started_at > ?",
                (yesterday,),
            ).fetchone()[0]
        finally:
            conn.close()
        sections.append(f"🤖 *Hermes sessions yesterday:* {count}")
    except Exception:
        pass

    tautulli_key = os.environ.get("TAUTULLI_API_KEY", "")
    media_lines: list[str] = []
    if tautulli_key:
        try:
            tautulli_url = os.environ.get("TAUTULLI_URL", "http://localhost:8181")
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{tautulli_url}/api/v2",
                    params={"apikey": tautulli_key, "cmd": "get_history", "length": "3"},
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as r:
                    hist = (await r.json()).get("response", {}).get("data", {}).get("data", [])
            if hist:
                media_lines.append("🎬 *Recently watched:*")
                for item in hist[:3]:
                    icon = "🎬" if item.get("media_type") == "movie" else "📺"
                    media_lines.append(f"  {icon} {item.get('full_title', item.get('title', '?'))}")
        except Exception:
            pass
    if media_lines:
        sections.extend(media_lines)

    sonarr_key = os.environ.get("SONARR_API_KEY", "")
    if sonarr_key:
        try:
            import datetime as _dt

            today = _dt.date.today().isoformat()
            tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
            sonarr_url = os.environ.get("SONARR_URL", "http://host.docker.internal:8989")
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{sonarr_url}/api/v3/calendar",
                    params={"apikey": sonarr_key, "start": today, "end": tomorrow},
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as r:
                    episodes = await r.json()
            if episodes:
                ep_lines = [f"📺 *Today on Sonarr ({len(episodes)} eps):*"]
                for ep in episodes[:3]:
                    show = ep.get("series", {}).get("title", ep.get("seriesTitle", "?"))
                    ep_lines.append(f"  • {show} S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}")
                sections.extend(ep_lines)
        except Exception:
            pass

    radarr_key = os.environ.get("RADARR_API_KEY", "")
    if radarr_key:
        try:
            radarr_url = os.environ.get("RADARR_URL", "http://host.docker.internal:7878")
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{radarr_url}/api/v3/wanted/missing",
                    params={"apikey": radarr_key, "pageSize": 1},
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as r:
                    wanted = await r.json()
            total = wanted.get("totalRecords", 0)
            if total:
                sections.append(f"🎬 *Radarr:* {total} movies still wanted")
        except Exception:
            pass

    try:
        uptime_url = os.environ.get("UPTIME_KUMA_URL", "http://host.docker.internal:3001")
        uptime_slug = os.environ.get("UPTIME_KUMA_STATUS_SLUG", "main")
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{uptime_url}/api/status-page/heartbeat/{uptime_slug}",
                timeout=aiohttp.ClientTimeout(total=4),
            ) as r:
                hb_data = await r.json()
        heartbeats = hb_data.get("heartbeatList", {})
        down_ids = [mid for mid, beats in heartbeats.items() if beats and beats[-1].get("status", 0) == 0]
        total_services = len(heartbeats)
        if down_ids:
            sections.append(f"⚠️ *Uptime Kuma:* {len(down_ids)}/{total_services} services DOWN")
        else:
            sections.append(f"✅ *Uptime Kuma:* all {total_services} services up")
    except Exception:
        pass

    # SABnzbd queue snapshot
    _sab_url = os.environ.get("SABNZBD_URL", "")
    _sab_key = os.environ.get("SABNZBD_API_KEY", "")
    if _sab_url and _sab_key:
        try:
            async with aiohttp.ClientSession() as _s:
                async with _s.get(
                    f"{_sab_url}/api",
                    params={"mode": "qstatus", "output": "json", "apikey": _sab_key},
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as _r:
                    _q = (await _r.json()).get("queue", {})
            _slots = _q.get("noofslots", 0)
            if _slots:
                _mb_left = float(_q.get("mbleft", 0))
                sections.append(f"📰 *SABnzbd:* {_slots} item(s) queued, {_mb_left / 1024:.1f} GB left")
            else:
                sections.append("📰 *SABnzbd:* queue empty")
        except Exception:
            pass

    # qBittorrent active downloads
    _qbt_url = os.environ.get("QBIT_URL", "")
    _qbt_user = os.environ.get("QBIT_USER", "admin")
    _qbt_pass = os.environ.get("QBIT_PASSWORD", "")
    if _qbt_url and _qbt_pass:
        try:
            import aiohttp as _aiohttp_qbt2

            _jar2 = _aiohttp_qbt2.CookieJar()
            async with _aiohttp_qbt2.ClientSession(cookie_jar=_jar2) as _s:
                await _s.post(
                    f"{_qbt_url}/api/v2/auth/login",
                    data={"username": _qbt_user, "password": _qbt_pass},
                    timeout=_aiohttp_qbt2.ClientTimeout(total=5),
                )
                async with _s.get(
                    f"{_qbt_url}/api/v2/transfer/info",
                    timeout=_aiohttp_qbt2.ClientTimeout(total=4),
                ) as _r:
                    _xfer2 = await _r.json()
                async with _s.get(
                    f"{_qbt_url}/api/v2/torrents/info",
                    params={"filter": "active"},
                    timeout=_aiohttp_qbt2.ClientTimeout(total=4),
                ) as _r:
                    _active2 = await _r.json()
            _dl2 = _xfer2.get("dl_info_speed", 0) / 1024 / 1024
            _count2 = len(_active2)
            if _count2:
                sections.append(f"🌊 *qBittorrent:* {_count2} active · ⬇️ {_dl2:.1f} MB/s")
            else:
                sections.append("🌊 *qBittorrent:* idle")
        except Exception:
            pass

    # AdGuard daily stats
    _ag_url = os.environ.get("ADGUARD_URL", "")
    _ag_user = os.environ.get("ADGUARD_USER", "")
    _ag_pass = os.environ.get("ADGUARD_PASSWORD", "")
    if _ag_url and _ag_user:
        try:
            import base64 as _b64

            _creds = _b64.b64encode(f"{_ag_user}:{_ag_pass}".encode()).decode()
            async with aiohttp.ClientSession() as _s:
                async with _s.get(
                    f"{_ag_url}/control/stats",
                    headers={"Authorization": f"Basic {_creds}"},
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as _r:
                    _ag = await _r.json()
            _total = _ag.get("num_dns_queries", 0)
            _blocked = _ag.get("num_blocked_filtering", 0)
            _pct = round(_blocked / max(_total, 1) * 100, 1)
            sections.append(f"🛡️ *AdGuard:* {_total:,} queries yesterday, {_pct}% blocked")
        except Exception:
            pass

    # NAS health (disk + containers)
    try:
        import asyncio as _asyncio_nas

        nas_host = os.environ.get("NAS_HOST", "192.168.1.8")
        nas_port = os.environ.get("NAS_SSH_PORT", "24")
        nas_user = os.environ.get("NAS_SSH_USER", "dave")
        nas_cmd = (
            "df -h /volume1 2>/dev/null | tail -1; "
            "echo '---'; "
            "/usr/local/bin/docker ps --format '{{.Status}}' | grep -c 'Up' 2>/dev/null || echo 0; "
            "/usr/local/bin/docker ps --format '{{.Names}}|{{.Status}}' | grep -i unhealthy | head -3"
        )
        _proc = await _asyncio_nas.create_subprocess_exec(
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "BatchMode=yes",
            "-p",
            nas_port,
            f"{nas_user}@{nas_host}",
            nas_cmd,
            stdout=_asyncio_nas.subprocess.PIPE,
            stderr=_asyncio_nas.subprocess.PIPE,
        )
        _stdout, _ = await _asyncio_nas.wait_for(_proc.communicate(), timeout=12)
        _nas_raw = _stdout.decode().strip().split("---")
        _disk_line = _nas_raw[0].strip() if _nas_raw else ""
        _cont_part = _nas_raw[1].strip().splitlines() if len(_nas_raw) > 1 else []
        _running = int(_cont_part[0]) if _cont_part and _cont_part[0].isdigit() else 0
        _unhealthy = [line.split("|")[0] for line in _cont_part[1:] if line.strip()]
        # Parse disk %
        _disk_pct = ""
        if _disk_line:
            _dcols = _disk_line.split()
            if len(_dcols) >= 5:
                _disk_pct = _dcols[4]
                _dp_num = int(_disk_pct.replace("%", "")) if _disk_pct.replace("%", "").isdigit() else 0
                _disk_icon = "🔴" if _dp_num >= 90 else "🟡" if _dp_num >= 75 else "🟢"
                _nas_line = f"🖥️ *NAS:* {_running} containers running, volume1 {_disk_icon} {_disk_pct} used"
                if _unhealthy:
                    _nas_line += f" ⚠️ unhealthy: {', '.join(_unhealthy)}"
                sections.append(_nas_line)
    except Exception:
        pass

    news_key = os.environ.get("NEWSAPI_KEY", "")
    if news_key:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={"country": "us", "pageSize": "3", "apiKey": news_key},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    news_data = await r.json()
            articles = news_data.get("articles", [])
            if articles:
                news_lines = ["📰 *Top headlines:*"]
                for article in articles[:3]:
                    title = article.get("title", "?")
                    if " - " in title:
                        title = title.rsplit(" - ", 1)[0]
                    news_lines.append(f"  • {title}")
                sections.extend(news_lines)
        except Exception:
            pass

    message = "\n".join(sections)
    try:
        await client.chat_postMessage(channel=notify_user, text=message)
        asyncio.create_task(_send_ntfy("☀️ Morning Briefing", "Daily briefing posted to Slack"))
        return "Morning briefing sent"
    except Exception as exc:
        log.error("Morning briefing failed: %s", exc)
        return f"Morning briefing failed: {exc}"


def _register_integration_handlers(app: Any) -> None:
    """Register integration handlers: Gmail (/inbox, /email, /today, /calendar), Dropbox (/clawbox), channels (/clawchan)."""
    global _slack_app_client
    _slack_app_client = app.client

    # ------------------------------------------------------------------
    # Wave 10 Leia: /inbox — show unread Gmail emails
    # ------------------------------------------------------------------

    @app.command("/inbox")
    async def handle_slash_inbox(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📧 Gmail is not connected. Ask Dave to run `scripts/google_oauth_setup.py`.",
            )
            return
        emails = await _get_gmail_unread(max_results=5)
        global _gmail_message_cache
        _gmail_message_cache = emails
        if not emails:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📭 No unread emails in your inbox.",
            )
            return
        blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": "📧 *Your unread emails:*"}}]
        for i, email in enumerate(emails, 1):
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{i}.* {email['subject']}\n_From: {email['from']}_",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📖 Summarize"},
                        "action_id": "gmail_summarize",
                        "value": email["id"],
                    },
                }
            )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            blocks=blocks,
            text="Your unread emails",
        )

    # ------------------------------------------------------------------
    # Wave 10 Leia / Wave 11: /email — per-user Gmail via IMAP, or server OAuth fallback
    # ------------------------------------------------------------------
    # Subcommands:
    #   /email setup <address> <app_password>  — store personal Gmail creds
    #   /email forget                          — remove stored creds
    #   /email [today|week|<keyword>]          — read inbox or search
    #   /email <number>                        — (legacy) summarize email # from /inbox cache
    # ------------------------------------------------------------------

    @app.command("/email")
    async def handle_slash_email(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        lower = text.lower()

        # -- /email setup <address> <app_password> --
        if lower.startswith("setup"):
            parts = text.split(None, 2)  # ["setup", "chuck@gmail.com", "xxxx xxxx xxxx xxxx"]
            if len(parts) < 3:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        "📧 *Gmail setup:*\n"
                        "`/email setup <your@gmail.com> <app password>`\n\n"
                        "To get an app password:\n"
                        "1. Go to myaccount.google.com → Security → 2-Step Verification (enable if needed)\n"
                        "2. myaccount.google.com → Security → *App Passwords* → Create\n"
                        "3. Copy the 16-character password and paste it here\n\n"
                        "_This message is only visible to you and is not stored in Slack._"
                    ),
                )
                return
            _, address, app_password = parts[0], parts[1], parts[2]
            _user_email_creds[user_id] = {"user": address, "password": app_password}
            _save_user_email_creds()
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"✅ *Gmail connected!*\n"
                    f"I'll use *{address}* when you run `/email`.\n"
                    f"Try `/email` to see your inbox, or `/email doctor` to search."
                ),
            )
            return

        # -- /email forget --
        if lower == "forget":
            if user_id in _user_email_creds:
                del _user_email_creds[user_id]
                _save_user_email_creds()
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="✅ Your Gmail credentials have been removed.",
                )
            else:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ℹ️ No Gmail credentials were stored for you.",
                )
            return

        # -- Personal IMAP path (per-user creds stored) --
        creds = _user_email_creds.get(user_id)
        if creds:
            try:
                from email_skills import read_inbox, search_emails
            except ImportError:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Email skills module not available.",
                )
                return

            import os as _os

            _orig_user = _os.environ.get("GMAIL_USER")
            _orig_pass = _os.environ.get("GMAIL_APP_PASSWORD")
            _os.environ["GMAIL_USER"] = creds["user"]
            _os.environ["GMAIL_APP_PASSWORD"] = creds["password"]
            try:
                if not text or lower in ("today", "inbox"):
                    result = await read_inbox(count=10)
                elif lower == "week":
                    result = await read_inbox(count=25)
                else:
                    try:
                        idx = int(text) - 1
                        # legacy: summarize by number — fall through to OAuth path
                        result = None
                    except ValueError:
                        result = await search_emails(text)
            finally:
                if _orig_user is not None:
                    _os.environ["GMAIL_USER"] = _orig_user
                elif "GMAIL_USER" in _os.environ:
                    del _os.environ["GMAIL_USER"]
                if _orig_pass is not None:
                    _os.environ["GMAIL_APP_PASSWORD"] = _orig_pass
                elif "GMAIL_APP_PASSWORD" in _os.environ:
                    del _os.environ["GMAIL_APP_PASSWORD"]

            if result is not None:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=result)
                return
            # fall through to legacy number-based summarize below

        # -- Legacy number-based summarize (server OAuth path) --
        if not _GOOGLE_REFRESH_TOKEN and not creds:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📧 *No Gmail connected yet.*\n"
                    "Run `/email setup your@gmail.com <app password>` to connect your own Gmail.\n\n"
                    "Need help? Type `/email setup` for step-by-step instructions."
                ),
            )
            return

        try:
            idx = int(text) - 1
        except ValueError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/email 1` — summarize email #1 from your inbox. Run `/inbox` first.",
            )
            return
        emails = _gmail_message_cache or await _get_gmail_unread()
        if idx < 0 or idx >= len(emails):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"No email #{idx + 1}. Run `/inbox` to see your emails.",
            )
            return
        msg_id = emails[idx]["id"]
        body_text = await _get_gmail_body(msg_id)
        summary = await _ask(
            f"Summarize this email in 3 bullet points:\n\n{body_text}",
            model_name=None,
        )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"📧 *Email summary:*\n{summary}",
        )

    # ------------------------------------------------------------------
    # /today — Show today's Google Calendar events (Wave 10 Yoda)
    # ------------------------------------------------------------------

    @app.command("/today")
    async def handle_slash_today(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📅 Google Calendar is not connected. Ask Dave to run `scripts/google_oauth_setup.py`.",
            )
            return
        events = await _get_calendar_events(days_ahead=0)
        msg = _format_calendar_events(events, label="today")
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)

    # ------------------------------------------------------------------
    # /calendar — Google Calendar events and event creation
    # ------------------------------------------------------------------

    @app.command("/calendar")
    async def handle_slash_calendar(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip().lower()
        name: str = _get_user_name(user_id)

        try:
            from calendar_skills import get_todays_events, get_upcoming_events
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Calendar skills module not found.",
            )
            return

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"📅 Fetching calendar for {name}…",
        )

        if not text or text == "today":
            result = await get_todays_events()
        elif text == "week":
            result = await get_upcoming_events(days=7)
        else:
            result = await get_upcoming_events(days=7)

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=result,
        )

    # ------------------------------------------------------------------
    # /dropbox — Browse and sync Dropbox folder (per-user or server token)
    # ------------------------------------------------------------------
    # Subcommands:
    #   /dropbox connect         — one-click OAuth2 link (recommended)
    #   /dropbox setup <token>   — store personal Dropbox access token (advanced)
    #   /dropbox forget          — remove stored token
    #   /dropbox list            — list recent files
    #   /dropbox sync            — check for new files
    #   /dropbox status          — connection status
    # ------------------------------------------------------------------

    @app.command("/clawbox")
    async def handle_slash_dropbox(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        lower = text.lower()

        # -- /dropbox connect — OAuth2 one-click flow --
        if lower == "connect":
            if not _DROPBOX_APP_KEY or not _OPENCLAW_PUBLIC_URL:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        "⚠️ Dropbox OAuth is not configured yet.\n\n"
                        "Ask Dave to add `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, and "
                        "`OPENCLAW_PUBLIC_URL` to the server's `.env` file."
                    ),
                )
                return

            state = secrets.token_urlsafe(16)
            _dropbox_oauth_states[state] = user_id

            redirect_uri = f"{_OPENCLAW_PUBLIC_URL}/dropbox/callback"
            auth_url = (
                "https://www.dropbox.com/oauth2/authorize"
                f"?client_id={urllib.parse.quote(_DROPBOX_APP_KEY)}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
                f"&response_type=code"
                f"&state={state}"
                f"&token_access_type=offline"
            )

            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📦 *Connect your Dropbox*\n\n"
                    "Click the link below, sign into Dropbox, and click *Allow*:\n"
                    f"<{auth_url}|👉 Connect my Dropbox to OpenClaw>\n\n"
                    "_This link is private — only you can see it. It expires in 10 minutes._\n\n"
                    "Once you approve, OpenClaw will DM you a confirmation."
                ),
            )
            return

        # -- /dropbox setup <token> --
        if lower.startswith("setup"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        "📦 *Dropbox setup:*\n"
                        "`/dropbox setup <your-access-token>`\n\n"
                        "To get a token:\n"
                        "1. Go to <https://www.dropbox.com/developers/apps|dropbox.com/developers/apps>\n"
                        "2. Create a new app → *Full Dropbox* access\n"
                        "3. Under *OAuth 2* → click *Generate* access token\n"
                        "4. Paste it here: `/dropbox setup sl.your_token_here`\n\n"
                        "_This message is only visible to you._"
                    ),
                )
                return
            token = parts[1].strip()
            _user_dropbox_tokens[user_id] = {"token": token, "watch_path": "/OpenClaw"}
            _save_user_dropbox_tokens()
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "✅ *Dropbox connected!*\n"
                    "I'll watch your `/OpenClaw` folder for new files.\n"
                    "Create that folder in Dropbox, then drop files there and I'll notify you.\n"
                    "Try `/dropbox list` to see recent files."
                ),
            )
            return

        # -- /dropbox forget --
        if lower == "forget":
            if user_id in _user_dropbox_tokens:
                del _user_dropbox_tokens[user_id]
                _save_user_dropbox_tokens()
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="✅ Your Dropbox token has been removed.",
                )
            else:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ℹ️ No Dropbox token was stored for you.",
                )
            return

        # -- Resolve active token: per-user first, then server-level --
        user_dbx_creds = _user_dropbox_tokens.get(user_id)
        active_token = (user_dbx_creds or {}).get("token") or _DROPBOX_TOKEN
        active_folder = (user_dbx_creds or {}).get("watch_path") or _DROPBOX_FOLDER

        if active_token is None:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📦 *Dropbox not connected yet.*\n"
                    "Run `/dropbox setup` to see how to connect your own Dropbox account.\n"
                    "Or ask Dave to set up the shared `DROPBOX_APP_TOKEN`."
                ),
            )
            return

        if lower in ("sync", ""):
            count = await _dropbox_sync_new_files(client)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"☁️ Dropbox sync complete — {count} new file(s) pulled.",
            )
        elif lower == "list":
            files = _dropbox_list_folder(active_folder, token=active_token)[:10]
            if not files:
                msg = f"☁️ No files found in Dropbox folder `{active_folder}`."
            else:
                lines = [f"☁️ *Dropbox — {active_folder}* (last {len(files)} files)"]
                for f in files:
                    lines.append(f"• 📄 {f['name']}  ·  {f['modified']}")
                msg = "\n".join(lines)
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
        elif lower == "status":
            folder_files = _dropbox_list_folder(active_folder, token=active_token)
            source = "personal" if user_dbx_creds else "shared"
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"✅ Dropbox connected ({source}). Watching `{active_folder}` — {len(folder_files)} file(s) found.",
            )
        else:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/dropbox list` · `/dropbox sync` · `/dropbox status` · `/dropbox setup <token>`",
            )

    # ------------------------------------------------------------------
    # Background: Dropbox poll loop (Wave 10)
    # ------------------------------------------------------------------

    async def _dropbox_poll_loop() -> None:
        """Poll Dropbox every 30 minutes for new files."""
        if _DROPBOX_TOKEN is None:
            return
        while True:
            await asyncio.sleep(1800)  # 30 minutes
            try:
                await _dropbox_sync_new_files(app.client)
            except Exception:  # noqa: BLE001
                pass

    asyncio.ensure_future(_dropbox_poll_loop())

    # ------------------------------------------------------------------
    # Handler: /channels — list and archive Slack channels (Wave 14)
    # Requires SLACK_USER_TOKEN (xoxp-...) with channels:read,
    # channels:manage, groups:read, groups:write scopes.
    # ------------------------------------------------------------------

    @app.command("/clawchan")
    async def handle_slash_channels(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        lower = text.lower()

        if not SLACK_USER_TOKEN or not SLACK_USER_TOKEN.startswith("xoxp-"):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "⚠️ *Channel management not configured.*\n\n"
                    "Ask Dave to add `SLACK_USER_TOKEN` (xoxp-...) to the server's `.env` file.\n"
                    "The token requires `channels:read`, `channels:manage`, `groups:read`, and `groups:write` scopes."
                ),
            )
            return

        import aiohttp as _aiohttp

        # Helper: call Slack API with user token
        async def _user_api(method: str, payload: dict) -> dict:
            url = f"https://slack.com/api/{method}"
            headers = {"Authorization": f"Bearer {SLACK_USER_TOKEN}", "Content-Type": "application/json"}
            async with _aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload, headers=headers) as resp:
                    return await resp.json()

        # -- /channels list --
        if not text or lower == "list":
            try:
                result = await _user_api(
                    "conversations.list",
                    {"types": "public_channel,private_channel", "exclude_archived": True, "limit": 50},
                )
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "unknown"))
                channels = result.get("channels", [])
                if not channels:
                    msg = "📋 No active channels found."
                else:
                    lines = ["📋 *Active Slack channels:*\n"]
                    for ch in sorted(channels, key=lambda c: c.get("name", "")):
                        name = ch.get("name", "?")
                        members = ch.get("num_members", "?")
                        is_private = "🔒" if ch.get("is_private") else "#"
                        lines.append(f"  {is_private} {name}  ({members} members)")
                    lines.append(
                        f"\n_{len(channels)} channel(s) total. Use `/clawchan archive <name>` to archive one._"
                    )
                    msg = "\n".join(lines)
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
            except Exception as exc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Failed to list channels: {exc}"
                )
            return

        # -- /channels archive <name> --
        if lower.startswith("archive "):
            target_name = text.split(None, 1)[1].strip().lstrip("#")
            try:
                # First: look up channel ID by name
                result = await _user_api(
                    "conversations.list",
                    {"types": "public_channel,private_channel", "exclude_archived": True, "limit": 200},
                )
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "unknown"))
                channels = result.get("channels", [])
                match = next((c for c in channels if c.get("name", "").lower() == target_name.lower()), None)
                if not match:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"⚠️ Channel `#{target_name}` not found (or already archived).",
                    )
                    return
                arch_result = await _user_api("conversations.archive", {"channel": match["id"]})
                if not arch_result.get("ok"):
                    raise RuntimeError(arch_result.get("error", "unknown"))
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"✅ Channel `#{target_name}` has been archived.",
                )
            except Exception as exc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Failed to archive `#{target_name}`: {exc}"
                )
            return

        # -- usage fallback --
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=(
                "📋 *Channel management commands:*\n"
                "• `/channels list` — list all active channels\n"
                "• `/channels archive <name>` — archive a channel\n\n"
                "_Note: channel deletion is not supported by Slack's API — archive is the closest option._"
            ),
        )

    # ------------------------------------------------------------------
    # /drive — Google Drive file browser and uploader
    # ------------------------------------------------------------------
    # Subcommands:
    #   /drive list [query]       — list Drive files, optional Drive query syntax
    #   /drive read <file_id>     — read/export a file as plain text
    #   /drive upload <name> <content> — (advanced) create a new text file
    # ------------------------------------------------------------------

    @app.command("/drive")
    async def handle_slash_drive(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()
        parts = text.split(None, 1)
        sub = parts[0].lower() if parts else "list"
        arg = parts[1] if len(parts) > 1 else ""

        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📁 Google Drive is not connected. Ask Dave to run `scripts/google_oauth_setup.py` with Drive scopes.",
            )
            return

        try:
            from calendar_skills import list_drive_files, read_drive_file
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Drive skills module not found.",
            )
            return

        if sub in ("list", ""):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"📁 Listing Drive files{f' matching `{arg}`' if arg else ''}…",
            )
            result = await list_drive_files(query=arg)
        elif sub == "read":
            if not arg:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/drive read <file_id>`",
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"📄 Reading Drive file `{arg}`…",
            )
            result = await read_drive_file(file_id=arg)
        else:
            result = (
                "📁 *Drive commands:*\n"
                "• `/drive list [query]` — list files (optional Drive query filter)\n"
                "• `/drive read <file_id>` — read/export a file as plain text"
            )

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=result,
        )

    # ------------------------------------------------------------------
    # /contacts — Google People / Contacts search
    # ------------------------------------------------------------------
    # Subcommands:
    #   /contacts search <query>       — search contacts by name or email
    #   /contacts get <resource_name>  — get full contact details
    # ------------------------------------------------------------------

    @app.command("/contacts")
    async def handle_slash_contacts(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()
        parts = text.split(None, 1)
        sub = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="👤 Google Contacts is not connected. Ask Dave to run `scripts/google_oauth_setup.py` with Contacts scopes.",
            )
            return

        try:
            from calendar_skills import get_contact, search_contacts
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Contacts skills module not found.",
            )
            return

        if sub == "search":
            if not arg:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/contacts search <name or email>`",
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"👤 Searching contacts for `{arg}`…",
            )
            result = await search_contacts(query=arg)
        elif sub == "get":
            if not arg:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/contacts get <resource_name>` (e.g. `people/c1234`)",
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"👤 Fetching contact `{arg}`…",
            )
            result = await get_contact(resource_name=arg)
        else:
            result = (
                "👤 *Contacts commands:*\n"
                "• `/contacts search <query>` — search by name or email\n"
                "• `/contacts get <resource_name>` — full contact details (e.g. `people/c1234`)"
            )

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=result,
        )

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Handler: /copilot — Phase 3 threaded interactive sessions
    #
    # HIGH-RISK: `/copilot` is owner-gated and creates a Slack thread-backed
    # agent session. By default it opens a long-lived `copilot --allow-all-tools`
    # process on the Mac Mini host via SSH; when `COPILOT_BACKEND=hermes`, it
    # runs the local Hermes CLI instead and reuses the session ID for thread turns.
    # `/hermes` below always forces the Hermes path regardless of env config.
    # ------------------------------------------------------------------

    def _copilot_owner_check(user_id: str, *, command_name: str = "/copilot") -> tuple[bool, str | None]:
        raw_allow = os.getenv("OPENCLAW_HOST_BRIDGE_ALLOWED_USERS", "") or SLACK_NOTIFY_USER_ID
        allowed = {p.strip() for p in raw_allow.split(",") if p.strip()}
        if not allowed:
            return False, f"🛑 `{command_name}` is not configured. Set `OPENCLAW_HOST_BRIDGE_ALLOWED_USERS`."
        if user_id not in allowed:
            return False, f"🛑 You are not allowed to run `{command_name}` on this workspace."
        return True, None

    # Directory shortcuts for --dir flag
    _COPILOT_DIR_SHORTCUTS: dict[str, str] = {
        "openclaw": "/Users/davevoyles/openclaw",
        "docker-stack": "/Users/davevoyles/docker-stack",
        "docker_stack": "/Users/davevoyles/docker-stack",
        "ai-files": "/Users/davevoyles/ai-files",
        "roms": "/Users/davevoyles/mnt/ROMs",
    }

    def _parse_copilot_flags(text: str) -> tuple[str, str | None]:
        """Strip --dir <value> from text, return (cleaned_prompt, workdir_or_None)."""
        import re as _re

        m = _re.search(r"--dir\s+(\S+)", text)
        if not m:
            return text, None
        raw = m.group(1)
        workdir = _COPILOT_DIR_SHORTCUTS.get(raw.lower(), raw if raw.startswith("/") else None)
        cleaned = (text[: m.start()] + text[m.end() :]).strip()
        return cleaned, workdir

    COPILOT_BACKEND = os.environ.get("COPILOT_BACKEND", "ssh").lower()

    def _copilot_backend() -> str:
        return "hermes" if COPILOT_BACKEND == "hermes" else "ssh"

    def _copilot_backend_label() -> str:
        return "Hermes CLI" if _copilot_backend() == "hermes" else "Copilot CLI (SSH)"

    async def _start_hermes_session(
        *,
        slack_user: str,
        slack_channel: str,
        slack_thread_ts: str,
        cwd: str | None,
    ) -> tuple[Any | None, str | None]:
        mgr = await _ensure_session_manager()
        if mgr is None:
            return None, "host_bridge module unavailable"
        try:
            from host_bridge_persistence import SessionRecord
        except ImportError as exc:
            return None, f"host_bridge_persistence unavailable: {exc}"

        active = [
            r
            for r in mgr.list_sessions(slack_user)
            if r.status in ("active", "idle")
            and (r.session_id in _hermes_live_procs or _is_hermes_session(r) or mgr.is_live(r.session_id))
        ]
        if len(active) >= 3:
            return None, "per-user session cap reached (3). End an existing session first."

        session_id = secrets.token_hex(6)
        workdir = cwd or os.getenv("OPENCLAW_HOST_BRIDGE_WORKDIR", "/Users/davevoyles/docker-stack")
        now = time.time()
        transcript_path = _HERMES_AUDIT_DIR / f"{session_id}.log"
        _append_copilot_transcript(
            str(transcript_path),
            (
                f"# session {session_id} (hermes)\n"
                f"# user:   {slack_user}\n"
                f"# cwd:    {workdir}\n"
                f"# opened: {now}\n"
                "# =====\n"
            ),
        )
        record = SessionRecord(
            session_id=session_id,
            slack_user=slack_user,
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            started_at=now,
            last_activity=now,
            cwd=workdir,
            status="idle",
            transcript_path=str(transcript_path),
            turns=0,
            meta={"backend": "hermes"},
        )
        await mgr.registry.add(record)
        return record, None

    async def _cancel_hermes_session(record: Any, *, slack_user: str) -> str | None:
        if record.slack_user != slack_user:
            return "not your session"
        proc = _hermes_live_procs.get(record.session_id)
        if proc is None:
            return "no active Hermes turn to cancel"
        try:
            proc.terminate()
        except Exception as exc:  # noqa: BLE001
            return f"cancel failed: {type(exc).__name__}: {exc}"
        return None

    async def _end_hermes_session(mgr: Any, record: Any, *, slack_user: str) -> str | None:
        if record.slack_user != slack_user:
            return "not your session"
        proc = _hermes_live_procs.get(record.session_id)
        if proc is not None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                _hermes_live_procs.pop(record.session_id, None)
        await mgr.registry.update(record.session_id, status="ended", last_activity=time.time())
        record.status = "ended"
        record.last_activity = time.time()
        return None

    @app.command("/copilot")
    async def handle_slash_copilot(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()

        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return

        if not text or text.lower() in {"help", "?"}:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"🤖 *{_copilot_backend_label()} — interactive sessions*\n"
                    "• `/copilot <prompt>` — open a thread and run the selected backend\n"
                    "• `/copilot --dir openclaw <prompt>` — run in the openclaw repo\n"
                    "• `/copilot --dir docker-stack <prompt>` — run in docker-stack repo (default)\n"
                    "• Reply in the thread to send another turn to the same session\n"
                    "• `/copilot-cancel <id>` — stop the current turn\n"
                    "• `/copilot-end <id>` — end the session\n"
                    "• `/copilot-sessions` — list your sessions\n"
                    "• `/copilot-attach <id>` — show details and last activity\n"
                    "• `/copilot-recap <id>` — summarize a session\n"
                    "• `/hermes <prompt>` — always open a Hermes session\n"
                    f"• Backend: `{_copilot_backend()}` • Per-user cap: 3 active sessions. Idle timeout: {os.getenv('OPENCLAW_HOST_BRIDGE_IDLE_TIMEOUT_S', '600')}s.\n"
                ),
            )
            return

        backend = _copilot_backend()
        mgr = None
        if backend == "ssh":
            if os.getenv("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() != "true":
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="🛑 Host bridge disabled. Set `OPENCLAW_HOST_BRIDGE_ENABLED=true` and redeploy.",
                )
                return

            mgr = await _ensure_session_manager()
            if mgr is None:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ host_bridge module unavailable",
                )
                return

        prompt_text, cwd_override = _parse_copilot_flags(text)
        if not prompt_text:
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text="❌ prompt is empty after parsing flags."
            )
            return

        dir_label = cwd_override or os.getenv("OPENCLAW_HOST_BRIDGE_WORKDIR", "/Users/davevoyles/docker-stack")

        try:
            parent = await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"🤖 <@{user_id}> opened a {_copilot_backend_label()} session\n"
                    f"_prompt:_ `{prompt_text[:300]}`\n"
                    f"_cwd:_ `{dir_label}`\n"
                    f"_backend:_ `{backend}`\n"
                    "_reply in this thread to continue_"
                ),
            )
            thread_ts = parent.get("ts") or parent.get("message", {}).get("ts") or ""
        except Exception as exc:  # noqa: BLE001
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ failed to open thread: `{exc}`",
            )
            return

        async def _bg() -> None:
            try:
                if backend == "hermes":
                    record, err = await _start_hermes_session(
                        slack_user=user_id,
                        slack_channel=channel_id,
                        slack_thread_ts=thread_ts,
                        cwd=cwd_override,
                    )
                else:
                    record, err = await mgr.start_session(
                        slack_user=user_id,
                        slack_channel=channel_id,
                        slack_thread_ts=thread_ts,
                        initial_prompt=prompt_text,
                        cwd=cwd_override,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("/copilot start_session crashed: %s", exc)
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ session failed to start: `{exc}`",
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            if err or record is None:
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ {err or 'unknown error'}",
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            try:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        f"✅ session `{record.session_id}` open via `{backend}` — "
                        f"`/copilot-end {record.session_id}` to close, "
                        f"`/copilot-cancel {record.session_id}` to stop the current turn"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

            if backend == "hermes":
                err = await _run_hermes_turn(record, prompt_text)
                if err:
                    try:
                        await client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=f"❌ {err}",
                        )
                    except Exception:  # noqa: BLE001
                        pass

        asyncio.create_task(_bg())

    async def _handle_hermes_slash(body: dict[str, Any], client: Any, *, command_name: str) -> None:
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()

        ok, msg = _copilot_owner_check(user_id, command_name=command_name)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return

        if not text:
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text=f"Usage: `{command_name} <your question>`"
            )
            return

        prompt_text, cwd_override = _parse_copilot_flags(text)
        if not prompt_text:
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text="❌ prompt is empty after parsing flags."
            )
            return

        dir_label = cwd_override or os.getenv("OPENCLAW_HOST_BRIDGE_WORKDIR", "/Users/davevoyles/docker-stack")

        try:
            parent = await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"🧠 <@{user_id}> opened a Hermes session\n"
                    f"_prompt:_ `{prompt_text[:300]}`\n"
                    f"_cwd:_ `{dir_label}`\n"
                    f"_command:_ `{command_name}`\n"
                    "_backend:_ `hermes`\n"
                    "_reply in this thread to continue_"
                ),
            )
            thread_ts = parent.get("ts") or parent.get("message", {}).get("ts") or ""
        except Exception as exc:  # noqa: BLE001
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ failed to open thread: `{exc}`",
            )
            return

        async def _bg() -> None:
            try:
                record, err = await _start_hermes_session(
                    slack_user=user_id,
                    slack_channel=channel_id,
                    slack_thread_ts=thread_ts,
                    cwd=cwd_override,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("%s start_session crashed: %s", command_name, exc)
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ session failed to start: `{exc}`",
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            if err or record is None:
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ {err or 'unknown error'}",
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            try:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        f"✅ Hermes session `{record.session_id}` open — "
                        f"`/copilot-end {record.session_id}` to close, "
                        f"`/copilot-cancel {record.session_id}` to stop the current turn"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

            err = await _run_hermes_turn(record, prompt_text)
            if err:
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ {err}",
                    )
                except Exception:  # noqa: BLE001
                    pass

        asyncio.create_task(_bg())

    @app.command("/hermes")
    async def handle_slash_hermes(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        await _handle_hermes_slash(body, client, command_name="/hermes")

    @app.command("/h")
    async def handle_slash_h(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        _ = say
        await ack()
        await _handle_hermes_slash(body, client, command_name="/h")

    async def _run_hermes_quick_command(
        prompt: str,
        *,
        resume_session_id: str | None = None,
        cwd: str | None = None,
        slack_user: str | None = None,
        cancel_id: str | None = None,
    ) -> tuple[str, str | None]:
        # Hermes runs on the host over SSH; stream and accumulate the full reply.
        # When slack_user + cancel_id are supplied, register a handle so the turn
        # can be hard-interrupted via /copilot-cancel <cancel_id>.
        from host_bridge import run_hermes_stream

        workdir = cwd or os.getenv("OPENCLAW_HOST_BRIDGE_WORKDIR", "/Users/davevoyles/docker-stack")
        parts: list[str] = []
        error: str | None = None

        handle: _HermesStreamHandle | None = None
        cancel_event: asyncio.Event | None = None
        if slack_user and cancel_id:
            handle = _HermesStreamHandle()
            handle.slack_user = slack_user
            _hermes_live_procs[cancel_id] = handle
            cancel_event = handle.cancel_event

        try:
            async for event in run_hermes_stream(
                prompt=prompt,
                slack_user_id=slack_user or "slack-quick",
                hermes_session_id=resume_session_id,
                cwd=workdir,
                cancel_event=cancel_event,
            ):
                etype = event.get("type")
                if etype == "chunk":
                    parts.append(event.get("text", ""))
                elif etype == "error":
                    error = event.get("error") or "hermes error"
                elif etype == "done" and not event.get("success") and error is None:
                    if event.get("cancelled"):
                        error = "cancelled"
                    else:
                        exit_code = event.get("exit_code")
                        error = event.get("error") or (
                            f"Hermes exited with status {exit_code}" if exit_code else "Hermes failed"
                        )
        except Exception as exc:  # noqa: BLE001
            return "", f"Hermes failed to start: {type(exc).__name__}: {exc}"
        finally:
            if handle is not None:
                handle.finish()
                if cancel_id is not None:
                    _hermes_live_procs.pop(cancel_id, None)

        if error:
            return "", error

        stdout_text = "".join(parts)
        cleaned_lines = [line for line in stdout_text.splitlines() if not _HERMES_SESSION_RE.match(line)]
        return "\n".join(cleaned_lines).strip(), None

    async def _post_slack_text_chunks(client: Any, channel_id: str, text: str, *, thread_ts: str | None = None) -> None:
        body = text.strip() or "(no response)"
        for start in range(0, len(body), 3500):
            await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=body[start : start + 3500])

    def _uptime_status_label(status: int) -> str:
        return {0: "down", 1: "up", 2: "pending", 3: "maintenance"}.get(status, "unknown")

    async def _fetch_uptime_kuma_snapshot() -> dict[str, Any]:
        uptime_url = os.environ.get("UPTIME_KUMA_URL", "http://host.docker.internal:3001")
        uptime_slug = os.environ.get("UPTIME_KUMA_STATUS_SLUG", "main")
        timeout = aiohttp.ClientTimeout(total=4)

        async with aiohttp.ClientSession() as session:

            async def _get_json(url: str) -> dict[str, Any]:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Uptime Kuma returned HTTP {resp.status}")
                    return await resp.json()

            page_data, hb_data = await asyncio.gather(
                _get_json(f"{uptime_url}/api/status-page/{uptime_slug}"),
                _get_json(f"{uptime_url}/api/status-page/heartbeat/{uptime_slug}"),
            )

        heartbeats = hb_data.get("heartbeatList", {})
        groups: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for group in page_data.get("publicGroupList", []):
            services: list[dict[str, Any]] = []
            for monitor in group.get("monitorList", []):
                monitor_id = str(monitor.get("id"))
                seen_ids.add(monitor_id)
                beats = heartbeats.get(monitor_id, [])
                latest = beats[-1] if beats else {}
                services.append(
                    {
                        "id": monitor_id,
                        "name": monitor.get("name") or latest.get("name") or f"Monitor {monitor_id}",
                        "status": latest.get("status", 0),
                        "msg": latest.get("msg", ""),
                    }
                )
            if services:
                groups.append({"name": group.get("name") or "Other", "services": services})

        extras: list[dict[str, Any]] = []
        for monitor_id, beats in heartbeats.items():
            monitor_key = str(monitor_id)
            if monitor_key in seen_ids:
                continue
            latest = beats[-1] if beats else {}
            extras.append(
                {
                    "id": monitor_key,
                    "name": latest.get("name") or f"Monitor {monitor_key}",
                    "status": latest.get("status", 0),
                    "msg": latest.get("msg", ""),
                }
            )
        if extras:
            groups.append({"name": "Other", "services": extras})

        services = [service for group in groups for service in group["services"]]
        down_services = [service for service in services if service.get("status") != 1]
        return {"groups": groups, "services": services, "down_services": down_services}

    @app.command("/status")
    async def handle_slash_status_quick(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        lines = ["*🖥️ OpenClaw System Status*\n"]

        try:
            import shutil

            if os.path.exists("/proc/loadavg") and os.path.exists("/proc/meminfo"):
                with open("/proc/loadavg", encoding="utf-8") as fh:
                    la = fh.read().split()
                with open("/proc/meminfo", encoding="utf-8") as fh:
                    mem = {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in fh if ":" in line)}
                mem_total = int(mem.get("MemTotal", "0 kB").split()[0]) / 1024 / 1024
                mem_avail = int(mem.get("MemAvailable", "0 kB").split()[0]) / 1024 / 1024
                mem_used = mem_total - mem_avail
                disk = shutil.disk_usage("/")
                lines.append(
                    f"• *Mac Mini:* load {la[0]} · RAM {mem_used:.1f}/{mem_total:.1f}GB · disk {disk.used / 1e9:.0f}/{disk.total / 1e9:.0f}GB"
                )
            else:
                la = os.getloadavg()
                disk = shutil.disk_usage("/")
                lines.append(f"• *Mac Mini:* load {la[0]:.2f} · disk {disk.used / 1e9:.0f}/{disk.total / 1e9:.0f}GB")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"• *Mac Mini:* error ({exc})")

        async def _check(ip: str) -> bool:
            try:
                _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, 22), timeout=2)
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
                return True
            except Exception:
                return False

        mbp_online = await _check("192.168.1.131")
        mbp2_online = await _check("192.168.1.136")
        lines.append(
            f"• *MacBook Pro:* {'✓ online' if mbp_online else 'offline'} · *MacBook Pro 2:* {'✓ online' if mbp2_online else 'offline'}"
        )

        try:
            import sqlite3

            db_path = os.path.expanduser("~/.hermes/state.db")
            if not os.path.exists(db_path):
                db_path = "/Users/davevoyles/.hermes/state.db"
            since = time.time() - 86400
            conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE started_at > ?",
                    (since,),
                ).fetchone()[0]
            finally:
                conn.close()
            lines.append(f"• *Hermes:* {count} session(s) today")
        except Exception:
            pass

        try:
            import subprocess

            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            containers = [line for line in result.stdout.strip().splitlines() if line]
            lines.append(f"• *Docker:* {len(containers)} container(s) running")
        except Exception:
            pass

        try:
            import shutil

            nas_paths = ["/Volumes/Misc", "/mnt/nas", os.environ.get("NAS_BACKUP_PATH", "")]
            for nas_path in nas_paths:
                if nas_path and os.path.exists(nas_path):
                    usage = shutil.disk_usage(nas_path)
                    lines.append(f"• *NAS:* {usage.free / 1e12:.1f}TB free of {usage.total / 1e12:.1f}TB")
                    break
        except Exception:
            pass

        plex_line = ""
        tautulli_key = os.environ.get("TAUTULLI_API_KEY", "")
        if tautulli_key:
            tautulli_url = os.environ.get("TAUTULLI_URL", "http://localhost:8181")
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        f"{tautulli_url}/api/v2",
                        params={"apikey": tautulli_key, "cmd": "get_activity"},
                        timeout=aiohttp.ClientTimeout(total=4),
                    ) as r:
                        act = (await r.json()).get("response", {}).get("data", {})
                count = len(act.get("sessions", []))
                plex_line = f"🎬 Plex: {count} streaming" if count else "🎬 Plex: idle"
            except Exception:
                plex_line = "🎬 Plex: unreachable"
        if plex_line:
            lines.append(f"• {plex_line}")

        try:
            uptime_snapshot = await _fetch_uptime_kuma_snapshot()
            total_services = len(uptime_snapshot["services"])
            down_names = [service["name"] for service in uptime_snapshot["down_services"]]
            if total_services:
                if down_names:
                    lines.append(
                        f"• ⚠️ *Services:* {len(down_names)} down: {', '.join(down_names[:3])}"
                        + (f" +{len(down_names) - 3} more" if len(down_names) > 3 else "")
                    )
                else:
                    lines.append(f"• *Services:* {total_services}/{total_services} up ✅")
        except Exception:
            pass

        # SABnzbd
        sabnzbd_url = os.environ.get("SABNZBD_URL", "")
        sabnzbd_key = os.environ.get("SABNZBD_API_KEY", "")
        if sabnzbd_url and sabnzbd_key:
            try:
                async with aiohttp.ClientSession() as _s:
                    async with _s.get(
                        f"{sabnzbd_url}/api",
                        params={"mode": "qstatus", "output": "json", "apikey": sabnzbd_key},
                        timeout=aiohttp.ClientTimeout(total=4),
                    ) as _r:
                        _q = (await _r.json()).get("queue", {})
                _status = _q.get("status", "?")
                _slots = _q.get("noofslots", 0)
                if _slots and _status.lower() != "idle":
                    _mb_left = float(_q.get("mbleft", 0))
                    lines.append(f"• 📰 SABnzbd: {_slots} job(s), {_mb_left / 1024:.1f} GB left")
                else:
                    lines.append("• 📰 SABnzbd: idle")
            except Exception:
                pass

        # AdGuard
        _adguard_url = os.environ.get("ADGUARD_URL", "")
        _adguard_user = os.environ.get("ADGUARD_USER", "")
        _adguard_pass = os.environ.get("ADGUARD_PASSWORD", "")
        if _adguard_url and _adguard_user:
            try:
                import base64 as _b64

                _creds = _b64.b64encode(f"{_adguard_user}:{_adguard_pass}".encode()).decode()
                async with aiohttp.ClientSession() as _s:
                    async with _s.get(
                        f"{_adguard_url}/control/stats",
                        headers={"Authorization": f"Basic {_creds}"},
                        timeout=aiohttp.ClientTimeout(total=4),
                    ) as _r:
                        _ag = await _r.json()
                _total = _ag.get("num_dns_queries", 0)
                _blocked = _ag.get("num_blocked_filtering", 0)
                _pct = round(_blocked / max(_total, 1) * 100, 1)
                lines.append(f"• 🛡️ AdGuard: {_total:,} queries, {_pct}% blocked")
            except Exception:
                pass

        # Lidarr
        _lidarr_url = os.environ.get("LIDARR_URL", "")
        _lidarr_key = os.environ.get("LIDARR_API_KEY", "")
        if _lidarr_url and _lidarr_key:
            try:
                async with aiohttp.ClientSession() as _s:
                    async with _s.get(
                        f"{_lidarr_url}/api/v1/queue",
                        headers={"X-Api-Key": _lidarr_key},
                        timeout=aiohttp.ClientTimeout(total=4),
                    ) as _r:
                        _lq = await _r.json()
                _active = [
                    i for i in (_lq if isinstance(_lq, list) else []) if i.get("status") in ("downloading", "queued")
                ]
                if _active:
                    lines.append(f"• 🎵 Lidarr: {len(_active)} downloading")
                else:
                    lines.append("• 🎵 Lidarr: queue empty")
            except Exception:
                pass

        # qBittorrent
        _qbt_url = os.environ.get("QBIT_URL", "")
        _qbt_user = os.environ.get("QBIT_USER", "admin")
        _qbt_pass = os.environ.get("QBIT_PASSWORD", "")
        if _qbt_url and _qbt_pass:
            try:
                import aiohttp as _aiohttp_qbt

                _jar = _aiohttp_qbt.CookieJar()
                async with _aiohttp_qbt.ClientSession(cookie_jar=_jar) as _s:
                    await _s.post(
                        f"{_qbt_url}/api/v2/auth/login",
                        data={"username": _qbt_user, "password": _qbt_pass},
                        timeout=_aiohttp_qbt.ClientTimeout(total=5),
                    )
                    async with _s.get(
                        f"{_qbt_url}/api/v2/transfer/info",
                        timeout=_aiohttp_qbt.ClientTimeout(total=4),
                    ) as _r:
                        _xfer = await _r.json()
                    async with _s.get(
                        f"{_qbt_url}/api/v2/torrents/info",
                        params={"filter": "active"},
                        timeout=_aiohttp_qbt.ClientTimeout(total=4),
                    ) as _r:
                        _active_torrents = await _r.json()
                _dl = _xfer.get("dl_info_speed", 0) / 1024 / 1024
                _up = _xfer.get("up_info_speed", 0) / 1024 / 1024
                _active_count = len(_active_torrents)
                if _active_count or _dl > 0.01:
                    lines.append(f"• 🌊 qBittorrent: {_active_count} active ⬇️{_dl:.1f} MB/s ⬆️{_up:.1f} MB/s")
                else:
                    lines.append("• 🌊 qBittorrent: idle")
            except Exception:
                pass

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
        except Exception as e:
            log.warning("Slack send failed in /status: %s", e)

    @app.command("/uptime")
    async def handle_slash_uptime(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        try:
            uptime_snapshot = await _fetch_uptime_kuma_snapshot()
            lines = ["*📊 Service Status*", ""]
            for group in uptime_snapshot["groups"]:
                services = group.get("services", [])
                if not services:
                    continue
                up_count = sum(1 for service in services if service.get("status") == 1)
                lines.append(f"*{group['name']}* ({up_count}/{len(services)} up)")
                for service in services:
                    status = service.get("status", 0)
                    name = service.get("name", "?")
                    if status == 1:
                        lines.append(f"  ✅ {name}")
                    elif status == 0:
                        lines.append(f"  ❌ {name} — down")
                    elif status == 3:
                        lines.append(f"  ⚠️ {name} — maintenance")
                    else:
                        lines.append(f"  ⚠️ {name} — {_uptime_status_label(status)}")
                lines.append("")

            if len(lines) == 2:
                lines.append("_No services found on the Uptime Kuma status page._")

            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines).strip())
        except Exception as exc:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"⚠️ Could not load Uptime Kuma status: {exc}",
            )

    @app.command("/morning")
    async def handle_slash_morning(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        result = await _send_morning_briefing(client)
        if result != "Morning briefing sent":
            log.warning("/morning result: %s", result)
        try:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="☀️ Morning briefing triggered!",
            )
        except Exception as e:
            log.warning("Slack send failed in /morning: %s", e)

    @app.command("/resume")
    async def handle_slash_resume(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        _ = say
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        prompt = (body.get("text") or "").strip()

        ok, msg = _copilot_owner_check(user_id, command_name="/resume")
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return

        try:
            import sqlite3

            db_path = "/Users/davevoyles/.hermes/state.db"
            conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
            try:
                row = conn.execute(
                    "SELECT id, title, COALESCE(message_count, 0) FROM sessions ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Could not read Hermes sessions: {exc}",
            )
            return

        if not row:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="No Hermes sessions found. Start one with `/h <prompt>`",
            )
            return

        session_id, title, msg_count = row
        session_id = str(session_id)
        short_id = session_id[:8]
        title_text = str(title or "Untitled")

        if not prompt:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"⚕ *Last session:* `{short_id}` — _{title_text}_ ({msg_count} messages)\n"
                    "Send your next message with `/resume <your message>`"
                ),
            )
            return

        cancel_id = "r" + secrets.token_hex(4)
        msg = await client.chat_postMessage(
            channel=channel_id,
            text=f"⚕ Resuming session `{short_id}`… _{title_text}_ _(`/copilot-cancel {cancel_id}` to stop)_",
        )
        thread_ts = msg.get("ts") or msg.get("message", {}).get("ts")

        async def _bg_resume() -> None:
            answer, err = await _run_hermes_quick_command(
                prompt, resume_session_id=session_id, slack_user=user_id, cancel_id=cancel_id
            )
            if err:
                text = "⏹ Cancelled." if err == "cancelled" else f"❌ Error: {err}"
                await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
                return
            await _post_slack_text_chunks(client, channel_id, answer, thread_ts=thread_ts)

        asyncio.create_task(_bg_resume())

    @app.command("/sessions")
    async def handle_slash_sessions(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import sqlite3
        import time as _time

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        text = (body.get("text", "") or "").strip()

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⏳ Reading Hermes sessions…")
        except Exception:
            pass

        db_path = "/Users/davevoyles/.hermes/state.db"

        try:
            conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
            try:
                sessions = conn.execute(
                    "SELECT id, title, message_count, started_at, model FROM sessions ORDER BY started_at DESC LIMIT 10"
                ).fetchall()
            finally:
                conn.close()
        except Exception as e:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Could not read sessions: {e}")
            return

        if not sessions:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="No Hermes sessions found.")
            return

        if text.startswith("resume "):
            try:
                idx = int(text.split()[1]) - 1
                if 0 <= idx < len(sessions):
                    session_id = sessions[idx][0]
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=(
                            f"Resuming session: `{str(session_id)[:16]}…`\n"
                            "Send `/copilot` or `/hermes` with your next message — session will continue."
                        ),
                    )
                else:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"Session #{idx + 1} not found. List has {len(sessions)} sessions.",
                    )
            except (ValueError, IndexError):
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/sessions resume <number>`"
                )
            return

        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(sessions):
                sid, title, mc, started, model = sessions[idx]
                started_str = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(started)) if started else "?"
                msg = (
                    f"*Session #{idx + 1}*\n"
                    f"ID: `{sid}`\n"
                    f"Title: {title or 'Untitled'}\n"
                    f"Model: {model or '?'}\n"
                    f"Messages: {mc or 0}\n"
                    f"Started: {started_str}\n\n"
                    f"Resume: `/sessions resume {idx + 1}`"
                )
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
                return

        lines = ["*⚕ Hermes Sessions*", ""]
        for i, (sid, title, mc, started, model) in enumerate(sessions, 1):
            started_str = _time.strftime("%m/%d %H:%M", _time.localtime(started)) if started else "?"
            short_title = (title or "Untitled")[:50]
            lines.append(f"{i}. _{short_title}_ ({mc or 0} msgs · {started_str})")
        lines.append("\n_Tip: `/sessions 3` for details · `/sessions resume 3` to continue_")
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))

    @app.command("/q")
    async def handle_slash_q(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        prompt = (body.get("text") or "").strip()

        ok, msg = _copilot_owner_check(user_id, command_name="/q")
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return

        if not prompt:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/q <your question>` — asks Hermes and shows the answer only to you",
            )
            return

        cancel_id = "q" + secrets.token_hex(4)
        await client.chat_postEphemeral(
            channel=channel_id, user=user_id, text=f"⚕ Thinking… _(`/copilot-cancel {cancel_id}` to stop)_"
        )
        answer, err = await _run_hermes_quick_command(prompt, slack_user=user_id, cancel_id=cancel_id)
        if err:
            text = "⏹ Cancelled." if err == "cancelled" else f"❌ Error: {err}"
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)
            return

        answer = answer or "(no response)"
        if len(answer) <= 2000:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"⚕ {answer}")
            return

        parent = await client.chat_postMessage(channel=channel_id, text="⚕ Answer to your quick question:")
        thread_ts = parent.get("ts") or parent.get("message", {}).get("ts")
        await _post_slack_text_chunks(client, channel_id, answer, thread_ts=thread_ts)

    # ------------------------------------------------------------------
    # Companion slash commands for Phase 3 session management
    # ------------------------------------------------------------------

    @app.command("/copilot-sessions")
    async def handle_slash_copilot_sessions(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return
        mgr = await _ensure_session_manager()
        if mgr is None:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ host_bridge unavailable")
            return
        rows = sorted(mgr.list_sessions(user_id), key=lambda r: -r.started_at)[:20]
        if not rows:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="_no Copilot sessions on record_")
            return
        lines = ["🧵 *Your Copilot sessions:*"]
        for r in rows:
            live = "🟢" if _session_is_live(mgr, r.session_id) else ("💀" if r.status == "crashed" else "⚫")
            age = int(time.time() - r.started_at)
            backend_label = "hermes" if _is_hermes_session(r) else "ssh"
            lines.append(
                f"{live} `{r.session_id}` — {backend_label} — {r.status} — turns:{r.turns} — age:{age}s — "
                f"thread:<#{r.slack_channel}>"
            )
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))

    @app.command("/copilot-cancel")
    async def handle_slash_copilot_cancel(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        sid = (body.get("text") or "").strip().split()[0] if (body.get("text") or "").strip() else ""
        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return
        if not sid:
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text="usage: `/copilot-cancel <session_id>`"
            )
            return
        mgr = await _ensure_session_manager()
        if mgr is None:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ host_bridge unavailable")
            return
        rec = mgr.get_record(sid)
        if rec is None:
            # One-shot /q and /resume turns have no persistent record; they
            # register a handle in _hermes_live_procs under their cancel id.
            qhandle = _hermes_live_procs.get(sid)
            if qhandle is not None and getattr(qhandle, "slack_user", None) == user_id:
                try:
                    qhandle.terminate()
                except Exception as exc:  # noqa: BLE001
                    await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ cancel failed: {exc}")
                    return
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"⏹ stop signal sent to `{sid}`")
                return
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ session not found")
            return
        if _is_hermes_session(rec):
            err = await _cancel_hermes_session(rec, slack_user=user_id)
        else:
            err = await mgr.cancel(sid, slack_user=user_id)
        if err:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ {err}")
        else:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"⏹ stop signal sent to `{sid}`")

    @app.command("/copilot-end")
    async def handle_slash_copilot_end(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        sid = (body.get("text") or "").strip().split()[0] if (body.get("text") or "").strip() else ""
        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return
        if not sid:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="usage: `/copilot-end <session_id>`")
            return
        mgr = await _ensure_session_manager()
        if mgr is None:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ host_bridge unavailable")
            return
        rec = mgr.get_record(sid)
        if rec is None:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ session not found")
            return
        if _is_hermes_session(rec):
            err = await _end_hermes_session(mgr, rec, slack_user=user_id)
        else:
            err = await mgr.end(sid, slack_user=user_id, reason="user_ended")
        if err:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ {err}")
        else:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"🛑 session `{sid}` ended")

    @app.command("/copilot-attach")
    async def handle_slash_copilot_attach(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        sid = (body.get("text") or "").strip().split()[0] if (body.get("text") or "").strip() else ""
        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return
        if not sid:
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text="usage: `/copilot-attach <session_id>`"
            )
            return
        mgr = await _ensure_session_manager()
        if mgr is None:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ host_bridge unavailable")
            return
        rec = mgr.get_record(sid)
        if rec is None or rec.slack_user != user_id:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="❌ session not found")
            return
        age = int(time.time() - rec.started_at)
        idle = int(time.time() - rec.last_activity)
        backend_label = "hermes" if _is_hermes_session(rec) else "ssh"
        live = "🟢 live" if _session_is_live(mgr, sid) else f"⚫ {rec.status}"
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=(
                f"📎 session `{sid}` — {live}\n"
                f"• backend: `{backend_label}`\n"
                f"• channel: <#{rec.slack_channel}> (thread `{rec.slack_thread_ts}`)\n"
                f"• cwd: `{rec.cwd}` • turns: {rec.turns}\n"
                f"• started: {age}s ago • idle: {idle}s\n"
                f"• transcript: `{rec.transcript_path}`"
            ),
        )

    # ------------------------------------------------------------------
    # /copilot-recap — summarise a session transcript via Gemini
    # ------------------------------------------------------------------

    @app.command("/copilot-recap")
    async def handle_slash_copilot_recap(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        sid = (body.get("text") or "").strip().split()[0] if (body.get("text") or "").strip() else ""
        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return
        if not sid:
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text="usage: `/copilot-recap <session_id>`"
            )
            return

        # Locate transcript file — try manager first, fall back to audit dir glob.
        transcript_text: str | None = None
        transcript_path: str | None = None
        mgr = await _ensure_session_manager()
        if mgr is not None:
            rec = mgr.get_record(sid)
            if rec and rec.transcript_path:
                transcript_path = rec.transcript_path
        if transcript_path is None:
            import glob as _glob

            audit_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "audit", "host_bridge")
            matches = _glob.glob(os.path.join(audit_dir, f"*{sid}*"))
            if matches:
                transcript_path = sorted(matches)[-1]
        if transcript_path and os.path.exists(transcript_path):
            try:
                with open(transcript_path) as fh:
                    transcript_text = fh.read()
            except OSError:
                pass

        if not transcript_text:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ No transcript found for session `{sid}`. Is the session ID correct?",
            )
            return

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"⏳ Generating recap for session `{sid}`…",
        )

        recap_prompt = (
            "You are summarising a GitHub Copilot CLI session transcript. "
            "Produce a concise, structured recap with these sections:\n"
            "1. **Goal** — what the user asked Copilot to do\n"
            "2. **Actions** — key steps Copilot took (tools used, commands run)\n"
            "3. **Files changed** — list any files created or modified (paths)\n"
            "4. **Outcome** — what was accomplished or left unfinished\n\n"
            "Keep it brief (< 300 words). Here is the transcript:\n\n"
            f"{transcript_text[:12000]}"
        )
        try:
            from ask_orchestrator import ask_question  # type: ignore[import]

            recap = await ask_question(recap_prompt, model_pref="gemini")
        except Exception as exc:  # noqa: BLE001
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ recap generation failed: `{exc}`",
            )
            return

        # Post to the session thread if we have it, otherwise as ephemeral.
        thread_ts_for_recap = None
        if mgr is not None:
            rec = mgr.get_record(sid)
            if rec:
                thread_ts_for_recap = rec.slack_thread_ts
        try:
            if thread_ts_for_recap:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts_for_recap,
                    text=f"📋 *Session recap — `{sid}`*\n\n{recap}",
                )
            else:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"📋 *Session recap — `{sid}`*\n\n{recap}",
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("/copilot-recap post failed: %s", exc)

    @app.command("/plex")
    async def handle_plex_command(ack: Any, respond: Any, command: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = command.get("user_id", "")
        channel_id = command.get("channel_id", "")
        text = (command.get("text") or "").strip()
        plex_url = (os.getenv("PLEX_URL", "") or "").strip().rstrip("/")
        overseerr_url = (os.getenv("OVERSEERR_URL", "") or "").strip().rstrip("/")
        overseerr_api_key = _decode_overseerr_api_key(os.getenv("OVERSEERR_API_KEY", ""))

        if not text:
            subcommand = "status"
            remainder = ""
        else:
            parts = text.split(maxsplit=1)
            subcommand = parts[0].lower()
            remainder = parts[1].strip() if len(parts) > 1 else ""

        if subcommand not in {"status", "recent", "search", "request"}:
            await respond(
                text=("Usage: `/plex status`, `/plex recent`, `/plex search <title>`, or `/plex request <title>`")
            )
            return

        if not plex_url:
            await respond(text="❌ `PLEX_URL` is not set. Add it to `.env` and redeploy OpenClaw.")
            return

        if subcommand in {"search", "request"}:
            if not overseerr_url:
                await respond(text="❌ `OVERSEERR_URL` is not set. Add it to `.env` and redeploy OpenClaw.")
                return
            if not overseerr_api_key:
                await respond(text="❌ `OVERSEERR_API_KEY` is not set. Add it to `.env` and redeploy OpenClaw.")
                return
            if not remainder:
                await respond(text=f"Usage: `/plex {subcommand} <title>`")
                return

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⏳ Checking Plex…")
        except Exception:
            pass

        if subcommand in {"status"}:
            if not overseerr_url:
                await respond(
                    text=(
                        f"{await _plex_status_summary(plex_url)}\n\n"
                        "⚠️ `OVERSEERR_URL` is not set, so search/request actions are unavailable."
                    )
                )
                return
            await respond(text=await _plex_status_summary(plex_url))
            return

        if subcommand == "recent":
            if not overseerr_url:
                await respond(
                    text=(
                        f"{await _plex_recent_summary(plex_url)}\n\n"
                        "⚠️ `OVERSEERR_URL` is not set, so search/request actions are unavailable."
                    )
                )
                return
            await respond(text=await _plex_recent_summary(plex_url))
            return

        try:
            search_status, search_body = await _overseerr_request(
                "GET",
                overseerr_url,
                overseerr_api_key,
                "/api/v1/search",
                params={"query": remainder},
            )
        except Exception as exc:  # noqa: BLE001
            await respond(text=f"❌ Overseerr request failed: {exc}")
            return

        if search_status != 200:
            detail = search_body if isinstance(search_body, str) else json.dumps(search_body)[:300]
            await respond(text=f"❌ Overseerr search failed (HTTP {search_status}): {detail}")
            return

        results = search_body.get("results", []) if isinstance(search_body, dict) else []
        media_results = [item for item in results if item.get("mediaType") in {"movie", "tv"}]
        if not media_results:
            await respond(text=f"🔎 No Plex/Overseerr matches found for *{remainder}*.")
            return

        if subcommand == "search":
            status_map = {2: "requested", 3: "processing", 4: "partially available", 5: "available"}
            lines = [f"🔎 *Overseerr results for* `{remainder}`"]
            for item in media_results[:5]:
                media_type = "TV" if item.get("mediaType") == "tv" else "Movie"
                year = (item.get("releaseDate") or item.get("firstAirDate") or "")[:4]
                media_info = item.get("mediaInfo") or {}
                availability = status_map.get(media_info.get("status"))
                suffix = f" ({year})" if year else ""
                status_suffix = f" — {availability}" if availability else ""
                lines.append(f"• {media_type}: {_overseerr_title(item)}{suffix}{status_suffix}")
            await respond(text="\n".join(lines))
            return

        match = _pick_overseerr_result(media_results, remainder)
        if match is None:
            await respond(text=f"🔎 No requestable result found for *{remainder}*.")
            return

        current_status = (match.get("mediaInfo") or {}).get("status")
        status_map = {2: "requested", 3: "processing", 4: "partially available", 5: "available"}
        if current_status in status_map:
            await respond(text=f"ℹ️ *{_overseerr_title(match)}* is already {status_map[current_status]} in Overseerr.")
            return

        media_type = match.get("mediaType")
        media_id = match.get("id")
        if media_type not in {"movie", "tv"} or media_id is None:
            await respond(text="❌ Overseerr returned a result without a requestable media ID/type.")
            return

        try:
            request_status, request_body = await _overseerr_request(
                "POST",
                overseerr_url,
                overseerr_api_key,
                "/api/v1/request",
                payload={"mediaType": media_type, "mediaId": media_id},
            )
        except Exception as exc:  # noqa: BLE001
            await respond(text=f"❌ Failed to create Overseerr request: {exc}")
            return

        if request_status not in {200, 201}:
            detail = request_body if isinstance(request_body, str) else json.dumps(request_body)[:300]
            await respond(text=f"❌ Overseerr request failed (HTTP {request_status}): {detail}")
            return

        request_id = request_body.get("id") if isinstance(request_body, dict) else None
        request_suffix = f" (request #{request_id})" if request_id else ""
        await respond(text=f"✅ Requested *{_overseerr_title(match)}* via Overseerr{request_suffix}.")

    @app.command("/request")
    async def handle_slash_request(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import aiohttp

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        query = (body.get("text", "") or "").strip()

        if not query:
            try:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/request <movie or TV show title>`\nExample: `/request Toy Story 5`",
                )
            except Exception:
                pass
            return

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"🔍 Searching for _{query}_…")
        except Exception:
            pass

        overseerr_url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
        api_key = _decode_overseerr_api_key(os.environ.get("OVERSEERR_API_KEY", ""))
        headers = {"X-Api-Key": api_key}

        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{overseerr_url}/api/v1/search",
                    params={"query": query, "page": 1},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as r:
                    data = await r.json()
            results = [item for item in data.get("results", []) if item.get("mediaType") in {"movie", "tv"}][:5]
            if not results:
                try:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"❌ No results found for _{query}_",
                    )
                except Exception:
                    pass
                return

            status_map = {1: "⏳ Pending", 2: "✅ Approved", 3: "❌ Declined", 4: "🟢 Available", 5: "🔄 Processing"}
            lines = [f'*🔍 Results for "{query}"*\n']
            for i, item in enumerate(results, 1):
                icon = "🎬" if item.get("mediaType") == "movie" else "📺"
                title = item.get("originalTitle") or item.get("originalName") or item.get("name", "?")
                year = str(item.get("releaseDate", "") or item.get("firstAirDate", ""))[:4]
                media_info = item.get("mediaInfo")
                if media_info and media_info.get("status"):
                    status = status_map.get(media_info["status"], f"#{media_info['status']}")
                    lines.append(f"{i}. {icon} *{title}* ({year}) — {status}")
                else:
                    lines.append(f"{i}. {icon} *{title}* ({year}) — not requested yet")

            auto_match = results[0] if len(results) == 1 else _pick_overseerr_result(results, query)
            if auto_match and (
                len(results) == 1 or _overseerr_title(auto_match).strip().casefold() == query.casefold()
            ):
                media_id = auto_match.get("id")
                media_type = auto_match.get("mediaType", "movie")
                payload = {"mediaType": media_type, "mediaId": media_id}
                if media_type == "tv":
                    payload["seasons"] = "all"
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                        f"{overseerr_url}/api/v1/request",
                        json=payload,
                        headers={**headers, "Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as rr:
                        request_status = rr.status
                        req_data = await rr.json()
                if request_status in (200, 201):
                    lines.append(f"\n✅ Requested: *{_overseerr_title(auto_match)}*")
                else:
                    lines.append(f"\n⚠ Could not auto-request: {req_data.get('message', 'unknown error')}")
            else:
                lines.append(
                    "\n_Refine the title and rerun `/request`, or use the dashboard search card for one-tap requesting._"
                )

            try:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
            except Exception as e:
                log.warning("Slack send failed in /request: %s", e)
        except Exception as e:
            try:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Overseerr error: {e}")
            except Exception:
                pass

    @app.command("/arr")
    async def handle_slash_arr(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import os

        import aiohttp

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        sonarr_url = os.environ.get("SONARR_URL", "http://localhost:8989")
        sonarr_key = os.environ.get("SONARR_API_KEY", "")
        radarr_url = os.environ.get("RADARR_URL", "http://localhost:7878")
        radarr_key = os.environ.get("RADARR_API_KEY", "")
        lidarr_url = os.environ.get("LIDARR_URL", "http://host.docker.internal:8686")
        lidarr_key = os.environ.get("LIDARR_API_KEY", "")

        def _pct(item: dict[str, Any]) -> int:
            size = item.get("size") or 0
            return round(100 * (1 - item.get("sizeleft", 0) / max(size, 1))) if size else 0

        lines = ["*📥 Download Queue*"]

        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(
                    f"{sonarr_url}/api/v3/queue",
                    params={"apikey": sonarr_key, "pageSize": 5},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    d = await r.json()
                    total = d.get("totalRecords", 0)
                    records = d.get("records", [])
                    if total == 0:
                        lines.append("📺 Sonarr: 0 active downloads")
                    else:
                        lines.append(f"📺 Sonarr: {total} in queue")
                        for item in records[:3]:
                            lines.append(f"  • {item.get('title', '?')} ({_pct(item)}%)")
            except Exception as e:
                lines.append(f"📺 Sonarr: error ({e})")

            try:
                async with s.get(
                    f"{radarr_url}/api/v3/queue",
                    params={"apikey": radarr_key, "pageSize": 5},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    d = await r.json()
                    total = d.get("totalRecords", 0)
                    records = d.get("records", [])
                    if total == 0:
                        lines.append("🎬 Radarr: 0 active downloads")
                    else:
                        lines.append(f"🎬 Radarr: {total} in queue")
                        for item in records[:3]:
                            lines.append(f"  • {item.get('title', '?')} ({_pct(item)}%)")
            except Exception as e:
                lines.append(f"🎬 Radarr: error ({e})")

            try:
                async with s.get(
                    f"{lidarr_url}/api/v1/queue?page=1&pageSize=20&apikey={lidarr_key}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    d = await r.json()
                    total = d.get("totalRecords", 0)
                    records = d.get("records", [])
                    if total == 0:
                        lines.append("🎵 Lidarr: 0 active downloads")
                    else:
                        lines.append(f"🎵 Lidarr: {total} in queue")
                        for item in records[:3]:
                            artist_name = (item.get("artist") or {}).get("artistName") or item.get("artistName", "")
                            title = item.get("title") or (item.get("album") or {}).get("title") or "?"
                            label = " - ".join(part for part in [artist_name, title] if part) or "?"
                            lines.append(f"  • {label} ({_pct(item)}%)")
            except Exception as e:
                lines.append(f"🎵 Lidarr: error ({e})")

            try:
                async with s.get(
                    f"{radarr_url}/api/v3/wanted/missing",
                    params={"apikey": radarr_key, "pageSize": 1},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    d = await r.json()
                    missing = d.get("totalRecords", 0)
                    if missing:
                        lines.append(f"_📋 {missing} movies on Radarr watchlist_")
            except Exception:
                pass

        await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))

    @app.command("/downloads")
    async def handle_slash_downloads(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import aiohttp

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        sections = []

        # --- SABnzbd ---
        sab_url = os.environ.get("SABNZBD_URL", "http://host.docker.internal:8775")
        sab_key = os.environ.get("SABNZBD_API_KEY", "")
        if sab_key:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{sab_url}/api",
                        params={"mode": "queue", "apikey": sab_key, "output": "json"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        data = await resp.json()

                q = data.get("queue", {})
                status = q.get("status", "Unknown")
                speed_kb = float(q.get("kbpersec", 0))
                speed_str = f"{speed_kb / 1024:.1f} MB/s" if speed_kb > 0 else "0 MB/s"
                slots = q.get("slots", [])
                total = int(q.get("noofslots_total", 0) or 0)
                size_left = q.get("sizeleft", "0 B")

                if not slots:
                    sections.append(f"📰 *SABnzbd:* {status} — queue empty")
                else:
                    sab_lines = [f"📰 *SABnzbd* — {status} at {speed_str} · *{total} item(s)* · {size_left} left"]
                    for s in slots[:4]:
                        pct = s.get("percentage", 0)
                        name = s.get("filename", s.get("name", "?"))[:48]
                        timeleft = s.get("timeleft", "")
                        eta = f" · ETA {timeleft}" if timeleft and timeleft != "0:00:00" else ""
                        sab_lines.append(f"  • {name} — {pct}%{eta}")
                    if total > 4:
                        sab_lines.append(f"  _...and {total - 4} more_")
                    sections.append("\n".join(sab_lines))
            except Exception as exc:
                sections.append(f"📰 *SABnzbd:* ❌ error — {exc}")

        # --- qBittorrent ---
        qbt_url = os.environ.get("QBIT_URL", "")
        qbt_user = os.environ.get("QBIT_USER", "admin")
        qbt_pass = os.environ.get("QBIT_PASSWORD", "")
        if qbt_url and qbt_pass:
            try:
                jar = aiohttp.CookieJar()
                async with aiohttp.ClientSession(cookie_jar=jar) as s:
                    async with s.post(
                        f"{qbt_url}/api/v2/auth/login",
                        data={"username": qbt_user, "password": qbt_pass},
                        timeout=aiohttp.ClientTimeout(total=6),
                    ) as r:
                        auth = await r.text()
                    if auth.strip() not in ("Ok.", "Ok"):
                        raise ValueError(f"auth failed: {auth}")

                    async with s.get(
                        f"{qbt_url}/api/v2/transfer/info",
                        timeout=aiohttp.ClientTimeout(total=4),
                    ) as r:
                        xfer = await r.json()

                    async with s.get(
                        f"{qbt_url}/api/v2/torrents/info",
                        params={"sort": "added_on", "reverse": "true", "limit": "8"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as r:
                        torrents = await r.json()

                dl = xfer.get("dl_info_speed", 0) / 1024 / 1024
                up = xfer.get("up_info_speed", 0) / 1024 / 1024
                free_gb = xfer.get("free_space_on_disk", 0) / 1e9
                active = [
                    t for t in torrents if t.get("state") not in ("pausedDL", "pausedUP", "stalledUP", "uploading")
                ]
                total_t = len(torrents)

                state_emoji = {
                    "downloading": "⬇️",
                    "uploading": "⬆️",
                    "stalledDL": "⏸",
                    "pausedDL": "⏹",
                    "queuedDL": "🕐",
                    "error": "❌",
                }
                if not active and dl < 0.01:
                    qbt_lines = [f"🌊 *qBittorrent:* idle · {total_t} torrent(s) · {free_gb:,.0f} GB free"]
                else:
                    qbt_lines = [f"🌊 *qBittorrent* — ⬇️ {dl:.1f} MB/s  ⬆️ {up:.1f} MB/s · {free_gb:,.0f} GB free"]
                for t in torrents[:5]:
                    state = t.get("state", "?")
                    emoji = state_emoji.get(state, "•")
                    name = t.get("name", "?")[:48]
                    pct = t.get("progress", 0) * 100
                    size_gb = t.get("size", 0) / 1e9
                    if pct < 100:
                        qbt_lines.append(f"  {emoji} {name} — {pct:.0f}% ({size_gb:.1f} GB)")
                    else:
                        qbt_lines.append(f"  ⬆️ {name} — seeding")
                if total_t > 5:
                    qbt_lines.append(f"  _...and {total_t - 5} more_")
                sections.append("\n".join(qbt_lines))
            except Exception as exc:
                sections.append(f"🌊 *qBittorrent:* ❌ error — {exc}")

        if not sections:
            text = "⚠️ No download managers configured (SABNZBD_API_KEY / QBIT_PASSWORD missing)."
        else:
            text = "\n\n".join(sections)

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text, mrkdwn=True)
        except Exception as exc:
            log.warning("Slack send failed in /downloads: %s", exc)

    @app.command("/upcoming")
    async def handle_slash_upcoming(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import os
        from datetime import datetime, timedelta, timezone

        import aiohttp

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="📅 Fetching upcoming episodes…")
        except Exception:
            pass

        sonarr_url = os.environ.get("SONARR_URL", "http://localhost:8989")
        sonarr_key = os.environ.get("SONARR_API_KEY", "")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        end = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{sonarr_url}/api/v3/calendar",
                    params={"apikey": sonarr_key, "start": today, "end": end, "includeSeries": "true"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    episodes = await r.json()

            if not episodes:
                try:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="📅 No episodes airing in the next 7 days",
                    )
                except Exception:
                    pass
                return

            by_date = {}
            for ep in sorted(episodes, key=lambda e: e.get("airDateUtc", "")):
                date = ep.get("airDateUtc", "")[:10]
                by_date.setdefault(date, []).append(ep)

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tomorrow_str = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

            lines = ["*📅 Upcoming Episodes (next 7 days)*"]
            for date, eps in by_date.items():
                label = "Today" if date == today_str else "Tomorrow" if date == tomorrow_str else date
                lines.append(f"\n*{label}*")
                for ep in eps:
                    series = ep.get("series", {}).get("title", ep.get("title", "?"))
                    s_num = ep.get("seasonNumber", 0)
                    e_num = ep.get("episodeNumber", 0)
                    has_file = "✓" if ep.get("hasFile") else "·"
                    lines.append(f"  {has_file} _{series}_ S{s_num:02d}E{e_num:02d}")

            try:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
            except Exception as e:
                log.warning("Slack send failed in /upcoming: %s", e)
        except Exception as e:
            try:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Sonarr error: {e}")
            except Exception:
                pass

    @app.command("/watching")
    async def handle_slash_watching(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")

        import os

        import aiohttp

        tautulli_url = os.environ.get("TAUTULLI_URL", "http://localhost:8181")
        tautulli_key = os.environ.get("TAUTULLI_API_KEY", "")

        if not tautulli_key:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="TAUTULLI_API_KEY not configured")
            return

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⏳ Checking Plex…")
        except Exception:
            pass

        lines = []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{tautulli_url}/api/v2",
                    params={"apikey": tautulli_key, "cmd": "get_activity"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    act = (await r.json()).get("response", {}).get("data", {})
                sessions = act.get("sessions", [])
                if sessions:
                    lines.append(
                        f"*🎬 Now Playing on Plex* ({len(sessions)} stream{'s' if len(sessions) > 1 else ''})\n"
                    )
                    for s2 in sessions:
                        pct = s2.get("progress_percent", 0)
                        lines.append(
                            f"• _{s2.get('full_title', s2.get('title', '?'))}_ — {s2.get('user', '')} ({pct}%)"
                        )
                else:
                    lines.append("*🎬 Plex* — Nothing streaming right now\n")
                    async with s.get(
                        f"{tautulli_url}/api/v2",
                        params={"apikey": tautulli_key, "cmd": "get_history", "length": "3"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as r:
                        hist = (await r.json()).get("response", {}).get("data", {}).get("data", [])
                    if hist:
                        lines.append("*Recently watched:*")
                        for item in hist[:3]:
                            icon = "🎬" if item.get("media_type") == "movie" else "📺"
                            lines.append(f"• {icon} _{item.get('full_title', item.get('title', '?'))}_")
        except Exception as e:
            lines.append(f"❌ Could not reach Tautulli: {e}")

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
        except Exception as e:
            log.warning("Slack send failed in /watching: %s", e)

    @app.command("/wake")
    async def handle_slash_wake(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        _ = say
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        requested = (body.get("text") or "").strip().lower()
        machines = _wol_machine_registry()
        available_lines = [
            f"• `{name}` — *{info['label']}*" + (f" (`{info['ip']}`)" if info.get("ip") else "")
            for name, info in machines.items()
        ]

        if not machines:
            text = "❌ No Wake-on-LAN targets are configured. Set `WOL_MACBOOK_PRO_MAC` and/or `WOL_MACBOOK_PRO2_MAC`."
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*⚡ Wake-on-LAN*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            ]
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text, blocks=blocks)
            return

        if requested not in machines:
            text = "❌ Usage: `/wake mbp` or `/wake mbp2`"
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*⚡ Wake-on-LAN*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Available machines*\n" + "\n".join(available_lines)},
                },
            ]
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text, blocks=blocks)
            return

        machine = machines[requested]
        broadcast_ip = os.environ.get("WOL_BROADCAST_IP", "192.168.1.255")
        try:
            _send_wol_magic_packet(str(machine["mac"]), broadcast_ip)
            text = f"✅ Sent a Wake-on-LAN packet to {machine['label']} ({requested})."
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*⚡ Wake-on-LAN*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (f"Target IP: `{machine.get('ip') or 'unknown'}` · Broadcast: `{broadcast_ip}`"),
                        }
                    ],
                },
            ]
        except Exception as exc:  # noqa: BLE001
            text = f"❌ Failed to send Wake-on-LAN packet: {exc}"
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*⚡ Wake-on-LAN*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Available machines*\n" + "\n".join(available_lines)},
                },
            ]
        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text, blocks=blocks)
        except Exception as e:
            log.warning("Slack send failed in /wake: %s", e)

    @app.command("/tailscale")
    async def handle_slash_tailscale(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import asyncio

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        output = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                "tailscale",
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                output = out.decode().strip()
        except Exception:
            output = ""

        if not output:
            try:
                from host_bridge import _enabled as _host_bridge_enabled
                from host_bridge import run_shell as _run_shell_ts

                if _host_bridge_enabled():
                    result = await _run_shell_ts(command="tailscale status", slack_user_id="slack", timeout_s=10)
                    output = result if isinstance(result, str) else (getattr(result, "stdout", "") or "")
            except Exception as exc:
                output = f"tailscale not available: {exc}"

        if not output:
            output = "No devices found"

        lines = output.splitlines()[:20]
        msg = f"*🌐 Tailscale Status*\n```\n{chr(10).join(lines)}\n```"

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
        except Exception as e:
            log.warning("Slack send failed in /tailscale: %s", e)

    @app.command("/nas")
    async def handle_slash_nas(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        _ = say
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        parts = text.split(maxsplit=1)
        subcommand = parts[0].lower() if parts else ""
        remainder = parts[1].strip() if len(parts) > 1 else ""

        if subcommand in {"df", "free"} or (subcommand == "ls" and remainder):
            try:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⏳ Querying NAS…")
            except Exception:
                pass

        def _usage_blocks() -> tuple[str, list[dict[str, Any]]]:
            usage_text = "❌ Usage: `/nas df`, `/nas ls <path>`, or `/nas free`"
            return (
                usage_text,
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*💾 NAS tools*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": usage_text}},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "*Examples*\n• `/nas df`\n• `/nas ls /Users/davevoyles/mnt/ROMs/ROMs`\n• `/nas free`"
                            ),
                        },
                    },
                ],
            )

        def _disk_blocks(
            payload: dict[str, Any], *, heading: str = "*💾 NAS disk usage*"
        ) -> tuple[str, list[dict[str, Any]]]:
            shares = payload.get("shares", []) if isinstance(payload, dict) else []
            if not shares:
                text_out = "❌ No NAS disk data is available right now."
                return (
                    text_out,
                    [
                        {"type": "section", "text": {"type": "mrkdwn", "text": heading}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                    ],
                )

            lines: list[str] = []
            for share in shares:
                pct_raw = str(share.get("use_pct", "0%"))
                try:
                    pct_num = int(pct_raw.rstrip("%") or "0")
                except ValueError:
                    pct_num = 0
                emoji = "⚠️" if pct_num >= 85 else "✅"
                label = str(share.get("label") or share.get("mount") or "share")
                lines.append(
                    f"{emoji} *{label}* — {share.get('used', '?')} / {share.get('size', '?')} used "
                    f"({pct_raw}) · free {share.get('avail', '?')}"
                )
            source = payload.get("source", "unknown") if isinstance(payload, dict) else "unknown"
            text_out = "\n".join(lines)
            return (
                text_out,
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": heading}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f"source: `{source}`"}]},
                ],
            )

        if not subcommand:
            # No subcommand → SSH status overview (disk + load + container summary)
            import os as _os

            nas_host = _os.environ.get("NAS_HOST", "192.168.1.8")
            nas_port = _os.environ.get("NAS_SSH_PORT", "24")
            nas_user = _os.environ.get("NAS_SSH_USER", "dave")
            nas_cmd = (
                "echo '=DISK='; df -h / /volume1 2>/dev/null; "
                "echo '=UPTIME='; uptime; "
                "echo '=CONTAINERS='; /usr/local/bin/docker ps --format '{{.Names}}|{{.Status}}'"
            )
            import asyncio as _asyncio
            import re as _re

            try:
                proc = await _asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "BatchMode=yes",
                    "-p",
                    nas_port,
                    f"{nas_user}@{nas_host}",
                    nas_cmd,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=15)
                raw = stdout.decode()
            except Exception as exc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Cannot reach NAS via SSH: {exc}"
                )
                return

            parts_out = []
            # Disk
            if "=DISK=" in raw:
                disk_block = raw.split("=DISK=\n", 1)[1].split("=UPTIME=")[0].strip()
                disk_lines = ["💾 *Disk*"]
                for ln in disk_block.splitlines()[1:]:
                    cols = ln.split()
                    if len(cols) >= 6:
                        pct_num = int(cols[4].replace("%", "")) if cols[4].replace("%", "").isdigit() else 0
                        icon = "🔴" if pct_num >= 90 else "🟡" if pct_num >= 75 else "🟢"
                        label = "System" if cols[5] == "/" else f"Volume1 ({cols[1]})"
                        disk_lines.append(f"  {icon} *{label}*: {cols[2]} / {cols[1]} ({cols[4]})")
                parts_out.append("\n".join(disk_lines))
            # Load
            if "=UPTIME=" in raw:
                uptime_block = raw.split("=UPTIME=\n", 1)[1].split("=CONTAINERS=")[0].strip()
                lm = _re.search(r"load average[s]?:?\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)", uptime_block)
                um = _re.search(r"up\s+(.+?),\s+\d+ user", uptime_block)
                if lm:
                    l1, l5, l15 = lm.groups()
                    load_icon = "🔴" if float(l1) > 4 else "🟡" if float(l1) > 2 else "🟢"
                    up_str = f" (up {um.group(1)})" if um else ""
                    parts_out.append(f"{load_icon} *Load*: {l1} / {l5} / {l15}{up_str}")
            # Containers summary
            if "=CONTAINERS=" in raw:
                cont_raw = raw.split("=CONTAINERS=\n", 1)[1].strip()
                cont_lines = [line for line in cont_raw.splitlines() if line.strip()]
                total = len(cont_lines)
                unhealthy = [
                    line.split("|")[0] for line in cont_lines if "unhealthy" in line.lower() or "exited" in line.lower()
                ]
                if unhealthy:
                    c_block = [f"🐳 *Containers* ({total} running, {len(unhealthy)} ⚠️)"]
                    for c in unhealthy:
                        c_block.append(f"  🔴 {c}")
                    parts_out.append("\n".join(c_block))
                else:
                    parts_out.append(f"🐳 *Containers*: {total} running ✅  — `/nas containers` for full list")

            text_out = f"🖥️ *NAS* — `{nas_host}`\n\n" + "\n\n".join(parts_out)
            text_out += "\n\n<https://homepage.davevoyles.synology.me|🔗 Dashboard>"
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, mrkdwn=True)
            return

        if subcommand == "df":
            try:
                status, payload = await _openclaw_local_json("GET", "/api/nas/disk", timeout_s=20)
            except Exception as exc:  # noqa: BLE001
                text_out = f"❌ Could not reach NAS disk endpoint: {exc}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*💾 NAS disk usage*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            if status != 200 or not isinstance(payload, dict):
                detail = payload if isinstance(payload, str) else json.dumps(payload)[:300]
                text_out = f"❌ NAS disk request failed (HTTP {status}): {detail}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*💾 NAS disk usage*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            text_out, blocks = _disk_blocks(payload)
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
            return

        if subcommand == "ls":
            if not remainder:
                usage_text, usage_blocks = _usage_blocks()
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=usage_text, blocks=usage_blocks)
                return

            try:
                status, payload = await _openclaw_local_json(
                    "GET",
                    "/api/nas/browse",
                    params={"path": remainder},
                    timeout_s=20,
                )
            except Exception as exc:  # noqa: BLE001
                text_out = f"❌ Could not reach NAS browse endpoint: {exc}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*📁 NAS browse*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            if status != 200 or not isinstance(payload, dict):
                detail = payload if isinstance(payload, str) else json.dumps(payload)[:300]
                text_out = f"❌ NAS browse failed (HTTP {status}): {detail}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*📁 NAS browse*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            error_text = str(payload.get("error") or "")
            if error_text:
                text_out = f"❌ {error_text}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*📁 NAS browse*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            entries = payload.get("entries", [])
            shown = entries[:20] if isinstance(entries, list) else []
            if shown:
                lines = []
                for entry in shown:
                    icon = "📁" if entry.get("is_dir") else "📄"
                    size_text = f" — `{entry.get('size')}`" if entry.get("size") else ""
                    lines.append(f"{icon} `{entry.get('name', '?')}`{size_text}")
                text_out = "\n".join(lines)
            else:
                text_out = "ℹ️ This folder is empty."

            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*📁 NAS browse*"}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"Path: `{payload.get('path', remainder)}`\n{text_out}"},
                },
            ]
            if isinstance(entries, list) and len(entries) > len(shown):
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"showing first {len(shown)} of {len(entries)} entries"}
                        ],
                    }
                )
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
            return

        if subcommand == "free":
            try:
                from nas import get_nas_storage_health

                summary = await get_nas_storage_health()
            except Exception as exc:  # noqa: BLE001
                summary = f"❌ NAS utilization query failed: {exc}"

            if summary and not summary.startswith("❌"):
                slack_summary = summary.replace("**", "*")
                text_out = "✅ NAS resource summary loaded."
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*🧠 NAS resources*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": slack_summary}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            try:
                status, payload = await _openclaw_local_json("GET", "/api/nas/disk", timeout_s=20)
            except Exception as exc:  # noqa: BLE001
                text_out = f"❌ NAS resource query failed and disk fallback was unavailable: {exc}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*🧠 NAS resources*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            if status != 200 or not isinstance(payload, dict):
                detail = payload if isinstance(payload, str) else json.dumps(payload)[:300]
                text_out = f"❌ NAS resource query failed and disk fallback returned HTTP {status}: {detail}"
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*🧠 NAS resources*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": text_out}},
                ]
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
                return

            text_out, blocks = _disk_blocks(
                payload, heading="*🧠 NAS resources*\n⚠️ DSM resource data unavailable — showing disk usage instead."
            )
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, blocks=blocks)
            return

        if subcommand == "containers":
            import asyncio as _asyncio
            import os as _os

            nas_host = _os.environ.get("NAS_HOST", "192.168.1.8")
            nas_port = _os.environ.get("NAS_SSH_PORT", "24")
            nas_user = _os.environ.get("NAS_SSH_USER", "dave")
            nas_cmd = "/usr/local/bin/docker ps --format '{{.Names}}|{{.Status}}|{{.Image}}' --no-trunc=false"
            try:
                proc = await _asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "BatchMode=yes",
                    "-p",
                    nas_port,
                    f"{nas_user}@{nas_host}",
                    nas_cmd,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=15)
                raw = stdout.decode().strip()
            except Exception as exc:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ SSH error: {exc}")
                return

            if not raw:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="⚠️ No containers running on NAS."
                )
                return

            lines = []
            healthy_count = unhealthy_count = 0
            for ln in raw.splitlines():
                parts = ln.split("|", 2)
                name = parts[0] if parts else "?"
                status = parts[1] if len(parts) > 1 else "?"
                if "unhealthy" in status.lower() or "exited" in status.lower():
                    icon = "🔴"
                    unhealthy_count += 1
                elif "healthy" in status.lower():
                    icon = "🟢"
                    healthy_count += 1
                else:
                    icon = "🟡"
                    healthy_count += 1
                # Shorten status
                short_status = status.split("(")[0].strip()
                lines.append(f"  {icon} `{name}` — {short_status}")

            header = f"🐳 *NAS Containers* ({len(lines)} total — {healthy_count} healthy"
            if unhealthy_count:
                header += f", {unhealthy_count} 🔴 unhealthy"
            header += ")"
            text_out = header + "\n" + "\n".join(lines)
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, mrkdwn=True)
            return

        if subcommand == "update":
            import asyncio as _asyncio
            import os as _os

            from audit import audit_log as _audit_log

            ok, msg = _copilot_owner_check(user_id, command_name="/nas update")
            if not ok:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 Admin only.")
                return
            container_name = remainder.strip().split()[0] if remainder.strip() else ""
            if not container_name:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Usage: `/nas update <container>`\nExample: `/nas update grafana`",
                )
                return
            nas_host = _os.environ.get("NAS_HOST", "192.168.1.8")
            nas_port = _os.environ.get("NAS_SSH_PORT", "24")
            nas_user_env = _os.environ.get("NAS_SSH_USER", "dave")
            # Get current image so we can pull and show before/after
            inspect_cmd = f"/usr/local/bin/docker inspect --format '{{{{.Config.Image}}}}' {container_name} 2>/dev/null"
            try:
                proc = await _asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "BatchMode=yes",
                    "-p",
                    nas_port,
                    f"{nas_user_env}@{nas_host}",
                    inspect_cmd,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=10)
                image_name = stdout.decode().strip()
            except Exception as exc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Could not inspect container: {exc}"
                )
                return
            if not image_name:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Container `{container_name}` not found on NAS."
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text=f"⏳ Pulling `{image_name}` for `{container_name}`…"
            )
            # Pull + restart
            update_cmd = (
                f"/usr/local/bin/docker pull {image_name} 2>&1 | tail -3; "
                f"echo '---RESTART---'; "
                f"/usr/local/bin/docker restart {container_name} 2>&1"
            )
            try:
                proc2 = await _asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "BatchMode=yes",
                    "-p",
                    nas_port,
                    f"{nas_user_env}@{nas_host}",
                    update_cmd,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                stdout2, _ = await _asyncio.wait_for(proc2.communicate(), timeout=120)
                output = stdout2.decode().strip()
                pull_out = output.split("---RESTART---")[0].strip() if "---RESTART---" in output else output
                updated = "Status: Image is up to date" not in pull_out
                status_line = "🆕 New image pulled" if updated else "✅ Already up to date"
                _audit_log(
                    user_id,
                    "nas_container_update",
                    detail=f"container={container_name} image={image_name} updated={updated}",
                    result="success",
                    severity="INFO",
                )
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"✅ *`{container_name}` updated*\n{status_line} — `{image_name}`\n```{pull_out}```",
                )
            except Exception as exc:
                _audit_log(
                    user_id,
                    "nas_container_update",
                    detail=f"container={container_name} image={image_name}",
                    result=f"error:{exc}",
                    severity="WARNING",
                )
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Update failed: {exc}")
            return

        if subcommand == "exec":
            import asyncio as _asyncio
            import os as _os

            from audit import audit_log as _audit_log

            # Admin-only
            ok, msg = _copilot_owner_check(user_id, command_name="/nas exec")
            if not ok:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 Admin only.")
                return
            # /nas exec <container> <cmd...>
            exec_parts = remainder.strip().split(None, 1) if remainder.strip() else []
            exec_container = exec_parts[0] if exec_parts else ""
            exec_cmd = exec_parts[1] if len(exec_parts) > 1 else ""
            if not exec_container or not exec_cmd:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Usage: `/nas exec <container> <command>`\nExample: `/nas exec grafana grafana-cli version`",
                )
                return
            # Blocklist — prevent destructive commands
            _BLOCKED = {
                "rm",
                "kill",
                "dd",
                "mkfs",
                "shutdown",
                "reboot",
                "truncate",
                "shred",
                "wipefs",
                "fdisk",
                "parted",
                "chmod",
                "chown",
                "passwd",
                "userdel",
                "useradd",
            }
            _first_token = exec_cmd.strip().split()[0].lstrip("/").lower()
            if _first_token in _BLOCKED:
                _audit_log(
                    user_id,
                    "nas_container_exec_blocked",
                    detail=f"container={exec_container} cmd={exec_cmd}",
                    result="blocked",
                    severity="WARNING",
                )
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"🛑 Command `{_first_token}` is blocked. `/nas exec` only allows read/inspect commands.",
                )
                return
            nas_host = _os.environ.get("NAS_HOST", "192.168.1.8")
            nas_port = _os.environ.get("NAS_SSH_PORT", "24")
            nas_user_env = _os.environ.get("NAS_SSH_USER", "dave")
            nas_cmd = f"/usr/local/bin/docker exec {exec_container} {exec_cmd}"
            _audit_log(
                user_id, "nas_container_exec", detail=f"container={exec_container} cmd={exec_cmd}", severity="INFO"
            )
            try:
                proc = await _asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "BatchMode=yes",
                    "-p",
                    nas_port,
                    f"{nas_user_env}@{nas_host}",
                    nas_cmd,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30)
                output = (stdout.decode() + stderr.decode()).strip()
            except Exception as exc:
                _audit_log(
                    user_id,
                    "nas_container_exec",
                    detail=f"container={exec_container} cmd={exec_cmd}",
                    result=f"error:{exc}",
                    severity="WARNING",
                )
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ SSH error: {exc}")
                return
            if len(output) > 2800:
                output = "…(truncated)\n" + output[-2800:]
            result_text = f"🖥️ *`{exec_container}` — `{exec_cmd}`*\n```{output or '(no output)'}```"
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=result_text, mrkdwn=True)
            return

        if subcommand == "restart":
            container_name = remainder.strip().split()[0] if remainder.strip() else ""
            if not container_name:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Usage: `/nas restart <container>`\nExample: `/nas restart grafana`",
                )
                return
            # Send confirmation prompt with danger button
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⚠️ Restart NAS container *`{container_name}`*?\nThis will briefly interrupt the service.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔄 Restart"},
                            "style": "danger",
                            "action_id": "nas_restart_confirm",
                            "value": container_name,
                            "confirm": {
                                "title": {"type": "plain_text", "text": "Confirm restart"},
                                "text": {"type": "mrkdwn", "text": f"Restart `{container_name}` on the NAS?"},
                                "confirm": {"type": "plain_text", "text": "Yes, restart"},
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Cancel"},
                            "action_id": "nas_restart_cancel",
                            "value": container_name,
                        },
                    ],
                },
            ]
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text=f"Restart `{container_name}`?", blocks=blocks
            )
            return

        if subcommand == "logs":
            import asyncio as _asyncio
            import os as _os

            nas_host = _os.environ.get("NAS_HOST", "192.168.1.8")
            nas_port = _os.environ.get("NAS_SSH_PORT", "24")
            nas_user = _os.environ.get("NAS_SSH_USER", "dave")
            # Syntax: /nas logs <container> [lines]
            log_parts = remainder.split() if remainder else []
            container_name = log_parts[0] if log_parts else ""
            try:
                lines_n = int(log_parts[1]) if len(log_parts) > 1 else 50
                lines_n = min(max(lines_n, 5), 200)  # clamp 5-200
            except (ValueError, IndexError):
                lines_n = 50
            if not container_name:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Usage: `/nas logs <container> [lines]`\nExample: `/nas logs grafana 100`",
                )
                return
            nas_cmd = f"/usr/local/bin/docker logs --tail {lines_n} {container_name} 2>&1"
            try:
                proc = await _asyncio.create_subprocess_exec(
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "BatchMode=yes",
                    "-p",
                    nas_port,
                    f"{nas_user}@{nas_host}",
                    nas_cmd,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=20)
                log_output = stdout.decode().strip()
            except Exception as exc:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ SSH error: {exc}")
                return
            if not log_output:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"ℹ️ No log output for `{container_name}`."
                )
                return
            # Truncate to Slack's 3000 char block limit
            if len(log_output) > 2800:
                log_output = "…(truncated)\n" + log_output[-2800:]
            text_out = f"📋 *Logs: `{container_name}`* (last {lines_n} lines)\n```{log_output}```"
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text_out, mrkdwn=True)
            return

        usage_text, usage_blocks = _usage_blocks()
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text=usage_text, blocks=usage_blocks)

    # ------------------------------------------------------------------
    # Phase 5 — /host quick-action shortcuts
    # Wraps vetted Copilot prompts ("show docker ps", "tail logs", "diagnose
    # plex") in one-word subcommands so phone users can dispatch the most
    # common operations without typing a full prompt. Each shortcut routes
    # through the same Phase 3 session machinery as /copilot.
    # ------------------------------------------------------------------

    @app.command("/host")
    async def handle_slash_host(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()

        ok, msg = _copilot_owner_check(user_id)
        if not ok:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg or "🛑 forbidden")
            return

        try:
            from host_bridge_shortcuts import ResolvedShortcut, resolve
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ host_bridge_shortcuts module unavailable",
            )
            return

        result = resolve(text)
        if not isinstance(result, ResolvedShortcut):
            # ShortcutError — message is already user-safe (help or error).
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=result.message)
            return

        if os.getenv("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() != "true":
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="🛑 Host bridge disabled. Set `OPENCLAW_HOST_BRIDGE_ENABLED=true` and redeploy.",
            )
            return

        mgr = await _ensure_session_manager()
        if mgr is None:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ host_bridge module unavailable",
            )
            return

        try:
            parent = await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"🤖 <@{user_id}> ran `/host {result.raw_text}`\n"
                    f"_shortcut:_ `{result.name}` — reply in this thread to continue"
                ),
            )
            thread_ts = parent.get("ts") or parent.get("message", {}).get("ts") or ""
        except Exception as exc:  # noqa: BLE001
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ failed to open thread: `{exc}`",
            )
            return

        async def _bg() -> None:
            try:
                record, err = await mgr.start_session(
                    slack_user=user_id,
                    slack_channel=channel_id,
                    slack_thread_ts=thread_ts,
                    initial_prompt=result.prompt,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("/host start_session crashed: %s", exc)
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ session failed to start: `{exc}`",
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            if err or record is None:
                try:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"❌ {err or 'unknown error'}",
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            try:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        f"✅ session `{record.session_id}` open — "
                        f"`/copilot-end {record.session_id}` to close, "
                        f"`/copilot-cancel {record.session_id}` to Ctrl-C the current turn"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        asyncio.create_task(_bg())

    # ------------------------------------------------------------------
    # /incident — incident-response slash command.
    #
    # Mirrors the Discord IncidentCog (src/cogs/incident_cog.py) by reusing
    # the same shared incident_store + incident_copilot modules. Owner-only:
    # access is gated by OPENCLAW_INCIDENT_ALLOWED_USERS (CSV of Slack user
    # IDs), falling back to SLACK_NOTIFY_USER_ID.
    #
    # Subcommands:
    #   /incident start <title>              — open incident + run Copilot
    #   /incident status <id>                — show incident summary
    #   /incident resolve <id> [postmortem]  — close incident
    #   /incident list                       — recent incidents
    #   /incident timeline <id>              — event timeline
    # ------------------------------------------------------------------

    @app.command("/incident")
    async def handle_slash_incident(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()

        allowed = _incident_allowed_user_ids()
        if not allowed:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "🛑 `/incident` is not configured. Set `OPENCLAW_INCIDENT_ALLOWED_USERS` "
                    "(or `SLACK_NOTIFY_USER_ID`) to a Slack user ID."
                ),
            )
            return
        if user_id not in allowed:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="🛑 You are not allowed to run `/incident` on this workspace.",
            )
            return

        # Lazy imports — these modules pull in LLM/skills layers and we want
        # slack_bot import to stay cheap when /incident isn't used.
        try:
            from incident_copilot import execute_incident_action, generate_incident_report  # noqa: F401
            from incident_workflows import incident_store
        except ImportError as exc:
            log.warning("/incident: incident modules unavailable: %s", exc)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Incident workflow not available: `{exc}`",
            )
            return

        parts = text.split(None, 2)
        sub = (parts[0].lower() if parts else "") or "help"
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        if sub in {"", "help"}:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "🚨 *Incident Copilot commands:*\n"
                    "• `/incident start <title>` — open incident, run Copilot, post actions\n"
                    "• `/incident status <id>` — show incident summary\n"
                    "• `/incident resolve <id> [postmortem...]` — close incident\n"
                    "• `/incident list` — recent incidents\n"
                    "• `/incident timeline <id>` — event timeline"
                ),
            )
            return

        if sub == "list":
            try:
                rows = incident_store.list_recent(limit=10)
            except Exception as exc:  # broad: surface to user
                log.warning("/incident list failed: %s", exc)
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ {exc}")
                return
            if not rows:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text="📭 No incidents recorded yet.")
                return
            lines = ["🚨 *Recent incidents:*"]
            for inc in rows:
                lines.append(
                    f"• #{inc.get('id')} [{inc.get('status', 'open')}] "
                    f"{inc.get('severity', '?')} — {(inc.get('title') or '')[:80]}"
                )
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
            return

        if sub == "status":
            inc_id = _parse_int(arg1)
            if inc_id is None:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text="Usage: `/incident status <id>`")
                return
            inc = incident_store.get_incident(inc_id)
            if not inc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Incident #{inc_id} not found."
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"🚨 *Incident #{inc['id']}* — `{inc.get('status', '?')}` / `{inc.get('severity', '?')}`\n"
                    f"*Title:* {inc.get('title', '')}\n"
                    f"*Description:* {(inc.get('description') or '_(none)_')[:1500]}\n"
                    f"*Created:* {inc.get('created_at', '?')} by {inc.get('created_by_name', '?')}"
                ),
            )
            return

        if sub == "timeline":
            inc_id = _parse_int(arg1)
            if inc_id is None:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident timeline <id>`"
                )
                return
            events = incident_store.get_timeline(inc_id, limit=20)
            if not events:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"📭 No events for incident #{inc_id}."
                )
                return
            lines = [f"🕒 *Timeline for incident #{inc_id}:*"]
            for ev in events:
                lines.append(
                    f"• `{ev.get('created_at', '?')}` *{ev.get('event_type', '?')}* — {(ev.get('note') or '')[:200]}"
                )
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines[:25]))
            return

        if sub == "resolve":
            inc_id = _parse_int(arg1)
            if inc_id is None:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident resolve <id> [postmortem...]`"
                )
                return
            existing = incident_store.get_incident(inc_id)
            if not existing:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Incident #{inc_id} not found."
                )
                return
            try:
                inc = incident_store.resolve_incident(
                    inc_id,
                    summary=str(existing.get("summary") or existing.get("title") or "Resolved via Slack"),
                    action_items=existing.get("action_items") or [],
                    postmortem_notes=(arg2 or "").strip(),
                    actor_id=None,
                    actor_name=user_id,
                )
            except Exception as exc:  # broad: surface to user
                log.warning("/incident resolve failed: %s", exc)
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ {exc}")
                return
            if not inc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Incident #{inc_id} not found."
                )
                return
            _incident_actions_cache.pop(inc_id, None)
            await client.chat_postMessage(
                channel=channel_id,
                text=f"✅ Incident #{inc['id']} resolved by <@{user_id}>.",
            )
            return

        if sub == "start":
            title = (arg1 + (" " + arg2 if arg2 else "")).strip()
            if not title:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident start <title>`"
                )
                return
            await _run_incident_start(client=client, channel_id=channel_id, user_id=user_id, title=title)
            return

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"❓ Unknown subcommand `{sub}`. Try `/incident help`.",
        )

    # ------------------------------------------------------------------
    # /nas-share <path>
    # ------------------------------------------------------------------
    @app.command("/nas-share")
    async def handle_nas_share(ack: Any, command: dict, client: Any) -> None:
        """Generate a Synology share link for a file or folder on the NAS."""
        await ack()
        user_id = command.get("user_id", "")
        channel_id = command.get("channel_id", "")
        path = (command.get("text") or "").strip()

        if not path:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "Usage: `/nas-share <path>`\n"
                    "Example: `/nas-share /Volumes/ROMs/ROMs/Sega - Saturn/shmups.md`\n"
                    "Also accepts NAS FileStation paths: `/nas-share /ROMs/...`"
                ),
            )
            return

        try:
            from nas import nas_create_share_link

            result = await nas_create_share_link(path)
        except Exception as exc:
            log.warning("/nas-share failed: %s", exc)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Error generating share link: {exc}",
            )
            return

        try:
            await client.chat_postMessage(channel=channel_id, text=result, mrkdwn=True)
        except Exception as e:
            log.warning("Slack send failed in /nas-share: %s", e)

    @app.command("/adguard")
    async def handle_adguard(ack: Any, command: dict, client: Any) -> None:
        """Show AdGuard Home DNS stats: queries, block rate, top blocked domains."""
        await ack()
        channel_id = command.get("channel_id", "")
        import base64 as _b64
        import os

        import aiohttp

        adguard_url = os.environ.get("ADGUARD_URL", "https://adguard.davevoyles.synology.me")
        user = os.environ.get("ADGUARD_USER", "")
        password = os.environ.get("ADGUARD_PASSWORD", "")
        creds = _b64.b64encode(f"{user}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{adguard_url}/control/stats", headers=headers, timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    stats = await r.json()
                async with session.get(
                    f"{adguard_url}/control/status", headers=headers, timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    status = await r.json()

            total = stats.get("num_dns_queries", 0)
            blocked = stats.get("num_blocked_filtering", 0)
            safebrowsing = stats.get("num_replaced_safebrowsing", 0)
            safesearch = stats.get("num_replaced_safesearch", 0)
            pct = round(blocked / max(total, 1) * 100, 1)
            avg_ms = round(stats.get("avg_processing_time", 0) * 1000, 2)
            protection = "🟢 On" if status.get("protection_enabled") else "🔴 Off"
            version = status.get("version", "?")

            top_blocked = stats.get("top_blocked_domains", [])[:5]
            top_lines = (
                "\n".join(
                    f"  {i + 1}. `{list(d.keys())[0]}` — {list(d.values())[0]:,}" for i, d in enumerate(top_blocked)
                )
                or "  _(none)_"
            )

            text = (
                f"🛡️ *AdGuard Home* ({version}) — Protection: {protection}\n\n"
                f"*Last 24 Hours*\n"
                f"• DNS queries: *{total:,}*\n"
                f"• Blocked ads/trackers: *{blocked:,}* ({pct}%)\n"
                f"• Safe browsing blocks: *{safebrowsing:,}*\n"
                f"• Safe search enforced: *{safesearch:,}*\n"
                f"• Avg response time: *{avg_ms} ms*\n\n"
                f"*Top Blocked Domains*\n{top_lines}"
            )

        except Exception as exc:
            log.warning("/adguard failed: %s", exc)
            text = f"❌ Could not reach AdGuard Home: {exc}"

        try:
            await client.chat_postMessage(channel=channel_id, text=text, mrkdwn=True)
        except Exception as e:
            log.warning("Slack send failed in /adguard: %s", e)

    @app.command("/grafana")
    async def handle_grafana(ack: Any, command: dict, client: Any) -> None:
        """Show Grafana status, live dashboards, and alert state."""
        await ack()
        import os

        import aiohttp as _aiohttp

        channel_id = command.get("channel_id", "")
        user_id = command.get("user_id", "")
        grafana_url = os.environ.get("GRAFANA_URL", "https://grafana.davevoyles.synology.me")
        api_key = os.environ.get("GRAFANA_API_KEY", "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        sections = []

        # Health check
        try:
            async with _aiohttp.ClientSession() as s:
                async with s.get(f"{grafana_url}/api/health", timeout=_aiohttp.ClientTimeout(total=5)) as r:
                    health = await r.json()
            version = health.get("version", "?")
            db = health.get("database", "?")
            sections.append(f"✅ *Grafana v{version}* — DB: {db}")
        except Exception as exc:
            sections.append(f"❌ Grafana unreachable: {exc}")

        # Live dashboards
        if api_key:
            try:
                async with _aiohttp.ClientSession(headers=headers) as s:
                    # Dashboards
                    async with s.get(
                        f"{grafana_url}/api/search",
                        params={"type": "dash-db", "limit": 10},
                        timeout=_aiohttp.ClientTimeout(total=5),
                    ) as r:
                        dashboards = await r.json()

                    # Active alerts
                    async with s.get(
                        f"{grafana_url}/api/prometheus/grafana/api/v1/alerts",
                        timeout=_aiohttp.ClientTimeout(total=5),
                    ) as r:
                        alert_data = await r.json()

                # Dashboards section
                if dashboards:
                    dash_lines = ["📋 *Dashboards*"]
                    for d in dashboards[:6]:
                        title = d.get("title", "?")
                        url = d.get("url", "")
                        dash_lines.append(f"  • <{grafana_url}{url}|{title}>")
                    sections.append("\n".join(dash_lines))

                # Alerts section
                alerts = alert_data.get("data", {}).get("alerts", [])
                firing = [a for a in alerts if a.get("state") == "firing"]
                if firing:
                    alert_lines = [f"🔴 *Firing Alerts* ({len(firing)})"]
                    for a in firing[:5]:
                        name = a.get("labels", {}).get("alertname", "?")
                        alert_lines.append(f"  • 🚨 {name}")
                    sections.append("\n".join(alert_lines))
                else:
                    sections.append("🟢 *Alerts* — All clear, no firing alerts")

            except Exception as exc:
                log.warning("Grafana API error in /grafana: %s", exc)

        # Quick links footer
        sections.append(
            f"*Quick Links*\n"
            f"• <{grafana_url}|🏠 Home>  "
            f"• <{grafana_url}/d/nas-overview|🖥️ NAS Overview>  "
            f"• <{grafana_url}/alerting/list|🔔 Alerts>  "
            f"• <{grafana_url}/dashboards|📋 All Dashboards>"
        )

        text = "📊 *Grafana*\n\n" + "\n\n".join(sections)
        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text, mrkdwn=True)
        except Exception as e:
            log.warning("Slack send failed in /grafana: %s", e)

    @app.command("/media")
    async def handle_media(ack: Any, command: dict, client: Any) -> None:
        """Show combined Plex/Tautulli: active streams, recent plays, recently added."""
        await ack()
        import os
        from datetime import datetime, timezone

        import aiohttp as _aiohttp

        channel_id = command.get("channel_id", "")
        user_id = command.get("user_id", "")
        tautulli_key = os.environ.get("TAUTULLI_API_KEY", "")
        tautulli_url = os.environ.get("TAUTULLI_URL", "")
        # Use public URL if the configured URL is internal
        if not tautulli_url or tautulli_url.startswith("http://host.") or tautulli_url.startswith("http://localhost"):
            tautulli_url = "https://tautulli.davevoyles.synology.me"

        if not tautulli_key:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⚠️ TAUTULLI_API_KEY not configured.")
            return

        sections = []

        try:
            async with _aiohttp.ClientSession() as s:
                # Active streams
                async with s.get(
                    f"{tautulli_url}/api/v2",
                    params={"apikey": tautulli_key, "cmd": "get_activity"},
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as r:
                    act = (await r.json()).get("response", {}).get("data", {})

                stream_count = act.get("stream_count", 0)
                sessions = act.get("sessions", [])
                if sessions:
                    stream_lines = [f"🎬 *Now Streaming* ({stream_count} active)"]
                    for sess in sessions[:3]:
                        media_type = sess.get("media_type", "")
                        emoji = "📺" if media_type == "episode" else "🎬" if media_type == "movie" else "🎵"
                        title = sess.get("full_title") or sess.get("grandchild_title") or sess.get("title") or "?"
                        user = sess.get("friendly_name") or sess.get("user", "?")
                        pct = sess.get("progress_percent", 0)
                        stream_lines.append(f"  {emoji} {title} — {user} ({pct}%)")
                    sections.append("\n".join(stream_lines))
                else:
                    sections.append("🎬 *Now Streaming:* nothing playing")

                # Recent history
                async with s.get(
                    f"{tautulli_url}/api/v2",
                    params={"apikey": tautulli_key, "cmd": "get_history", "length": "5"},
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as r:
                    hist_data = (await r.json()).get("response", {}).get("data", {}).get("data", [])

                if hist_data:
                    hist_lines = ["*📖 Recently Played*"]
                    for item in hist_data[:5]:
                        title = item.get("full_title") or item.get("title") or "?"
                        user = item.get("friendly_name") or item.get("user", "?")
                        stopped = item.get("stopped", 0)
                        if stopped:
                            dt = datetime.fromtimestamp(stopped, tz=timezone.utc)
                            when = dt.strftime("%-m/%-d %-I:%M%p").lower()
                        else:
                            when = "recently"
                        hist_lines.append(f"  • {title} — {user} @ {when}")
                    sections.append("\n".join(hist_lines))

                # Recently added
                async with s.get(
                    f"{tautulli_url}/api/v2",
                    params={"apikey": tautulli_key, "cmd": "get_recently_added", "count": "5"},
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as r:
                    added_data = (await r.json()).get("response", {}).get("data", {}).get("recently_added", [])

                if added_data:
                    added_lines = ["*✨ Recently Added to Plex*"]
                    for item in added_data[:5]:
                        media_type = item.get("media_type", "")
                        if media_type == "episode":
                            ep_title = item.get("grandchild_title") or item.get("title") or "?"
                            show = item.get("title") or item.get("parent_title") or ""
                            label = f"📺 {show} — {ep_title}" if show and show != ep_title else f"📺 {ep_title}"
                        elif media_type == "movie":
                            label = f"🎬 {item.get('title', '?')}"
                        else:
                            label = f"🎵 {item.get('title', '?')}"
                        added_lines.append(f"  • {label}")
                    sections.append("\n".join(added_lines))

        except Exception as exc:
            log.warning("/media failed: %s", exc)
            sections.append(f"❌ Media data unavailable: {exc}")

        try:
            await client.chat_postMessage(channel=channel_id, text="\n\n".join(sections), mrkdwn=True)
        except Exception as e:
            log.warning("Slack send failed in /media: %s", e)

    @app.command("/qbt")
    async def handle_qbt(ack: Any, command: dict, client: Any) -> None:
        """Show qBittorrent active torrents, speeds, and free space."""
        await ack()
        import os

        import aiohttp as _aiohttp

        channel_id = command.get("channel_id", "")
        user_id = command.get("user_id", "")
        qbt_url = os.environ.get("QBIT_URL", "https://qbittorrent.davevoyles.synology.me")
        qbt_user = os.environ.get("QBIT_USER", "admin")
        qbt_pass = os.environ.get("QBIT_PASSWORD", "")

        if not qbt_pass:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⚠️ QBIT_PASSWORD not configured.")
            return

        try:
            jar = _aiohttp.CookieJar()
            async with _aiohttp.ClientSession(cookie_jar=jar) as s:
                # Authenticate
                async with s.post(
                    f"{qbt_url}/api/v2/auth/login",
                    data={"username": qbt_user, "password": qbt_pass},
                    timeout=_aiohttp.ClientTimeout(total=6),
                ) as r:
                    auth_result = await r.text()
                if auth_result.strip() not in ("Ok.", "Ok"):
                    raise ValueError(f"Auth failed: {auth_result}")

                # Get torrent list
                async with s.get(
                    f"{qbt_url}/api/v2/torrents/info",
                    params={"sort": "added_on", "reverse": "true", "limit": "10"},
                    timeout=_aiohttp.ClientTimeout(total=6),
                ) as r:
                    torrents = await r.json()

                # Get transfer stats
                async with s.get(
                    f"{qbt_url}/api/v2/transfer/info",
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as r:
                    xfer = await r.json()

            dl_speed = xfer.get("dl_info_speed", 0) / 1024 / 1024
            up_speed = xfer.get("up_info_speed", 0) / 1024 / 1024
            free_gb = xfer.get("free_space_on_disk", 0) / 1e9

            state_emoji = {
                "downloading": "⬇️",
                "uploading": "⬆️",
                "stalledDL": "⏸",
                "stalledUP": "⏸",
                "pausedDL": "⏹",
                "pausedUP": "⏹",
                "queuedDL": "🕐",
                "queuedUP": "🕐",
                "checkingDL": "🔍",
                "error": "❌",
                "missingFiles": "⚠️",
            }

            all_count = len(torrents)

            lines = [
                f"🌊 *qBittorrent* — ⬇️ {dl_speed:.1f} MB/s  ⬆️ {up_speed:.1f} MB/s  💾 {free_gb:,.0f} GB free\n"
                f"*{all_count} torrent(s)*"
            ]
            for t in torrents[:8]:
                state = t.get("state", "?")
                emoji = state_emoji.get(state, "•")
                name = t.get("name", "?")[:50]
                pct = t.get("progress", 0) * 100
                size_gb = t.get("size", 0) / 1e9
                if pct < 100:
                    lines.append(f"  {emoji} {name} — {pct:.0f}% ({size_gb:.1f} GB)")
                else:
                    lines.append(f"  {emoji} {name} — seeding")
            if all_count > 8:
                lines.append(f"  _...and {all_count - 8} more_")

            text = "\n".join(lines)
        except Exception as exc:
            log.warning("/qbt failed: %s", exc)
            text = f"❌ qBittorrent error: {exc}"

        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text, mrkdwn=True)
        except Exception as e:
            log.warning("Slack send failed in /qbt: %s", e)

    @app.command("/notify")
    async def handle_notify(ack: Any, command: dict, client: Any) -> None:
        """Send a push notification via ntfy.sh. Usage: /notify <message> or /notify high: <message>"""
        await ack()
        channel_id = command.get("channel_id", "")
        user_id = command.get("user_id", "")
        text = (command.get("text") or "").strip()

        if not text:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/notify <message>`\nOptional priority prefix: `high:`, `low:`, `urgent:`\nExample: `/notify high: Server is down`",
            )
            return

        priority = "default"
        for p in ("urgent", "high", "low"):
            if text.lower().startswith(f"{p}:"):
                priority = p
                text = text[len(p) + 1 :].strip()
                break

        import os

        ntfy_url = os.environ.get("NTFY_URL", "")
        ntfy_topic = os.environ.get("NTFY_TOPIC", "")
        if not ntfy_url or not ntfy_topic:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="⚠️ NTFY_URL / NTFY_TOPIC not configured.",
            )
            return

        try:
            await _send_ntfy("📣 OpenClaw", text, priority=priority)
            emoji = {"urgent": "🚨", "high": "🔴", "low": "🔵"}.get(priority, "🔔")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"{emoji} Notification sent: _{text}_",
            )
        except Exception as exc:
            log.warning("/notify failed: %s", exc)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Failed to send notification: {exc}",
            )


def _register_action_handlers(app: Any) -> None:
    """Register all @app.action handlers: file actions, retry/clarify, and Gmail summarize."""

    # ------------------------------------------------------------------
    # Handler: 🔄 NAS container restart (confirm / cancel)
    # ------------------------------------------------------------------

    @app.action("nas_restart_confirm")
    async def handle_nas_restart_confirm(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import asyncio
        import os

        from audit import audit_log

        user_id = (body.get("user") or {}).get("id", "")
        user_name = (body.get("user") or {}).get("username", user_id)
        channel_id = (body.get("channel") or {}).get("id", user_id)
        actions = body.get("actions", [{}])
        container_name = (actions[0] if actions else {}).get("value", "")
        if not container_name:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="⚠️ No container name found.")
            return
        nas_host = os.environ.get("NAS_HOST", "192.168.1.8")
        nas_port = os.environ.get("NAS_SSH_PORT", "24")
        nas_user = os.environ.get("NAS_SSH_USER", "dave")
        nas_cmd = f"/usr/local/bin/docker restart {container_name}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "BatchMode=yes",
                "-p",
                nas_port,
                f"{nas_user}@{nas_host}",
                nas_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = (stdout.decode() + stderr.decode()).strip()
            if proc.returncode == 0:
                audit_log(
                    user_name,
                    "nas_container_restart",
                    detail=f"container={container_name} host={nas_host}",
                    result="success",
                    severity="INFO",
                )
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"✅ `{container_name}` restarted successfully on NAS."
                )
            else:
                audit_log(
                    user_name,
                    "nas_container_restart",
                    detail=f"container={container_name} host={nas_host}",
                    result="failed",
                    severity="WARNING",
                )
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"❌ Restart failed for `{container_name}`:\n```{output[:500]}```",
                )
        except Exception as exc:
            audit_log(
                user_name,
                "nas_container_restart",
                detail=f"container={container_name} host={nas_host}",
                result=f"error: {exc}",
                severity="WARNING",
            )
            await client.chat_postEphemeral(
                channel=channel_id, user=user_id, text=f"❌ SSH error during restart: {exc}"
            )

    @app.action("nas_restart_cancel")
    async def handle_nas_restart_cancel(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "")
        channel_id = (body.get("channel") or {}).get("id", user_id)
        actions = body.get("actions", [{}])
        container_name = (actions[0] if actions else {}).get("value", "")
        await client.chat_postEphemeral(
            channel=channel_id, user=user_id, text=f"↩️ Restart of `{container_name}` cancelled."
        )

    # ------------------------------------------------------------------
    # Handler: 🔀 Compare — first step, store Document A and prompt for B
    # ------------------------------------------------------------------

    @app.action("file_compare_start")
    async def handle_compare_start(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        actions = body.get("actions", [{}])
        file_id = (actions[0] if actions else {}).get("value", "")
        if not file_id:
            await say(text="⚠️ Couldn't identify the file. Please try again.")
            return
        _compare_pending[user_id] = file_id
        file_obj_entry = _file_registry.get(file_id) or {}
        if isinstance(file_obj_entry, dict) and "file_obj" in file_obj_entry:
            file_obj_entry = file_obj_entry["file_obj"]
        filename = (file_obj_entry.get("name") or "the file") if file_obj_entry else "the file"
        await say(text=f"📄 Got *{filename}* as Document A. Now upload or share Document B and I'll compare them.")

    # ------------------------------------------------------------------
    # Handler: 🌍 Translate — language picker + translation dispatch
    # ------------------------------------------------------------------

    @app.action("file_translate")
    async def handle_translate_pick(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        actions = body.get("actions", [{}])
        file_id = (actions[0] if actions else {}).get("value", "")
        channel = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")

        if user_id not in _user_prefs:
            _user_prefs[user_id] = {}
        _user_prefs[user_id]["translate_file_id"] = file_id
        _save_prefs()

        lang_options = [
            {"text": {"type": "plain_text", "text": lang}, "value": lang}
            for lang in [
                "Spanish",
                "French",
                "German",
                "Italian",
                "Portuguese",
                "Japanese",
                "Chinese (Simplified)",
                "Korean",
                "Arabic",
                "Russian",
            ]
        ]
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="Pick a language to translate to:",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "🌍 *Pick a language to translate to:*"},
                    "accessory": {
                        "type": "static_select",
                        "placeholder": {"type": "plain_text", "text": "Select language"},
                        "options": lang_options,
                        "action_id": "translate_lang_selected",
                    },
                }
            ],
        )

    @app.action("translate_lang_selected")
    async def handle_translate_lang_selected(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        actions = body.get("actions", [{}])
        selected_lang = (actions[0] if actions else {}).get("selected_option", {}).get("value", "Spanish")
        channel = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")

        file_id = (_user_prefs.get(user_id) or {}).get("translate_file_id", "")
        if not file_id:
            await say(text="⚠️ Couldn't find the file to translate. Please tap 🌍 Translate again.")
            return

        file_obj_entry = _file_registry.get(file_id) or {}
        if isinstance(file_obj_entry, dict) and "file_obj" in file_obj_entry:
            file_obj = file_obj_entry["file_obj"]
        else:
            file_obj = file_obj_entry or {}

        if user_id not in _user_prefs:
            _user_prefs[user_id] = {}
        _user_prefs[user_id]["translate_lang"] = selected_lang
        _save_prefs()

        thinking_resp = await say(text=f"⏳ Translating to {selected_lang}…")
        thinking_ts = (thinking_resp or {}).get("ts")
        use_simple = _get_user_simple(user_id)

        translate_prompt = (
            f"Please translate this document into {selected_lang}. "
            "Preserve the original formatting and structure as much as possible. "
            "Return only the translated text."
        )
        prompt = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, translate_prompt)

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            simple=use_simple,
            model_pref="gemini",
        )

    # ------------------------------------------------------------------
    # Handler: retry / clarify button actions
    # ------------------------------------------------------------------

    @app.action("retry_last_prompt")
    async def handle_retry_last_prompt(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        prompt_hash: str = (body.get("actions") or [{}])[0].get("value", "")
        user_id: str = (body.get("user") or {}).get("id", "")
        channel: str = (body.get("channel") or {}).get("id", "")

        prompt = _retry_cache.get(prompt_hash)
        if not prompt:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="⚠️ Retry context expired — please send your message again.",
            )
            return

        use_simple = _get_user_simple(user_id)
        thinking_resp = await say(text="⏳ Retrying…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref="auto",
            simple=use_simple,
        )

    @app.action("clarify_file")
    async def handle_clarify_file(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user", {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", user_id)
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="📄 Go ahead and upload your file, then type your question about it!",
        )

    @app.action("clarify_question")
    async def handle_clarify_question(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user", {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", user_id)
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="💬 Of course! What would you like to know? Just type your question.",
        )

    @app.action("clarify_write")
    async def handle_clarify_write(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user", {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", user_id)
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="📝 Happy to help! What are you working on — a letter, email, list, or something else?",
        )

    # ------------------------------------------------------------------
    # Handler: Gmail summarize button action
    # ------------------------------------------------------------------

    @app.action("gmail_summarize")
    async def handle_gmail_summarize(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        action = body.get("actions", [{}])[0]
        message_id = action.get("value", "")
        user_id = body.get("user", {}).get("id", "")
        channel_id = body.get("channel", {}).get("id", user_id)
        if not message_id:
            return
        body_text = await _get_gmail_body(message_id)
        summary = await _ask(
            f"Summarize this email in 3 bullet points:\n\n{body_text}",
            model_name=None,
        )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"📧 *Email summary:*\n{summary}",
        )

    # ------------------------------------------------------------------
    # Handler: 🚨 Incident action — execute a Copilot-suggested action
    #
    # Mirrors the Discord IncidentActionView button flow. Identity-gated by
    # OPENCLAW_INCIDENT_ALLOWED_USERS (or SLACK_NOTIFY_USER_ID). Actions are
    # additionally gated server-side by incident_copilot.SAFE_RESTART_TARGETS.
    # ------------------------------------------------------------------

    @app.action("incident_action_run")
    async def handle_incident_action_run(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        channel_id = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")
        message_ts = (body.get("message") or {}).get("ts") or (body.get("container") or {}).get("message_ts", "")

        allowed = _incident_allowed_user_ids()
        if not allowed or user_id not in allowed:
            await client.chat_postEphemeral(
                channel=channel_id or user_id,
                user=user_id,
                text="🛑 You are not allowed to run incident actions on this workspace.",
            )
            return

        actions_payload = body.get("actions") or [{}]
        raw_value = (actions_payload[0] if actions_payload else {}).get("value", "")
        parsed = _parse_action_button_value(raw_value)
        if parsed is None:
            await client.chat_postEphemeral(
                channel=channel_id or user_id, user=user_id, text="❌ Malformed action button payload."
            )
            return
        incident_id, action_idx = parsed

        cached = _incident_cache_get(incident_id)
        if not cached or action_idx < 0 or action_idx >= len(cached):
            await client.chat_postEphemeral(
                channel=channel_id or user_id,
                user=user_id,
                text=(
                    f"⌛ Incident #{incident_id} actions are no longer cached "
                    f"(TTL {_INCIDENT_CACHE_TTL_S}s). Re-run `/incident start <title>` to refresh."
                ),
            )
            return
        action = cached[action_idx]
        if not action.get("executable"):
            await client.chat_postEphemeral(
                channel=channel_id or user_id, user=user_id, text="ℹ️ That action is recommendation-only."
            )
            return

        try:
            from incident_copilot import execute_incident_action
            from incident_workflows import incident_store
        except ImportError as exc:
            await client.chat_postEphemeral(
                channel=channel_id or user_id, user=user_id, text=f"❌ Incident modules unavailable: `{exc}`"
            )
            return

        title = str(action.get("title", "action"))[:120]
        command = str(action.get("command", ""))
        target = str(action.get("target", ""))

        try:
            incident_store.append_event(
                incident_id,
                event_type="copilot_action_requested",
                note=f"{command}:{target} requested by {user_id} via Slack",
                actor_id=None,
                actor_name=user_id,
            )
        except Exception as exc:  # broad: telemetry must not block execution
            log.warning("incident_action_run: append_event(requested) failed: %s", exc)

        try:
            result = await execute_incident_action(action)
        except Exception as exc:  # broad: surface to user
            log.warning("incident_action_run: execute failed: %s", exc)
            result = f"❌ Action failed: {exc}"

        try:
            incident_store.append_event(
                incident_id,
                event_type="copilot_action_executed",
                note=f"{command}:{target} => {str(result)[:300]}",
                actor_id=None,
                actor_name=user_id,
            )
        except Exception as exc:  # broad
            log.warning("incident_action_run: append_event(executed) failed: %s", exc)

        reply_text = (
            f"🚨 *Incident #{incident_id}* — action *{title}* executed by <@{user_id}>\n```\n{str(result)[:1800]}\n```"
        )
        try:
            if channel_id and message_ts:
                await client.chat_postMessage(channel=channel_id, thread_ts=message_ts, text=reply_text)
            else:
                await client.chat_postMessage(channel=channel_id or user_id, text=reply_text)
        except Exception as exc:  # broad: fall back to ephemeral
            log.warning("incident_action_run: chat_postMessage failed: %s", exc)
            await client.chat_postEphemeral(channel=channel_id or user_id, user=user_id, text=reply_text)


def create_slack_app() -> Any | None:  # type: ignore[return]
    """Build and return a configured AsyncApp, or None if Slack is disabled."""
    if not _slack_is_configured():
        return None

    try:
        from slack_bolt.async_app import AsyncApp
    except ImportError:
        log.error("slack_bolt not installed — run: pip install slack_bolt>=1.18.0")
        return None

    app = AsyncApp(token=SLACK_BOT_TOKEN)
    global _slack_app_client
    _slack_app_client = app.client

    try:
        from llm.providers import set_fallback_notify_hook

        set_fallback_notify_hook(_on_model_fallback)
    except Exception:
        pass

    _register_core_handlers(app)
    _register_file_handlers(app)
    _register_slash_commands(app)
    _register_integration_handlers(app)
    _register_action_handlers(app)

    return app


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def create_slack_handler() -> Any | None:  # type: ignore[return]
    """Return an AsyncSocketModeHandler configured for this app, or None."""
    global _BOT_USER_ID, _BOT_START_TIME

    app = create_slack_app()
    if app is None:
        return None

    try:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
    except ImportError:
        log.error("AsyncSocketModeHandler not available — ensure slack_bolt[async]>=1.18.0 is installed")
        return None

    _BOT_START_TIME = time.monotonic()

    # Resolve the bot's own user ID so thread-history can distinguish bot messages
    try:
        auth = await app.client.auth_test()
        _BOT_USER_ID = auth.get("user_id", "")
        log.info("Slack bot user ID: %s", _BOT_USER_ID)
    except Exception as exc:
        log.warning("Could not resolve Slack bot user ID: %s", exc)

    # Replay any DMs that arrived while the bot was offline or reconnecting.
    asyncio.create_task(_catchup_missed_dms(app.client))

    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    log.info("Slack Socket Mode handler created")

    # Hook into connect_to_new_endpoint (called only on reconnects, not initial connect)
    # to send a DM notification and replay any missed messages after each WS drop.
    _install_ws_reconnect_hook(handler, app.client)

    # Start proactive file-alert loop (works whether bot is started via
    # start_slack_bot() or the main Discord bot's create_slack_handler()).
    if SLACK_NOTIFY_USER_ID:
        asyncio.create_task(_file_alert_loop(app.client))
        log.info("Proactive file-alert loop started (notifying %s)", SLACK_NOTIFY_USER_ID)

    # Make the Slack client available to the upload HTTP handler
    global _slack_client_ref
    _slack_client_ref = app.client

    # Seed digest prefs for notify user so digest is on by default
    if SLACK_NOTIFY_USER_ID:
        _prefs = _load_digest_prefs()
        if SLACK_NOTIFY_USER_ID not in _prefs:
            _prefs[SLACK_NOTIFY_USER_ID] = {"enabled": True, "last_sent": 0}
            _save_digest_prefs(_prefs)
            log.info("Seeded digest prefs for notify user %s", SLACK_NOTIFY_USER_ID)

    # Start digest background loop
    asyncio.create_task(_digest_loop(app.client))
    log.info("Digest loop started")

    async def _morning_briefing() -> str:
        return await _send_morning_briefing(app.client)

    async def _weekly_digest() -> str:
        """Sunday 9am: summarize the week via Hermes and send to Slack+ntfy."""
        import os
        import sqlite3
        import time

        from slack_sdk.web.async_client import AsyncWebClient

        from host_bridge import run_hermes_stream

        db_path = "/Users/davevoyles/.hermes/state.db"
        rows: list[tuple[Any, ...]] = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
            try:
                one_week_ago = time.time() - 7 * 24 * 3600
                rows = conn.execute(
                    "SELECT title, message_count, started_at FROM sessions WHERE started_at > ? ORDER BY started_at DESC",
                    (one_week_ago,),
                ).fetchall()
            finally:
                conn.close()
            session_summaries = (
                "\n".join([f"- {r[0] or 'Untitled'} ({r[1]} messages)" for r in rows])
                if rows
                else "No sessions this week"
            )
        except Exception as e:
            session_summaries = f"(Could not read sessions: {e})"

        context = f"""Here is my week's activity summary:

Hermes sessions ({len(rows)}):
{session_summaries}

Please write a brief, friendly weekly recap for me. Keep it under 10 bullet points. Be conversational and highlight anything interesting. Start with a one-sentence summary, then bullet points."""

        try:
            result_parts: list[str] = []
            async for chunk in run_hermes_stream(prompt=context, slack_user_id="weekly-digest", hermes_session_id=None):
                if isinstance(chunk, dict) and chunk.get("type") == "chunk":
                    result_parts.append(chunk.get("text", ""))
                elif isinstance(chunk, dict) and chunk.get("type") == "error":
                    raise RuntimeError(chunk.get("error", "Unknown Hermes error"))
                elif isinstance(chunk, dict) and chunk.get("type") == "done":
                    break
            digest = "".join(result_parts).strip()
            if not digest:
                digest = f"Weekly summary: {len(rows)} Hermes sessions this week.\n\n{session_summaries}"
        except Exception as e:
            digest = f"Weekly digest failed: {e}\n\nSessions this week:\n{session_summaries}"

        slack_msg = f"📊 *OpenClaw Weekly Digest*\n\n{digest}"

        notify_user_id = os.environ.get("SLACK_NOTIFY_USER_ID", "")
        if notify_user_id:
            try:
                web_client = AsyncWebClient(token=os.environ.get("SLACK_BOT_TOKEN", ""))
                dm = await web_client.conversations_open(users=notify_user_id)
                dm_id = dm["channel"]["id"]
                await web_client.chat_postMessage(channel=dm_id, text=slack_msg)
            except Exception as e:
                log.warning("Weekly digest Slack send failed: %s", e)

        await _send_ntfy(
            "📊 Weekly Digest Ready",
            digest[:200] + "..." if len(digest) > 200 else digest,
        )
        return "Weekly digest sent"

    async def _hermes_nightly_upgrade() -> str:
        try:
            from host_bridge import HERMES_BIN, run_shell
            from host_bridge import _enabled as _host_bridge_enabled

            if not _host_bridge_enabled():
                return "Host bridge disabled"

            old_result = await run_shell(command=f"{HERMES_BIN} --version", slack_user_id="scheduler", timeout_s=10)
            old_stdout = old_result if isinstance(old_result, str) else getattr(old_result, "stdout", "")
            old_ver = old_stdout.strip().splitlines()[0] if old_stdout else ""

            upg_result = await run_shell(
                command=f"{HERMES_BIN} --version && uv tool upgrade hermes-agent",
                slack_user_id="scheduler",
                timeout_s=120,
            )
            upgrade_stdout = upg_result if isinstance(upg_result, str) else getattr(upg_result, "stdout", "") or ""
            new_result = await run_shell(command=f"{HERMES_BIN} --version", slack_user_id="scheduler", timeout_s=10)
            new_stdout = new_result if isinstance(new_result, str) else getattr(new_result, "stdout", "")
            new_ver = new_stdout.strip().splitlines()[0] if new_stdout else ""

            if new_ver and old_ver and new_ver != old_ver:
                msg = f"⚕ Hermes updated: {old_ver} → {new_ver}"
                if SLACK_NOTIFY_USER_ID:
                    try:
                        await app.client.chat_postMessage(channel=SLACK_NOTIFY_USER_ID, text=msg)
                    except Exception:
                        pass
                return msg

            return upgrade_stdout.strip() or (new_ver or old_ver or "Hermes already current")
        except Exception as exc:
            log.warning("_hermes_nightly_upgrade failed: %s", exc)
            return f"Hermes nightly upgrade failed: {exc}"

    try:
        from scheduler import scheduler

        scheduler.register_skills(
            {
                "morning_briefing": _morning_briefing,
                "weekly_digest": _weekly_digest,
                "hermes_nightly_upgrade": _hermes_nightly_upgrade,
            }
        )

        briefing_hour = int(os.getenv("MORNING_BRIEFING_HOUR", "8"))
        morning_task = next((task for task in scheduler.list_tasks() if task.action == "morning_briefing"), None)
        if morning_task is None:
            scheduler.create(
                action="morning_briefing",
                args={},
                hour=briefing_hour,
                minute=0,
                created_by="system",
                alert_only=False,
            )
            log.info("Registered morning briefing task for %02d:00 UTC", briefing_hour)
        elif morning_task.cron_hour != briefing_hour or morning_task.cron_minute != 0:
            scheduler.update(morning_task.task_id, cron_hour=briefing_hour, cron_minute=0, enabled=True)
            log.info("Updated morning briefing task to %02d:00 UTC", briefing_hour)

        weekly_task = next((task for task in scheduler.list_tasks() if task.action == "weekly_digest"), None)
        if weekly_task is None:
            scheduler.create(
                action="weekly_digest",
                args={},
                cron_expression="0 9 * * 0",
                created_by="system",
                alert_only=False,
            )
            log.info("Registered weekly_digest task for Sundays at 09:00 UTC")
        elif weekly_task.cron_expression != "0 9 * * 0":
            scheduler.update(weekly_task.task_id, cron_expression="0 9 * * 0", enabled=True)
            log.info("Updated weekly_digest task to Sundays at 09:00 UTC")

        if not any(task.action == "hermes_nightly_upgrade" for task in scheduler.list_tasks()):
            scheduler.create(
                action="hermes_nightly_upgrade",
                args={},
                hour=3,
                minute=0,
                created_by="system",
                alert_only=False,
            )
            log.info("Registered nightly Hermes upgrade task for 03:00 UTC")
        scheduler.start()
    except Exception as exc:
        log.warning("Failed to initialize scheduled Slack tasks: %s", exc)

    # Start Dropbox watch loop (no-op when DROPBOX_ACCESS_TOKEN not set)
    try:
        from dropbox_sync import DROPBOX_CONFIGURED, dropbox_watch_loop

        if DROPBOX_CONFIGURED and SLACK_NOTIFY_USER_ID:
            asyncio.create_task(dropbox_watch_loop(app.client, SLACK_NOTIFY_USER_ID))
            log.info("Dropbox watch loop started")
    except ImportError:
        pass

    return handler


async def start_slack_bot() -> None:
    """Create the Socket Mode handler and run until the process exits."""
    # Start the HTTP health server first so docker healthchecks pass even if
    # Slack auth/socket setup takes a moment.
    await _start_health_server()

    handler = await create_slack_handler()
    if handler is None:
        log.warning("Slack bot not started (disabled or misconfigured)")
        # Keep the process alive so the health server stays up and the
        # container doesn't crash-loop. Useful when Slack creds are missing.
        await asyncio.Event().wait()
        return

    log.info("Starting Slack Socket Mode bot…")
    await handler.start_async()


# ---------------------------------------------------------------------------
# Health server (port 8765) — replaces discord_web.start_health_server
# ---------------------------------------------------------------------------

_HEALTH_RUNNER: Any = None


def _read_git_sha() -> str:
    """Return short HEAD SHA from src/_git_sha.txt or 'unknown'."""
    sha_file = Path(__file__).parent / "_git_sha.txt"
    if sha_file.exists():
        try:
            return sha_file.read_text().strip() or "unknown"
        except OSError:
            pass
    return "unknown"


async def _start_health_server() -> None:
    """Start a minimal aiohttp server on :8765 exposing /health.

    Slack-only replacement for the previous Discord-coupled health server.
    Contract: `/health` returns JSON with `status`, `uptime_seconds`, `git_sha`,
    matching what docker-compose healthcheck + Uptime Kuma probe expect.
    """
    global _HEALTH_RUNNER, _BOT_START_TIME

    if _BOT_START_TIME == 0.0:
        _BOT_START_TIME = time.monotonic()

    from aiohttp import web

    async def health(_req: "web.Request") -> "web.Response":
        uptime_s = time.monotonic() - _BOT_START_TIME
        checks: dict[str, str] = {}
        try:
            import sqlite3

            from thread_store import DB_PATH as _db_path

            conn = sqlite3.connect(str(_db_path), timeout=2)
            conn.execute("SELECT 1")
            conn.close()
            checks["db"] = "ok"
        except Exception:
            checks["db"] = "error"

        overall = "healthy" if checks.get("db") == "ok" else "degraded"
        return web.json_response(
            {
                "status": overall,
                "uptime_seconds": round(uptime_s, 1),
                "interface": "slack",
                "git_sha": _read_git_sha(),
                "checks": checks,
                "ts": time.time(),
            }
        )

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", lambda _req: web.HTTPFound("/dashboard"))

    try:
        from dashboard.auth import require_action_auth, require_session
        from dashboard.routes import setup_dashboard

        setup_dashboard(app, require_action_auth=require_action_auth, require_session=require_session)
        log.info("Dashboard routes registered at /dashboard")
    except Exception as _exc:
        log.warning("Dashboard unavailable: %s", _exc)

    port = int(os.getenv("HEALTH_PORT", "8765"))

    # Suppress INFO log noise from high-frequency polling endpoints.
    class _QuietAccessLogger(aiohttp.abc.AbstractAccessLogger):
        _QUIET_PATHS = frozenset(
            {
                "/health",
                "/",
                "/api/agent/sessions",
                "/api/copilot/sessions",
                "/api/hermes/status",
                "/api/hermes/memory-seed",
                "/api/hermes/skills-seed",
                "/install-hermes",
            }
        )

        def log(self, request: Any, response: Any, time: float) -> None:
            if response.status == 200 and request.path in self._QUIET_PATHS:
                logging.getLogger("aiohttp.access").debug(
                    '%s "%s %s" %d', request.remote, request.method, request.path, response.status
                )
            else:
                logging.getLogger("aiohttp.access").info(
                    '%s [%s] "%s %s HTTP/%s" %d',
                    request.remote,
                    request.headers.get("Date", "-"),
                    request.method,
                    request.path,
                    f"{request.version.major}.{request.version.minor}",
                    response.status,
                )

    runner = web.AppRunner(app, access_log_class=_QuietAccessLogger)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    _HEALTH_RUNNER = runner
    log.info("Health server listening on :%d/health", port)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(start_slack_bot())
