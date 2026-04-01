"""
Tests for agentmail.py — send_agent_mail.

All HTTP calls are mocked via aioresponses to avoid real network traffic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agentmail as agentmail_module
from agentmail import send_agent_mail

# ---------------------------------------------------------------------------
# Configuration guards
# ---------------------------------------------------------------------------


class TestConfigGuards:
    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error(self):
        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", ""):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", "openclaw"):
                result = await send_agent_mail("user@example.com", "hello", "body")
                assert "❌" in result
                assert "API key" in result

    @pytest.mark.asyncio
    async def test_missing_inbox_returns_error(self):
        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", "test-key"):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", ""):
                result = await send_agent_mail("user@example.com", "hello", "body")
                assert "❌" in result
                assert "inbox" in result.lower()


# ---------------------------------------------------------------------------
# Successful send
# ---------------------------------------------------------------------------


class TestSuccessfulSend:
    @pytest.mark.asyncio
    async def test_success_returns_confirmation(self):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "msg-abc123"})

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(
            return_value=_make_async_context(mock_response)
        )

        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", "real-key"):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", "openclaw"):
                with patch("agentmail._get_session", new=AsyncMock(return_value=mock_session)):
                    result = await send_agent_mail("user@example.com", "Test", "Body")
                    assert "✅" in result
                    assert "msg-abc123" in result

    @pytest.mark.asyncio
    async def test_inbox_without_at_sign_gets_domain_appended(self):
        """Inbox like 'openclaw' should become 'openclaw@agentmail.to' in the URL."""
        captured_urls = []

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "x"})

        class FakePost:
            def __init__(self, url, **kwargs):
                captured_urls.append(url)
                self._resp = mock_response

            async def __aenter__(self):
                return self._resp

            async def __aexit__(self, *args):
                pass

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = FakePost

        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", "key"):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", "openclaw"):
                with patch("agentmail._get_session", new=AsyncMock(return_value=mock_session)):
                    await send_agent_mail("a@b.com", "s", "b")
                    assert any("openclaw%40agentmail.to" in u or "openclaw@agentmail.to" in u
                               for u in captured_urls)


# ---------------------------------------------------------------------------
# Error responses
# ---------------------------------------------------------------------------


class TestErrorResponses:
    @pytest.mark.asyncio
    async def test_http_error_status_returns_failure(self):
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value="Forbidden")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(
            return_value=_make_async_context(mock_response)
        )

        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", "key"):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", "openclaw"):
                with patch("agentmail._get_session", new=AsyncMock(return_value=mock_session)):
                    result = await send_agent_mail("a@b.com", "s", "b")
                    assert "❌" in result
                    assert "403" in result

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        import asyncio

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        class TimeoutPost:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                raise asyncio.TimeoutError()

            async def __aexit__(self, *args):
                pass

        mock_session.post = TimeoutPost

        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", "key"):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", "openclaw"):
                with patch("agentmail._get_session", new=AsyncMock(return_value=mock_session)):
                    result = await send_agent_mail("a@b.com", "s", "b")
                    assert "❌" in result
                    assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_returns_error(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        class RaisingPost:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                raise ConnectionError("network unreachable")

            async def __aexit__(self, *args):
                pass

        mock_session.post = RaisingPost

        with patch.object(agentmail_module, "AGENTMAIL_API_KEY", "key"):
            with patch.object(agentmail_module, "AGENTMAIL_INBOX", "openclaw"):
                with patch("agentmail._get_session", new=AsyncMock(return_value=mock_session)):
                    result = await send_agent_mail("a@b.com", "s", "b")
                    assert "❌" in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_context(response):
    """Wrap a mock response in an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx
