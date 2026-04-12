"""Startup capability scan — run once at bot startup to log provider availability."""

import asyncio
import logging
import os
import time

from llm.providers import COPILOT_PROXY_ENABLED, check_proxy_health
from model_routing_policy import is_ollama_alive

log = logging.getLogger(__name__)

# Providers that are network-pinged vs key-checked (affects log label on failure)
_PINGED_PROVIDERS = frozenset({"copilot", "ollama"})


async def scan_providers() -> dict[str, dict]:
    """Parallel-ping all providers; return availability and latency map.

    Returns:
        {
            "copilot":   {"available": bool, "latency_ms": float | None},
            "ollama":    {"available": bool, "latency_ms": float | None},
            "openai":    {"available": bool, "latency_ms": None},
            "anthropic": {"available": bool, "latency_ms": None},
        }
    Latency is None when a provider is not reachable or is key-checked only.
    """
    results = await asyncio.gather(
        _ping_copilot(),
        _ping_ollama(),
        return_exceptions=True,
    )

    def _unpack(r) -> tuple[bool, float | None]:
        if isinstance(r, Exception):
            return False, None
        return r  # already (bool, latency_ms)

    copilot_ok, copilot_ms = _unpack(results[0])
    ollama_ok, ollama_ms = _unpack(results[1])

    status: dict[str, dict] = {
        "copilot": {"available": copilot_ok, "latency_ms": copilot_ms},
        "ollama": {"available": ollama_ok, "latency_ms": ollama_ms},
        "openai": {"available": bool(os.getenv("OPENAI_API_KEY")), "latency_ms": None},
        "anthropic": {"available": bool(os.getenv("ANTHROPIC_API_KEY")), "latency_ms": None},
    }
    _log_availability_summary(status)
    return status


async def _timed_ping(coro) -> tuple[bool, float | None]:
    """Await *coro*, returning (success, latency_ms); swallows all exceptions."""
    start = time.perf_counter()
    try:
        result = await coro
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        return bool(result), latency_ms
    except Exception:
        return False, None


async def _ping_copilot() -> tuple[bool, float | None]:
    if not COPILOT_PROXY_ENABLED:
        return False, None
    return await _timed_ping(check_proxy_health())


async def _ping_ollama() -> tuple[bool, float | None]:
    return await _timed_ping(is_ollama_alive())


def _log_availability_summary(status: dict[str, dict]) -> None:
    lines = ["── Provider Availability ──"]
    for provider, info in status.items():
        ok = info["available"]
        ms = info.get("latency_ms")
        if ok:
            ms_str = f" {ms:.0f}ms" if ms is not None else ""
            indicator = f"✅{ms_str}"
        elif provider in _PINGED_PROVIDERS:
            indicator = "❌ timeout"
        else:
            indicator = "❌ unavailable"
        lines.append(f"  {provider:<12} {indicator}")
    log.info("\n".join(lines))
