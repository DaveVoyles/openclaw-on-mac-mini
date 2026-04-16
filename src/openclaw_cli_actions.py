"""Execution, file-editing, and approval helpers for the OpenClaw CLI."""

from __future__ import annotations

import difflib
import os
import shlex
import sys
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from subprocess_utils import run as run_subprocess

try:
    from approval_models import RiskLevel
except ImportError:
    class RiskLevel(Enum):
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        CRITICAL = "CRITICAL"


try:
    from approval_store import approval_store as approval_store
except ImportError:
    @dataclass
    class _FallbackApprovalRequest:
        request_id: str
        action: str
        target: str
        risk_level: RiskLevel
        requester_id: int
        requester_name: str
        channel_id: int
        detail: str = ""
        resolved: bool = False
        approved: bool = False
        resolver_id: int | None = None
        resolver_name: str | None = None
        session_id: str = ""
        plan_id: str = ""
        task_id: str = ""

    class _FallbackApprovalStore:
        def __init__(self) -> None:
            self._requests: dict[str, _FallbackApprovalRequest] = {}

        def create(self, **payload: Any) -> _FallbackApprovalRequest:
            request = _FallbackApprovalRequest(request_id=uuid.uuid4().hex[:12], **payload)
            self._requests[request.request_id] = request
            return request

        def resolve(
            self,
            *,
            request_id: str,
            approved: bool,
            resolver_id: int,
            resolver_name: str,
        ) -> _FallbackApprovalRequest | None:
            request = self._requests.get(request_id)
            if request is None:
                return None
            request.resolved = True
            request.approved = approved
            request.resolver_id = resolver_id
            request.resolver_name = resolver_name
            return request

    approval_store = _FallbackApprovalStore()


def atomic_write(path: Path, data: str) -> None:
    """Write text atomically without requiring the wider OpenClaw package tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


@dataclass
class ShellCommandResult:
    """Structured result from a CLI shell execution."""

    command: str
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class FileEditResult:
    """Structured result from a CLI file edit."""

    path: str
    changed: bool
    diff: str
    summary: str


def normalize_cwd(cwd: str | os.PathLike[str] | None = None) -> Path:
    """Resolve a working directory for shell and file operations."""
    target = Path(cwd or Path.cwd()).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Working directory does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"Working directory is not a directory: {target}")
    return target


def infer_command_risk(command_parts: list[str]) -> RiskLevel:
    """Best-effort command risk classification for CLI approvals."""
    normalized = " ".join(command_parts).lower().strip()
    first = str(command_parts[0] or "").lower() if command_parts else ""
    if first == "rm" or any(token in normalized for token in ("mkfs", "shutdown", "reboot", "diskutil erase", "git reset --hard")):
        return RiskLevel.CRITICAL
    if first in {"docker", "brew", "chmod", "chown", "kill"} or any(token in normalized for token in ("pip install", "npm install", "git checkout", "git clean")):
        return RiskLevel.HIGH
    if any(token in normalized for token in ("python", "pytest", "make", "git status", "git diff", "ls", "cat", "rg", "grep")):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def infer_file_edit_risk(path: str | os.PathLike[str]) -> RiskLevel:
    """Best-effort file-edit risk classification for CLI approvals."""
    normalized = str(path).lower()
    if any(token in normalized for token in (".env", ".pem", ".key", "secrets", "id_rsa")):
        return RiskLevel.CRITICAL
    if any(token in normalized for token in ("docker-compose", "compose.yaml", ".github/workflows", "package.json", "pyproject.toml", "makefile", "requirements")):
        return RiskLevel.HIGH
    return RiskLevel.MEDIUM


def risk_level_from_name(raw_value: str | None, *, default: RiskLevel) -> RiskLevel:
    """Parse a CLI risk string into a ``RiskLevel``."""
    value = str(raw_value or "").strip().upper()
    if not value:
        return default
    try:
        return RiskLevel[value]
    except KeyError as exc:
        raise ValueError(f"Unknown risk level: {raw_value}") from exc


def _print_cli_approval_review_block(
    *,
    dim: str,
    reset: str,
    review_lines: list[str],
    trust_note: str,
    recovery_hint: str,
) -> None:
    for line in review_lines:
        print(f"  {dim}{line}{reset}")
    if trust_note:
        print(f"  {dim}Trust cue: {trust_note}{reset}")
    if recovery_hint:
        print(f"  {dim}Recovery cue: {recovery_hint}{reset}")


def _approval_overlay_available() -> bool:
    """Return True when the approval review overlay can safely prompt."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def _approval_overlay_query_score(text: str, query: str) -> int:
    """Return a small fuzzy score for approval overlay filtering."""
    haystack = " ".join(str(text or "").lower().split())
    needle = " ".join(str(query or "").lower().split())
    if not needle:
        return 1
    if needle in haystack:
        return 1000 - max(0, haystack.find(needle))
    tokens = [token for token in needle.split(" ") if token]
    if tokens and all(token in haystack for token in tokens):
        return 700 - sum(max(0, haystack.find(token)) for token in tokens)
    pos = -1
    score = 0
    for ch in needle:
        next_pos = haystack.find(ch, pos + 1)
        if next_pos == -1:
            return 0
        score += max(1, 20 - min(19, next_pos - pos))
        pos = next_pos
    return score


