"""
Tests for email_skills.py — IMAP read and SMTP send helpers.

Covers: provider detection, config hints, email validation,
send_email, read_inbox, and credential checks.
All IMAP/SMTP calls are mocked.
"""

import imaplib
import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import email_skills as mod


# ---------------------------------------------------------------------------
# _provider_creds
# ---------------------------------------------------------------------------


class TestProviderCreds:
    def test_gmail_returns_tuple_when_configured(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "a@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "secret")
        result = mod._provider_creds("gmail")
        assert result is not None
        assert result[0] == "a@gmail.com"
        assert result[2] == mod.GMAIL_IMAP_HOST

    def test_gmail_returns_none_when_missing(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "")
        assert mod._provider_creds("gmail") is None

    def test_outlook_returns_tuple_when_configured(self, monkeypatch):
        monkeypatch.setattr(mod, "OUTLOOK_USER", "b@outlook.com")
        monkeypatch.setattr(mod, "OUTLOOK_APP_PASSWORD", "pass")
        result = mod._provider_creds("outlook")
        assert result is not None
        assert result[0] == "b@outlook.com"

    def test_outlook_returns_none_when_missing(self, monkeypatch):
        monkeypatch.setattr(mod, "OUTLOOK_USER", "")
        monkeypatch.setattr(mod, "OUTLOOK_APP_PASSWORD", "")
        assert mod._provider_creds("outlook") is None


# ---------------------------------------------------------------------------
# _config_hint
# ---------------------------------------------------------------------------


class TestConfigHint:
    def test_gmail_hint(self):
        hint = mod._config_hint("gmail")
        assert "GMAIL_USER" in hint
        assert "GMAIL_APP_PASSWORD" in hint

    def test_outlook_hint(self):
        hint = mod._config_hint("outlook")
        assert "OUTLOOK_USER" in hint
        assert "OUTLOOK_APP_PASSWORD" in hint


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


class TestSendEmail:
    async def test_send_missing_creds(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "")
        result = await mod.send_email("x@test.com", "hi", "body", provider="gmail")
        assert "not configured" in result

    async def test_send_invalid_email_rejected(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "me@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "pw")
        result = await mod.send_email("not-an-email", "hi", "body")
        assert "Invalid" in result

    async def test_send_success(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "me@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "pw")
        with patch.object(mod, "_smtp_send"):
            result = await mod.send_email("dest@test.com", "Subject", "Body")
        assert "✅" in result
        assert "dest@test.com" in result

    async def test_send_auth_error(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "me@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "pw")
        with patch.object(
            mod,
            "_smtp_send",
            side_effect=smtplib.SMTPAuthenticationError(535, b"bad creds"),
        ):
            result = await mod.send_email("dest@test.com", "Sub", "Body")
        assert "Authentication failed" in result


# ---------------------------------------------------------------------------
# read_inbox
# ---------------------------------------------------------------------------


class TestReadInbox:
    async def test_read_missing_creds(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "")
        result = await mod.read_inbox("gmail")
        assert "not configured" in result

    async def test_read_empty_inbox(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "me@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "pw")
        with patch.object(mod, "_imap_read_inbox", return_value=[]):
            result = await mod.read_inbox("gmail")
        assert "No messages" in result

    async def test_read_parses_messages(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "me@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "pw")
        fake_msgs = [
            {"from": "alice@test.com", "subject": "Hello!", "date": "2025-01-15 10:00"},
            {"from": "bob@test.com", "subject": "Meeting", "date": "2025-01-14 09:00"},
        ]
        with patch.object(mod, "_imap_read_inbox", return_value=fake_msgs):
            result = await mod.read_inbox("gmail", count=5)
        assert "Hello!" in result
        assert "Meeting" in result
        assert "Gmail Inbox" in result

    async def test_read_auth_failure(self, monkeypatch):
        monkeypatch.setattr(mod, "GMAIL_USER", "me@gmail.com")
        monkeypatch.setattr(mod, "GMAIL_APP_PASSWORD", "pw")
        with patch.object(
            mod,
            "_imap_read_inbox",
            side_effect=imaplib.IMAP4.error("LOGIN failed"),
        ):
            result = await mod.read_inbox("gmail")
        assert "IMAP error" in result
