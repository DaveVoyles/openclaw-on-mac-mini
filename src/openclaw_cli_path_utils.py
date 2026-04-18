"""
openclaw_cli_path_utils — File path detection, link formatting, and follow-up suggestions.

Imports from: openclaw_cli_ui_core (ANSI constants, _get_is_tty)
Does NOT import from: openclaw_cli.py (avoids circular imports)
"""
from __future__ import annotations

import os
import re

try:
    from openclaw_cli_ui_core import _B, _BCY, _CY, _DM, _GR, _IT, _R, _UL, _get_is_tty
except ImportError:
    _B = _R = _CY = _DM = _GR = _IT = _UL = _BCY = ""

    def _get_is_tty() -> bool:  # type: ignore[misc]
        import sys
        return sys.stdout.isatty()

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(r'(https?://[^\s\)\]\>\"\']+)', re.IGNORECASE)

_FILE_PATH_PATTERN = re.compile(
    r'(?<!\w)((?:~|\.{1,2})?/[\w\-./]+\.\w{1,8}|(?:src|tests|docs|scripts|config|plugins)/[\w\-./]+\.\w{1,8})',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Pure utilities (no prefs dependency)
# ---------------------------------------------------------------------------


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
    """Return non-empty lines without duplicates, preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _detect_file_paths(text: str) -> list[str]:
    """Extract file path candidates from text. Excludes URL-like paths."""
    paths: list[str] = []
    for m in _FILE_PATH_PATTERN.finditer(text):
        p = m.group(1)
        if p.startswith("//"):
            continue
        if p not in paths:
            paths.append(p)
    return paths[:5]


# Action verbs that indicate the user wants the CLI to fetch and read a URL
_URL_ACTION_VERBS = re.compile(
    r'\b(summarize|summarise|read|explain|describe|analyze|analyse|check|review|'
    r'what does|what is|tell me about|show me|open|fetch|get|look at)\b',
    re.IGNORECASE,
)


def _detect_url_mentions(text: str) -> list[str]:
    """Return URLs that appear to be fetch targets based on nearby action verbs.

    Only returns URLs when the prompt also contains an action verb (summarize,
    read, explain, etc.) — this avoids auto-fetching incidental citation URLs.
    """
    if not _URL_ACTION_VERBS.search(text):
        return []
    urls: list[str] = []
    for m in _URL_PATTERN.finditer(text):
        url = m.group(1).rstrip(".,;:!?")
        if url not in urls:
            urls.append(url)
    return urls[:3]


# Matches @file:/path/to/file, @url:https://..., @dir:/path, @clip injection markers.
_EXPLICIT_REF_PATTERN = re.compile(
    r'@(file|url|dir):([^\s\)\]\>\"\']+)|@(clip)\b',
    re.IGNORECASE,
)


def _detect_explicit_refs(text: str) -> list[tuple[str, str]]:
    """Extract explicit @file:, @url:, @dir:, and @clip injection markers from text.

    Returns a list of (kind, target) tuples where kind is 'file', 'url', 'dir',
    or 'clip' (target is empty string for @clip). Always injected, no action verb needed.
    """
    refs: list[tuple[str, str]] = []
    for m in _EXPLICIT_REF_PATTERN.finditer(text):
        if m.group(3):  # @clip
            entry: tuple[str, str] = ("clip", "")
        else:
            kind = m.group(1).lower()
            target = m.group(2).rstrip(".,;:!?")
            entry = (kind, target)
        if entry not in refs:
            refs.append(entry)
    return refs[:5]


def _strip_explicit_refs(text: str) -> str:
    """Remove @file:, @url:, @dir:, and @clip markers from text (for clean display/send)."""
    return _EXPLICIT_REF_PATTERN.sub("", text).strip()


# ---------------------------------------------------------------------------
# @git: injection helpers
# ---------------------------------------------------------------------------

_GIT_REF_PATTERN = re.compile(
    r'@git:(staged|HEAD(?:~\d+)?|log|status|diff)',
    re.IGNORECASE,
)


def _detect_git_refs(text: str) -> list[tuple[str, str]]:
    """Extract @git:<variant> markers. Returns list of ('git', variant) tuples.

    Supported variants: staged, HEAD, HEAD~1, HEAD~2, log, status, diff
    """
    refs: list[tuple[str, str]] = []
    for m in _GIT_REF_PATTERN.finditer(text):
        variant = m.group(1)
        entry: tuple[str, str] = ("git", variant)
        if entry not in refs:
            refs.append(entry)
    return refs


def _resolve_git_ref(variant: str, cwd: str) -> str | None:
    """Run the git command for the given variant and return its output, or None on error."""
    import subprocess
    _CAP = 100_000
    variant_lower = variant.lower()
    try:
        if variant_lower == "staged":
            cmd = ["git", "diff", "--cached"]
        elif variant_lower.startswith("head~"):
            cmd = ["git", "show", variant]
        elif variant_lower == "head":
            cmd = ["git", "show", "HEAD"]
        elif variant_lower == "log":
            cmd = ["git", "log", "--oneline", "-20"]
        elif variant_lower == "status":
            cmd = ["git", "status", "--short"]
        elif variant_lower == "diff":
            cmd = ["git", "diff"]
        else:
            return None
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, cwd=cwd
        )
        if result.returncode != 0:
            return None
        output = result.stdout
        if variant_lower in ("staged", "head", "diff") or variant_lower.startswith("head~"):
            output = output[:_CAP]
        return output or None
    except Exception:
        return None


def _strip_git_refs(text: str) -> str:
    """Remove @git:... markers from text."""
    return _GIT_REF_PATTERN.sub("", text).strip()


# ---------------------------------------------------------------------------
# @cmd: injection helpers
# ---------------------------------------------------------------------------

_CMD_ALLOWLIST = frozenset({
    "git", "ls", "cat", "grep", "find", "head", "tail", "wc", "echo",
    "pwd", "env", "which", "uname", "date", "python", "python3", "pip",
    "pip3", "node", "npm", "docker", "kubectl", "curl", "jq",
})

_CMD_REF_PATTERN = re.compile(
    r'@cmd:([^\n@]+?)(?=\s@|\s*$|[,;])',
    re.IGNORECASE,
)


def _detect_cmd_refs(text: str) -> list[tuple[str, str]]:
    """Extract @cmd:<shell command> markers. Returns list of ('cmd', command_string) tuples."""
    refs: list[tuple[str, str]] = []
    for m in _CMD_REF_PATTERN.finditer(text):
        cmd_str = m.group(1).strip()
        if not cmd_str:
            continue
        entry: tuple[str, str] = ("cmd", cmd_str)
        if entry not in refs:
            refs.append(entry)
    return refs


def _is_cmd_allowlisted(cmd: str) -> bool:
    """Check if the first token of the command is in the allowlist."""
    first_token = cmd.strip().split()[0] if cmd.strip() else ""
    return first_token in _CMD_ALLOWLIST


def _strip_cmd_refs(text: str) -> str:
    """Remove @cmd:... markers from text."""
    return _CMD_REF_PATTERN.sub("", text).strip()


# ---------------------------------------------------------------------------
# @gh: injection helpers
# ---------------------------------------------------------------------------

_GH_REF_PATTERN = re.compile(
    r'@gh:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)',
    re.IGNORECASE,
)


def _detect_gh_refs(text: str) -> list[tuple[str, str]]:
    """Extract @gh:<owner/repo#N> markers. Returns list of ('gh', 'owner/repo#N') tuples."""
    refs: list[tuple[str, str]] = []
    for m in _GH_REF_PATTERN.finditer(text):
        ref = m.group(1)
        entry: tuple[str, str] = ("gh", ref)
        if entry not in refs:
            refs.append(entry)
    return refs


def _resolve_gh_ref(ref: str) -> str | None:
    """Resolve owner/repo#N to formatted markdown. Tries issue first, then PR.

    Returns None if gh is not available, not authed, or the ref is not found.
    """
    import json as _json
    import subprocess
    _CAP = 50_000

    try:
        repo_part, num_str = ref.rsplit("#", 1)
        num = int(num_str)
    except (ValueError, AttributeError):
        return None

    def _run_gh(args: list[str]) -> dict | None:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return None
            return _json.loads(result.stdout)
        except Exception:
            return None

    # Try issue first
    issue_data = _run_gh([
        "gh", "issue", "view", str(num),
        "--repo", repo_part,
        "--json", "title,body,state,labels,comments",
    ])
    if issue_data:
        title = issue_data.get("title", "")
        state = issue_data.get("state", "")
        body = issue_data.get("body", "") or ""
        labels = ", ".join(
            lbl.get("name", "") for lbl in (issue_data.get("labels") or [])
        )
        comments = issue_data.get("comments") or []
        comment_md = ""
        for c in comments[:5]:
            author = (c.get("author") or {}).get("login", "unknown")
            cbody = (c.get("body") or "")[:500]
            comment_md += f"\n**{author}:** {cbody}\n"
        md = (
            f"# Issue {ref}: {title}\n"
            f"**State:** {state}  **Labels:** {labels}\n\n"
            f"{body}\n"
        )
        if comment_md:
            md += f"\n## Comments\n{comment_md}"
        return md[:_CAP]

    # Fall back to PR
    pr_data = _run_gh([
        "gh", "pr", "view", str(num),
        "--repo", repo_part,
        "--json", "title,body,state,additions,deletions,files",
    ])
    if pr_data:
        title = pr_data.get("title", "")
        state = pr_data.get("state", "")
        body = pr_data.get("body", "") or ""
        additions = pr_data.get("additions", 0)
        deletions = pr_data.get("deletions", 0)
        files = pr_data.get("files") or []
        file_list = "\n".join(
            f"- {f.get('path', '')}" for f in files[:20]
        )
        md = (
            f"# PR {ref}: {title}\n"
            f"**State:** {state}  **+{additions}/-{deletions}**\n\n"
            f"{body}\n\n"
            f"## Files Changed\n{file_list}\n"
        )
        return md[:_CAP]

    return None


def _strip_gh_refs(text: str) -> str:
    """Remove @gh:... markers from text."""
    return _GH_REF_PATTERN.sub("", text).strip()


def output_name_from_title(title: str, *, default_stem: str, suffix: str) -> str:
    """Build a safe output filename from free-form user input."""
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", str(title or "").strip().lower()).strip("-")
    return f"{(stem or default_stem)[:40]}{suffix}"


def missing_feature_hint(feature: str) -> str:
    """Explain when a standalone CLI install is missing optional dependencies."""
    return (
        f"`{feature}` needs the full OpenClaw runtime dependencies. "
        "Use a repo checkout/package install for advanced commands, or stick to core standalone flows like "
        "ask/chat/health/analyze/write/exec/edit/watch."
    )


# ---------------------------------------------------------------------------
# Prefs-aware helpers (accept prefs dict to avoid circular imports)
# ---------------------------------------------------------------------------


def _a11y_plain(prefs: dict | None) -> bool:
    return bool((prefs or {}).get("plain_mode", False))


def _a11y_reduced(prefs: dict | None) -> bool:
    return bool((prefs or {}).get("reduced_motion", False))


def _make_clickable_link(url: str, text: str = "", *, prefs: dict | None = None, is_tty: bool | None = None) -> str:
    """Return an OSC 8 clickable hyperlink if supported, otherwise plain URL."""
    if not (prefs or {}).get("clickable_links", True) or _a11y_plain(prefs):
        return text or url
    if is_tty is None:
        is_tty = _get_is_tty()
    if not is_tty:
        return text or url
    display = text or url
    return f"\033]8;;{url}\033\\{_UL}{_CY}{display}{_R}\033]8;;\033\\"


def _linkify_response(text: str, *, prefs: dict | None = None, is_tty: bool | None = None) -> str:
    """Replace bare URLs in response text with OSC 8 clickable links."""
    if not (prefs or {}).get("clickable_links", True) or _a11y_plain(prefs):
        return text
    if is_tty is None:
        is_tty = _get_is_tty()
    if not is_tty:
        return text

    lines = text.split("\n")
    result = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and not line.startswith("|"):
            line = _URL_PATTERN.sub(lambda m: _make_clickable_link(m.group(1), prefs=prefs, is_tty=is_tty), line)
        result.append(line)
    return "\n".join(result)


def _print_path_hints(paths: list[str], *, prefs: dict | None = None, is_tty: bool | None = None, rich_available: bool | None = None) -> None:
    """Print quick-action hints for file paths mentioned in the response."""
    if not (prefs or {}).get("path_hints", True) or _a11y_plain(prefs):
        return
    if is_tty is None:
        is_tty = _get_is_tty()
    if not is_tty:
        return

    existing = [p for p in paths if os.path.exists(os.path.expanduser(p))]
    if not existing:
        return

    use_rich = _RICH_AVAILABLE if rich_available is None else rich_available
    if use_rich and is_tty:
        _RICH_CONSOLE.print(f"\n[dim]📁 File{'s' if len(existing) > 1 else ''} mentioned:[/]", end="")
        for p in existing[:3]:
            _RICH_CONSOLE.print(f"  [dim cyan]{p}[/]", end="")
        _RICH_CONSOLE.print("  [dim](use /view or /edit)[/]\n")
    else:
        hint = "  ".join(existing[:3])
        print(f"\n  {_DM}📁 Files: {hint}  (use /view or /edit){_R}")


def _suggest_followups(
    last_prompt: str,
    *,
    response_text: str = "",
    session_id: str = "",
) -> list[str]:
    """Return 2-3 relevant follow-up command suggestions based on the last prompt."""
    prompt_lower = last_prompt.lower()
    response_lower = str(response_text or "").lower()
    suggestions: list[str] = []
    mentioned_paths = _detect_file_paths(response_text) if response_text else []

    if mentioned_paths:
        suggestions.append(f"/view {mentioned_paths[0]} — inspect the file mentioned above")
    if session_id:
        suggestions.append("/context — verify what the next request will inherit")
    if "sources" in response_lower or "http://" in response_lower or "https://" in response_lower:
        suggestions.append("/links — revisit the cited sources")

    if any(w in prompt_lower for w in ["file", "path", "directory", "folder", "ls", "find"]):
        suggestions.append("/pathhints — show detected file paths in response")
    if any(w in prompt_lower for w in ["history", "recap", "summary", "week", "yesterday"]):
        suggestions.append("/recall 5 — review your last 5 prompts")
    if any(w in prompt_lower for w in ["error", "fail", "broken", "fix", "debug", "crash"]):
        suggestions.append("/exec — run a shell command to investigate")
    if any(w in prompt_lower for w in ["json", "data", "api", "response", "output"]):
        suggestions.append("/jsonformat — format JSON in the response")
    if any(w in prompt_lower for w in ["link", "url", "http", "website", "source"]):
        suggestions.append("/links — view clickable source links")
    if any(w in prompt_lower for w in ["search", "find", "look", "where"]):
        suggestions.append("/histsearch — search your prompt history")
    if any(w in prompt_lower for w in ["compare", "diff", "change", "before", "after"]):
        suggestions.append("/diff — compare files or show git changes")
    if any(w in prompt_lower for w in ["pin", "save", "remember", "keep", "note"]):
        suggestions.append("/pin — pin this conversation point")
    if any(w in prompt_lower for w in ["rate", "quality", "good", "bad", "helpful"]):
        suggestions.append("/rate — rate this response 1-5")

    if not suggestions:
        suggestions.append("/export md — save this session as markdown")
        suggestions.append("/rate — rate this response")
        suggestions.append("/recall 3 — review recent prompts")

    return _dedupe_preserve_order(suggestions)[:3]


def _print_predictive_affordances(
    hints: list[str],
    *,
    title: str = "Next steps",
    border_style: str = "dim",
    prefs: dict | None = None,
    is_tty: bool | None = None,
    rich_available: bool | None = None,
) -> None:
    """Render a compact, fallback-safe next-step menu."""
    clean = _dedupe_preserve_order(hints)[:4]
    if not clean:
        return
    if is_tty is None:
        is_tty = _get_is_tty()
    use_rich = _RICH_AVAILABLE if rich_available is None else rich_available
    if use_rich and is_tty and not _a11y_plain(prefs):
        body = _RichText()
        for hint in clean:
            body.append("  • ", style="dim")
            body.append(f"{hint}\n")
        _RICH_CONSOLE.print(
            _RichPanel(body, title=f"[bold]{title}[/]", border_style=border_style, padding=(0, 1))
        )
        return
    print(f"{title}:")
    for hint in clean:
        print(f"  - {hint}")


def _print_followup_suggestions(
    suggestions: list[str],
    *,
    mode: str = "chat",
    prefs: dict | None = None,
    is_tty: bool | None = None,
    rich_available: bool | None = None,
) -> None:
    """Print follow-up suggestions as a compact bottom-hint footer."""
    if not suggestions:
        return
    if is_tty is None:
        is_tty = _get_is_tty()
    if not is_tty:
        return
    clean = _dedupe_preserve_order(suggestions)[:3]
    if not clean:
        return
    use_rich = _RICH_AVAILABLE if rich_available is None else rich_available
    if _a11y_plain(prefs) or _a11y_reduced(prefs):
        _print_predictive_affordances(
            [f"mode: {mode}", *clean],
            title="Bottom bar",
            border_style="cyan",
            prefs=prefs,
            is_tty=is_tty,
            rich_available=rich_available,
        )
        return

    if use_rich and is_tty:
        _RICH_CONSOLE.print()
        _RICH_CONSOLE.print(f"  [dim]mode: {mode}[/] [dim]│[/]", end="")
        for i, s in enumerate(clean):
            sep = "  ·  " if i > 0 else "  "
            cmd = s.split(" — ")[0]
            desc = s.split(" — ")[1] if " — " in s else ""
            _RICH_CONSOLE.print(
                f"[dim]{sep}[/][bold cyan]{cmd}[/][dim]{' — ' + desc if desc else ''}[/]", end=""
            )
        _RICH_CONSOLE.print()
    else:
        print(
            f"\n  {_DM}mode: {mode}{_R} {_DM}|{_R} "
            + "  ·  ".join(
                f"{_BCY}{s.split(' — ')[0]}{_R}"
                f"{_DM}{' — ' + s.split(' — ')[1] if ' — ' in s else ''}{_R}"
                for s in clean
            )
        )
