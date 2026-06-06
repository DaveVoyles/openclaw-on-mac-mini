"""Phase 5 — Quick-action shortcuts that wrap common operations.

Each shortcut maps a `/host <subcommand> [args...]` invocation to a vetted
Copilot prompt. Shortcuts route through the same Phase 3 session machinery as
`/copilot`, so they inherit threaded replies, owner checks, idle timeouts, and
the per-user concurrency cap.

Pure data + a tiny dispatcher — no Slack imports, no I/O. Safe to unit test.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    """Static metadata for a `/host` subcommand."""

    name: str
    description: str
    # Either a prompt template (formatted with kwargs from parsed args)
    # or a callable that takes the parsed args list and returns the prompt.
    prompt_template: str
    # When True, the shortcut needs at least one positional argument.
    requires_arg: bool = False
    # Human-readable usage hint shown in help and on missing args.
    usage: str = ""


# ---------------------------------------------------------------------------
# Registry — keep prompts narrow, action-oriented, and machine-checkable.
# Each entry is what the user *would have typed* into `/copilot` themselves.
# ---------------------------------------------------------------------------
SHORTCUTS: dict[str, Shortcut] = {
    "status": Shortcut(
        name="status",
        description="Show host + Docker status (read-only)",
        prompt_template=(
            "Show a concise host status report for this Mac Mini: "
            "run `docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'`, "
            "`brew services list`, and top 5 processes by CPU and memory. "
            "Flag anything that looks unhealthy (restarting, high CPU, OOM). "
            "Read-only — do not change anything."
        ),
        usage="/host status",
    ),
    "logs": Shortcut(
        name="logs",
        description="Tail a container's logs and flag anomalies",
        prompt_template=(
            "Tail the last {n} lines of logs for the Docker container `{service}` "
            "via `docker logs --tail {n} {service} 2>&1`. "
            "Summarize what the service is doing right now and flag errors, "
            "warnings, repeated stack traces, or anomalous patterns. "
            "Read-only."
        ),
        requires_arg=True,
        usage="/host logs <service> [n=200]",
    ),
    "restart": Shortcut(
        name="restart",
        description="Restart a Docker container (or Plex native app)",
        prompt_template=(
            "Restart the service `{service}`. "
            "If `{service}` is `plex`, restart the native macOS Plex Media Server app "
            "via AppleScript (it is not a Docker container on this host). "
            "Otherwise run `docker restart {service}` and then verify it is healthy "
            "with `docker ps --filter name={service}`. Report the outcome."
        ),
        requires_arg=True,
        usage="/host restart <service>",
    ),
    "disk": Shortcut(
        name="disk",
        description="Disk usage report — flag anything filling up",
        prompt_template=(
            "Report disk usage on this Mac Mini: "
            "run `df -h`, then `du -sh ~/docker-stack/*` sorted largest first, "
            "and `du -sh ~/openclaw/data/* 2>/dev/null` if it exists. "
            "Flag any partition >85% full or any directory that has grown "
            "unexpectedly large. Read-only."
        ),
        usage="/host disk",
    ),
    "net": Shortcut(
        name="net",
        description="Network reachability checks",
        prompt_template=(
            "Run network reachability checks from this Mac Mini: "
            "ping the Synology NAS at 192.168.1.8 (3 packets), "
            "curl http://localhost/ via Traefik (expect HTTP response), "
            "and curl http://localhost:32400/identity (Plex web). "
            "Report each check pass/fail with latency where applicable. "
            "Read-only."
        ),
        usage="/host net",
    ),
    "plex-fix": Shortcut(
        name="plex-fix",
        description="Diagnose and resolve Plex media-not-found issues",
        prompt_template=(
            "Diagnose and resolve Plex 'media not found' or playback issues on this "
            "Mac Mini. Plex runs as a native macOS app (not Docker). "
            "Investigate: are the SMB/NFS media mounts from the NAS still attached? "
            "Are file paths Plex expects still resolvable? Is the Plex Media Server "
            "process running? Are recent scan errors visible in its logs at "
            "`~/Library/Logs/Plex Media Server/`? "
            "Fix any clearly-correctable issues (remount shares, restart the app) "
            "and report what you did, what's still broken, and what needs my input."
        ),
        usage="/host plex-fix",
    ),
    "git": Shortcut(
        name="git",
        description="Run a git command against ~/docker-stack",
        prompt_template=(
            "Run `git -C ~/docker-stack {args}` and show me the output verbatim. "
            "If the command would modify state (commit, push, reset, checkout, "
            "branch -D), confirm intent first by showing what would change."
        ),
        requires_arg=True,
        usage="/host git <args...>",
    ),
}


# Result types -------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedShortcut:
    """A successfully resolved shortcut ready to dispatch."""

    name: str
    prompt: str
    raw_text: str


@dataclass(frozen=True)
class ShortcutError:
    """Resolution failed — `message` is safe to show the user."""

    message: str


ResolveResult = ResolvedShortcut | ShortcutError


# Dispatch -----------------------------------------------------------------


def list_shortcuts() -> list[Shortcut]:
    """Stable-ordered list of all shortcuts."""
    return [SHORTCUTS[k] for k in sorted(SHORTCUTS)]


def help_text() -> str:
    """Multi-line Slack-friendly help for `/host` with no args."""
    lines = ["🤖 *`/host` — quick-action shortcuts*", ""]
    for sc in list_shortcuts():
        lines.append(f"• `{sc.usage}` — {sc.description}")
    lines.append("")
    lines.append(
        "_Each shortcut opens a Copilot thread (same as `/copilot`). "
        "Reply in-thread to follow up. `/copilot-end <id>` closes it._"
    )
    return "\n".join(lines)


def resolve(text: str) -> ResolveResult:
    """Parse `<subcommand> [args...]` and return the resolved prompt.

    Empty / `help` / `?` returns help text wrapped in a ShortcutError so the
    caller posts it ephemerally.
    """
    raw = (text or "").strip()
    if not raw or raw.lower() in {"help", "?", "-h", "--help"}:
        return ShortcutError(help_text())

    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        return ShortcutError(f"❌ couldn't parse arguments: `{exc}`")

    if not parts:
        return ShortcutError(help_text())

    name = parts[0].lower().lstrip("/")
    args = parts[1:]

    sc = SHORTCUTS.get(name)
    if sc is None:
        return ShortcutError(f"❌ unknown subcommand `{name}`.\n\n{help_text()}")

    if sc.requires_arg and not args:
        return ShortcutError(f"❌ `{sc.name}` requires arguments.\nUsage: `{sc.usage}`")

    try:
        prompt = _format_prompt(sc, args)
    except _FormatError as exc:
        return ShortcutError(str(exc))

    return ResolvedShortcut(name=sc.name, prompt=prompt, raw_text=raw)


# Internal -----------------------------------------------------------------


class _FormatError(ValueError):
    pass


def _format_prompt(sc: Shortcut, args: list[str]) -> str:
    """Bind args into the prompt template based on the shortcut shape."""
    if sc.name == "logs":
        service = args[0]
        n_str = args[1] if len(args) > 1 else "200"
        try:
            n = int(n_str)
        except ValueError as exc:
            raise _FormatError(f"❌ `n` must be an integer, got `{n_str}`") from exc
        n = max(1, min(n, 5000))
        return sc.prompt_template.format(service=_safe(service), n=n)

    if sc.name == "restart":
        return sc.prompt_template.format(service=_safe(args[0]))

    if sc.name == "git":
        joined = " ".join(_safe(a) for a in args)
        return sc.prompt_template.format(args=joined)

    # No-arg shortcuts — template is literal.
    return sc.prompt_template


def _safe(s: str) -> str:
    """Best-effort scrubbing of shell metacharacters in user-supplied args.

    The shortcut prompts are *prompts*, not shell commands — the LLM mediates
    execution — but stripping backticks/dollars/pipes keeps the prompt
    unambiguous and prevents a user from sneaking a literal command-substitution
    string past the model.
    """
    return "".join(ch for ch in s if ch not in "`$|;<>\n\r")
