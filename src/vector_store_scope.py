"""Vector store scope resolution and metadata helpers."""

from __future__ import annotations

import logging
from typing import Any, Optional

from vector_store_config import (
    _RECALL_DOMAIN_DIRECTIVE_RE,
    _RECALL_DOMAIN_TERMS,
)

log = logging.getLogger(__name__)


def _extract_explicit_recall_domains(query: str) -> set[str]:
    return {str(match.group(1)).lower() for match in _RECALL_DOMAIN_DIRECTIVE_RE.finditer(query or "")}


def _infer_recall_domains(text: str) -> set[str]:
    lowered = (text or "").lower()
    domains: set[str] = set()
    for domain, terms in _RECALL_DOMAIN_TERMS.items():
        if domain in lowered:
            domains.add(domain)
            continue
        hits = sum(1 for term in terms if term in lowered)
        if domain == "wwe" and hits >= 1:
            domains.add(domain)
        elif domain == "sports" and hits >= 2:
            domains.add(domain)
    return domains


def _normalize_scope_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_scope(
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> tuple[str | None, str | None]:
    if channel_id is None or thread_id is None:
        from runtime_state import get_current_channel_id, get_current_thread_id

        if channel_id is None:
            channel_id = get_current_channel_id()
        if thread_id is None:
            thread_id = get_current_thread_id()
    return _normalize_scope_id(channel_id), _normalize_scope_id(thread_id)


def _inject_scope_metadata(
    metadata: Optional[dict],
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> dict:
    meta = dict(metadata or {})
    resolved_channel_id, resolved_thread_id = _resolve_scope(channel_id=channel_id, thread_id=thread_id)
    if resolved_channel_id and not _normalize_scope_id(meta.get("channel_id")):
        meta["channel_id"] = resolved_channel_id
    if resolved_thread_id and not _normalize_scope_id(meta.get("thread_id")):
        meta["thread_id"] = resolved_thread_id
    return meta


def _combine_scope_where(
    base_where: Optional[dict],
    *,
    channel_id: str | None,
    thread_id: str | None,
) -> Optional[dict]:
    scope_filters: list[dict] = []
    if channel_id:
        scope_filters.append({"channel_id": channel_id})
    if thread_id:
        scope_filters.append({"thread_id": thread_id})
    if not scope_filters:
        return base_where
    if base_where:
        return {"$and": [base_where, *scope_filters]}
    if len(scope_filters) == 1:
        return scope_filters[0]
    return {"$and": scope_filters}


def _is_legacy_metadata(meta: dict) -> bool:
    return not _normalize_scope_id(meta.get("channel_id")) and not _normalize_scope_id(meta.get("thread_id"))


def _allow_fallback_result(
    meta: dict,
    *,
    channel_id: str,
    thread_id: str | None,
) -> bool:
    doc_channel_id = _normalize_scope_id(meta.get("channel_id"))
    doc_thread_id = _normalize_scope_id(meta.get("thread_id"))
    if thread_id:
        return doc_channel_id == channel_id and doc_thread_id == thread_id
    return doc_channel_id == channel_id
