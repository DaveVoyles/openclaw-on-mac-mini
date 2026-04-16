"""
Decorator patterns for OpenClaw.
Provides reusable decorators for retry logic, timing, rate limiting, etc.
"""

import asyncio
import functools
import logging
import time
from typing import Any, Callable, TypeVar

log = logging.getLogger("openclaw.decorators")

# Type variable for decorated function return type
T = TypeVar("T")


def retry_on_error(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """
    Retry decorator for async functions.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
        backoff: Multiplier for delay after each retry (default: 2.0)
        exceptions: Tuple of exceptions to catch (default: all exceptions)

    Examples:
        @retry_on_error(max_retries=3, delay=1.0)
        async def fetch_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        log.error(
                            "%s failed after %d retries: %s",
                            func.__name__,
                            max_retries,
                            e,
                        )
                        raise

                    log.warning(
                        "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        func.__name__,
                        attempt + 1,
                        max_retries + 1,
                        e,
                        current_delay,
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            # Should never reach here, but satisfies type checker
            raise last_exception  # type: ignore

        return wrapper
    return decorator


def log_execution_time(func: Callable) -> Callable:
    """
    Log execution time of async function.

    Examples:
        @log_execution_time
        async def slow_operation():
            ...
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            duration = time.perf_counter() - start
            log.info("%s completed in %.3fs", func.__name__, duration)
            return result
        except Exception as e:  # broad: intentional
            duration = time.perf_counter() - start
            log.error("%s failed after %.3fs: %s", func.__name__, duration, e)
            raise

    return wrapper


def timeout(seconds: float) -> Callable:
    """
    Timeout decorator for async functions.

    Args:
        seconds: Maximum execution time in seconds

    Examples:
        @timeout(30.0)
        async def api_call():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                log.error("%s timed out after %.1fs", func.__name__, seconds)
                raise

        return wrapper
    return decorator


def cache_result(ttl_seconds: float = 60.0) -> Callable:
    """
    Cache the result of an async function for a specified time.

    Args:
        ttl_seconds: Time to live for cached result in seconds (default: 60.0)

    Examples:
        @cache_result(ttl_seconds=300)
        async def expensive_operation():
            ...

    Note: This is a simple cache that doesn't consider arguments.
    For argument-aware caching, use functools.lru_cache or aiocache.
    """
    def decorator(func: Callable) -> Callable:
        cached_result = None
        cache_time = None

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal cached_result, cache_time

            current_time = time.monotonic()

            # Return cached result if still valid
            if cache_time and (current_time - cache_time) < ttl_seconds:
                log.debug("%s: returning cached result", func.__name__)
                return cached_result

            # Fetch new result
            log.debug("%s: cache miss, fetching new result", func.__name__)
            result = await func(*args, **kwargs)
            cached_result = result
            cache_time = current_time
            return result

        return wrapper
    return decorator


def rate_limit(calls: int, period: float) -> Callable:
    """
    Rate limit decorator for async functions.

    Args:
        calls: Maximum number of calls allowed
        period: Time period in seconds

    Examples:
        @rate_limit(calls=10, period=60.0)  # 10 calls per minute
        async def api_call():
            ...
    """
    def decorator(func: Callable) -> Callable:
        call_times: list[float] = []

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal call_times

            current_time = time.monotonic()

            # Remove calls outside the time window
            call_times = [t for t in call_times if current_time - t < period]

            # Check if rate limit exceeded
            if len(call_times) >= calls:
                oldest_call = call_times[0]
                wait_time = period - (current_time - oldest_call)
                log.warning(
                    "%s: rate limit exceeded (%d calls/%ds). Waiting %.1fs...",
                    func.__name__,
                    calls,
                    int(period),
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                # After waiting, remove old calls again
                current_time = time.monotonic()
                call_times = [t for t in call_times if current_time - t < period]

            # Record this call
            call_times.append(current_time)

            return await func(*args, **kwargs)

        return wrapper
    return decorator


def catch_and_log(
    fallback: Any = None,
    exceptions: tuple = (Exception,),
    level: int = logging.ERROR,
) -> Callable:
    """
    Catch exceptions, log them, and return a fallback value.

    Args:
        fallback: Value to return if exception occurs (default: None)
        exceptions: Tuple of exceptions to catch (default: all exceptions)
        level: Logging level (default: ERROR)

    Examples:
        @catch_and_log(fallback="Error occurred")
        async def risky_operation():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except exceptions as e:
                log.log(level, "%s failed: %s", func.__name__, e, exc_info=True)
                return fallback

        return wrapper
    return decorator


def deprecated(message: str = "", replacement: str = "") -> Callable:
    """
    Mark a function as deprecated.

    Args:
        message: Deprecation message
        replacement: Suggested replacement function name

    Examples:
        @deprecated(replacement="new_function")
        async def old_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            warning = f"{func.__name__} is deprecated."
            if message:
                warning += f" {message}"
            if replacement:
                warning += f" Use {replacement} instead."
            log.warning(warning)
            return await func(*args, **kwargs)

        return wrapper
    return decorator
