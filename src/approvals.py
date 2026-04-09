"""
OpenClaw Approvals — re-export facade for backward compatibility.

The approval system has been split into focused modules:
  - approval_models.py  — RiskLevel, ApprovalRequest, APPROVAL_TTL
  - approval_store.py   — ApprovalStore, approval_store, emergency stop
  - approval_ui.py      — ApprovalView, build_approval_embed, RISK_COLORS, RISK_EMOJI

Import directly from those modules in new code. This file re-exports
everything so existing `from approvals import ...` call sites work unchanged.
"""

# isort: skip_file  (keep re-exports grouped by source module)

from approval_models import APPROVAL_TTL, ApprovalRequest, RiskLevel
from approval_store import (
    ALLOWED_APPROVER_IDS,
    ApprovalStore,
    approval_store,
    is_emergency_stopped,
    set_emergency_stop,
)
from approval_ui import (
    RISK_COLORS,
    RISK_EMOJI,
    ApprovalView,
    build_approval_embed,
)


def is_authorized_approver(user_id: int) -> bool:
    """Return True if user is in the configured allowed approver list.

    Defined here (not re-exported from approval_store) so that tests can
    monkeypatch ``approvals.ALLOWED_APPROVER_IDS`` and have this function
    pick up the patched value at call time via the module-level name lookup.
    """
    return bool(ALLOWED_APPROVER_IDS) and int(user_id) in ALLOWED_APPROVER_IDS


__all__ = [
    # models
    "APPROVAL_TTL",
    "ApprovalRequest",
    "RiskLevel",
    # store
    "ALLOWED_APPROVER_IDS",
    "ApprovalStore",
    "approval_store",
    "is_authorized_approver",
    "is_emergency_stopped",
    "set_emergency_stop",
    # ui
    "RISK_COLORS",
    "RISK_EMOJI",
    "ApprovalView",
    "build_approval_embed",
]

