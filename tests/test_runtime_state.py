"""Tests for runtime_state request context bindings."""

from runtime_state import (
    get_current_channel_id,
    get_current_thread_id,
    request_context,
)


def test_request_context_sets_channel_and_thread():
    assert get_current_channel_id() is None
    assert get_current_thread_id() is None


def test_request_context_nested_contexts_do_not_bleed():
    with request_context(channel_id=100, thread_id=200):
        assert get_current_channel_id() == 100
        assert get_current_thread_id() == 200

        with request_context(channel_id=300):
            assert get_current_channel_id() == 300
            assert get_current_thread_id() == 200

        assert get_current_channel_id() == 100
        assert get_current_thread_id() == 200

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None


def test_request_context_resets_after_exception():
    try:
        with request_context(channel_id=77, thread_id=88):
            assert get_current_channel_id() == 77
            assert get_current_thread_id() == 88
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None

    with request_context(channel_id=123, thread_id=456):
        assert get_current_channel_id() == 123
        assert get_current_thread_id() == 456

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None
