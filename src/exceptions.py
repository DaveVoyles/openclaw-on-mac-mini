"""
OpenClaw custom exception hierarchy.

Use these instead of generic Python exceptions for any OpenClaw-specific
error conditions. Hierarchy allows broad (except OpenClawError) or specific
(except RateLimitError) catch blocks.
"""


class OpenClawError(Exception):
    """Root exception for all OpenClaw-specific errors."""


# ---------------------------------------------------------------------------
# API / network errors
# ---------------------------------------------------------------------------

class APIError(OpenClawError):
    """Raised when an external API call fails (non-2xx, parse error, etc.)."""

    def __init__(self, message: str, status_code: int | None = None, service: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.service = service


class RateLimitError(APIError):
    """Raised when an external API returns 429 Too Many Requests."""

    def __init__(self, service: str, retry_after: float | None = None):
        super().__init__(f"{service} rate limit exceeded", status_code=429, service=service)
        self.retry_after = retry_after


class TimeoutError(OpenClawError):
    """Raised when an external API call times out."""

    def __init__(self, service: str, timeout_seconds: float | None = None):
        super().__init__(
            f"{service} timed out" + (f" after {timeout_seconds}s" if timeout_seconds else "")
        )
        self.service = service
        self.timeout_seconds = timeout_seconds


class AuthenticationError(APIError):
    """Raised when authentication fails (missing or invalid API key)."""

    def __init__(self, api_name: str, detail: str = ""):
        self.api_name = api_name
        self.detail = detail
        msg = f"Authentication failed for {api_name}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg, status_code=401, service=api_name)


class APIConnectionError(APIError):
    """Raised when an API connection fails at the network level."""

    def __init__(self, api_name: str, reason: str):
        self.api_name = api_name
        self.reason = reason
        super().__init__(f"API connection failed for {api_name}: {reason}", service=api_name)


# ---------------------------------------------------------------------------
# Tool / skill errors
# ---------------------------------------------------------------------------

class ToolError(OpenClawError):
    """Raised when a Gemini tool/skill execution fails."""

    def __init__(self, tool_name: str, reason: str):
        super().__init__(f"Tool '{tool_name}' failed: {reason}")
        self.tool_name = tool_name
        self.reason = reason


# ---------------------------------------------------------------------------
# Configuration / infrastructure errors
# ---------------------------------------------------------------------------

class ConfigError(OpenClawError):
    """Raised for missing or invalid configuration (API keys, env vars, etc.)."""


# Backward-compatible alias
ConfigurationError = ConfigError


class DatabaseError(OpenClawError):
    """Raised when a database operation fails."""


class StorageError(OpenClawError):
    """Raised when file-system storage operations fail (read/write)."""

    def __init__(self, operation: str, path: str, reason: str):
        self.operation = operation
        self.path = path
        self.reason = reason
        super().__init__(f"Storage {operation} failed for {path}: {reason}")


# ---------------------------------------------------------------------------
# Request / validation errors
# ---------------------------------------------------------------------------

class InvalidRequestError(OpenClawError):
    """Raised when request parameters are invalid."""

    def __init__(self, reason: str, parameter: str = ""):
        self.reason = reason
        self.parameter = parameter
        msg = f"Invalid request: {reason}"
        if parameter:
            msg = f"{msg} (parameter: {parameter})"
        super().__init__(msg)


class ValidationError(OpenClawError):
    """Raised when data validation fails."""

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"Validation failed for {field}: {reason}")


class ResourceNotFoundError(OpenClawError):
    """Raised when a requested resource is not found."""

    def __init__(self, resource_type: str, resource_id: str):
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type} not found: {resource_id}")


# ---------------------------------------------------------------------------
# Authorization errors
# ---------------------------------------------------------------------------

class PermissionError(OpenClawError):
    """Raised when a user lacks permission for an action."""

    def __init__(self, user_id: int, action: str):
        super().__init__(f"User {user_id} lacks permission for: {action}")
        self.user_id = user_id
        self.action = action
