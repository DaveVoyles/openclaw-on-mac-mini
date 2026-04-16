"""
OpenClaw Worker Agent — Sub-agent spawning for autonomous task delegation.

The main Gemini agent can call spawn_worker() to delegate a focused subtask to
a lightweight sub-agent that runs its own tool loop and returns the result.

Usage pattern:
  - User asks a complex question with multiple independent sub-tasks
  - Main agent calls spawn_worker(goal="...", context="...")
  - Worker gets a fresh Gemini session via the shared adapter/orchestrator path,
    runs tools through ToolOrchestrator, and returns a clean answer.
  - Main agent synthesizes the worker's result with its own reasoning.

This enables genuine parallel / delegated work within a single interaction.
Workers now route through build_tool_provider_context + ToolOrchestrator so they
automatically benefit from rate limiting, circuit-breaking, usage recording, and
future multi-provider tool support.
"""

import asyncio
import logging

log = logging.getLogger("openclaw.worker")

_WORKER_SYSTEM_PROMPT = (
    "You are a focused worker sub-agent for OpenClaw. You have been assigned a specific task.\n"
    "Rules:\n"
    "- Complete the task fully using whatever tools are needed.\n"
    "- Be thorough but concise in your final answer — return facts, not reasoning.\n"
    "- If a tool fails, try an alternative approach once, then report what you found.\n"
    "- Do NOT mention being a sub-agent or worker in your output.\n"
    "- Return only the final result text — no preamble."
)


async def spawn_worker(
    goal: str,
    context: str = "",
    max_rounds: int = 8,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Spawn a focused sub-agent to accomplish a specific goal autonomously.

    The worker uses the shared ToolOrchestrator path (build_tool_provider_context
    + _run_tool_loop) rather than a raw Gemini client, so it benefits from the
    same rate limiter, circuit breaker, usage recording, and adapter contract as
    the main chat path.

    Args:
        goal:       Clear description of what the worker should accomplish.
        context:    Optional background information or constraints.
        max_rounds: Maximum tool call rounds (default 8, capped at MAX_TOOL_ROUNDS).
        conversation_history: Optional recent conversation turns for context inheritance.

    Returns:
        The worker's synthesized result as a string.
    """
    from llm import (
        GOOGLE_API_KEY,
        MAX_TOKENS,
        MAX_TOOL_ROUNDS,
        MODEL_NAME,
        TEMPERATURE,
        _init_gemini_model,
        _rate_limiter,
        _record_usage,
    )
    from llm_tools import _run_tool_loop
    from tool_orchestration import build_tool_provider_context

    if not GOOGLE_API_KEY:
        return "❌ Worker: GOOGLE_API_KEY not set."

    if not _rate_limiter.check():
        return "❌ Worker: rate limit reached — try again in a moment."

    effective_rounds = min(max_rounds, MAX_TOOL_ROUNDS)

    # Build a worker-specific model config with its own system prompt and
    # a slightly lower temperature for more deterministic sub-task results.
    worker_model = _init_gemini_model(
        MODEL_NAME,
        temperature=max(0.1, TEMPERATURE - 0.2),
        max_tokens=MAX_TOKENS,
        with_tools=True,
        system_prompt=_WORKER_SYSTEM_PROMPT,
    )

    # Build the initial message, optionally injecting recent history for context.
    initial_message = goal
    if context:
        initial_message = f"Context: {context}\n\nTask: {goal}"

    if conversation_history:
        recent = conversation_history[-5:]
        history_lines = []
        for msg in recent:
            role = msg.get("role", "user")
            parts = msg.get("parts", [])
            text = " ".join(p for p in parts if isinstance(p, str))
            if text:
                history_lines.append(f"  {role}: {text[:200]}")
        if history_lines:
            history_summary = "\n".join(history_lines)
            initial_message = (
                f"Recent conversation context:\n{history_summary}\n\n{initial_message}"
            )

    log.info("Worker spawned for goal: %.80s…", goal)

    try:
        # Create a fresh, adapter-backed session (empty history — each worker is fresh).
        provider_context = build_tool_provider_context(
            "gemini",
            model=worker_model,
            history=[],
        )
        chat_session = provider_context.session

        # Send the initial message, then delegate the tool loop to ToolOrchestrator.
        loop = asyncio.get_running_loop()
        _rate_limiter.record()
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(initial_message)
        )
        await _record_usage(response)

        response, rounds = await _run_tool_loop(
            chat_session,
            response,
            max_rounds=effective_rounds,
            parallel=True,
            label="Worker",
        )

        result_text = provider_context.adapter.extract_final_text(
            response,
            rounds,
            chat_session,
            max_rounds=effective_rounds,
        )

        if not result_text:
            result_text = "Worker completed but returned no output."

        log.info("Worker finished (rounds=%d): %.80s…", rounds, result_text)
        return result_text

    except Exception as e:  # broad: intentional
        return f"❌ Worker failed: {e}"


WORKER_SKILLS = {
    "spawn_worker": spawn_worker,
}
