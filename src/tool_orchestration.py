"""
Provider-agnostic tool orchestration contracts and adapters.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from google import genai

from llm_ratelimit import rate_limiter as _rate_limiter
from trace_context import get_trace_id

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    name: str
    args: dict[str, Any]
    result: str


class DirectTextResponse:
    """Synthetic response used when a tool result should be returned unchanged."""

    def __init__(self, text: str):
        self.text = text
        self.direct_final_text = text
        self.candidates: list[Any] = []


@dataclass(frozen=True, slots=True)
class ToolProviderContext:
    provider: str
    model_name: str
    adapter: "ToolProviderAdapter"
    session: Any


class ToolProviderAdapter(Protocol):
    """Provider-specific adapter for tool-capable orchestration."""

    def create_session(self, *, model: Any, history: list[dict]) -> Any:
        ...

    def extract_tool_calls(self, response: Any) -> list[ToolCallRequest]:
        ...

    def latest_user_query(self, session: Any) -> str:
        ...

    def build_tool_result_message(self, tool_results: list[ToolCallResult]) -> Any:
        ...

    async def send_tool_result_message(self, session: Any, message: Any) -> Any:
        ...

    def build_direct_text_response(self, text: str) -> Any:
        ...

    def extract_final_text(
        self,
        response: Any,
        rounds: int,
        session: Any,
        *,
        max_rounds: int,
    ) -> str:
        ...

    def extract_history(self, session: Any) -> list[dict]:
        ...

    def merge_direct_final_history(
        self,
        history: list[dict],
        text: str,
    ) -> list[dict]:
        ...


def _backfill_tool_query(
    tool_call: ToolCallRequest,
    latest_user_query: str,
) -> ToolCallRequest:
    if (
        tool_call.name == "generate_sports_watch_report"
        and not str(tool_call.args.get("query") or "").strip()
    ):
        return ToolCallRequest(
            name=tool_call.name,
            args={**tool_call.args, "query": latest_user_query},
        )
    return tool_call


def _latest_user_query_from_gemini(session: Any) -> str:
    """Best-effort recovery of the last plain-English user ask from Gemini history."""
    try:
        history = list(session.get_history())
    except (AttributeError, TypeError):
        return ""

    for content in reversed(history):
        if getattr(content, "role", "") != "user":
            continue
        parts = getattr(content, "parts", None) or []
        text = "".join(
            getattr(part, "text", "")
            for part in parts
            if getattr(part, "text", "")
        ).strip()
        if not text:
            continue
        question_match = re.search(r"User's question:\s*(.+)$", text, re.DOTALL)
        if question_match:
            return question_match.group(1).strip()
        if "Routing hints inferred from the user's wording:" in text:
            segments = [segment.strip() for segment in text.split("\n\n") if segment.strip()]
            if segments:
                return segments[-1]
        return text
    return ""


def _to_gemini_content(msg: dict) -> dict[str, Any]:
    parts = []
    for part in msg.get("parts", []):
        if isinstance(part, str):
            parts.append({"text": part})
        elif isinstance(part, dict):
            parts.append(part)
        else:
            parts.append({"text": str(part)})
    return {"role": msg["role"], "parts": parts}


class GeminiToolAdapter:
    """Gemini-native implementation of the tool orchestration adapter contract."""

    def create_session(self, *, model: Any, history: list[dict]) -> Any:
        from llm_client import _client

        gemini_history = [_to_gemini_content(msg) for msg in history]
        return _client.chats.create(
            model=model.model_name,
            config=model.config,
            history=gemini_history,
        )

    def extract_tool_calls(self, response: Any) -> list[ToolCallRequest]:
        try:
            all_parts = response.candidates[0].content.parts
        except (IndexError, AttributeError, TypeError):
            return []

        if all_parts is None:
            return []

        return [
            ToolCallRequest(
                name=part.function_call.name,
                args=dict(part.function_call.args) if part.function_call.args else {},
            )
            for part in all_parts
            if hasattr(part, "function_call") and part.function_call and part.function_call.name
        ]

    def latest_user_query(self, session: Any) -> str:
        return _latest_user_query_from_gemini(session)

    def build_tool_result_message(self, tool_results: list[ToolCallResult]) -> list[Any]:
        return [
            genai.types.Part(
                function_response=genai.types.FunctionResponse(
                    name=tool_result.name,
                    response={"result": tool_result.result},
                )
            )
            for tool_result in tool_results
        ]

    async def send_tool_result_message(self, session: Any, message: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda parts=message: session.send_message(parts),
        )

    def build_direct_text_response(self, text: str) -> DirectTextResponse:
        return DirectTextResponse(text)

    def extract_final_text(
        self,
        response: Any,
        rounds: int,
        session: Any,
        *,
        max_rounds: int,
    ) -> str:
        try:
            text = response.text or ""
        except (AttributeError, ValueError):
            try:
                candidates = getattr(response, "candidates", None) or []
                first_candidate = candidates[0] if candidates else None
                content = getattr(first_candidate, "content", None)
                parts = getattr(content, "parts", None) or []
                text = "".join(part.text for part in parts if hasattr(part, "text") and part.text)
            except (AttributeError, TypeError, IndexError) as exc:
                log.debug("Response text extraction fallback failed: %s", exc)
                text = ""

            if not text and rounds >= max_rounds:
                log.info("Tool round limit hit with no synthesis — requesting forced summary")
                try:
                    _rate_limiter.record()
                    synthesis_response = session.send_message(
                        "You have reached the maximum number of tool calls. "
                        "Please synthesize everything you have gathered so far "
                        "into a final, helpful answer for the user. "
                        "Do not call any more tools."
                    )
                    text = synthesis_response.text
                except Exception as exc:  # broad: intentional
                    log.error("Forced synthesis failed: %s", exc)

            if not text:
                text = "I processed your request but the model returned no text content."
                if hasattr(response, "prompt_feedback") and response.prompt_feedback:
                    text += f" (Safety/Blocked: {response.prompt_feedback})"

        if rounds >= max_rounds:
            text += f"\n\n⚠️ *Tool call limit reached ({max_rounds}) — some sources may not have been checked.*"
        return text

    def extract_history(self, session: Any) -> list[dict]:
        history = []
        for content in session.get_history():
            parts = []
            for part in (content.parts or []):
                if hasattr(part, "text") and part.text:
                    parts.append(part.text)
                elif hasattr(part, "function_call") and part.function_call and part.function_call.name:
                    parts.append(f"[Called {part.function_call.name}]")
                elif hasattr(part, "function_response") and part.function_response and part.function_response.name:
                    parts.append(f"[Result from {part.function_response.name}]")
            if parts:
                history.append({"role": content.role, "parts": parts})
        return history

    def merge_direct_final_history(
        self,
        history: list[dict],
        text: str,
    ) -> list[dict]:
        if not text:
            return list(history)

        merged_history = list(history)
        final_message = {"role": "model", "parts": [text]}

        if merged_history and merged_history[-1].get("role") == "model":
            merged_history[-1] = final_message
            return merged_history

        merged_history.append(final_message)
        return merged_history


def build_tool_provider_context(
    provider: str,
    *,
    model: Any,
    history: list[dict],
) -> ToolProviderContext:
    normalized = (provider or "").strip().lower()
    if normalized != "gemini":
        raise ValueError(f"Unsupported tool provider: {provider}")

    adapter = GeminiToolAdapter()
    session = adapter.create_session(model=model, history=history)
    return ToolProviderContext(
        provider="gemini",
        model_name=str(getattr(model, "model_name", "unknown")),
        adapter=adapter,
        session=session,
    )


class ToolOrchestrator:
    """Shared provider-neutral tool loop driven by a provider adapter."""

    def __init__(
        self,
        *,
        adapter: ToolProviderAdapter,
        execute_tool_call: Callable[[str, dict[str, Any]], Awaitable[str]],
        rate_limiter: Any,
        record_usage: Callable[[Any], Awaitable[None]],
        should_return_tool_result_directly: Callable[[str, str], bool],
    ):
        self._adapter = adapter
        self._execute_tool_call = execute_tool_call
        self._rate_limiter = rate_limiter
        self._record_usage = record_usage
        self._should_return_tool_result_directly = should_return_tool_result_directly

    async def run(
        self,
        session: Any,
        response: Any,
        *,
        max_rounds: int,
        on_tool_call: Any | None = None,
        parallel: bool = True,
        label: str = "LLM",
        notification_callback: Any | None = None,
    ) -> tuple[Any, int]:
        """Run the tool loop.

        Args:
            notification_callback: Optional async callable ``(message: str) -> None``.
                When provided, called with human-readable streaming notifications
                before and after each tool execution (W12-2).
        """
        rounds = 0

        while rounds < max_rounds:
            tool_calls = self._adapter.extract_tool_calls(response)
            if not tool_calls:
                break

            if not parallel:
                tool_calls = tool_calls[:1]

            latest_user_query = self._adapter.latest_user_query(session)
            if latest_user_query:
                tool_calls = [
                    _backfill_tool_query(tool_call, latest_user_query)
                    for tool_call in tool_calls
                ]

            log.info(
                "%s function call(s) [round %d] trace=%s: %s",
                label,
                rounds + 1,
                get_trace_id(),
                ", ".join(f"{tool.name}({tool.args})" for tool in tool_calls),
            )

            if on_tool_call:
                for tool_call in tool_calls:
                    try:
                        await on_tool_call(tool_call.name, rounds + 1, args=tool_call.args)
                    except Exception as exc:  # broad: intentional
                        log.debug("on_tool_call callback failed: %s", exc)

            # W12-2: emit pre-call streaming notifications
            if notification_callback:
                for tool_call in tool_calls:
                    try:
                        await notification_callback(f"🔧 Calling `{tool_call.name}`…")
                    except Exception as exc:  # broad: intentional
                        log.debug("notification_callback pre-call failed: %s", exc)

            results = await asyncio.gather(*[
                self._execute_tool_call(tool_call.name, tool_call.args)
                for tool_call in tool_calls
            ])

            if on_tool_call:
                for tool_call, result in zip(tool_calls, results):
                    try:
                        await on_tool_call(tool_call.name, rounds + 1, result_preview=result[:200])
                    except Exception as exc:  # broad: intentional
                        log.debug("on_tool_call result callback failed: %s", exc)

            # W12-2: emit post-call streaming notifications
            if notification_callback:
                for tool_call in tool_calls:
                    try:
                        await notification_callback(f"✅ Got results from `{tool_call.name}`")
                    except Exception as exc:  # broad: intentional
                        log.debug("notification_callback post-call failed: %s", exc)

            self._rate_limiter.record()

            if len(tool_calls) == 1:
                direct_call = tool_calls[0]
                direct_result = results[0]
                if self._should_return_tool_result_directly(direct_call.name, direct_result):
                    log.info(
                        "%s returning direct tool result for %s without provider rewrite",
                        label,
                        direct_call.name,
                    )
                    return self._adapter.build_direct_text_response(direct_result), rounds + 1

            if not self._rate_limiter.check():
                return response, rounds + 1

            tool_results = [
                ToolCallResult(name=tool_call.name, args=tool_call.args, result=result)
                for tool_call, result in zip(tool_calls, results)
            ]
            outbound_message = self._adapter.build_tool_result_message(tool_results)
            response = await self._adapter.send_tool_result_message(session, outbound_message)
            await self._record_usage(response)
            rounds += 1

        return response, rounds
