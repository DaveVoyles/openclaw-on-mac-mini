"""
OpenClaw Approval Store — in-memory CRUD for pending approval requests,
emergency stop flag, and authorization check.

Imports only from approval_models (no discord dependency) so it can be
used safely from background tasks and tests without a Discord client.
"""

import logging
import threading
import uuid

from approval_models import APPROVAL_TTL, ApprovalRequest, RiskLevel
from config import cfg

log = logging.getLogger("openclaw.approvals")

ALLOWED_APPROVER_IDS = set(getattr(cfg, "allowed_user_ids", []))


# ---------------------------------------------------------------------------
# Approval store
# ---------------------------------------------------------------------------


class ApprovalStore:
    """In-memory store for pending approval requests."""

    def __init__(self):
        self._pending: dict[str, ApprovalRequest] = {}

    def create(
        self,
        action: str,
        target: str,
        risk_level: RiskLevel,
        requester_id: int,
        requester_name: str,
        channel_id: int,
        detail: str = "",
    ) -> ApprovalRequest:
        """Create a new approval request and return it."""
        request_id = uuid.uuid4().hex[:8]
        req = ApprovalRequest(
            request_id=request_id,
            action=action,
            target=target,
            risk_level=risk_level,
            requester_id=requester_id,
            requester_name=requester_name,
            channel_id=channel_id,
            detail=detail,
        )
        self._pending[request_id] = req
        log.info(
            "Approval request created: %s (%s %s) by %s",
            request_id, action, target, requester_name,
        )
        return req

    def get(self, request_id: str) -> ApprovalRequest | None:
        req = self._pending.get(request_id)
        if req and req.is_expired and not req.resolved:
            req.resolved = True  # Auto-expire
        return req

    def resolve(
        self,
        request_id: str,
        approved: bool,
        resolver_id: int,
        resolver_name: str,
    ) -> ApprovalRequest | None:
        """Approve or deny a request. Returns the request if found and still open."""
        req = self._pending.get(request_id)
        if req is None or req.resolved:
            return None
        if req.is_expired:
            req.resolved = True
            return None
        req.resolved = True
        req.approved = approved
        req.resolver_id = resolver_id
        req.resolver_name = resolver_name
        return req

    def cleanup_expired(self):
        """Remove old resolved/expired requests."""
        cutoff = __import__("time").monotonic() - APPROVAL_TTL * 2
        expired = [k for k, v in self._pending.items() if v.created_at < cutoff]
        for k in expired:
            del self._pending[k]

    @property
    def pending_count(self) -> int:
        return sum(1 for v in self._pending.values() if not v.resolved and not v.is_expired)

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all active pending requests."""
        return [v for v in self._pending.values() if not v.resolved and not v.is_expired]


# Global instance
approval_store = ApprovalStore()


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------

_emergency_stop = False
_stop_lock = threading.Lock()


def is_emergency_stopped() -> bool:
    """Check if the emergency stop is active (thread-safe)."""
    with _stop_lock:
        return _emergency_stop


def set_emergency_stop(active: bool) -> None:
    """Toggle the emergency stop flag (thread-safe)."""
    global _emergency_stop
    with _stop_lock:
        _emergency_stop = active
    log.warning("Emergency stop %s", "ACTIVATED" if active else "deactivated")


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def is_authorized_approver(user_id: int) -> bool:
    """Return True if the user is in the configured allowed approver list."""
    return bool(ALLOWED_APPROVER_IDS) and int(user_id) in ALLOWED_APPROVER_IDS
