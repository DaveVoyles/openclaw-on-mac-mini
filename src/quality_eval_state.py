"""Quality evaluation scorecard building, persistence, and retrieval."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from anchor_context_state import get_scoped_recall_alerts
from channel_profile_state import _get_channel_profile_db

_CROSS_CHANNEL_OPT_IN_RE = re.compile(r"(?i)(--cross-channel\b|#cross-channel\b|\[cross-channel\])")
_FOLLOWUP_HINT_RE = re.compile(r"(?i)^(follow up|what about|and |also |more on |next |continue )")
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001faff"
    "\U00002700-\U000027bf"
    "]+",
    flags=re.UNICODE,
)
_QUALITY_METRICS = (
    "channel_leakage_prevention",
    "followup_anchor_correctness",
    "profile_adherence",
    "table_readability_copy_safety",
)


def _init_metric_counter() -> dict[str, int]:
    return {"pass": 0, "fail": 0}


def _is_followup_like(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False
    return len(text.split()) < 10 or _FOLLOWUP_HINT_RE.search(text) is not None


def _contains_markdown_table(text: str) -> bool:
    if not text:
        return False
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        if idx + 1 < len(lines):
            nxt = lines[idx + 1].strip()
            if nxt.startswith("|") and all(c in "|-: " for c in nxt.replace("|", "")):
                return True
    return False


def _contains_discord_table(text: str) -> bool:
    if not text:
        return False
    return "```text" in text and "|" in text and "+" in text


def _contains_copy_safe_table(text: str) -> bool:
    return bool(text and "📋 Table" in text)


def _safe_rate(passes: int, fails: int) -> float:
    total = passes + fails
    return round(passes / total, 3) if total > 0 else 1.0


def build_quality_eval_scorecard(
    *,
    window_hours: float = 24,
    limit: int = 250,
    now: float | None = None,
) -> dict[str, Any]:
    """Score recent run/response telemetry across key quality metrics."""
    try:
        from error_tracker import get_recent_outcomes

        runs = list(get_recent_outcomes(hours=window_hours, limit=limit))
    except Exception:  # broad: intentional
        runs = []

    counters = {name: _init_metric_counter() for name in _QUALITY_METRICS}

    for run in runs:
        if not isinstance(run, dict):
            continue
        question = str(run.get("question") or "")
        response = str(run.get("response_preview") or run.get("response_text") or "")
        scope_mode = str(run.get("scope_mode") or "channel")
        lock_mode = str(run.get("lock_mode") or "none")
        anchor_id = str(run.get("anchor_id") or "").strip()
        profile_values = run.get("profile_values")
        if not isinstance(profile_values, dict):
            profile_values = {}

        # 1) Channel leakage prevention
        if scope_mode in {"channel", "thread", "cross-channel"}:
            opt_in = _CROSS_CHANNEL_OPT_IN_RE.search(question) is not None
            if scope_mode == "cross-channel" and not opt_in:
                counters["channel_leakage_prevention"]["fail"] += 1
            else:
                counters["channel_leakage_prevention"]["pass"] += 1

        # 2) Follow-up anchor correctness
        followup_expected = _is_followup_like(question) or lock_mode == "prior_report"
        if followup_expected or anchor_id:
            if followup_expected and anchor_id:
                counters["followup_anchor_correctness"]["pass"] += 1
            else:
                counters["followup_anchor_correctness"]["fail"] += 1

        # 3) Profile adherence
        if response and profile_values:
            checks: list[bool] = []
            word_count = len(response.split())
            emoji_level = str(profile_values.get("emoji_level") or "light")
            report_depth = str(profile_values.get("report_depth") or "standard")
            tone = str(profile_values.get("tone") or "neutral")

            if emoji_level == "none":
                checks.append(_EMOJI_RE.search(response) is None)
            if report_depth == "brief":
                checks.append(word_count <= 260)
            elif report_depth == "detailed":
                checks.append(word_count >= 80)
            if tone == "concise":
                checks.append(word_count <= 320)

            if all(checks) if checks else True:
                counters["profile_adherence"]["pass"] += 1
            else:
                counters["profile_adherence"]["fail"] += 1

        # 4) Table readability / copy safety
        if response and (
            _contains_markdown_table(response)
            or _contains_discord_table(response)
            or _contains_copy_safe_table(response)
        ):
            expected_style = str(profile_values.get("table_style") or "discord")
            if expected_style == "copy-safe":
                ok = _contains_copy_safe_table(response)
            else:
                ok = _contains_discord_table(response) or _contains_markdown_table(response)
            if ok:
                counters["table_readability_copy_safety"]["pass"] += 1
            else:
                counters["table_readability_copy_safety"]["fail"] += 1

    # Give leakage metric signal credit for blocked attempts
    blocked = [
        item
        for item in get_scoped_recall_alerts(limit=min(100, max(5, limit // 2)))
        if str(item.get("category") or "").strip().lower() == "scope_guard_block"
    ]
    if blocked:
        counters["channel_leakage_prevention"]["pass"] += len(blocked)

    metrics: dict[str, dict[str, Any]] = {}
    summary_passes = 0
    summary_failures = 0
    for name in _QUALITY_METRICS:
        passed = counters[name]["pass"]
        failed = counters[name]["fail"]
        metrics[name] = {
            "pass": passed,
            "fail": failed,
            "sample": passed + failed,
            "rate": _safe_rate(passed, failed),
        }
        summary_passes += passed
        summary_failures += failed

    return {
        "timestamp": float(now if now is not None else time.time()),
        "window_hours": float(window_hours),
        "limit": int(limit),
        "sample_size": int(len(runs)),
        "summary": {
            "pass": summary_passes,
            "fail": summary_failures,
            "rate": _safe_rate(summary_passes, summary_failures),
        },
        "metrics": metrics,
    }


def save_quality_eval_scorecard(
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    """Persist a quality eval scorecard snapshot and return normalized payload."""
    ts = float(scorecard.get("timestamp") or time.time())
    window_hours = float(scorecard.get("window_hours") or 24.0)
    sample_size = int(scorecard.get("sample_size") or 0)
    summary = scorecard.get("summary") if isinstance(scorecard.get("summary"), dict) else {}
    metrics = scorecard.get("metrics") if isinstance(scorecard.get("metrics"), dict) else {}

    summary_passes = int(summary.get("pass") or 0)
    summary_failures = int(summary.get("fail") or 0)
    summary_rate = float(summary.get("rate") or _safe_rate(summary_passes, summary_failures))
    metrics_json = json.dumps(metrics, separators=(",", ":"))

    db = _get_channel_profile_db()
    cur = db.execute(
        """
        INSERT INTO quality_eval_scorecards (
            ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json),
    )
    db.commit()
    return {
        "scorecard_id": int(cur.lastrowid or 0),
        "timestamp": ts,
        "window_hours": window_hours,
        "sample_size": sample_size,
        "summary": {"pass": summary_passes, "fail": summary_failures, "rate": summary_rate},
        "metrics": metrics,
    }


