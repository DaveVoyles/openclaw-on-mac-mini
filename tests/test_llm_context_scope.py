import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_genai_mock = MagicMock()
_genai_mock.types.GenerateContentConfig = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _genai_mock)
sys.modules.setdefault("google.genai.types", _genai_mock.types)

import llm.context as ctx  # noqa: E402
import runtime_state as runtime_state_mod  # noqa: E402
from llm import chat_stream  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_runtime_state_before_each_test(tmp_path, monkeypatch):
    """Guarantee a clean runtime-state slate before (and after) every test.

    This prevents state leakage between tests when running under xdist where
    all tests in this file share the same worker process.  Without this,
    a test that fails mid-run will skip its manual cleanup calls and leave
    dirty anchor/lock state that causes subsequent tests to fail.
    """
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-scope-test.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()
    runtime_state_mod.reset_anchor_state()
    yield
    runtime_state_mod.reset_anchor_state()
    runtime_state_mod._reset_channel_profile_store_for_tests()


@pytest.mark.asyncio
async def test_trim_history_uses_salience_compression_with_anti_drift_metadata():
    history: list[dict] = [
        {"role": "model", "parts": ["[System] You are OpenClaw."]},
        {"role": "user", "parts": ["Let's plan a migration."]},
    ]
    # Older thread content with one high-salience decision.
    for i in range(45):
        if i == 8:
            text = "Decision: move billing to Stripe and keep legacy invoices read-only."
        elif i == 17:
            text = "Root cause found: timeout from /api/payments after 30s."
        else:
            text = f"General discussion item {i} about migration details and notes."
        role = "user" if i % 2 == 0 else "model"
        history.append({"role": role, "parts": [text]})

    quality: dict[str, object] = {}
    trimmed = await ctx._trim_history(history, model_hint="default", context_quality=quality)

    assert len(trimmed) <= 20
    packed = next(
        (m for m in trimmed if "[Compressed Thread Context" in " ".join(m.get("parts", []))),
        None,
    )
    assert packed is not None
    packed_text = " ".join(p for p in packed.get("parts", []) if isinstance(p, str))
    assert "Decision: move billing to Stripe" in packed_text
    assert "[Anti-drift]" in packed_text

    assert quality.get("compression_applied") is True
    assert isinstance(quality.get("compression_ratio"), float)
    assert quality.get("retained_key_facts_count", 0) >= 1
    assert quality.get("key_topics_retained", 0) >= 0
    assert quality.get("drift_risk") in {"low", "medium", "high"}


def test_extract_cross_channel_opt_in_strips_marker():
    cleaned, enabled = ctx._extract_cross_channel_opt_in("Find this --cross-channel")
    assert enabled is True
    assert cleaned == "Find this"


def test_extract_cross_channel_opt_in_requires_explicit_marker():
    cleaned, enabled = ctx._extract_cross_channel_opt_in("Can you search across channels for this?")
    assert enabled is False
    assert cleaned == "Can you search across channels for this?"


def test_extract_context_controls_supports_reset_and_anchor_override():
    cleaned, controls = ctx._extract_context_controls("help --reset-context --use-prior-report --anchor=report_42")
    assert cleaned == "help"
    assert controls["reset_context"] is True
    assert controls["use_prior_report"] is True
    assert controls["anchor_override"] == "report_42"


def test_extract_cross_channel_opt_in_supports_hash_marker():
    cleaned, enabled = ctx._extract_cross_channel_opt_in("Need broader recall #cross-channel please")
    assert enabled is True
    assert cleaned == "Need broader recall please"


def test_extract_context_controls_supports_no_anchor_and_space_anchor():
    cleaned, controls = ctx._extract_context_controls("run recap --no-anchor --anchor report_7")
    assert cleaned == "run recap"
    assert controls["disable_anchor"] is True
    assert controls["anchor_override"] == "report_7"


def test_merge_structured_context_controls_prefers_slash_options():
    merged_cross, merged_controls = ctx._merge_structured_context_controls(
        cross_channel=True,
        controls={
            "reset_context": False,
            "use_prior_report": False,
            "disable_anchor": False,
            "anchor_override": "report_legacy",
        },
        structured_controls={
            "scope": "prior-report",
            "reset_context": True,
            "anchor": "report_structured",
        },
    )
    assert merged_cross is False
    assert merged_controls["use_prior_report"] is True
    assert merged_controls["reset_context"] is True
    assert merged_controls["anchor_override"] == "report_structured"
    assert merged_controls["disable_anchor"] is False


def test_merge_structured_context_controls_anchor_none_disables_anchor():
    merged_cross, merged_controls = ctx._merge_structured_context_controls(
        cross_channel=False,
        controls={
            "reset_context": False,
            "use_prior_report": True,
            "disable_anchor": False,
            "anchor_override": "report_legacy",
        },
        structured_controls={"anchor": "none"},
    )
    assert merged_cross is False
    assert merged_controls["disable_anchor"] is True
    assert merged_controls["anchor_override"] is None


@pytest.mark.asyncio
async def test_auto_recall_context_passes_cross_channel_flag(monkeypatch):
    captured = {}

    async def _recall_for_context(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return ""

    monkeypatch.setattr(ctx.cfg, "auto_recall_enabled", True)
    monkeypatch.setitem(
        sys.modules,
        "vector_store",
        SimpleNamespace(recall_for_context=_recall_for_context),
    )
    monkeypatch.setitem(
        sys.modules,
        "user_profile",
        SimpleNamespace(get_profile_prompt=lambda: ""),
    )

    async def _rules(*args, **kwargs):
        return []

    monkeypatch.setitem(
        sys.modules,
        "rules_engine",
        SimpleNamespace(get_relevant_rules=_rules),
    )

    monkeypatch.setattr("runtime_state.get_current_channel_id", lambda: 111)
    monkeypatch.setattr("runtime_state.get_current_thread_id", lambda: 222)

    await ctx._auto_recall_context("hello", cross_channel=True)

    assert captured["query"] == "hello"
    assert captured["kwargs"]["cross_channel"] is True


@pytest.mark.asyncio
async def test_auto_recall_context_uses_anchor_override(monkeypatch):
    captured = {}

    async def _recall_for_context(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return ""

    monkeypatch.setattr(ctx.cfg, "auto_recall_enabled", True)
    monkeypatch.setitem(
        sys.modules,
        "vector_store",
        SimpleNamespace(recall_for_context=_recall_for_context),
    )
    monkeypatch.setitem(
        sys.modules,
        "user_profile",
        SimpleNamespace(get_profile_prompt=lambda: ""),
    )

    async def _rules(*args, **kwargs):
        return []

    monkeypatch.setitem(
        sys.modules,
        "rules_engine",
        SimpleNamespace(get_relevant_rules=_rules),
    )

    monkeypatch.setattr("runtime_state.get_current_channel_id", lambda: 111)
    monkeypatch.setattr("runtime_state.get_current_thread_id", lambda: 222)
    monkeypatch.setattr("llm.context.get_current_user_id", lambda: "u-1")
    monkeypatch.setattr("llm.context.resolve_context_lock", lambda **_: (None, None))
    monkeypatch.setattr("llm.context.resolve_anchor_state", lambda **_: (None, None))

    await ctx._auto_recall_context("hello", anchor_override="report_99")
    assert captured["kwargs"]["anchor_id"] == "report_99"


@pytest.mark.asyncio
async def test_auto_recall_context_appends_guard_notes_to_routing_notes(monkeypatch):
    async def _recall_for_context(query, **kwargs):
        return ""

    monkeypatch.setattr(ctx.cfg, "auto_recall_enabled", True)
    monkeypatch.setitem(
        sys.modules,
        "vector_store",
        SimpleNamespace(
            recall_for_context=_recall_for_context,
            consume_recall_guard_notes=lambda: ["suppressed 1 out-of-scope sports/WWE memory candidates"],
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "user_profile",
        SimpleNamespace(get_profile_prompt=lambda: ""),
    )

    async def _rules(*args, **kwargs):
        return []

    monkeypatch.setitem(
        sys.modules,
        "rules_engine",
        SimpleNamespace(get_relevant_rules=_rules),
    )

    monkeypatch.setattr("runtime_state.get_current_channel_id", lambda: 111)
    monkeypatch.setattr("runtime_state.get_current_thread_id", lambda: 222)

    notes: list[str] = []
    await ctx._auto_recall_context("hello", routing_notes=notes)
    assert any(note.startswith("Context guard: suppressed 1 out-of-scope") for note in notes)


def test_build_context_explainability_includes_scope_lock_anchor_and_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-test.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()

    runtime_state_mod.set_channel_profile(
        111,
        thread_id=222,
        tone="friendly",
        table_style="copy-safe",
        emoji_level="rich",
        report_depth="detailed",
        source_strictness="strict",
    )
    runtime_state_mod.set_context_lock(
        user_id="u-explain",
        mode="thread",
        channel_id=111,
        thread_id=222,
    )
    runtime_state_mod.set_anchor_state(111, 222, "report_42", timestamp=time.time() - 75)

    with runtime_state_mod.request_context(channel_id=111, thread_id=222, user_id="u-explain"):
        payload = ctx._build_context_explainability(
            cross_channel=False,
            followup=True,
            use_prior_report=False,
            anchor_override=None,
            disable_anchor=False,
        )
        note = ctx._format_context_explainability_note(payload)

    assert payload["scope_mode"] == "thread"
    assert payload["lock_mode"] == "thread"
    assert payload["anchor_id"] == "report_42"
    assert isinstance(payload["anchor_age_seconds"], int)
    assert payload["anchor_age_seconds"] >= 60
    assert payload["effective_profile"]["tone"] == "friendly"
    assert payload["effective_profile"]["table_style"] == "copy-safe"
    assert "thread" in note
    assert "report_42" in note
    assert "friendly/copy-safe/rich/detailed/strict" in note

    runtime_state_mod.reset_context_lock("u-explain")
    runtime_state_mod.reset_anchor_state(channel_id=111, thread_id=222)
    runtime_state_mod._reset_channel_profile_store_for_tests()


def test_build_context_explainability_cross_channel_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-cross.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()

    with runtime_state_mod.request_context(channel_id=999, thread_id=None, user_id="u-cross"):
        payload = ctx._build_context_explainability(
            cross_channel=True,
            followup=False,
            use_prior_report=False,
            anchor_override=None,
            disable_anchor=True,
        )

    assert payload["scope_mode"] == "cross-channel"
    assert payload["lock_mode"] == "none"
    assert payload["anchor_id"] is None


def test_build_context_explainability_includes_ignored_scope_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-ignored.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()

    runtime_state_mod.set_context_lock(
        user_id="u-ignored",
        mode="thread",
        channel_id=111,
        thread_id=222,
    )

    with runtime_state_mod.request_context(channel_id=111, thread_id=999, user_id="u-ignored"):
        payload = ctx._build_context_explainability(
            cross_channel=False,
            followup=True,
            use_prior_report=False,
            anchor_override=None,
            disable_anchor=False,
        )
        note = ctx._format_context_explainability_note(payload)

    assert payload["lock_mode"] == "none"
    assert "lock:scope_mismatch" in payload["ignored"]
    assert "ignored:lock:scope_mismatch" in note

    runtime_state_mod.reset_context_lock("u-ignored")
    runtime_state_mod._reset_channel_profile_store_for_tests()


def test_build_context_explainability_reports_stale_anchor(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-stale-anchor.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()
    runtime_state_mod.set_anchor_state(
        111,
        222,
        "report_stale",
        timestamp=time.time() - (runtime_state_mod.ANCHOR_EXPIRY_SECONDS + 10),
    )

    with runtime_state_mod.request_context(channel_id=111, thread_id=222, user_id="u-stale-anchor"):
        payload = ctx._build_context_explainability(
            cross_channel=False,
            followup=True,
            use_prior_report=False,
            anchor_override=None,
            disable_anchor=False,
        )
        note = ctx._format_context_explainability_note(payload)

    assert payload["anchor_id"] is None
    assert "anchor:stale" in payload["ignored"]
    assert "ignored:" in note

    runtime_state_mod.reset_anchor_state(channel_id=111, thread_id=222)
    runtime_state_mod._reset_channel_profile_store_for_tests()


@pytest.mark.asyncio
async def test_chat_stream_exposes_explainability_metadata(monkeypatch, tmp_path):
    import sys

    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-meta.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()

    runtime_state_mod.set_context_lock(
        user_id="u-meta",
        mode="channel",
        channel_id=333,
        thread_id=None,
    )
    chat_module = sys.modules["llm.chat"]
    monkeypatch.setattr(chat_module, "LOCAL_LLM_ENABLED", False)

    with runtime_state_mod.request_context(channel_id=333, thread_id=None, user_id="u-meta"):
        final_meta = None
        async for _, is_final, meta in chat_stream("hello --cross-channel", model_preference="local"):
            if is_final:
                final_meta = meta
                break

    assert final_meta is not None
    payload = final_meta.get("explainability")
    assert isinstance(payload, dict)
    assert payload.get("scope_mode") == "cross-channel"
    assert payload.get("lock_mode") == "channel"
    note = final_meta.get("explainability_note", "")
    assert isinstance(note, str)
    assert "cross-channel" in note
    assert "lock:channel" in note

    runtime_state_mod.reset_context_lock("u-meta")
    runtime_state_mod._reset_channel_profile_store_for_tests()


@pytest.mark.asyncio
async def test_chat_stream_exposes_context_quality_metadata(monkeypatch, tmp_path):
    import sys

    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-quality.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()
    chat_module = sys.modules["llm.chat"]
    monkeypatch.setattr(chat_module, "LOCAL_LLM_ENABLED", False)

    history: list[dict] = []
    for i in range(55):
        text = (
            "Decision: keep thread-level context lock for deployment reports."
            if i == 11
            else f"Long thread discussion turn {i} with routine context text."
        )
        history.append({"role": "user" if i % 2 == 0 else "model", "parts": [text]})

    with runtime_state_mod.request_context(channel_id=333, thread_id=444, user_id="u-quality"):
        final_meta = None
        async for _, is_final, meta in chat_stream("hello", history=history, model_preference="local"):
            if is_final:
                final_meta = meta
                break

    assert isinstance(final_meta, dict)
    quality = final_meta.get("context_quality")
    assert isinstance(quality, dict)
    assert quality.get("compression_applied") is True
    assert isinstance(quality.get("compression_ratio"), float)
    assert quality.get("retained_key_facts_count", 0) >= 1


@pytest.mark.asyncio
async def test_chat_stream_structured_controls_override_legacy_flags(monkeypatch, tmp_path):
    import sys

    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-context-structured.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()
    chat_module = sys.modules["llm.chat"]
    monkeypatch.setattr(chat_module, "LOCAL_LLM_ENABLED", False)

    with runtime_state_mod.request_context(channel_id=333, thread_id=None, user_id="u-structured"):
        final_meta = None
        async for _, is_final, meta in chat_stream(
            "hello --cross-channel --anchor=legacy_1",
            model_preference="local",
            context_controls={
                "scope": "current",
                "reset_context": True,
                "anchor": "report_777",
            },
        ):
            if is_final:
                final_meta = meta
                break

    assert isinstance(final_meta, dict)
    payload = final_meta.get("explainability")
    assert isinstance(payload, dict)
    assert payload.get("scope_mode") == "channel"
    assert payload.get("anchor_id") == "report_777"
    assert final_meta.get("context_mode") == "context-reset"
