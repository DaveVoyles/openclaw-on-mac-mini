"""Discord-facing SMS UX helpers (state, verification, and guardrails)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sms_provider import (
    SMSDeliveryResult,
    SMSProviderConfigError,
    SMSProviderError,
    SMSProviderSendError,
    SMSVerificationResult,
    SMSVerificationUnavailableError,
    build_sms_provider,
)

SMS_PREFS_PATH = Path(os.getenv("SMS_PREFS_PATH", "/app/data/sms_prefs.json"))
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
SMS_MAX_BODY = 480
SMS_COOLDOWN_SECONDS = 20
SMS_RATE_WINDOW_SECONDS = 600
SMS_RATE_MAX_SENDS = 5


class SMSUXError(RuntimeError):
    """Raised for user-facing SMS workflow errors."""


@dataclass
class UserSMSPrefs:
    user_id: int
    phone_number: str = ""
    is_verified: bool = False
    verification_sid: str = ""
    verification_status: str = ""
    verification_started_at: float = 0.0
    verified_at: float = 0.0
    last_sent_at: float = 0.0
    send_timestamps: list[float] = field(default_factory=list)
    recent_sends: list[dict[str, str | float]] = field(default_factory=list)


class SMSPrefsStore:
    """Thread-safe JSON-backed per-user SMS preferences."""

    def __init__(self, path: Path | None = None):
        self._path = path or SMS_PREFS_PATH
        self._prefs: dict[int, UserSMSPrefs] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for uid_str, prefs_dict in data.items():
                self._prefs[int(uid_str)] = UserSMSPrefs(**prefs_dict)
        except Exception:
            self._prefs = {}

    async def _save(self) -> None:
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {str(uid): asdict(p) for uid, p in self._prefs.items()}
            self._path.write_text(json.dumps(data, indent=2))

    def get(self, user_id: int) -> UserSMSPrefs:
        if user_id not in self._prefs:
            self._prefs[user_id] = UserSMSPrefs(user_id=user_id)
        return self._prefs[user_id]

    async def update(self, prefs: UserSMSPrefs) -> None:
        self._prefs[prefs.user_id] = prefs
        await self._save()


sms_prefs = SMSPrefsStore()


def normalize_phone_number(phone_number: str) -> str:
    cleaned = phone_number.strip().replace(" ", "")
    if not PHONE_RE.fullmatch(cleaned):
        raise SMSUXError("❌ Invalid phone number. Use E.164 format, e.g. `+15551234567`.")
    return cleaned


def mask_phone_number(phone_number: str) -> str:
    if len(phone_number) < 4:
        return "not set"
    return f"{phone_number[:2]}••••••{phone_number[-2:]}"


def _prune_send_timestamps(prefs: UserSMSPrefs, now: float) -> None:
    prefs.send_timestamps = [ts for ts in prefs.send_timestamps if now - ts < SMS_RATE_WINDOW_SECONDS]


def validate_sms_body(body: str) -> str:
    content = body.strip()
    if not content:
        raise SMSUXError("❌ SMS message cannot be empty.")
    if len(content) > SMS_MAX_BODY:
        raise SMSUXError(f"❌ SMS message too long ({len(content)} chars). Max is {SMS_MAX_BODY}.")
    return content


def _rate_limit_error(prefs: UserSMSPrefs, now: float) -> SMSUXError | None:
    _prune_send_timestamps(prefs, now)
    if prefs.last_sent_at and now - prefs.last_sent_at < SMS_COOLDOWN_SECONDS:
        retry_after = max(1, int(SMS_COOLDOWN_SECONDS - (now - prefs.last_sent_at)))
        return SMSUXError(f"⏳ SMS cooldown active. Try again in {retry_after}s.")
    if len(prefs.send_timestamps) >= SMS_RATE_MAX_SENDS:
        retry_after = max(1, int(SMS_RATE_WINDOW_SECONDS - (now - prefs.send_timestamps[0])))
        return SMSUXError(f"🚦 SMS rate limit reached ({SMS_RATE_MAX_SENDS}/10m). Try again in {retry_after}s.")
    return None


async def configure_sms_phone(user_id: int, phone_number: str) -> UserSMSPrefs:
    normalized = normalize_phone_number(phone_number)
    prefs = sms_prefs.get(user_id)
    changed = prefs.phone_number != normalized
    prefs.phone_number = normalized
    if changed:
        prefs.is_verified = False
        prefs.verification_sid = ""
        prefs.verification_status = ""
        prefs.verification_started_at = 0.0
        prefs.verified_at = 0.0
    await sms_prefs.update(prefs)
    return prefs


async def start_sms_verification(user_id: int) -> SMSVerificationResult:
    prefs = sms_prefs.get(user_id)
    if not prefs.phone_number:
        raise SMSUXError("❌ No phone configured yet. Run `/sms config` first.")
    provider = build_sms_provider()
    result = await provider.start_verification(to=prefs.phone_number)
    prefs.is_verified = False
    prefs.verification_sid = result.sid
    prefs.verification_status = result.status or "pending"
    prefs.verification_started_at = time.time()
    await sms_prefs.update(prefs)
    return result


async def check_sms_verification(user_id: int, code: str) -> tuple[SMSVerificationResult, bool]:
    prefs = sms_prefs.get(user_id)
    if not prefs.phone_number:
        raise SMSUXError("❌ No phone configured yet. Run `/sms config` first.")
    clean_code = code.strip()
    if not clean_code:
        raise SMSUXError("❌ Verification code cannot be empty.")
    provider = build_sms_provider()
    result = await provider.check_verification(to=prefs.phone_number, code=clean_code)
    approved = (result.status or "").lower() in {"approved", "valid"}
    prefs.verification_status = result.status or ""
    prefs.verification_sid = result.sid
    prefs.is_verified = approved
    if approved:
        prefs.verified_at = time.time()
    await sms_prefs.update(prefs)
    return result, approved


async def send_configured_sms(user_id: int, body: str) -> SMSDeliveryResult:
    prefs = sms_prefs.get(user_id)
    if not prefs.phone_number:
        raise SMSUXError("❌ No phone configured. Run `/sms config phone:+15551234567` first.")
    if not prefs.is_verified:
        raise SMSUXError("❌ Phone not verified. Run `/sms test` to start verification, then `/sms test code:<code>`.")

    message = validate_sms_body(body)
    now = time.time()
    rate_error = _rate_limit_error(prefs, now)
    if rate_error:
        raise rate_error

    provider = build_sms_provider()
    result = await provider.send_sms(to=prefs.phone_number, body=message)
    prefs.last_sent_at = now
    prefs.send_timestamps.append(now)
    prefs.recent_sends.append(
        {
            "sent_at": now,
            "to": prefs.phone_number,
            "provider": result.provider,
            "sid": result.sid,
            "status": result.status or "unknown",
            "preview": message[:80],
        }
    )
    prefs.recent_sends = prefs.recent_sends[-25:]
    _prune_send_timestamps(prefs, now)
    await sms_prefs.update(prefs)
    return result


def status_snapshot(user_id: int) -> dict[str, str | bool | int]:
    prefs = sms_prefs.get(user_id)
    now = time.time()
    _prune_send_timestamps(prefs, now)
    remaining = max(0, SMS_RATE_MAX_SENDS - len(prefs.send_timestamps))
    return {
        "phone_number": prefs.phone_number,
        "masked_phone": mask_phone_number(prefs.phone_number) if prefs.phone_number else "not set",
        "is_verified": prefs.is_verified,
        "verification_status": prefs.verification_status or "unknown",
        "remaining_sends": remaining,
    }


def recent_sends_snapshot(user_id: int, limit: int = 10) -> list[dict[str, str | float]]:
    prefs = sms_prefs.get(user_id)
    if not prefs.recent_sends:
        return []
    return list(reversed(prefs.recent_sends))[: max(1, limit)]


def format_sms_error(exc: Exception) -> str:
    if isinstance(exc, SMSUXError):
        return str(exc)
    if isinstance(exc, SMSVerificationUnavailableError):
        return "ℹ️ Verification is unavailable (missing `TWILIO_VERIFY_SERVICE_SID`)."
    if isinstance(exc, SMSProviderConfigError):
        return f"❌ SMS configuration error: {exc}"
    if isinstance(exc, SMSProviderSendError):
        return f"❌ SMS send failed: {exc}"
    if isinstance(exc, SMSProviderError):
        return f"❌ SMS error: {exc}"
    return f"❌ SMS failed: {exc}"
