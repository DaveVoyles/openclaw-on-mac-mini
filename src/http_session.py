"""
Shared HTTP session manager — replaces per-module _get_session() / close_session()
boilerplate across 6+ modules.

Usage in any skill module:

    from http_session import SessionManager

    _sessions = SessionManager(timeout=10)

    async def some_skill():
        session = await _sessions.get()
        async with session.get("https://...") as resp:
            ...

On bot shutdown, call ``close_all()`` to tear down every managed session.
"""
from __future__ import annotations

import logging
import weakref
from typing import Any

import aiohttp

log = logging.getLogger("openclaw.http")

# Registry of all SessionManager instances for bulk shutdown
_registry: weakref.WeakSet[SessionManager] = weakref.WeakSet()


class SessionManager:
    """Lazy aiohttp.ClientSession with automatic registry for bulk shutdown."""

    __slots__ = ("_session", "_timeout", "_connector_kwargs", "_name", "__weakref__")

    def __init__(
        self,
        *,
        timeout: int | float = 10,
        name: str = "",
        connector_limit: int | None = None,
        connector_limit_per_host: int | None = None,
        ttl_dns_cache: int | None = None,
    ):
        self._session: aiohttp.ClientSession | None = None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._name = name or f"session-{id(self)}"
        self._connector_kwargs: dict[str, Any] = {}
        if connector_limit is not None:
            self._connector_kwargs["limit"] = connector_limit
        if connector_limit_per_host is not None:
            self._connector_kwargs["limit_per_host"] = connector_limit_per_host
        if ttl_dns_cache is not None:
            self._connector_kwargs["ttl_dns_cache"] = ttl_dns_cache
        _registry.add(self)

    async def get(self) -> aiohttp.ClientSession:
        """Return the shared session, creating it lazily if needed."""
        if self._session is None or self._session.closed:
            connector = (
                aiohttp.TCPConnector(**self._connector_kwargs)
                if self._connector_kwargs
                else None
            )
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                connector=connector,
            )
        return self._session

    async def close(self) -> None:
        """Close the managed session if open."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def name(self) -> str:
        return self._name


async def close_all() -> None:
    """Close every registered session. Called once during bot shutdown."""
    for mgr in _registry:
        try:
            await mgr.close()
        except Exception as exc:
            log.debug("close %s: %s", mgr.name, exc)
