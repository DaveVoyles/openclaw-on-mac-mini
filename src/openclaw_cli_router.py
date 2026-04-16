"""
openclaw_cli_router — REPL routing and intent classification.

Pure logic module: no UI rendering, no global state.
Classifies user input into route decisions (edit, plan, grounded search, etc.)

Imports from:
  - openclaw_cli_sessions (SessionSummary, load_session, append_event)
  - openclaw_cli_ui_core (ANSI constants, for confidence badge / announcement)
Does NOT import from openclaw_cli.py.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

try:
    from openclaw_cli_sessions import SessionSummary, append_event, load_session
except ImportError:  # pragma: no cover
    SessionSummary = Any  # type: ignore[assignment,misc]

    def load_session(session_id: str) -> Any:  # type: ignore[misc]
        return None

    def append_event(*args: Any, **kwargs: Any) -> None:  # type: ignore[misc]
        pass


try:
    from openclaw_cli_ui_core import _BGR, _BYE, _CY, _DM, _R, _RE, _YE
except ImportError:  # pragma: no cover
    _R = _CY = _DM = _YE = _RE = _BGR = _BYE = ""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPL_ROUTE_AUTO_THRESHOLD = 0.74
REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT = 80
REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT = 72

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplRouteStepContext:
    """Resolved plan-step grounding used to route ambiguous REPL prompts."""

    num: int
    description: str
    status: str = ""


@dataclass(frozen=True)
class ReplRouteGrounding:
    """Active session context that can sharpen freeform REPL routing."""

    session_id: str = ""
    cwd: str = ""
    plan_id: str = ""
    plan_goal: str = ""
    task_id: str = ""
    task_title: str = ""
    task_status: str = ""
    task_description: str = ""
    current_step: ReplRouteStepContext | None = None
    plan: Any | None = None


class ReplRouteKind(str, Enum):
    """Supported route kinds for freeform REPL prompts."""

    CHAT = "chat"
    PLAN = "plan"
    ANALYZE = "analyze"
    RESEARCH = "research"
    WRITE = "write"
    EXEC = "exec"
    EDIT = "edit"


@dataclass(frozen=True)
class ReplPlanStep:
    """A single ordered step inside a multi-step plan candidate."""

    index: int
    kind: ReplRouteKind
    target_text: str
    args_text: str
    rationale: str


@dataclass(frozen=True)
class ReplRouteDecision:
    """Structured routing outcome for a freeform REPL prompt."""

    kind: ReplRouteKind
    confidence: float
    target_text: str
    args_text: str
    rationale: str
    steps: tuple[ReplPlanStep, ...] = ()

    def should_auto_route(self, *, threshold: float = REPL_ROUTE_AUTO_THRESHOLD) -> bool:
        """Return whether this decision is confident enough to auto-route."""
        return (
            self.kind not in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}
            and self.confidence >= threshold
            and bool(self.args_text.strip())
        )

    def should_auto_execute_plan(self, *, threshold: float = REPL_ROUTE_AUTO_THRESHOLD) -> bool:
        """Return whether this decision is a high-confidence multi-step plan."""
        return (
            self.kind == ReplRouteKind.PLAN
            and self.confidence >= threshold
            and len(self.steps) >= 2
        )

    def to_slash_command(self) -> str:
        """Render this decision as an equivalent slash command."""
        if self.kind in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
            return ""
        args = self.args_text.strip()
        return f"/{self.kind.value} {args}".strip()


# ---------------------------------------------------------------------------
# Regex / hint constants
# ---------------------------------------------------------------------------

_ROUTE_DOC_HINTS = (
    "doc",
    "docs",
    "documentation",
    "readme",
    "guide",
    "summary",
    "recap",
    "report",
    "memo",
    "notes",
    "markdown",
    "release notes",
)
_ROUTE_ANALYZE_HINTS = (
    "repo",
    "repository",
    "codebase",
    "file",
    "files",
    "module",
    "modules",
    "directory",
    "workspace",
    "project",
    "architecture",
    "flow",
    "implementation",
)
_ROUTE_SHELL_HINTS = (
    "git",
    "pytest",
    "python",
    "pip",
    "npm",
    "node",
    "go",
    "cargo",
    "make",
    "ls",
    "cat",
    "grep",
    "rg",
    "docker",
    "kubectl",
    "uv",
)
_ROUTE_ACTION_HINTS = (
    "analyze",
    "inspect",
    "review",
    "audit",
    "research",
    "investigate",
    "look up",
    "search",
    "look into",
    "take a look",
    "dig into",
    "write",
    "draft",
    "compose",
    "summarize",
    "summarise",
    "run",
    "execute",
    "command",
    "shell",
    "edit",
    "modify",
    "update",
    "change",
    "append",
    "replace",
    "tweak",
)
_PLAN_ROUTE_SPLIT_RE = re.compile(
    r"\s*(?:;|\band then\b|\bafter that\b|\bafterward(?:s)?\b|\bthen\b|\bfinally\b)\s*",
    re.IGNORECASE,
)
_PLAN_ROUTE_LEAD_RE = re.compile(
    r"^(?:and then|after that|afterward(?:s)?|then|finally|first|second|third|lastly)\b[\s,:-]*",
    re.IGNORECASE,
)
_EDIT_ROUTE_RE = re.compile(
    r"^(?P<verb>edit|modify|update|change|append(?:\s+to)?|tweak)\s+(?:the\s+)?(?:file\s+)?(?P<path>\S+)(?P<rest>.*)$",
    re.IGNORECASE,
)
_PLAN_CREATE_RESULT_RE = re.compile(r"Created plan `([^`]+)`")
_ROUTE_STEP_REF_RE = re.compile(
    r"\bstep\s+(?P<step>(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|eleventh|twelfth))\b",
    re.IGNORECASE,
)
_ROUTE_CURRENT_STEP_RE = re.compile(r"\b(?:the\s+)?current\s+step\b", re.IGNORECASE)
_ROUTE_CURRENT_TASK_RE = re.compile(r"\b(?:the\s+)?current\s+task\b", re.IGNORECASE)
_ROUTE_PROGRESS_PREFIXES = (
    "finish ",
    "complete ",
    "continue ",
    "resume ",
    "work on ",
    "keep working on ",
)
_ROUTE_STEP_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}

# ---------------------------------------------------------------------------
# Filesystem helpers (used by task-record loading for grounding)
# ---------------------------------------------------------------------------


def _candidate_workspace_roots(cwd: str | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    seeds = [str(cwd or "").strip(), str(Path.cwd())]
    for seed in seeds:
        if not seed:
            continue
        resolved = Path(seed).expanduser().resolve()
        for candidate in (resolved, *resolved.parents):
            marker = str(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            roots.append(candidate)
    return roots


def _resolve_local_source(path_text: str, *, cwd: str | None = None) -> Path:
    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        return candidate
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
    return (base / candidate).resolve()


def _find_local_tasks_file(*, cwd: str | None = None) -> Path | None:
    candidates: list[Path] = []
    env_path = str(os.getenv("MC_TASKS_FILE") or "").strip()
    if env_path:
        candidates.append(_resolve_local_source(env_path, cwd=cwd))
    candidates.extend(root / "data" / "tasks.json" for root in _candidate_workspace_roots(cwd))
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file():
            return candidate
    return None


def _load_task_record(task_id: str, *, cwd: str | None = None) -> dict[str, Any] | None:
    normalized = str(task_id or "").strip()
    if not normalized:
        return None
    tasks_file = _find_local_tasks_file(cwd=cwd)
    if tasks_file is None:
        return None
    try:
        payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        return None
    return next(
        (
            item
            for item in tasks
            if isinstance(item, dict) and str(item.get("id") or "").strip() == normalized
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Grounding helpers
# ---------------------------------------------------------------------------


def _load_route_plan(plan_id: str) -> Any | None:
    normalized = str(plan_id or "").strip()
    if not normalized:
        return None
    try:
        from agent_loop import load_plan as load_agent_plan
    except ImportError:
        return None
    try:
        return load_agent_plan(normalized)
    except Exception:  # broad: intentional
        return None


def _normalize_route_step_context(step: Any) -> ReplRouteStepContext | None:
    if step is None:
        return None
    try:
        num = int(getattr(step, "num", 0) or 0)
    except (TypeError, ValueError):
        num = 0
    description = str(getattr(step, "description", "") or "").strip()
    status = str(getattr(step, "status", "") or "").strip()
    if num <= 0 or not description:
        return None
    return ReplRouteStepContext(num=num, description=description, status=status)


def _active_plan_step(plan: Any | None) -> ReplRouteStepContext | None:
    steps = list(getattr(plan, "steps", []) or [])
    if not steps:
        return None
    for step in steps:
        if str(getattr(step, "status", "") or "").strip().lower() == "in-progress":
            return _normalize_route_step_context(step)
    for step in steps:
        if str(getattr(step, "status", "") or "").strip().lower() not in {"done", "failed", "skipped"}:
            return _normalize_route_step_context(step)
    return None


def _find_plan_step_context(plan: Any | None, step_num: int) -> ReplRouteStepContext | None:
    if plan is None or step_num <= 0:
        return None
    for step in list(getattr(plan, "steps", []) or []):
        try:
            current_num = int(getattr(step, "num", 0) or 0)
        except (TypeError, ValueError):
            current_num = 0
        if current_num == step_num:
            return _normalize_route_step_context(step)
    return None


def _load_repl_route_grounding(
    *,
    session_id: str = "",
    session: Any | None = None,
    validate_plan_fn: Callable[..., Any] | None = None,
) -> ReplRouteGrounding | None:
    resolved_session = session
    if resolved_session is None and session_id:
        resolved_session = load_session(session_id)
    if resolved_session is None:
        return None

    plan_id = str(resolved_session.plan_id or "").strip()
    task_id = str(resolved_session.task_id or "").strip()
    if not plan_id and not task_id:
        return None

    plan = _load_route_plan(plan_id) if plan_id else None
    plan_goal = str(getattr(plan, "goal", "") or "").strip()
    if not plan_goal and plan_id and validate_plan_fn is not None:
        plan_goal = str(validate_plan_fn(plan_id, cwd=resolved_session.cwd).summary or "").strip()

    task_record = _load_task_record(task_id, cwd=resolved_session.cwd) if task_id else None
    task_title = str((task_record or {}).get("title") or "").strip()
    task_status = str((task_record or {}).get("status") or "").strip()
    task_description = _normalize_prompt_text(
        " ".join(
            str((task_record or {}).get(f) or "").strip()
            for f in ("summary", "description", "notes")
            if str((task_record or {}).get(f) or "").strip()
        )
    )

    return ReplRouteGrounding(
        session_id=resolved_session.session_id,
        cwd=resolved_session.cwd,
        plan_id=plan_id,
        plan_goal=plan_goal,
        task_id=task_id,
        task_title=task_title,
        task_status=task_status,
        task_description=task_description,
        current_step=_active_plan_step(plan),
        plan=plan,
    )


# ---------------------------------------------------------------------------
# Core text normalisation helpers
# ---------------------------------------------------------------------------


def _normalize_prompt_text(prompt: str) -> str:
    return " ".join(str(prompt or "").strip().split())


def _clean_route_token(token: str) -> str:
    return str(token or "").strip().strip("`'\"()[]{}.,:;")


def _unwrap_route_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"`", "'", '"'}:
        return cleaned[1:-1].strip()
    return cleaned


def _normalize_route_field(text: str) -> str:
    cleaned = _normalize_prompt_text(text)
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {"`", "'", '"'}
        and cleaned.count(cleaned[0]) == 2
    ):
        return cleaned[1:-1].strip()
    return cleaned


def _extract_fenced_route_block(text: str) -> str:
    match = re.search(r"```(?:[a-z0-9_.+-]+)?\s*(.*?)```", str(text or ""), re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _normalize_prompt_text(match.group(1))


def _iter_route_quoted_segments(text: str) -> list[str]:
    raw = str(text or "")
    segments: list[str] = []
    patterns = (
        r"```(?:[a-z0-9_.+-]+)?\s*(.*?)```",
        r"`([^`]+)`",
        r'"([^"]+)"',
        r"'([^']+)'",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, raw, re.IGNORECASE | re.DOTALL):
            candidate = _normalize_prompt_text(match.group(1))
            if candidate:
                segments.append(candidate)
    return segments


def _shell_split_route_tokens(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _first_shell_token(text: str) -> str:
    parts = _shell_split_route_tokens(text)
    return parts[0] if parts else _normalize_prompt_text(text)


def _shell_quote_route_arg(text: str) -> str:
    return shlex.quote(str(text or ""))


def _looks_like_path(token: str) -> bool:
    candidate = _clean_route_token(token)
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered in {"readme", "readme.md", "makefile", "dockerfile", "pyproject.toml", "package.json"}:
        return True
    return (
        "/" in candidate
        or "\\" in candidate
        or candidate.startswith(".")
        or candidate.startswith("~")
        or bool(re.search(r"\.[a-z0-9]{1,12}$", lowered))
    )


def _extract_first_path(prompt: str) -> str:
    for candidate in _iter_route_quoted_segments(prompt):
        if _looks_like_path(candidate):
            return candidate
    for token in _normalize_prompt_text(prompt).split():
        candidate = _clean_route_token(token)
        if _looks_like_path(candidate):
            return candidate
    return ""


def _strip_request_lead(text: str) -> str:
    stripped = _normalize_prompt_text(text)
    lowered = stripped.lower()
    for prefix in (
        "please ",
        "can you please ",
        "could you please ",
        "would you please ",
        "can you ",
        "could you ",
        "would you ",
    ):
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip()
    return stripped


def _extract_after_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    stripped = _strip_request_lead(text)
    lowered = stripped.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip()
    return ""


def _strip_route_prefixes(text: str, prefixes: tuple[str, ...]) -> str:
    candidate = _normalize_prompt_text(text).strip(" ,;:")
    lowered = candidate.lower()
    changed = True
    while candidate and changed:
        changed = False
        for prefix in prefixes:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix):].strip(" ,;:")
                lowered = candidate.lower()
                changed = True
                break
    return candidate


def _clean_route_fragment(text: str) -> str:
    candidate = _extract_fenced_route_block(text) or _normalize_prompt_text(text)
    candidate = candidate.strip(" ,;:")
    candidate = _normalize_route_field(candidate)
    return candidate.strip(" ,;:")


def _extract_route_quoted_content(text: str, *, exclude: tuple[str, ...] = ()) -> str:
    excluded = {_normalize_prompt_text(item) for item in exclude if item}
    for candidate in _iter_route_quoted_segments(text):
        if candidate not in excluded:
            return candidate
    return ""


def _find_route_path_span(text: str, path: str) -> tuple[int, int] | None:
    if not text or not path:
        return None
    for pattern in (
        rf"([`\"']){re.escape(path)}\1",
        re.escape(path),
    ):
        match = re.search(pattern, text)
        if match:
            return match.span()
    return None


def _extract_append_content(prompt: str, path: str) -> str:
    quoted = _extract_route_quoted_content(prompt, exclude=(path,))
    if quoted:
        return quoted
    span = _find_route_path_span(prompt, path)
    before = prompt[: span[0]] if span else prompt
    after = prompt[span[1]:] if span else ""

    after_candidate = _strip_route_prefixes(
        after,
        ("with ", "containing ", "content ", "contents ", "saying ", "that says ", "to say ", ":", "- ", "to "),
    )
    after_candidate = _clean_route_fragment(after_candidate)
    if after_candidate:
        return after_candidate

    before_candidate = _normalize_prompt_text(before)
    before_candidate = re.sub(r"^(?:append(?:\s+to)?|add)\b", "", before_candidate, flags=re.IGNORECASE).strip(" ,;:")
    before_candidate = re.sub(r"\bto\b$", "", before_candidate, flags=re.IGNORECASE).strip(" ,;:")
    return _clean_route_fragment(before_candidate)


def _extract_replace_values(prompt: str, path: str) -> tuple[str, str] | None:
    if not path:
        return None
    path_pattern = rf"(?:[`\"'])?{re.escape(path)}(?:[`\"'])?(?:[ ,;:.!?]+)?$"
    patterns = (
        rf"\breplace\s+(?P<old>.+?)\s+with\s+(?P<new>.+?)\s+(?:in|inside|within)\s+(?:the\s+)?(?:file\s+)?{path_pattern}",
        rf"\bchange\s+(?P<old>.+?)\s+to\s+(?P<new>.+?)\s+(?:in|inside|within)\s+(?:the\s+)?(?:file\s+)?{path_pattern}",
        rf"\b(?:edit|modify|update)\s+(?:the\s+)?(?:file\s+)?(?:[`\"'])?{re.escape(path)}(?:[`\"'])?\s+to\s+replace\s+(?P<old>.+?)\s+with\s+(?P<new>.+?)(?:[ ,;:.!?]+)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if not match:
            continue
        old = _clean_route_fragment(match.group("old"))
        new = _clean_route_fragment(match.group("new"))
        if old and new and old != new:
            return old, new
    return None


def _extract_structured_edit_route(prompt: str) -> tuple[str, str, float, str] | None:
    stripped = _strip_request_lead(prompt)
    lowered = stripped.lower()
    if not re.match(r"^(?:edit|modify|update|change|append(?:\s+to)?|tweak|replace)\b", lowered):
        return None
    path = _extract_first_path(stripped)
    if not path:
        return None

    replace_values = _extract_replace_values(stripped, path)
    if replace_values:
        old, new = replace_values
        args = f"{_shell_quote_route_arg(path)} --replace {_shell_quote_route_arg(old)} {_shell_quote_route_arg(new)}"
        return (
            args,
            path,
            0.95,
            "deterministic match for an explicit file replacement request",
        )

    if lowered.startswith("append"):
        content = _extract_append_content(stripped, path)
        if content:
            args = f"{_shell_quote_route_arg(path)} --append {_shell_quote_route_arg(content)}"
            return (
                args,
                path,
                0.95,
                "deterministic match for an explicit file append request",
            )
        return (
            _shell_quote_route_arg(path),
            path,
            0.68,
            "explicit file-append request matched a file target but inline content was ambiguous",
        )

    edit_match = _EDIT_ROUTE_RE.match(stripped)
    if edit_match:
        rest = edit_match.group("rest").strip()
        if not rest:
            return (
                _shell_quote_route_arg(path),
                path,
                0.96,
                "deterministic match for an explicit file-edit request",
            )
        return (
            _shell_quote_route_arg(path),
            path,
            0.68,
            "explicit file-edit request matched a file target but inline change details were ambiguous",
        )
    return None


def _extract_write_payload(prompt: str) -> tuple[str, str]:
    args = _extract_after_prefix(prompt, ("write ", "draft ", "compose ", "summarize ", "summarise ")) or _normalize_prompt_text(prompt)
    args = _clean_route_fragment(args) or _normalize_prompt_text(prompt)
    target = ""
    patterns = (
        r"\b(?:into|as|for)\s+(?P<target>.+)$",
        r"^(?P<target>.+?)\s+(?:about|from|using|based on|covering)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, args, re.IGNORECASE)
        if not match:
            continue
        candidate = _clean_route_fragment(match.group("target"))
        if candidate and any(hint in candidate.lower() for hint in _ROUTE_DOC_HINTS):
            target = candidate
            break
    if not target:
        match = re.match(
            r"^(?P<target>(?:(?:a|an|the)\s+)?(?:(?:short|brief|concise|detailed|weekly|daily|release|incident|status)\s+)*(?:release notes|summary|recap|report|memo|notes|readme|guide|documentation))\b",
            args,
            re.IGNORECASE,
        )
    if match:
        target = _clean_route_fragment(match.group("target"))
    return args, target


def _parse_route_step_number(raw_step: str) -> int | None:
    token = str(raw_step or "").strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return _ROUTE_STEP_WORDS.get(token)


def _remove_route_span(text: str, span: tuple[int, int] | None) -> str:
    if not span:
        return _normalize_prompt_text(text)
    return _normalize_prompt_text(f"{text[: span[0]]} {text[span[1] :]}")


# ---------------------------------------------------------------------------
# Routing decision builders
# ---------------------------------------------------------------------------


def _build_chat_route(prompt: str, rationale: str, *, confidence: float = 0.0) -> ReplRouteDecision:
    return ReplRouteDecision(
        kind=ReplRouteKind.CHAT,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        target_text="",
        args_text=_normalize_prompt_text(prompt),
        rationale=rationale,
    )


def _build_route_decision(
    kind: ReplRouteKind,
    *,
    args_text: str,
    target_text: str = "",
    confidence: float,
    rationale: str,
    steps: tuple[ReplPlanStep, ...] = (),
) -> ReplRouteDecision:
    normalized_args = (
        _normalize_prompt_text(args_text)
        if kind in {ReplRouteKind.EXEC, ReplRouteKind.EDIT}
        else _normalize_route_field(args_text)
    )
    return ReplRouteDecision(
        kind=kind,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        target_text=_normalize_route_field(target_text) or normalized_args,
        args_text=normalized_args,
        rationale=rationale,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Grounded routing
# ---------------------------------------------------------------------------


def _grounded_subject_route(subject_text: str) -> ReplRouteDecision | None:
    normalized = _clean_route_fragment(subject_text)
    if not normalized:
        return None
    deterministic = _deterministic_repl_route(normalized)
    if deterministic is not None and deterministic.kind != ReplRouteKind.PLAN:
        return deterministic
    classified = lightweight_classify_repl_prompt(normalized)
    if classified is not None and classified.kind not in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
        return classified
    if any(hint in normalized.lower() for hint in _ROUTE_DOC_HINTS):
        args, target = _extract_write_payload(f"draft {normalized}")
        return _build_route_decision(
            ReplRouteKind.WRITE,
            args_text=target or args or normalized,
            target_text=target or normalized,
            confidence=0.74,
            rationale="grounded subject matched an active document target",
        )
    if any(re.search(rf"\b{re.escape(token)}\b", normalized.lower()) for token in _ROUTE_SHELL_HINTS):
        args = _extract_exec_args(normalized)
        return _build_route_decision(
            ReplRouteKind.EXEC,
            args_text=args,
            target_text=_first_shell_token(args),
            confidence=0.72,
            rationale="grounded subject matched an active shell target",
        )
    path = _extract_first_path(normalized)
    if path:
        return _build_route_decision(
            ReplRouteKind.ANALYZE,
            args_text=normalized,
            target_text=path,
            confidence=0.68,
            rationale="grounded subject matched an active workspace target",
        )
    return None


def _grounding_intent(prompt: str) -> tuple[ReplRouteKind | None, bool]:
    normalized = _clean_route_fragment(prompt)
    lowered = normalized.lower()
    for pattern, kind in (
        (r"^(?:analyze|inspect|review|audit|take a look at|look into|dig into)\b", ReplRouteKind.ANALYZE),
        (r"^(?:research|investigate|look up|search for|find information on|gather sources on)\b", ReplRouteKind.RESEARCH),
        (r"^(?:write|draft|compose|summarize|summarise)\b", ReplRouteKind.WRITE),
        (r"^(?:run|execute|exec)\b", ReplRouteKind.EXEC),
        (r"^(?:edit|modify|update|change|append|replace|tweak)\b", ReplRouteKind.EDIT),
    ):
        if re.match(pattern, lowered):
            return kind, False
    return None, any(re.match(rf"^{re.escape(prefix.strip())}\b", lowered) for prefix in _ROUTE_PROGRESS_PREFIXES)


def _grounded_prompt_route(prompt: str, subject_text: str) -> ReplRouteDecision | None:
    subject_decision = _grounded_subject_route(subject_text)
    intent_kind, is_progress = _grounding_intent(prompt)
    if intent_kind in {ReplRouteKind.ANALYZE, ReplRouteKind.RESEARCH, ReplRouteKind.WRITE, ReplRouteKind.EXEC}:
        grounded_prompt = f"{intent_kind.value} {subject_text}".strip()
        decision = _grounded_subject_route(grounded_prompt)
        return decision or subject_decision
    if intent_kind == ReplRouteKind.EDIT:
        grounded_prompt = f"update {subject_text}".strip()
        decision = _grounded_subject_route(grounded_prompt)
        if subject_decision is not None and subject_decision.kind in {ReplRouteKind.WRITE, ReplRouteKind.EDIT}:
            return subject_decision if subject_decision.confidence >= getattr(decision, "confidence", 0.0) else decision
        return decision or subject_decision
    if is_progress:
        return subject_decision
    return subject_decision


def _apply_grounding_to_route(
    decision: ReplRouteDecision | None,
    *,
    label: str,
    detail: str,
    boost: float,
) -> ReplRouteDecision | None:
    if decision is None:
        return None
    detail_text = _truncate_repl_route_text(detail, limit=84)
    rationale = f"{decision.rationale}; grounded by {label}: {detail_text}".strip()
    target_text = decision.target_text
    if decision.kind in {ReplRouteKind.ANALYZE, ReplRouteKind.EDIT}:
        grounded_path = _extract_first_path(f"{decision.target_text} {decision.args_text}")
        if grounded_path:
            target_text = grounded_path
    return ReplRouteDecision(
        kind=decision.kind,
        confidence=round(min(0.93, decision.confidence + boost), 2),
        target_text=target_text,
        args_text=decision.args_text,
        rationale=rationale,
        steps=decision.steps,
    )


def _maybe_route_with_grounding(
    prompt: str,
    *,
    grounding: ReplRouteGrounding | None,
) -> ReplRouteDecision | None:
    if grounding is None:
        return None
    normalized = _normalize_prompt_text(prompt)

    step_match = _ROUTE_STEP_REF_RE.search(normalized)
    if step_match and grounding.plan is not None:
        step_num = _parse_route_step_number(step_match.group("step"))
        step = _find_plan_step_context(grounding.plan, step_num or 0)
        if step is not None:
            remainder = _remove_route_span(normalized, step_match.span())
            decision = _grounded_prompt_route(remainder, step.description)
            return _apply_grounding_to_route(
                decision,
                label=f"active plan {grounding.plan_id} step {step.num}",
                detail=step.description,
                boost=0.15,
            )

    current_step_match = _ROUTE_CURRENT_STEP_RE.search(normalized)
    if current_step_match and grounding.current_step is not None:
        remainder = _remove_route_span(normalized, current_step_match.span())
        decision = _grounded_prompt_route(remainder, grounding.current_step.description)
        return _apply_grounding_to_route(
            decision,
            label=f"current step in plan {grounding.plan_id or 'session'}",
            detail=grounding.current_step.description,
            boost=0.12,
        )

    current_task_match = _ROUTE_CURRENT_TASK_RE.search(normalized)
    subject_text = grounding.task_title or grounding.task_description
    if current_task_match and subject_text:
        remainder = _remove_route_span(normalized, current_task_match.span())
        decision = _grounded_prompt_route(remainder, subject_text)
        detail = subject_text
        if grounding.task_status:
            detail = f"{detail} ({grounding.task_status})"
        return _apply_grounding_to_route(
            decision,
            label=f"active task {grounding.task_id or 'session task'}",
            detail=detail,
            boost=0.08,
        )

    return None


# ---------------------------------------------------------------------------
# Plan route decomposition
# ---------------------------------------------------------------------------


def _clean_plan_clause(text: str) -> str:
    clause = _normalize_prompt_text(text).strip(" ,;:.?!")
    if not clause:
        return ""
    clause = _PLAN_ROUTE_LEAD_RE.sub("", clause).strip(" ,;:.?!")
    clause = _strip_request_lead(clause)
    return clause.strip(" ,;:.?!")


def _classify_repl_clause(clause: str) -> ReplRouteDecision | None:
    normalized = _clean_plan_clause(clause)
    if not normalized:
        return None
    deterministic = _deterministic_repl_route(normalized)
    if deterministic is not None and deterministic.kind != ReplRouteKind.PLAN:
        return deterministic
    if not _looks_action_like(normalized):
        return None
    classified = lightweight_classify_repl_prompt(normalized)
    if classified is None or classified.kind in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
        return None
    return classified


def _maybe_build_plan_route(prompt: str, *, min_confidence: float) -> ReplRouteDecision | None:
    normalized = _normalize_prompt_text(prompt)
    if not normalized:
        return None
    if not _PLAN_ROUTE_SPLIT_RE.search(normalized):
        return None

    clauses = [_clean_plan_clause(part) for part in _PLAN_ROUTE_SPLIT_RE.split(normalized)]
    clauses = [clause for clause in clauses if clause]
    if len(clauses) < 2:
        return None

    step_decisions: list[ReplRouteDecision] = []
    for clause in clauses:
        step_decision = _classify_repl_clause(clause)
        if step_decision is None:
            return None
        step_decisions.append(step_decision)

    step_confidences = [decision.confidence for decision in step_decisions]
    if min(step_confidences) < 0.58:
        return None

    unique_kinds = {decision.kind for decision in step_decisions}
    confidence = sum(step_confidences) / len(step_confidences)
    confidence += 0.08
    confidence += min(0.08, 0.04 * (len(step_decisions) - 1))
    if len(unique_kinds) > 1:
        confidence += 0.03
    confidence = round(min(0.97, confidence), 2)
    if confidence < min_confidence:
        return None

    steps = tuple(
        ReplPlanStep(
            index=index + 1,
            kind=decision.kind,
            target_text=decision.target_text,
            args_text=decision.args_text,
            rationale=decision.rationale,
        )
        for index, decision in enumerate(step_decisions)
    )
    rationale = "decomposition matched ordered action clauses via sequencing markers"
    return _build_route_decision(
        ReplRouteKind.PLAN,
        args_text=normalized,
        target_text=steps[0].target_text or steps[0].args_text,
        confidence=confidence,
        rationale=rationale,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Deterministic and lightweight classifiers
# ---------------------------------------------------------------------------


def _deterministic_repl_route(prompt: str) -> ReplRouteDecision | None:
    normalized = _normalize_prompt_text(prompt)
    if not normalized:
        return None

    exec_args = _extract_after_prefix(normalized, ("run ", "execute ", "exec "))
    if exec_args:
        exec_args = _extract_exec_args(normalized)
        return _build_route_decision(
            ReplRouteKind.EXEC,
            args_text=exec_args,
            target_text=_first_shell_token(exec_args),
            confidence=0.98,
            rationale="deterministic match for an explicit run/execute request",
        )

    structured_edit = _extract_structured_edit_route(normalized)
    if structured_edit is not None:
        edit_args, path, confidence, rationale = structured_edit
        return _build_route_decision(
            ReplRouteKind.EDIT,
            args_text=edit_args,
            target_text=path,
            confidence=confidence,
            rationale=rationale,
        )

    research_args = _extract_after_prefix(
        normalized,
        (
            "research ",
            "investigate ",
            "look up ",
            "search for ",
            "find information on ",
            "gather sources on ",
        ),
    )
    if research_args:
        return _build_route_decision(
            ReplRouteKind.RESEARCH,
            args_text=research_args,
            confidence=0.95,
            rationale="deterministic match for an explicit research request",
        )

    analyze_args = _extract_after_prefix(normalized, ("analyze ",))
    if analyze_args:
        return _build_route_decision(
            ReplRouteKind.ANALYZE,
            args_text=analyze_args,
            confidence=0.95,
            rationale="deterministic match for an explicit analyze request",
        )

    soft_analyze_args = _extract_after_prefix(normalized, ("inspect ", "review ", "audit "))
    if soft_analyze_args and (_extract_first_path(soft_analyze_args) or any(hint in soft_analyze_args.lower() for hint in _ROUTE_ANALYZE_HINTS)):
        return _build_route_decision(
            ReplRouteKind.ANALYZE,
            args_text=soft_analyze_args,
            confidence=0.88,
            rationale="deterministic match for a workspace inspection request",
        )

    write_args, write_target = _extract_write_payload(normalized)
    if write_args and any(hint in write_args.lower() for hint in _ROUTE_DOC_HINTS):
        return _build_route_decision(
            ReplRouteKind.WRITE,
            args_text=write_args,
            target_text=write_target,
            confidence=0.9,
            rationale="deterministic match for a document-writing request",
        )

    return None


def _looks_action_like(prompt: str) -> bool:
    lowered = _normalize_prompt_text(prompt).lower()
    return bool(_extract_first_path(lowered)) or any(hint in lowered for hint in _ROUTE_ACTION_HINTS)


def _extract_exec_args(prompt: str) -> str:
    raw = str(prompt or "").strip()
    fenced = _extract_fenced_route_block(raw)
    if fenced:
        return fenced
    normalized = _normalize_prompt_text(raw)
    inline = re.search(r"`([^`]+)`", normalized)
    if inline:
        return _normalize_prompt_text(inline.group(1))
    explicit = _extract_after_prefix(raw, ("run ", "execute ", "exec "))
    if explicit:
        fenced = _extract_fenced_route_block(explicit)
        if fenced:
            return fenced
        inline = re.search(r"`([^`]+)`", explicit)
        if inline:
            return _normalize_prompt_text(inline.group(1))
        return _normalize_prompt_text(explicit)
    words = normalized.split()
    for index, token in enumerate(words):
        cleaned = _clean_route_token(token).lower()
        if cleaned in _ROUTE_SHELL_HINTS:
            return " ".join(words[index:]).strip()
    return normalized


def _extract_route_payload(kind: ReplRouteKind, prompt: str) -> tuple[str, str]:
    normalized = _normalize_prompt_text(prompt)
    if kind == ReplRouteKind.EXEC:
        args = _extract_exec_args(normalized)
        return args, _first_shell_token(args)
    if kind == ReplRouteKind.EDIT:
        structured = _extract_structured_edit_route(normalized)
        if structured is not None:
            args, path, _confidence, _rationale = structured
            return args, path
        path = _extract_first_path(normalized)
        return (_shell_quote_route_arg(path), path) if path else (normalized, "")
    if kind == ReplRouteKind.ANALYZE:
        args = _extract_after_prefix(normalized, ("analyze ", "inspect ", "review ", "audit ", "take a look at ", "look into ", "dig into "))
        return (args or normalized, _extract_first_path(args or normalized))
    if kind == ReplRouteKind.RESEARCH:
        args = _extract_after_prefix(
            normalized,
            ("research ", "investigate ", "look up ", "search for ", "find information on ", "gather sources on "),
        )
        return (args or normalized, "")
    if kind == ReplRouteKind.WRITE:
        return _extract_write_payload(normalized)
    return normalized, ""


def lightweight_classify_repl_prompt(prompt: str) -> ReplRouteDecision | None:
    """Classify action-like prompts with lightweight keyword scoring."""
    normalized = _normalize_prompt_text(prompt)
    lowered = normalized.lower()
    if not normalized:
        return None

    scores: dict[ReplRouteKind, float] = {
        ReplRouteKind.ANALYZE: 0.0,
        ReplRouteKind.RESEARCH: 0.0,
        ReplRouteKind.WRITE: 0.0,
        ReplRouteKind.EXEC: 0.0,
        ReplRouteKind.EDIT: 0.0,
    }
    reasons: dict[ReplRouteKind, list[str]] = {kind: [] for kind in scores}

    def add(kind: ReplRouteKind, weight: float, reason: str) -> None:
        scores[kind] += weight
        reasons[kind].append(reason)

    for phrase, weight in (
        ("take a look", 0.5),
        ("look into", 0.45),
        ("dig into", 0.45),
        ("inspect", 0.4),
        ("review", 0.35),
        ("audit", 0.4),
        ("explain", 0.2),
    ):
        if phrase in lowered:
            add(ReplRouteKind.ANALYZE, weight, phrase)

    for phrase, weight in (
        ("research", 0.5),
        ("investigate", 0.4),
        ("look up", 0.38),
        ("search for", 0.35),
        ("sources", 0.2),
        ("compare", 0.18),
    ):
        if phrase in lowered:
            add(ReplRouteKind.RESEARCH, weight, phrase)

    for phrase, weight in (
        ("draft", 0.45),
        ("compose", 0.4),
        ("write", 0.25),
        ("summarize", 0.22),
        ("summarise", 0.22),
    ):
        if phrase in lowered:
            add(ReplRouteKind.WRITE, weight, phrase)

    for phrase, weight in (
        ("run", 0.25),
        ("execute", 0.4),
        ("shell", 0.25),
        ("command", 0.22),
        ("show me", 0.15),
    ):
        if phrase in lowered:
            add(ReplRouteKind.EXEC, weight, phrase)

    for phrase, weight in (
        ("edit", 0.45),
        ("modify", 0.38),
        ("update", 0.28),
        ("change", 0.22),
        ("append", 0.25),
        ("replace", 0.42),
        ("tweak", 0.3),
    ):
        if phrase in lowered:
            add(ReplRouteKind.EDIT, weight, phrase)

    if any(hint in lowered for hint in _ROUTE_ANALYZE_HINTS):
        add(ReplRouteKind.ANALYZE, 0.18, "workspace hint")
    if any(hint in lowered for hint in _ROUTE_DOC_HINTS):
        add(ReplRouteKind.WRITE, 0.22, "document hint")
    if any(re.search(rf"\b{re.escape(token)}\b", lowered) for token in _ROUTE_SHELL_HINTS):
        add(ReplRouteKind.EXEC, 0.2, "shell token")
    path = _extract_first_path(normalized)
    if path:
        add(ReplRouteKind.EDIT, 0.22, "path target")
        add(ReplRouteKind.ANALYZE, 0.18, "path target")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_kind, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < 0.58:
        return None

    confidence = best_score
    if best_score - runner_up < 0.12:
        confidence -= 0.12 - (best_score - runner_up)
    confidence = max(0.0, min(0.92, confidence))
    args_text, target_text = _extract_route_payload(best_kind, normalized)
    rationale_bits = ", ".join(reasons[best_kind][:3]) or "keyword scoring"
    return _build_route_decision(
        best_kind,
        args_text=args_text,
        target_text=target_text,
        confidence=confidence,
        rationale=f"lightweight classifier matched {rationale_bits}",
    )


def route_repl_prompt(
    prompt: str,
    *,
    classifier_func: Callable[[str], ReplRouteDecision | None] = lightweight_classify_repl_prompt,
    min_confidence: float = REPL_ROUTE_AUTO_THRESHOLD,
    session_id: str = "",
    session: Any | None = None,
    validate_plan_fn: Callable[..., Any] | None = None,
) -> ReplRouteDecision:
    """Decide how a freeform REPL prompt should be handled."""
    normalized = _normalize_prompt_text(prompt)
    if not normalized:
        return _build_chat_route(prompt, "empty prompt")

    grounded = _maybe_route_with_grounding(
        normalized,
        grounding=_load_repl_route_grounding(
            session_id=session_id,
            session=session,
            validate_plan_fn=validate_plan_fn,
        ),
    )
    if grounded is not None:
        return grounded

    decomposed = _maybe_build_plan_route(normalized, min_confidence=min_confidence)
    if decomposed is not None:
        return decomposed

    deterministic = _deterministic_repl_route(normalized)
    if deterministic is not None:
        return deterministic

    if not _looks_action_like(normalized):
        return _build_chat_route(normalized, "defaulting to chat for a conversational prompt")

    classified = classifier_func(normalized) if classifier_func is not None else None
    if classified is None:
        return _build_chat_route(normalized, "defaulting to chat because no confident action route was found")
    if classified.kind == ReplRouteKind.PLAN:
        if classified.confidence >= min_confidence and len(classified.steps) >= 2:
            return classified
        return _build_chat_route(
            normalized,
            f"{classified.rationale}; confidence {classified.confidence:.2f} below plan threshold {min_confidence:.2f}",
            confidence=classified.confidence,
        )
    if classified.should_auto_route(threshold=min_confidence):
        return classified
    return _build_chat_route(
        normalized,
        f"{classified.rationale}; confidence {classified.confidence:.2f} below auto-route threshold {min_confidence:.2f}",
        confidence=classified.confidence,
    )


# ---------------------------------------------------------------------------
# Route output helpers
# ---------------------------------------------------------------------------


def _truncate_repl_route_text(text: str, *, limit: int) -> str:
    compact = _normalize_prompt_text(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _session_auto_route_enabled(session_id: str) -> bool:
    if not session_id:
        return False
    session = load_session(session_id)
    if session is None:
        return False
    return bool(getattr(session, "repl_auto_route", True))


def _confidence_badge(confidence: float) -> str:
    """Return a color-coded ANSI badge string for the given confidence value."""
    if confidence >= 0.80:
        return f"{_BGR}[HIGH]{_R}"
    if confidence >= 0.50:
        return f"{_YE}[MED]{_R}"
    return f"{_RE}[LOW]{_R}"


def _format_route_announcement(decision: ReplRouteDecision) -> str:
    badge = _confidence_badge(decision.confidence)
    if decision.kind == ReplRouteKind.PLAN:
        step_summary = " → ".join(f"{step.index}:{step.kind.value}" for step in decision.steps)
        preview = _truncate_repl_route_text(step_summary, limit=REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT)
        rationale = _truncate_repl_route_text(
            decision.rationale,
            limit=REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT,
        )
        return (
            f"{_BYE}⚡ plan{_R} {_YE}{len(decision.steps)} steps ({preview}){_R}  "
            f"{badge} {_DM}{decision.confidence:.2f} · {rationale}{_R}"
        )
    slash_command = _truncate_repl_route_text(
        decision.to_slash_command(),
        limit=REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT,
    )
    rationale = _truncate_repl_route_text(
        decision.rationale,
        limit=REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT,
    )
    return (
        f"{_BYE}⚡ auto-route{_R} {_CY}→ {slash_command}{_R}  "
        f"{badge} {_DM}{decision.confidence:.2f} · {rationale}{_R}"
    )


def _append_repl_route_event(session_id: str, prompt: str, decision: ReplRouteDecision) -> None:
    slash_command = decision.to_slash_command()
    summary_prefix = (
        f"plan candidate with {len(decision.steps)} steps ({decision.confidence:.2f})"
        if decision.kind == ReplRouteKind.PLAN
        else f"auto-routed to {slash_command} ({decision.confidence:.2f})"
    )
    append_event(
        session_id,
        kind="route",
        content=prompt,
        metadata={
            "summary": _truncate_repl_route_text(
                summary_prefix,
                limit=90,
            ),
            "source": "repl.autoroute",
            "route_kind": decision.kind.value,
            "slash_command": slash_command,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "target_text": decision.target_text,
            "args_text": decision.args_text,
            "steps": [
                {
                    "index": step.index,
                    "kind": step.kind.value,
                    "target_text": step.target_text,
                    "args_text": step.args_text,
                    "rationale": step.rationale,
                }
                for step in decision.steps
            ],
        },
    )


# ---------------------------------------------------------------------------
# Plan step helpers
# ---------------------------------------------------------------------------


def _plan_step_slash_command(step: ReplPlanStep) -> str:
    if step.kind in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
        return ""
    args = step.args_text.strip()
    return f"/{step.kind.value} {args}".strip()


def _extract_created_plan_id(create_result: str) -> str:
    match = _PLAN_CREATE_RESULT_RE.search(str(create_result or ""))
    return match.group(1).strip() if match else ""