def create_quality_eval_scorecard(
    *,
    window_hours: float = 24,
    limit: int = 250,
    persist: bool = True,
) -> dict[str, Any]:
    """Build and optionally persist a quality evaluation scorecard snapshot."""
    scorecard = build_quality_eval_scorecard(window_hours=window_hours, limit=limit)
    if not persist:
        scorecard["scorecard_id"] = None
        return scorecard
    return save_quality_eval_scorecard(scorecard)


def list_quality_eval_scorecards(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent persisted quality scorecards (newest first)."""
    capped = max(1, min(int(limit), 200))
    db = _get_channel_profile_db()
    rows = db.execute(
        """
        SELECT scorecard_id, ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json
        FROM quality_eval_scorecards
        ORDER BY ts DESC, scorecard_id DESC
        LIMIT ?
        """,
        (capped,),
    ).fetchall()
    cards: list[dict[str, Any]] = []
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        except json.JSONDecodeError:
            metrics = {}
        cards.append(
            {
                "scorecard_id": int(row["scorecard_id"]),
                "timestamp": float(row["ts"]),
                "window_hours": float(row["window_hours"]),
                "sample_size": int(row["sample_size"]),
                "summary": {
                    "pass": int(row["summary_passes"]),
                    "fail": int(row["summary_failures"]),
                    "rate": float(row["summary_rate"]),
                },
                "metrics": metrics if isinstance(metrics, dict) else {},
            }
        )
    return cards


def ensure_quality_eval_scorecard(
    *,
    window_hours: float = 24,
    limit: int = 250,
    min_interval_seconds: int = 1800,
) -> dict[str, Any]:
    """Return latest scorecard; create a fresh snapshot when stale."""
    latest = list_quality_eval_scorecards(limit=1)
    if latest:
        age_seconds = time.time() - float(latest[0].get("timestamp") or 0)
        if age_seconds < max(60, int(min_interval_seconds)):
            return latest[0]
    return create_quality_eval_scorecard(window_hours=window_hours, limit=limit, persist=True)
