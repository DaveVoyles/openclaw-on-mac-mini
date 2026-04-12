"""Tests for worker_agent module — spawn_worker routing through ToolOrchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_provider_context(result_text: str = "Worker result: task completed successfully") -> MagicMock:
    """Build a fake ToolProviderContext whose adapter extracts a predetermined text."""
    response = MagicMock()
    response.direct_final_text = ""

    adapter = MagicMock()
    adapter.extract_final_text.return_value = result_text

    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.adapter = adapter
    return ctx, response


@pytest.fixture
def patched_worker_deps():
    """Patch the three key dependencies spawn_worker() calls into."""
    ctx, response = _make_provider_context()

    with (
        patch("llm.GOOGLE_API_KEY", "test-key"),
        patch("llm.MAX_TOKENS", 2048),
        patch("llm.MAX_TOOL_ROUNDS", 10),
        patch("llm.MODEL_NAME", "gemini-2.0-flash"),
        patch("llm.TEMPERATURE", 0.7),
        patch("llm._init_gemini_model", return_value=MagicMock()) as mock_init_model,
        patch("llm._rate_limiter") as mock_rl,
        patch("llm._record_usage", new_callable=AsyncMock),
        patch("tool_orchestration.build_tool_provider_context", return_value=ctx) as mock_ctx,
        patch("llm_tools._run_tool_loop", new_callable=AsyncMock, return_value=(response, 1)) as mock_loop,
    ):
        mock_rl.check.return_value = True
        mock_rl.record = MagicMock()
        yield {
            "ctx": ctx,
            "response": response,
            "mock_init_model": mock_init_model,
            "mock_rl": mock_rl,
            "mock_ctx": mock_ctx,
            "mock_loop": mock_loop,
        }


@pytest.mark.asyncio
async def test_spawn_worker_basic(patched_worker_deps):
    """Worker returns text result for simple goal."""
    from worker_agent import spawn_worker
    result = await spawn_worker("Test task", context="Test context")
    assert "Worker result" in result


@pytest.mark.asyncio
async def test_spawn_worker_no_api_key():
    """Worker fails gracefully without API key."""
    with patch("llm.GOOGLE_API_KEY", ""):
        from worker_agent import spawn_worker
        result = await spawn_worker("Test task")
        assert "GOOGLE_API_KEY" in result


@pytest.mark.asyncio
async def test_spawn_worker_rate_limited():
    """Worker fails gracefully when rate limited."""
    with patch("llm.GOOGLE_API_KEY", "test-key"), patch("llm._rate_limiter") as mock_rl:
        mock_rl.check.return_value = False
        from worker_agent import spawn_worker
        result = await spawn_worker("Test task")
        assert "rate limit" in result.lower()


@pytest.mark.asyncio
async def test_spawn_worker_uses_worker_system_prompt(patched_worker_deps):
    """Worker builds model with the worker system prompt, not the default one."""
    from worker_agent import _WORKER_SYSTEM_PROMPT, spawn_worker

    await spawn_worker("Do subtask X")

    mock_init_model = patched_worker_deps["mock_init_model"]
    call_kwargs = mock_init_model.call_args.kwargs
    assert call_kwargs.get("system_prompt") == _WORKER_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_spawn_worker_uses_reduced_temperature(patched_worker_deps):
    """Worker uses a slightly lower temperature for determinism."""
    with patch("llm.TEMPERATURE", 0.7):
        from worker_agent import spawn_worker

        await spawn_worker("Do subtask X")

        mock_init_model = patched_worker_deps["mock_init_model"]
        temp = mock_init_model.call_args.kwargs.get("temperature", 0.7)
        assert temp <= 0.5  # 0.7 - 0.2 = 0.5


@pytest.mark.asyncio
async def test_spawn_worker_delegates_to_tool_orchestrator(patched_worker_deps):
    """Worker delegates the tool loop to _run_tool_loop, not its own loop."""
    from worker_agent import spawn_worker

    await spawn_worker("Subtask: find scores")

    mock_loop = patched_worker_deps["mock_loop"]
    mock_loop.assert_called_once()
    # The session passed to _run_tool_loop should match the provider context's session
    args, kwargs = mock_loop.call_args
    assert args[0] is patched_worker_deps["ctx"].session


@pytest.mark.asyncio
async def test_spawn_worker_with_conversation_history(patched_worker_deps):
    """Worker injects recent conversation context into the initial message."""
    from worker_agent import spawn_worker

    history = [
        {"role": "user", "parts": ["What neighborhoods are good in Delco?"]},
        {"role": "model", "parts": ["Narberth, Havertown, and Ardmore are popular."]},
    ]

    ctx = patched_worker_deps["ctx"]
    initial_sent: list[str] = []

    original_send = ctx.session.send_message

    def capture_message(msg, *a, **kw):
        initial_sent.append(msg)
        return MagicMock()

    ctx.session.send_message = capture_message

    await spawn_worker("Find home prices in Narberth", conversation_history=history)

    assert initial_sent, "send_message was never called"
    sent_text = initial_sent[0]
    assert "conversation context" in sent_text.lower() or "Narberth" in sent_text


@pytest.mark.asyncio
async def test_spawn_worker_empty_result_fallback(patched_worker_deps):
    """Worker returns fallback message when adapter returns empty string."""
    patched_worker_deps["ctx"].adapter.extract_final_text.return_value = ""

    from worker_agent import spawn_worker
    result = await spawn_worker("Empty result task")
    assert result == "Worker completed but returned no output."


@pytest.mark.asyncio
async def test_spawn_worker_no_genai_import():
    """Worker module must not import google.genai directly anymore."""

    # Ensure the module is freshly inspected
    import worker_agent

    # If google.genai was imported at module level, it would appear in the
    # module's global namespace via `from google import genai` or `import google.genai`.
    assert "genai" not in vars(worker_agent), (
        "worker_agent should not import google.genai directly; "
        "it should route through build_tool_provider_context"
    )
