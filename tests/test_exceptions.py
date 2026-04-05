"""Tests for custom exception types."""

import pytest

from exceptions import (
    APIConnectionError,
    AuthenticationError,
    ConfigurationError,
    InvalidRequestError,
    OpenClawError,
    PermissionError,
    RateLimitError,
    ResourceNotFoundError,
    StorageError,
    TimeoutError,
    ValidationError,
)


class TestOpenClawError:
    def test_base_exception_is_exception(self):
        assert issubclass(OpenClawError, Exception)

    def test_can_raise_and_catch(self):
        with pytest.raises(OpenClawError):
            raise OpenClawError("test error")


class TestConfigurationError:
    def test_inherits_from_openclaw_error(self):
        assert issubclass(ConfigurationError, OpenClawError)

    def test_message_preserved(self):
        err = ConfigurationError("Missing API key")
        assert str(err) == "Missing API key"


class TestAPIConnectionError:
    def test_includes_api_name_and_reason(self):
        err = APIConnectionError("newsapi", "Connection timeout")
        assert err.api_name == "newsapi"
        assert err.reason == "Connection timeout"
        assert "newsapi" in str(err)
        assert "Connection timeout" in str(err)


class TestRateLimitError:
    def test_includes_retry_after(self):
        err = RateLimitError(60)
        assert err.retry_after == 60
        assert "60s" in str(err)

    def test_includes_api_name_when_provided(self):
        err = RateLimitError(30, "github")
        assert err.api_name == "github"
        assert "github" in str(err)


class TestAuthenticationError:
    def test_includes_api_name(self):
        err = AuthenticationError("slack")
        assert err.api_name == "slack"
        assert "slack" in str(err)

    def test_includes_detail_when_provided(self):
        err = AuthenticationError("slack", "Invalid token")
        assert err.detail == "Invalid token"
        assert "Invalid token" in str(err)


class TestInvalidRequestError:
    def test_includes_reason(self):
        err = InvalidRequestError("Body too large")
        assert err.reason == "Body too large"
        assert "Body too large" in str(err)

    def test_includes_parameter_when_provided(self):
        err = InvalidRequestError("Must be positive", "timeout")
        assert err.parameter == "timeout"
        assert "timeout" in str(err)


class TestResourceNotFoundError:
    def test_includes_resource_info(self):
        err = ResourceNotFoundError("Conversation", "conv-123")
        assert err.resource_type == "Conversation"
        assert err.resource_id == "conv-123"
        assert "Conversation" in str(err)
        assert "conv-123" in str(err)


class TestTimeoutError:
    def test_includes_operation_and_duration(self):
        err = TimeoutError("API call", 30)
        assert err.operation == "API call"
        assert err.timeout_seconds == 30
        assert "API call" in str(err)
        assert "30s" in str(err)


class TestPermissionError:
    def test_includes_user_and_operation(self):
        err = PermissionError(12345, "delete_server")
        assert err.user_id == 12345
        assert err.operation == "delete_server"
        assert "12345" in str(err)
        assert "delete_server" in str(err)


class TestStorageError:
    def test_includes_all_context(self):
        err = StorageError("write", "/data/config.json", "Permission denied")
        assert err.operation == "write"
        assert err.path == "/data/config.json"
        assert err.reason == "Permission denied"
        assert "write" in str(err)
        assert "/data/config.json" in str(err)
        assert "Permission denied" in str(err)


class TestValidationError:
    def test_includes_field_and_reason(self):
        err = ValidationError("email", "Invalid format")
        assert err.field == "email"
        assert err.reason == "Invalid format"
        assert "email" in str(err)
        assert "Invalid format" in str(err)


class TestExceptionHierarchy:
    """Ensure all custom exceptions inherit from OpenClawError."""

    def test_all_inherit_from_base(self):
        exceptions = [
            ConfigurationError,
            APIConnectionError,
            RateLimitError,
            AuthenticationError,
            InvalidRequestError,
            ResourceNotFoundError,
            TimeoutError,
            PermissionError,
            StorageError,
            ValidationError,
        ]
        for exc_class in exceptions:
            assert issubclass(exc_class, OpenClawError)

    def test_can_catch_all_with_base(self):
        """Verify we can catch any custom exception with OpenClawError."""
        with pytest.raises(OpenClawError):
            raise RateLimitError(60, "test")

        with pytest.raises(OpenClawError):
            raise APIConnectionError("test", "failed")

        with pytest.raises(OpenClawError):
            raise ValidationError("field", "bad")
