"""
OpenClaw Custom Exceptions
Provides specific exception types for better error handling and debugging.
"""


class OpenClawError(Exception):
    """Base exception for all OpenClaw errors."""
    pass


class ConfigurationError(OpenClawError):
    """Raised when configuration is invalid or missing."""
    pass


class APIConnectionError(OpenClawError):
    """Raised when API connection fails."""

    def __init__(self, api_name: str, reason: str):
        self.api_name = api_name
        self.reason = reason
        super().__init__(f"API connection failed for {api_name}: {reason}")


class RateLimitError(OpenClawError):
    """Raised when API rate limit is exceeded."""

    def __init__(self, retry_after: int, api_name: str = ""):
        self.retry_after = retry_after
        self.api_name = api_name
        msg = f"Rate limited. Retry after {retry_after}s"
        if api_name:
            msg = f"{api_name}: {msg}"
        super().__init__(msg)


class AuthenticationError(OpenClawError):
    """Raised when authentication fails (missing or invalid API key)."""

    def __init__(self, api_name: str, detail: str = ""):
        self.api_name = api_name
        self.detail = detail
        msg = f"Authentication failed for {api_name}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


class InvalidRequestError(OpenClawError):
    """Raised when request parameters are invalid."""

    def __init__(self, reason: str, parameter: str = ""):
        self.reason = reason
        self.parameter = parameter
        msg = f"Invalid request: {reason}"
        if parameter:
            msg = f"{msg} (parameter: {parameter})"
        super().__init__(msg)


class ResourceNotFoundError(OpenClawError):
    """Raised when a requested resource is not found."""

    def __init__(self, resource_type: str, resource_id: str):
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type} not found: {resource_id}")


class TimeoutError(OpenClawError):
    """Raised when an operation times out."""

    def __init__(self, operation: str, timeout_seconds: int):
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(f"{operation} timed out after {timeout_seconds}s")


class PermissionError(OpenClawError):
    """Raised when user lacks permission for an operation."""

    def __init__(self, user_id: int, operation: str):
        self.user_id = user_id
        self.operation = operation
        super().__init__(f"User {user_id} lacks permission for: {operation}")


class StorageError(OpenClawError):
    """Raised when storage operations fail (read/write)."""

    def __init__(self, operation: str, path: str, reason: str):
        self.operation = operation
        self.path = path
        self.reason = reason
        super().__init__(f"Storage {operation} failed for {path}: {reason}")


class ValidationError(OpenClawError):
    """Raised when data validation fails."""

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"Validation failed for {field}: {reason}")
