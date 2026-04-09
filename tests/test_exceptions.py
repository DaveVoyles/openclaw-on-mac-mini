"""Tests for the OpenClaw custom exception hierarchy."""

import pytest

from exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    ConfigError,
    ConfigurationError,
    DatabaseError,
    InvalidRequestError,
    OpenClawError,
    PermissionError,
    RateLimitError,
    ResourceNotFoundError,
    StorageError,
    TimeoutError,
    ToolError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Importability
# ---------------------------------------------------------------------------

class TestImportability:
    def test_all_exceptions_importable(self):
        for cls in (
            OpenClawError,
            APIError,
            RateLimitError,
            TimeoutError,
            ToolError,
            ConfigError,
            ConfigurationError,
            DatabaseError,
            PermissionError,
            APIConnectionError,
            AuthenticationError,
            InvalidRequestError,
            ResourceNotFoundError,
            StorageError,
            ValidationError,
        ):
            assert cls is not None

    def test_configuration_error_is_alias_for_config_error(self):
        assert ConfigurationError is ConfigError


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------

class TestHierarchy:
    def test_rate_limit_is_api_error(self):
        assert issubclass(RateLimitError, APIError)

    def test_api_error_is_openclaw_error(self):
        assert issubclass(APIError, OpenClawError)

    def test_openclaw_error_is_exception(self):
        assert issubclass(OpenClawError, Exception)

    def test_rate_limit_full_chain(self):
        err = RateLimitError("TestService")
        assert isinstance(err, RateLimitError)
        assert isinstance(err, APIError)
        assert isinstance(err, OpenClawError)
        assert isinstance(err, Exception)

    def test_all_inherit_from_openclaw_error(self):
        for cls in (
            APIError, RateLimitError, TimeoutError, ToolError,
            ConfigError, DatabaseError, PermissionError,
            APIConnectionError, AuthenticationError,
            InvalidRequestError, ResourceNotFoundError,
            StorageError, ValidationError,
        ):
            assert issubclass(cls, OpenClawError), f"{cls.__name__} must inherit from OpenClawError"


# ---------------------------------------------------------------------------
# APIError
# ---------------------------------------------------------------------------

class TestAPIError:
    def test_optional_fields_default_none(self):
        err = APIError("something failed")
        assert err.status_code is None
        assert err.service is None

    def test_fields_set_when_provided(self):
        err = APIError("bad gateway", status_code=502, service="GitHub")
        assert err.status_code == 502
        assert err.service == "GitHub"

    def test_message_preserved(self):
        err = APIError("bad gateway")
        assert str(err) == "bad gateway"


# ---------------------------------------------------------------------------
# RateLimitError
# ---------------------------------------------------------------------------

class TestRateLimitError:
    def test_carries_service_and_status_code(self):
        err = RateLimitError("OpenAI")
        assert err.service == "OpenAI"
        assert err.status_code == 429

    def test_retry_after_default_none(self):
        err = RateLimitError("OpenAI")
        assert err.retry_after is None

    def test_retry_after_set(self):
        err = RateLimitError("OpenAI", retry_after=30.5)
        assert err.retry_after == 30.5

    def test_message_format(self):
        err = RateLimitError("OpenAI")
        assert str(err) == "OpenAI rate limit exceeded"

    def test_catchable_as_api_error(self):
        with pytest.raises(APIError):
            raise RateLimitError("svc")

    def test_catchable_as_openclaw_error(self):
        with pytest.raises(OpenClawError):
            raise RateLimitError("svc")


# ---------------------------------------------------------------------------
# TimeoutError
# ---------------------------------------------------------------------------

class TestTimeoutError:
    def test_message_without_timeout_seconds(self):
        err = TimeoutError("Spotify")
        assert str(err) == "Spotify timed out"

    def test_message_with_timeout_seconds(self):
        err = TimeoutError("Spotify", timeout_seconds=10.0)
        assert str(err) == "Spotify timed out after 10.0s"

    def test_carries_service_and_timeout(self):
        err = TimeoutError("Spotify", timeout_seconds=5.0)
        assert err.service == "Spotify"
        assert err.timeout_seconds == 5.0

    def test_catchable_as_openclaw_error(self):
        with pytest.raises(OpenClawError):
            raise TimeoutError("svc")


# ---------------------------------------------------------------------------
# ToolError
# ---------------------------------------------------------------------------

class TestToolError:
    def test_carries_tool_name_and_reason(self):
        err = ToolError("my_tool", "something went wrong")
        assert err.tool_name == "my_tool"
        assert err.reason == "something went wrong"

    def test_message_format(self):
        err = ToolError("my_tool", "something went wrong")
        assert str(err) == "Tool 'my_tool' failed: something went wrong"

    def test_catchable_as_openclaw_error(self):
        with pytest.raises(OpenClawError):
            raise ToolError("t", "r")


# ---------------------------------------------------------------------------
# PermissionError
# ---------------------------------------------------------------------------

class TestPermissionError:
    def test_carries_user_id_and_action(self):
        err = PermissionError(42, "delete_all")
        assert err.user_id == 42
        assert err.action == "delete_all"

    def test_message_format(self):
        err = PermissionError(42, "delete_all")
        assert str(err) == "User 42 lacks permission for: delete_all"

    def test_catchable_as_openclaw_error(self):
        with pytest.raises(OpenClawError):
            raise PermissionError(1, "action")


# ---------------------------------------------------------------------------
# ConfigError / DatabaseError (simple subclasses)
# ---------------------------------------------------------------------------

class TestConfigError:
    def test_inherits_from_openclaw_error(self):
        assert issubclass(ConfigError, OpenClawError)

    def test_message_preserved(self):
        err = ConfigError("Missing API key")
        assert str(err) == "Missing API key"


class TestDatabaseError:
    def test_inherits_from_openclaw_error(self):
        assert issubclass(DatabaseError, OpenClawError)

    def test_message_preserved(self):
        err = DatabaseError("connection refused")
        assert str(err) == "connection refused"


# ---------------------------------------------------------------------------
# Remaining existing classes
# ---------------------------------------------------------------------------

class TestAPIConnectionError:
    def test_includes_api_name_and_reason(self):
        err = APIConnectionError("newsapi", "Connection timeout")
        assert err.api_name == "newsapi"
        assert err.reason == "Connection timeout"
        assert "newsapi" in str(err)
        assert "Connection timeout" in str(err)

    def test_is_api_error(self):
        assert issubclass(APIConnectionError, APIError)


class TestAuthenticationError:
    def test_includes_api_name(self):
        err = AuthenticationError("slack")
        assert err.api_name == "slack"
        assert "slack" in str(err)

    def test_includes_detail_when_provided(self):
        err = AuthenticationError("slack", "Invalid token")
        assert err.detail == "Invalid token"
        assert "Invalid token" in str(err)

    def test_is_api_error(self):
        assert issubclass(AuthenticationError, APIError)


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


# ---------------------------------------------------------------------------
# Broad-catch regression
# ---------------------------------------------------------------------------

class TestBroadCatch:
    def test_can_catch_all_with_base(self):
        for exc in (
            RateLimitError("svc"),
            APIConnectionError("svc", "err"),
            ValidationError("f", "r"),
            ToolError("t", "r"),
            ConfigError("missing key"),
            DatabaseError("down"),
            PermissionError(1, "act"),
        ):
            with pytest.raises(OpenClawError):
                raise exc
