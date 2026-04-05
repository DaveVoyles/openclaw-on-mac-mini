"""Tests for decorator patterns."""

import asyncio
import time

import pytest
from decorators import (
    cache_result,
    catch_and_log,
    deprecated,
    log_execution_time,
    rate_limit,
    retry_on_error,
    timeout,
)


class TestRetryOnError:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        call_count = 0
        
        @retry_on_error(max_retries=3)
        async def succeeds():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = await succeeds()
        assert result == "success"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        call_count = 0
        
        @retry_on_error(max_retries=2, delay=0.01)
        async def fails_twice_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Not yet")
            return "success"
        
        result = await fails_twice_then_succeeds()
        assert result == "success"
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        call_count = 0
        
        @retry_on_error(max_retries=2, delay=0.01)
        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")
        
        with pytest.raises(ValueError):
            await always_fails()
        
        assert call_count == 3  # Initial + 2 retries


class TestLogExecutionTime:
    @pytest.mark.asyncio
    async def test_logs_execution_time(self):
        @log_execution_time
        async def slow_func():
            await asyncio.sleep(0.01)
            return "done"
        
        result = await slow_func()
        assert result == "done"
    
    @pytest.mark.asyncio
    async def test_logs_on_exception(self):
        @log_execution_time
        async def failing_func():
            await asyncio.sleep(0.01)
            raise ValueError("Failed")
        
        with pytest.raises(ValueError):
            await failing_func()


class TestTimeout:
    @pytest.mark.asyncio
    async def test_completes_before_timeout(self):
        @timeout(1.0)
        async def fast_func():
            await asyncio.sleep(0.01)
            return "done"
        
        result = await fast_func()
        assert result == "done"
    
    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        @timeout(0.05)
        async def slow_func():
            await asyncio.sleep(1.0)
            return "done"
        
        with pytest.raises(asyncio.TimeoutError):
            await slow_func()


class TestCacheResult:
    @pytest.mark.asyncio
    async def test_caches_result(self):
        call_count = 0
        
        @cache_result(ttl_seconds=1.0)
        async def expensive_func():
            nonlocal call_count
            call_count += 1
            return f"result_{call_count}"
        
        # First call
        result1 = await expensive_func()
        assert result1 == "result_1"
        assert call_count == 1
        
        # Second call should use cache
        result2 = await expensive_func()
        assert result2 == "result_1"  # Same result
        assert call_count == 1  # Not called again
    
    @pytest.mark.asyncio
    async def test_cache_expires(self):
        call_count = 0
        
        @cache_result(ttl_seconds=0.05)
        async def expensive_func():
            nonlocal call_count
            call_count += 1
            return f"result_{call_count}"
        
        # First call
        result1 = await expensive_func()
        assert call_count == 1
        
        # Wait for cache to expire
        await asyncio.sleep(0.1)
        
        # Second call should fetch new result
        result2 = await expensive_func()
        assert call_count == 2


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_allows_calls_within_limit(self):
        call_times = []
        
        @rate_limit(calls=3, period=1.0)
        async def limited_func():
            call_times.append(time.monotonic())
            return "done"
        
        # Should allow 3 calls immediately
        for _ in range(3):
            await limited_func()
        
        assert len(call_times) == 3
    
    @pytest.mark.asyncio
    async def test_waits_when_limit_exceeded(self):
        @rate_limit(calls=2, period=0.2)
        async def limited_func():
            return "done"
        
        start = time.monotonic()
        
        # First 2 calls should be immediate
        await limited_func()
        await limited_func()
        
        # Third call should wait
        await limited_func()
        
        duration = time.monotonic() - start
        assert duration >= 0.2  # Should have waited


class TestCatchAndLog:
    @pytest.mark.asyncio
    async def test_returns_fallback_on_error(self):
        @catch_and_log(fallback="error")
        async def failing_func():
            raise ValueError("Failed")
        
        result = await failing_func()
        assert result == "error"
    
    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        @catch_and_log(fallback="error")
        async def succeeding_func():
            return "success"
        
        result = await succeeding_func()
        assert result == "success"


class TestDeprecated:
    @pytest.mark.asyncio
    async def test_logs_deprecation_warning(self):
        @deprecated(message="This is old", replacement="new_func")
        async def old_func():
            return "result"
        
        result = await old_func()
        assert result == "result"
