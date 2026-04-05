"""Tests for sms_provider.py Twilio abstraction and config wiring."""

from types import SimpleNamespace

import pytest

import sms_provider as mod


def _base_cfg(**overrides):
    data = {
        "sms_provider": "twilio",
        "twilio_enabled": True,
        "twilio_account_sid": "AC123",
        "twilio_auth_token": "auth-token",
        "twilio_from_number": "+15551234567",
        "twilio_messaging_service_sid": "",
        "twilio_verify_service_sid": "VA123",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(sid="SM123", status="queued", to=kwargs.get("to"))


class _FakeVerifications:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(sid="VE123", status="pending", to=kwargs.get("to"))


class _FakeVerificationChecks:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(sid="VEC123", status="approved", to=kwargs.get("to"))


class _FakeService:
    def __init__(self):
        self.verifications = _FakeVerifications()
        self.verification_checks = _FakeVerificationChecks()


class _FakeServices:
    def __init__(self):
        self.calls = []
        self._service = _FakeService()

    def __call__(self, service_sid):
        self.calls.append(service_sid)
        return self._service


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()
        self._services = _FakeServices()
        self.verify = SimpleNamespace(v2=SimpleNamespace(services=self._services))


class TestTwilioConfig:
    def test_missing_credentials_raise_explicit_error(self):
        cfg = _base_cfg(twilio_account_sid="", twilio_auth_token="")
        with pytest.raises(mod.SMSProviderConfigError, match="TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN"):
            mod.TwilioSMSConfig.from_config(cfg)

    def test_missing_sender_config_raises_error(self):
        cfg = _base_cfg(twilio_from_number="", twilio_messaging_service_sid="")
        with pytest.raises(mod.SMSProviderConfigError, match="TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID"):
            mod.TwilioSMSConfig.from_config(cfg)

    def test_disabled_twilio_raises_error(self):
        cfg = _base_cfg(twilio_enabled=False)
        with pytest.raises(mod.SMSProviderConfigError, match="TWILIO_ENABLED=true"):
            mod.build_sms_provider(config=cfg)


class TestTwilioProvider:
    async def test_send_sms_uses_from_number_when_no_messaging_service(self):
        cfg = mod.TwilioSMSConfig.from_config(_base_cfg())
        fake_client = _FakeClient()
        provider = mod.TwilioSMSProvider(cfg, client_factory=lambda *_: fake_client)

        result = await provider.send_sms(to="+15557654321", body="hello world")

        assert result.sid == "SM123"
        assert fake_client.messages.calls[0]["from_"] == "+15551234567"
        assert "messaging_service_sid" not in fake_client.messages.calls[0]

    async def test_send_sms_uses_messaging_service_sid_when_set(self):
        cfg = mod.TwilioSMSConfig.from_config(
            _base_cfg(twilio_from_number="", twilio_messaging_service_sid="MG123")
        )
        fake_client = _FakeClient()
        provider = mod.TwilioSMSProvider(cfg, client_factory=lambda *_: fake_client)

        await provider.send_sms(to="+15557654321", body="hello world")

        assert fake_client.messages.calls[0]["messaging_service_sid"] == "MG123"
        assert "from_" not in fake_client.messages.calls[0]

    async def test_start_verification_calls_twilio_verify_api(self):
        cfg = mod.TwilioSMSConfig.from_config(_base_cfg(twilio_verify_service_sid="VA999"))
        fake_client = _FakeClient()
        provider = mod.TwilioSMSProvider(cfg, client_factory=lambda *_: fake_client)

        result = await provider.start_verification(to="+15557654321")

        assert result.sid == "VE123"
        assert fake_client._services.calls == ["VA999"]
        assert fake_client._services._service.verifications.calls[0]["channel"] == "sms"

    async def test_check_verification_calls_twilio_verify_check_api(self):
        cfg = mod.TwilioSMSConfig.from_config(_base_cfg(twilio_verify_service_sid="VA555"))
        fake_client = _FakeClient()
        provider = mod.TwilioSMSProvider(cfg, client_factory=lambda *_: fake_client)

        result = await provider.check_verification(to="+15557654321", code="123456")

        assert result.status == "approved"
        assert fake_client._services.calls == ["VA555"]
        assert fake_client._services._service.verification_checks.calls[0]["code"] == "123456"

    async def test_verification_requires_verify_service_sid(self):
        cfg = mod.TwilioSMSConfig.from_config(_base_cfg(twilio_verify_service_sid=""))
        fake_client = _FakeClient()
        provider = mod.TwilioSMSProvider(cfg, client_factory=lambda *_: fake_client)

        with pytest.raises(mod.SMSVerificationUnavailableError, match="TWILIO_VERIFY_SERVICE_SID"):
            await provider.start_verification(to="+15557654321")
