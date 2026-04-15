"""Unit tests for openclaw_cli_cmd_workflow.py — workflow and automation handlers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import openclaw_cli_cmd_workflow as mod  # type: ignore
from openclaw_cli_types import ChatCommandContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CMD_CONTINUE = "continue"


def _ctx(args: str = "", session_id: str = "sess-1") -> ChatCommandContext:
    return ChatCommandContext(history=[], session_id=session_id, args=args)


def _mock_session(**kwargs) -> MagicMock:
    s = MagicMock()
    s.session_id = kwargs.get("session_id", "sess-1")
    s.plan_id = kwargs.get("plan_id", None)
    s.cwd = kwargs.get("cwd", "/project")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _mock_cli(**kwargs) -> MagicMock:
    """Create a minimal mock of the main openclaw_cli module."""
    # Extract special list/dict kwargs before building the mock so the generic
    # loop at the end doesn't accidentally overwrite MagicMock callables.
    _risk_entries = kwargs.pop("_risk_entries", [])
    _alerts = kwargs.pop("_alerts", [])
    _workflows = kwargs.pop("_workflows", {})
    _session = kwargs.pop("_session", _mock_session())

    m = MagicMock()
    m._RICH_AVAILABLE = False
    m._IS_TTY = False
    m._PREFS = kwargs.pop("_PREFS", {})
    m._require_session_or_warn = MagicMock(return_value=_session)
    m._print_error = MagicMock()
    m._validate_plan_id_local = MagicMock(return_value=MagicMock(available=True))
    m._link_validation_suffix = MagicMock(return_value="")
    m._risk_entries = MagicMock(return_value=_risk_entries)
    m._collect_operator_alerts = MagicMock(return_value=_alerts)
    m._acknowledged_alert_ids = MagicMock(return_value=set())
    m._set_acknowledged_alert_ids = MagicMock()
    m._workflow_store = MagicMock(return_value=_workflows)
    m._print_workflow_preview = MagicMock()
    m._macro_run = MagicMock(return_value=_CMD_CONTINUE)
    m._print_automation_dashboard = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _cmd_risk
# ---------------------------------------------------------------------------

def test_cmd_risk_list_no_risks(capsys):
    cli = _mock_cli(_risk_entries=[])
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_risk(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "(none)" in captured.out


def test_cmd_risk_list_with_risks(capsys):
    risks = [{"risk_level": "high", "actor": "operator", "content": "DB timeout"}]
    cli = _mock_cli(_risk_entries=risks)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_risk(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "HIGH" in captured.out
    assert "DB timeout" in captured.out


def test_cmd_risk_add_valid(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_workflow.append_event") as mock_append:
        result = mod._cmd_risk(_ctx("add high Database is down"))
    assert result == _CMD_CONTINUE
    mock_append.assert_called_once()
    captured = capsys.readouterr()
    assert "Recorded high risk" in captured.out


def test_cmd_risk_add_invalid_level(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_risk(_ctx("add extreme something"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_risk_add_missing_text(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_risk(_ctx("add high"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_risk_clear_valid(capsys):
    risks = [{"risk_level": "medium", "actor": "operator", "content": "Old risk"}]
    cli = _mock_cli(_risk_entries=risks)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_workflow.append_event") as mock_append:
        result = mod._cmd_risk(_ctx("clear 1"))
    assert result == _CMD_CONTINUE
    mock_append.assert_called_once()
    captured = capsys.readouterr()
    assert "Cleared" in captured.out


def test_cmd_risk_clear_out_of_range(capsys):
    risks = [{"risk_level": "low", "content": "Risk A"}]
    cli = _mock_cli(_risk_entries=risks)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_risk(_ctx("clear 99"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_risk_unknown_sub_shows_error(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_risk(_ctx("invalid"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_alerts
# ---------------------------------------------------------------------------

def test_cmd_alerts_list_empty(capsys):
    cli = _mock_cli(_alerts=[])
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_alerts(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "(none)" in captured.out


def test_cmd_alerts_list_with_items(capsys):
    alerts = [
        {"id": "a1", "severity": "warning", "title": "High memory", "message": "Usage > 90%"}
    ]
    cli = _mock_cli(_alerts=alerts)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_alerts(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "High memory" in captured.out


def test_cmd_alerts_acknowledge_valid(capsys):
    alerts = [{"id": "alert-1", "severity": "info", "title": "FYI", "message": "Note"}]
    acked = set()
    cli = _mock_cli(_alerts=alerts)
    cli._acknowledged_alert_ids = MagicMock(return_value=acked)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_alerts(_ctx("acknowledge 1"))
    assert result == _CMD_CONTINUE
    cli._set_acknowledged_alert_ids.assert_called_once()
    captured = capsys.readouterr()
    assert "Acknowledged alert 1" in captured.out


def test_cmd_alerts_acknowledge_out_of_range(capsys):
    alerts = [{"id": "a1", "severity": "low", "title": "T", "message": "M"}]
    cli = _mock_cli(_alerts=alerts)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_alerts(_ctx("acknowledge 99"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_alerts_acknowledge_non_numeric(capsys):
    alerts = [{"id": "a1", "severity": "low", "title": "T", "message": "M"}]
    cli = _mock_cli(_alerts=alerts)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_alerts(_ctx("acknowledge abc"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_alerts_unknown_sub(capsys):
    cli = _mock_cli(_alerts=[])
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_alerts(_ctx("delete"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_workflow
# ---------------------------------------------------------------------------

def test_cmd_workflow_list_empty(capsys):
    cli = _mock_cli(_workflows={})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_workflow(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "no workflows" in captured.out.lower() or "Workflows" in captured.out


def test_cmd_workflow_list_with_items(capsys):
    cli = _mock_cli(_workflows={"deploy": ["step1", "step2"]})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_workflow(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "deploy" in captured.out


def test_cmd_workflow_preview_not_found(capsys):
    cli = _mock_cli(_workflows={})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_workflow(_ctx("preview missing"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_workflow_preview_calls_print(capsys):
    cli = _mock_cli(_workflows={"myflow": ["step1"]})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_workflow(_ctx("preview myflow"))
    assert result == _CMD_CONTINUE
    cli._print_workflow_preview.assert_called_once()


def test_cmd_workflow_run_calls_macro_run():
    cli = _mock_cli(_workflows={"myflow": ["step1"]})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_workflow(_ctx("run myflow"))
    cli._macro_run.assert_called_once()


def test_cmd_workflow_unknown_sub(capsys):
    cli = _mock_cli(_workflows={})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_workflow(_ctx("badtoken"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_macrostatus
# ---------------------------------------------------------------------------

def test_cmd_macrostatus_no_macros(capsys):
    cli = _mock_cli(_PREFS={"macros": {}})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_macrostatus(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No macros" in captured.out


def test_cmd_macrostatus_lists_macros(capsys):
    cli = _mock_cli(_PREFS={"macros": {"build": ["npm run build", "npm test"]}})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_macrostatus(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "build" in captured.out


# ---------------------------------------------------------------------------
# _cmd_fleet
# ---------------------------------------------------------------------------

def test_cmd_fleet_status_calls_dashboard():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_fleet(_ctx("status"))
    assert result == _CMD_CONTINUE
    cli._print_automation_dashboard.assert_called_once()


def test_cmd_fleet_health_calls_dashboard():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_fleet(_ctx("health"))
    assert result == _CMD_CONTINUE
    cli._print_automation_dashboard.assert_called_once()


def test_cmd_fleet_default_calls_dashboard():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_fleet(_ctx(""))
    assert result == _CMD_CONTINUE
    cli._print_automation_dashboard.assert_called_once()


def test_cmd_fleet_unknown_sub_shows_error():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_fleet(_ctx("invalid"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_watch — sub-command routing
# ---------------------------------------------------------------------------

def test_cmd_watch_no_session_returns_continue():
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_watch(_ctx("status"))
    assert result == _CMD_CONTINUE


def test_cmd_watch_retry_limit_invalid(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_workflow.load_watch_state", return_value={"retry_limit": 5}):
        result = mod._cmd_watch(_ctx("retry-limit notanumber"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_watch_retry_limit_no_state():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_workflow.load_watch_state", return_value=None):
        result = mod._cmd_watch(_ctx("retry-limit 3"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_watch_unknown_sub_shows_error():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_workflow.load_watch_state", return_value=None):
        result = mod._cmd_watch(_ctx("bogus"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()