def _filter_approval_overlay_items(
    items: list[dict[str, Any]],
    *,
    query: str,
    limit: int = 9,
) -> list[dict[str, Any]]:
    """Return the best approval overlay matches for a query."""
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        score = _approval_overlay_query_score(
            f"{item.get('label', '')} {item.get('detail', '')}",
            query,
        )
        if score > 0:
            scored.append((score, index, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in scored[:limit]]


def _build_cli_approval_overlay_items(
    *,
    review_lines: list[str],
    trust_note: str,
    recovery_hint: str,
    review_callback: Any,
) -> list[dict[str, Any]]:
    """Build approval review overlay entries from the current review context."""
    items: list[dict[str, Any]] = []
    if callable(review_callback):
        items.append(
            {
                "label": "Replay exact queued preview",
                "detail": "Reprint the full queued preview/diff before deciding.",
                "action": review_callback,
            }
        )
    if review_lines:
        items.append({"label": "Review summary", "detail": "\n".join(review_lines)})
        for index, line in enumerate(review_lines, start=1):
            items.append({"label": f"Review line {index}", "detail": line})
    if trust_note:
        items.append({"label": "Trust cue", "detail": trust_note})
    if recovery_hint:
        items.append({"label": "Recovery cue", "detail": recovery_hint})
    return items


def _run_cli_approval_review_overlay(
    *,
    review_lines: list[str],
    trust_note: str,
    recovery_hint: str,
    input_func: Any = input,
    review_callback: Any = None,
) -> None:
    """Run a lightweight searchable approval-review surface."""
    items = _build_cli_approval_overlay_items(
        review_lines=review_lines,
        trust_note=trust_note,
        recovery_hint=recovery_hint,
        review_callback=review_callback,
    )
    if not items:
        return
    is_tty = sys.stdout.isatty()
    dim = "\033[2m" if is_tty else ""
    cyan = "\033[36m" if is_tty else ""
    reset = "\033[0m" if is_tty else ""
    query = ""
    while True:
        matches = _filter_approval_overlay_items(items, query=query)
        print("\nApproval review overlay")
        if query:
            print(f"  {dim}filter:{reset} {query}")
        if matches:
            for index, item in enumerate(matches, start=1):
                print(f"  {cyan}{index}.{reset} {item.get('label', '')}")
        else:
            print(f"  {dim}No matches for '{query}'.{reset}")
        print(f"  {dim}Type a search term, a number to inspect, or Enter to return to approval.{reset}")
        choice = str(input_func("overlay> ")).strip()
        if not choice or choice.lower() in {"q", "quit", "exit"}:
            print(f"  {dim}Overlay closed.{reset}")
            return
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(matches):
                selected = matches[selected_index]
                detail = str(selected.get("detail") or "").strip()
                if detail:
                    print(detail)
                action = selected.get("action")
                if callable(action):
                    action()
                continue
            print(f"  {dim}Selection out of range.{reset}")
            continue
        query = choice


def request_cli_approval(
    *,
    action: str,
    target: str,
    risk_level: RiskLevel,
    detail: str = "",
    review_lines: list[str] | None = None,
    trust_note: str = "",
    recovery_hint: str = "",
    auto_approve: bool = False,
    session_id: str = "",
    plan_id: str = "",
    task_id: str = "",
    input_func: Any = input,
    review_callback: Any = None,
) -> bool:
    """Apply the CLI approval policy and record decisions for dashboard visibility."""
    if risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}:
        return True

    request = approval_store.create(
        action=action,
        target=target,
        risk_level=risk_level,
        requester_id=0,
        requester_name="openclaw-cli",
        channel_id=0,
        detail=(detail or "")[:500],
        session_id=str(session_id or "").strip(),
        plan_id=str(plan_id or "").strip(),
        task_id=str(task_id or "").strip(),
    )
    if auto_approve:
        approval_store.resolve(
            request_id=request.request_id,
            approved=True,
            resolver_id=0,
            resolver_name="openclaw-cli --yes",
        )
        return True

    if not sys.stdin.isatty():
        approval_store.resolve(
            request_id=request.request_id,
            approved=False,
            resolver_id=0,
            resolver_name="openclaw-cli non-interactive",
        )
        return False

    _is_tty = sys.stdout.isatty()
    _bold_red = "[1;31m" if _is_tty else ""
    _bold_yellow = "[1;33m" if _is_tty else ""
    _dim = "[2m" if _is_tty else ""
    _reset = "[0m" if _is_tty else ""
    risk_val = risk_level.value.upper() if hasattr(risk_level, "value") else str(risk_level).upper()
    if "CRITICAL" in risk_val:
        risk_colored = f"{_bold_red}{risk_val}{_reset}"
        prefix = "⚠️  "
    else:
        risk_colored = f"{_bold_yellow}{risk_val}{_reset}"
        prefix = "⚠️  "
    if "HIGH" in risk_val or "CRITICAL" in risk_val:
        _rationale_line = "⚠️   High risk — this action modifies files or runs code that could have side effects"
    elif "MEDIUM" in risk_val:
        _rationale_line = "⚡  Medium risk — review the command before approving"
    else:
        _rationale_line = "✅  Low risk — limited scope, safe to approve"
    print(f"  {_dim}{_rationale_line}{_reset}")
    normalized_review = [str(line).strip() for line in (review_lines or []) if str(line).strip()]
    review_supported = bool(normalized_review or trust_note or recovery_hint or callable(review_callback))
    review_overlay_supported = review_supported and _approval_overlay_available()
    if review_supported:
        _print_cli_approval_review_block(
            dim=_dim,
            reset=_reset,
            review_lines=normalized_review,
            trust_note=trust_note,
            recovery_hint=recovery_hint,
        )
    if review_overlay_supported:
        prompt_suffix = ' [y]es/[n]o/[r]eview/[o]verlay: '
    elif review_supported:
        prompt_suffix = ' [y]es/[n]o/[r]eview: '
    else:
        prompt_suffix = ' [y/N]: '
    prompt = (
        f"\n{prefix}{risk_colored} risk  {_dim}`{action}`{_reset}"
        f"  on  {_dim}`{target}`{_reset}"
        f"\n   Proceed?{prompt_suffix}"
    )
    while True:
        response = str(input_func(prompt)).strip().lower()
        if response in {"y", "yes"}:
            approved = True
            break
        if response in {"", "n", "no"}:
            approved = False
            break
        if review_supported and response in {"r", "review", "p", "preview"}:
            if callable(review_callback):
                review_callback()
            else:
                _print_cli_approval_review_block(
                    dim=_dim,
                    reset=_reset,
                    review_lines=normalized_review,
                    trust_note=trust_note,
                    recovery_hint=recovery_hint,
                )
            continue
        if review_overlay_supported and response in {"o", "overlay"}:
            _run_cli_approval_review_overlay(
                review_lines=normalized_review,
                trust_note=trust_note,
                recovery_hint=recovery_hint,
                input_func=input_func,
                review_callback=review_callback,
            )
            continue
        retry_hint = (
            'Enter y to approve, n to deny, r to review again, or o for the overlay.'
            if review_overlay_supported
            else (
                'Enter y to approve, n to deny, or r to review again.'
                if review_supported
                else 'Enter y to approve or n to deny.'
            )
        )
        print(f"  {_dim}{retry_hint}{_reset}")
    approval_store.resolve(
        request_id=request.request_id,
        approved=approved,
        resolver_id=0,
        resolver_name="openclaw-cli prompt",
    )
    return approved


