"""Request tracing with correlation IDs for structured logging."""
import contextvars
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_current_trace: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "current_trace", default=None
)


@dataclass
class TraceContext:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    command: str = ""
    user_id: int = 0
    channel_id: int = 0
    model: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def get_trace() -> "TraceContext | None":
    """Return the current trace context or None if not in a trace."""
    return _current_trace.get()


def get_trace_id() -> str:
    """Return the current trace ID or 'no-trace' if not in a trace context."""
    trace = _current_trace.get()
    return trace.trace_id if trace else "no-trace"


@contextmanager
def trace_context(command: str = "", user_id: int = 0, channel_id: int = 0, **extra):
    """Context manager that sets up request tracing."""
    ctx = TraceContext(command=command, user_id=user_id, channel_id=channel_id, extra=extra)
    token = _current_trace.set(ctx)
    try:
        yield ctx
    finally:
        _current_trace.reset(token)


class TraceLogFilter(logging.Filter):
    """Logging filter that injects trace_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace = _current_trace.get()
        # Extend LogRecord with trace context attributes
        setattr(record, "trace_id", trace.trace_id if trace else "-")
        setattr(record, "trace_cmd", trace.command if trace else "-")
        setattr(record, "trace_user", trace.user_id if trace else 0)
        return True


def set_trace(command: str = "", user_id: int = 0, channel_id: int = 0, **extra) -> None:
    """Set trace context for the current async task.

    Creates a new TraceContext with a fresh trace_id and stores it in the
    ContextVar.  Unlike ``trace_context()``, this does *not* automatically
    reset on exit — call ``clear_trace()`` when the request is done, or use
    the ``trace_context()`` context manager instead.
    """
    ctx = TraceContext(command=command, user_id=user_id, channel_id=channel_id, extra=extra)
    _current_trace.set(ctx)


def clear_trace() -> None:
    """Clear the trace context for the current async task."""
    _current_trace.set(None)


# Alias kept for callers that use the longer name from the task spec.
TraceLoggingFilter = TraceLogFilter


def setup_trace_logging() -> None:
    """Configure the openclaw logger with trace context injection.

    Call once at startup. Adds a TraceLogFilter to the root logger so all
    loggers benefit, and updates existing handler format strings to include
    ``[trace_id]``.
    """
    trace_filter = TraceLogFilter()

    # Register on root logger — filter runs once regardless of handler count.
    logging.root.addFilter(trace_filter)

    handlers = logging.getLogger("openclaw").handlers or logging.root.handlers
    for handler in handlers:
        if handler.formatter:
            fmt = handler.formatter._fmt
            if fmt and "trace_id" not in fmt:
                new_fmt = fmt.replace("%(levelname)s", "%(levelname)s [%(trace_id)s]")
                handler.setFormatter(logging.Formatter(new_fmt))
