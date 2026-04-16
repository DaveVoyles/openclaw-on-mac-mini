"""SMS provider abstraction with a Twilio-first implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from config import cfg as _cfg


class SMSProviderError(RuntimeError):
    """Base SMS provider error."""


class SMSProviderConfigError(SMSProviderError):
    """Raised when SMS provider configuration is missing or invalid."""


class SMSProviderSendError(SMSProviderError):
    """Raised when sending SMS fails."""


class SMSVerificationUnavailableError(SMSProviderError):
    """Raised when verification is requested but provider is not configured for it."""


@dataclass(frozen=True)
class SMSDeliveryResult:
    """Normalized SMS send result across providers."""

    provider: str
    sid: str
    status: str | None
    to: str | None


@dataclass(frozen=True)
class SMSVerificationResult:
    """Normalized verification result across providers."""

    provider: str
    sid: str
    status: str | None
    to: str | None


@dataclass(frozen=True)
class TwilioSMSConfig:
    """Twilio SMS credentials/settings loaded from config/env."""

    enabled: bool
    account_sid: str
    auth_token: str
    from_number: str
    messaging_service_sid: str
    verify_service_sid: str

    @classmethod
    def from_config(cls, config: Any = _cfg) -> "TwilioSMSConfig":
        """Build config from OpenClaw config namespace."""
        cfg = cls(
            enabled=bool(getattr(config, "twilio_enabled", False)),
            account_sid=str(getattr(config, "twilio_account_sid", "")).strip(),
            auth_token=str(getattr(config, "twilio_auth_token", "")).strip(),
            from_number=str(getattr(config, "twilio_from_number", "")).strip(),
            messaging_service_sid=str(getattr(config, "twilio_messaging_service_sid", "")).strip(),
            verify_service_sid=str(getattr(config, "twilio_verify_service_sid", "")).strip(),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Raise explicit config errors for missing Twilio settings."""
        if not self.enabled:
            raise SMSProviderConfigError("Twilio SMS disabled. Set TWILIO_ENABLED=true to enable one-tap SMS.")

        missing: list[str] = []
        if not self.account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if missing:
            joined = ", ".join(missing)
            raise SMSProviderConfigError(f"Missing Twilio credentials: {joined}.")

        if not self.from_number and not self.messaging_service_sid:
            raise SMSProviderConfigError(
                "Missing Twilio sender configuration: set TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID."
            )


class SMSProvider(Protocol):
    """Provider contract for outbound SMS and optional verification."""

    provider_name: str

    async def send_sms(self, *, to: str, body: str) -> SMSDeliveryResult:
        """Send an SMS message."""

    async def start_verification(self, *, to: str, channel: str = "sms") -> SMSVerificationResult:
        """Create a verification challenge."""

    async def check_verification(self, *, to: str, code: str) -> SMSVerificationResult:
        """Check a submitted verification code."""


def _default_twilio_client_factory(account_sid: str, auth_token: str) -> Any:
    try:
        from twilio.rest import Client
    except ImportError as exc:  # pragma: no cover - exercised only in environments without dependency
        raise SMSProviderConfigError("Twilio SDK not installed. Run `pip install twilio`.") from exc
    return Client(account_sid, auth_token)


class TwilioSMSProvider:
    """Twilio-backed provider for SMS send + Verify API helpers."""

    provider_name = "twilio"

    def __init__(
        self,
        config: TwilioSMSConfig,
        *,
        client_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.config = config
        factory = client_factory or _default_twilio_client_factory
        self._client = factory(config.account_sid, config.auth_token)

    async def send_sms(self, *, to: str, body: str) -> SMSDeliveryResult:
        payload: dict[str, str] = {"to": to, "body": body}
        if self.config.messaging_service_sid:
            payload["messaging_service_sid"] = self.config.messaging_service_sid
        else:
            payload["from_"] = self.config.from_number

        try:
            message = await asyncio.to_thread(self._client.messages.create, **payload)
        except Exception as exc:  # broad: intentional  # pragma: no cover - depends on Twilio internals
            raise SMSProviderSendError(f"Twilio SMS send failed: {exc}") from exc

        return SMSDeliveryResult(
            provider=self.provider_name,
            sid=getattr(message, "sid", ""),
            status=getattr(message, "status", None),
            to=getattr(message, "to", to),
        )

    async def start_verification(self, *, to: str, channel: str = "sms") -> SMSVerificationResult:
        if not self.config.verify_service_sid:
            raise SMSVerificationUnavailableError(
                "Twilio Verify not configured. Set TWILIO_VERIFY_SERVICE_SID to enable verification flows."
            )

        verification_api = self._client.verify.v2.services(self.config.verify_service_sid)
        verification = await asyncio.to_thread(verification_api.verifications.create, to=to, channel=channel)
        return SMSVerificationResult(
            provider=self.provider_name,
            sid=getattr(verification, "sid", ""),
            status=getattr(verification, "status", None),
            to=getattr(verification, "to", to),
        )

    async def check_verification(self, *, to: str, code: str) -> SMSVerificationResult:
        if not self.config.verify_service_sid:
            raise SMSVerificationUnavailableError(
                "Twilio Verify not configured. Set TWILIO_VERIFY_SERVICE_SID to enable verification flows."
            )

        verification_api = self._client.verify.v2.services(self.config.verify_service_sid)
        check = await asyncio.to_thread(verification_api.verification_checks.create, to=to, code=code)
        return SMSVerificationResult(
            provider=self.provider_name,
            sid=getattr(check, "sid", ""),
            status=getattr(check, "status", None),
            to=getattr(check, "to", to),
        )


def build_sms_provider(provider_name: str | None = None, config: Any = _cfg) -> SMSProvider:
    """Build an SMS provider instance from config/env."""
    selected = (provider_name or getattr(config, "sms_provider", "twilio")).strip().lower() or "twilio"
    if selected != "twilio":
        raise SMSProviderConfigError(f"Unsupported SMS provider '{selected}'. Supported providers: twilio.")
    return TwilioSMSProvider(TwilioSMSConfig.from_config(config))