async def run_shell_command(
    command_parts: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = 60,
) -> ShellCommandResult:
    """Run a CLI shell command inside the requested working directory."""
    resolved_cwd = normalize_cwd(cwd)
    returncode, stdout, stderr = await run_subprocess(command_parts, timeout=timeout, cwd=resolved_cwd)
    return ShellCommandResult(
        command=shlex.join(command_parts),
        cwd=str(resolved_cwd),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out="timed out" in stderr.lower(),
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def render_diff(path: Path, before: str, after: str) -> str:
    """Create a unified diff for a text edit."""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"{path}",
        tofile=f"{path}",
        lineterm="",
    )
    return "\n".join(diff)


def replace_text_in_file(
    path: str | os.PathLike[str],
    *,
    old: str,
    new: str,
    dry_run: bool = False,
) -> FileEditResult:
    """Replace text in a file using atomic writes and return a diff preview."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"File does not exist: {target}")
    if not target.is_file():
        raise IsADirectoryError(f"Expected a file path, got directory: {target}")

    before = _read_text(target)
    if old not in before:
        return FileEditResult(
            path=str(target),
            changed=False,
            diff="",
            summary=f"No changes made because `{old}` was not found.",
        )
    after = before.replace(old, new)
    diff = render_diff(target, before, after)
    if not dry_run:
        atomic_write(target, after)
    return FileEditResult(
        path=str(target),
        changed=True,
        diff=diff,
        summary="Updated file with requested replacement." if not dry_run else "Previewed file replacement.",
    )


def write_text_file(
    path: str | os.PathLike[str],
    *,
    content: str,
    append: bool = False,
    dry_run: bool = False,
) -> FileEditResult:
    """Write or append text to a file using atomic writes."""
    target = Path(path).expanduser().resolve()
    before = _read_text(target) if target.exists() and target.is_file() else ""
    after = before + content if append else content
    diff = render_diff(target, before, after)
    if not dry_run:
        atomic_write(target, after)
    summary = "Appended content to file." if append else "Wrote file content."
    if dry_run:
        summary = "Previewed file write."
    return FileEditResult(path=str(target), changed=(before != after), diff=diff, summary=summary)


def format_shell_result(result: ShellCommandResult) -> str:
    """Render a shell execution result for terminal output."""
    parts = [f"$ {result.command}", f"cwd: {result.cwd}", f"exit: {result.returncode}"]
    if result.stdout.strip():
        parts.append("\nstdout:\n" + result.stdout.rstrip())
    if result.stderr.strip():
        parts.append("\nstderr:\n" + result.stderr.rstrip())
    return "\n".join(parts).strip()


def preview_file_result(result: FileEditResult) -> str:
    """Render a text edit result for terminal output."""
    parts = [result.summary, f"path: {result.path}"]
    if result.diff:
        parts.append(result.diff)
    return "\n".join(parts).strip()
