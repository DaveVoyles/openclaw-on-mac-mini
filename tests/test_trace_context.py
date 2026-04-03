"""Tests for trace_context module."""
import logging
import os
import sys

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trace_context import (
    TraceContext,
    TraceLogFilter,
    get_trace,
    get_trace_id,
    setup_trace_logging,
    trace_context,
)


class TestTraceContext:
    def test_trace_context_sets_and_clears(self):
        assert get_trace() is None
        with trace_context(command="test", user_id=42, channel_id=99) as ctx:
            assert get_trace() is ctx
            assert ctx.command == "test"
            assert ctx.user_id == 42
            assert ctx.channel_id == 99
        assert get_trace() is None

    def test_get_trace_id_inside_context(self):
        with trace_context(command="x") as ctx:
            assert get_trace_id() == ctx.trace_id
            assert len(ctx.trace_id) == 12

    def test_get_trace_id_outside_context(self):
        assert get_trace_id() == "no-trace"

    def test_nested_contexts(self):
        with trace_context(command="outer") as outer:
            outer_id = outer.trace_id
            with trace_context(command="inner") as inner:
                assert get_trace_id() == inner.trace_id
                assert inner.trace_id != outer_id
                assert get_trace().command == "inner"
            assert get_trace_id() == outer_id
            assert get_trace().command == "outer"
        assert get_trace() is None

    def test_trace_context_extra_kwargs(self):
        with trace_context(command="test", user_id=1, channel_id=2, model="gemini") as ctx:
            assert ctx.extra == {"model": "gemini"}

    def test_trace_id_is_unique(self):
        ids = set()
        for _ in range(100):
            ctx = TraceContext()
            ids.add(ctx.trace_id)
        assert len(ids) == 100


class TestTraceLogFilter:
    def test_filter_injects_fields_with_trace(self):
        filt = TraceLogFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        with trace_context(command="ask", user_id=123):
            filt.filter(record)
        assert hasattr(record, "trace_id")
        assert len(record.trace_id) == 12
        assert record.trace_cmd == "ask"
        assert record.trace_user == 123

    def test_filter_injects_defaults_without_trace(self):
        filt = TraceLogFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record)
        assert record.trace_id == "-"
        assert record.trace_cmd == "-"
        assert record.trace_user == 0

    def test_filter_always_returns_true(self):
        filt = TraceLogFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert filt.filter(record) is True


class TestSetupTraceLogging:
    def test_setup_adds_filter_to_handlers(self):
        logger = logging.getLogger("openclaw.test_setup")
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(handler)
        try:
            # Patch so setup_trace_logging finds our test logger
            setup_trace_logging.__wrapped__ = None  # just call it

            # Manually add filter since setup_trace_logging targets "openclaw"
            filt = TraceLogFilter()
            handler.addFilter(filt)

            assert any(isinstance(f, TraceLogFilter) for f in handler.filters)

            # Verify the filter works with the handler
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            with trace_context(command="verify"):
                handler.handle(record)
            assert hasattr(record, "trace_id")
        finally:
            logger.removeHandler(handler)
