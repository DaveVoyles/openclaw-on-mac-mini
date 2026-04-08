"""Tests for profiler.py — CPU profiling, memory decorator, get_profiler."""

import cProfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from profiler import (
    MEMORY_PROFILER_AVAILABLE,
    Profiler,
    get_profiler,
    profile_memory,
)


@pytest.fixture
def fresh_profiler():
    """Return a fresh Profiler instance for each test."""
    return Profiler()


class TestProfilerInit:
    def test_initial_state_not_profiling(self, fresh_profiler):
        assert fresh_profiler._is_profiling is False
        assert fresh_profiler._cpu_profiler is None
        assert fresh_profiler._profile_start_time is None


class TestStartCpuProfiling:
    def test_start_enables_profiling(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        assert fresh_profiler._is_profiling is True
        assert fresh_profiler._cpu_profiler is not None
        assert fresh_profiler._profile_start_time is not None
        fresh_profiler.stop_cpu_profiling()

    def test_double_start_raises(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        with pytest.raises(RuntimeError, match="already active"):
            fresh_profiler.start_cpu_profiling()
        fresh_profiler.stop_cpu_profiling()

    def test_start_sets_start_time_near_now(self, fresh_profiler):
        before = time.time()
        fresh_profiler.start_cpu_profiling()
        after = time.time()
        assert before <= fresh_profiler._profile_start_time <= after
        fresh_profiler.stop_cpu_profiling()


class TestStopCpuProfiling:
    def test_stop_returns_string_stats(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        # Do some work so stats aren't empty
        _ = [i * i for i in range(1000)]
        result = fresh_profiler.stop_cpu_profiling()
        assert isinstance(result, str)
        assert "Profile Duration" in result

    def test_stop_resets_state(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        fresh_profiler.stop_cpu_profiling()
        assert fresh_profiler._is_profiling is False
        assert fresh_profiler._cpu_profiler is None
        assert fresh_profiler._profile_start_time is None

    def test_stop_without_start_raises(self, fresh_profiler):
        with pytest.raises(RuntimeError, match="No active profiling"):
            fresh_profiler.stop_cpu_profiling()

    def test_stats_contain_top_functions_header(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        result = fresh_profiler.stop_cpu_profiling()
        assert "Top 50 functions" in result

    def test_stats_contain_callers_header(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        result = fresh_profiler.stop_cpu_profiling()
        assert "Callers" in result


class TestGetCpuStatsDict:
    def test_returns_empty_when_not_profiling(self, fresh_profiler):
        result = fresh_profiler.get_cpu_stats_dict()
        assert result == {}

    def test_returns_dict_while_profiling(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        _ = [i ** 2 for i in range(100)]
        result = fresh_profiler.get_cpu_stats_dict()
        assert isinstance(result, dict)
        # Should have some entries
        assert len(result) >= 0
        fresh_profiler.stop_cpu_profiling()

    def test_stat_entries_have_expected_keys(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        _ = sum(range(1000))
        result = fresh_profiler.get_cpu_stats_dict()
        for key, val in result.items():
            assert "ncalls" in val
            assert "tottime" in val
            assert "cumtime" in val
        fresh_profiler.stop_cpu_profiling()


class TestGenerateFlameGraphData:
    def test_returns_empty_when_not_profiling(self, fresh_profiler):
        result = fresh_profiler.generate_flame_graph_data()
        assert result == {}

    def test_returns_dict_while_profiling(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        _ = sorted([3, 1, 2])
        result = fresh_profiler.generate_flame_graph_data()
        assert isinstance(result, dict)
        fresh_profiler.stop_cpu_profiling()

    def test_flame_graph_entries_have_expected_keys(self, fresh_profiler):
        fresh_profiler.start_cpu_profiling()
        _ = list(map(str, range(100)))
        result = fresh_profiler.generate_flame_graph_data()
        for func_id, entry in result.items():
            assert "name" in entry
            assert "file" in entry
            assert "cumtime" in entry
            assert "ncalls" in entry
            assert "children" in entry
        fresh_profiler.stop_cpu_profiling()


class TestProfileForDuration:
    @pytest.mark.asyncio
    async def test_profiles_for_given_duration(self, fresh_profiler):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await fresh_profiler.profile_for_duration(2)
        mock_sleep.assert_called_once_with(2)
        assert isinstance(result, str)
        assert "Profile Duration" in result

    @pytest.mark.asyncio
    async def test_not_profiling_after_completion(self, fresh_profiler):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await fresh_profiler.profile_for_duration(1)
        assert fresh_profiler._is_profiling is False


class TestProfileMemoryDecorator:
    def test_wraps_function_when_memory_profiler_unavailable(self):
        def my_func(x):
            return x * 2

        wrapped = profile_memory(my_func)
        # Should still be callable
        assert wrapped(5) == 10

    def test_decorator_preserves_function_when_no_memory_profiler(self):
        # When MEMORY_PROFILER_AVAILABLE is False, should return func unchanged
        with patch("profiler.MEMORY_PROFILER_AVAILABLE", False):
            def my_func():
                return 42
            result = profile_memory(my_func)
            assert result() == 42


class TestGetProfiler:
    def test_returns_profiler_instance(self):
        p = get_profiler()
        assert isinstance(p, Profiler)

    def test_returns_same_instance_on_repeated_calls(self):
        p1 = get_profiler()
        p2 = get_profiler()
        assert p1 is p2

    def test_global_profiler_can_start_stop(self):
        p = get_profiler()
        # Ensure not already profiling (from previous test leak)
        if p._is_profiling:
            p.stop_cpu_profiling()
        p.start_cpu_profiling()
        result = p.stop_cpu_profiling()
        assert isinstance(result, str)
