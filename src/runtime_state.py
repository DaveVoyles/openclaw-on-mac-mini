"""Thin re-export hub for backward-compatible imports from runtime_state.

All implementation has been split into focused sub-modules:
  - channel_profile_state  : dataclasses, channel profile DB, interaction state
  - anchor_context_state   : anchor state, context locks, scoped recall alerts
  - memory_compaction_state: memory compaction event tracking
  - quality_eval_state     : quality evaluation scorecard
"""

from __future__ import annotations

from anchor_context_state import (
    _ANCHOR_STATE_BY_SCOPE,
    _ANCHOR_STATE_LOCK,
    _CONTEXT_LOCKS,
    _CONTEXT_LOCKS_LOCK,
    _LAST_ANCHOR_STATE,
    _MAX_SCOPED_RECALL_ALERTS,
    _SCOPED_RECALL_ALERTS,
    _SCOPED_RECALL_ALERTS_LOCK,
    ANCHOR_EXPIRY_SECONDS,
    CONTEXT_LOCK_EXPIRY_SECONDS,
    anchor_matches,
    get_anchor_state,
    get_context_lock,
    get_scoped_recall_alerts,
    record_scoped_recall_alert,
    reset_anchor_state,
    reset_context_lock,
    resolve_anchor_state,
    resolve_context_lock,
    set_anchor_state,
    set_context_lock,
)

# ruff: noqa: F401 — re-exports for backward compatibility
from channel_profile_state import (
    _BOT,
    _CHANNEL_CONFIG_STATE,
    _CHANNEL_PROFILE_ALLOWED,
    _CHANNEL_PROFILE_DB,
    _CHANNEL_PROFILE_DEFAULTS,
    _CHANNEL_PROFILE_INT_BOUNDS,
    _CHANNEL_PROFILE_INT_DEFAULTS,
    _CHANNEL_PROFILE_LOCK,
    _CONVERSATION_STATE,
    _CURRENT_CHANNEL_ID,
    _CURRENT_THREAD_ID,
    _CURRENT_USER_ID,
    _INTERACTION_STATE,
    _PROFILE_USAGE_SIGNALS,
    RUNTIME_STATE_CONTEXTS,
    RuntimeStateContexts,
    _ChannelConfigState,
    _ConversationState,
    _get_channel_profile_db,
    _InteractionState,
    _normalize_profile_int_value,
    _normalize_profile_value,
    _reset_channel_profile_store_for_tests,
    _scope_thread_id,
    clear_channel_profile,
    get_bot,
    get_channel_profile,
    get_channel_profile_defaults,
    get_channel_profile_usage_signals,
    get_channel_prompts,
    get_channel_roles,
    get_current_channel_id,
    get_current_thread_id,
    get_current_user_id,
    get_effective_channel_profile,
    get_memory_lifecycle_policy,
    list_channel_profile_recommendations,
    record_channel_profile_signal,
    refresh_channel_profile_recommendations,
    request_context,
    set_bot,
    set_channel_config,
    set_channel_profile,
    set_current_user_id,
    update_channel_profile_recommendation,
)
from memory_compaction_state import (
    _MAX_MEMORY_COMPACTION_EVENTS,
    _MEMORY_COMPACTION_EVENTS,
    _MEMORY_COMPACTION_EVENTS_LOCK,
    get_memory_compaction_events,
    record_memory_compaction_event,
)
from quality_eval_state import (
    build_quality_eval_scorecard,
    create_quality_eval_scorecard,
    ensure_quality_eval_scorecard,
    list_quality_eval_scorecards,
    save_quality_eval_scorecard,
)
