"""Unit tests for openclaw_cli_cli_parser.py — argument parser construction."""
from __future__ import annotations

import pytest

from openclaw_cli_cli_parser import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    build_parser,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_model(self):
        assert DEFAULT_MODEL == "auto"

    def test_default_timeout_positive(self):
        assert DEFAULT_TIMEOUT_SECONDS > 0

    def test_default_timeout_is_int(self):
        assert isinstance(DEFAULT_TIMEOUT_SECONDS, int)


# ---------------------------------------------------------------------------
# build_parser() — top-level parser
# ---------------------------------------------------------------------------

class TestBuildParser:
    def setup_method(self):
        self.parser = build_parser()

    def test_returns_argument_parser(self):
        import argparse
        assert isinstance(self.parser, argparse.ArgumentParser)

    def test_version_flag(self):
        with pytest.raises(SystemExit) as exc:
            self.parser.parse_args(["--version"])
        assert exc.value.code == 0

    def test_health_flag(self):
        ns = self.parser.parse_args(["--health"])
        assert ns.health is True

    def test_health_flag_default_false(self):
        ns = self.parser.parse_args([])
        assert ns.health is False

    def test_url_flag(self):
        ns = self.parser.parse_args(["--url", "http://example.com"])
        assert ns.url == "http://example.com"

    def test_url_default_none(self):
        ns = self.parser.parse_args([])
        assert ns.url is None

    def test_token_flag(self):
        ns = self.parser.parse_args(["--token", "secret"])
        assert ns.token == "secret"

    def test_model_flag_default(self):
        ns = self.parser.parse_args([])
        assert ns.model == DEFAULT_MODEL

    def test_model_flag_custom(self):
        ns = self.parser.parse_args(["--model", "gemini"])
        assert ns.model == "gemini"

    def test_timeout_flag_default(self):
        ns = self.parser.parse_args([])
        assert ns.timeout == DEFAULT_TIMEOUT_SECONDS

    def test_timeout_flag_custom(self):
        ns = self.parser.parse_args(["--timeout", "60"])
        assert ns.timeout == 60

    def test_json_flag(self):
        ns = self.parser.parse_args(["--json"])
        assert ns.json is True

    def test_json_flag_default_false(self):
        ns = self.parser.parse_args([])
        assert ns.json is False

    def test_no_stream_flag(self):
        ns = self.parser.parse_args(["--no-stream"])
        assert ns.no_stream is True

    def test_no_banner_flag(self):
        ns = self.parser.parse_args(["--no-banner"])
        assert ns.no_banner is True

    def test_user_name_flag(self):
        ns = self.parser.parse_args(["--user-name", "alice"])
        assert ns.user_name == "alice"

    def test_client_name_flag(self):
        ns = self.parser.parse_args(["--client-name", "laptop"])
        assert ns.client_name == "laptop"

    def test_session_flag(self):
        ns = self.parser.parse_args(["--session", "sess-123"])
        assert ns.session == "sess-123"

    def test_no_command_sets_command_none(self):
        ns = self.parser.parse_args([])
        assert ns.command is None


# ---------------------------------------------------------------------------
# ask subcommand
# ---------------------------------------------------------------------------

class TestAskSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_ask_no_prompt(self):
        ns = self.parser.parse_args(["ask"])
        assert ns.command == "ask"
        assert ns.prompt == []

    def test_ask_with_prompt(self):
        ns = self.parser.parse_args(["ask", "hello", "world"])
        assert ns.command == "ask"
        assert ns.prompt == ["hello", "world"]


# ---------------------------------------------------------------------------
# auth subcommand
# ---------------------------------------------------------------------------

class TestAuthSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_auth_login(self):
        ns = self.parser.parse_args(["auth", "login"])
        assert ns.command == "auth"
        assert ns.auth_command == "login"

    def test_auth_login_with_token(self):
        ns = self.parser.parse_args(["auth", "login", "--token", "mytoken"])
        assert ns.token == "mytoken"

    def test_auth_status(self):
        ns = self.parser.parse_args(["auth", "status"])
        assert ns.auth_command == "status"

    def test_auth_logout(self):
        ns = self.parser.parse_args(["auth", "logout"])
        assert ns.auth_command == "logout"

    def test_auth_requires_subcommand(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(["auth"])


# ---------------------------------------------------------------------------
# analyze subcommand
# ---------------------------------------------------------------------------

class TestAnalyzeSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_analyze_basic(self):
        ns = self.parser.parse_args(["analyze", "summarize this repo"])
        assert ns.command == "analyze"

    def test_analyze_cwd(self):
        ns = self.parser.parse_args(["analyze", "--cwd", "/some/path"])
        assert ns.cwd == "/some/path"

    def test_analyze_files(self):
        ns = self.parser.parse_args(["analyze", "--file", "a.py", "--file", "b.py"])
        assert ns.files == ["a.py", "b.py"]

    def test_analyze_plan_task_ids(self):
        ns = self.parser.parse_args(["analyze", "--plan-id", "p1", "--task-id", "t1"])
        assert ns.plan_id == "p1"
        assert ns.task_id == "t1"


# ---------------------------------------------------------------------------
# watch subcommand
# ---------------------------------------------------------------------------

class TestWatchSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_watch_defaults(self):
        ns = self.parser.parse_args(["watch"])
        assert ns.command == "watch"
        assert ns.mode == "analyze"
        assert ns.interval == 30
        assert ns.iterations == 5
        assert ns.on_change is False

    def test_watch_custom_mode(self):
        ns = self.parser.parse_args(["watch", "--mode", "research"])
        assert ns.mode == "research"

    def test_watch_on_change(self):
        ns = self.parser.parse_args(["watch", "--on-change"])
        assert ns.on_change is True

    def test_watch_custom_iterations(self):
        ns = self.parser.parse_args(["watch", "--iterations", "10"])
        assert ns.iterations == 10


# ---------------------------------------------------------------------------
# exec subcommand
# ---------------------------------------------------------------------------

class TestExecSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_exec_defaults(self):
        ns = self.parser.parse_args(["exec"])
        assert ns.command == "exec"
        assert ns.command_timeout == 60
        assert ns.yes is False

    def test_exec_with_risk(self):
        ns = self.parser.parse_args(["exec", "--risk", "low"])
        assert ns.risk == "low"

    def test_exec_auto_approve(self):
        ns = self.parser.parse_args(["exec", "--yes"])
        assert ns.yes is True


# ---------------------------------------------------------------------------
# session subcommand
# ---------------------------------------------------------------------------

class TestSessionSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_session_list(self):
        ns = self.parser.parse_args(["session", "list"])
        assert ns.command == "session"
        assert ns.session_command == "list"
        assert ns.limit == 20

    def test_session_list_custom_limit(self):
        ns = self.parser.parse_args(["session", "list", "--limit", "5"])
        assert ns.limit == 5

    def test_session_create(self):
        ns = self.parser.parse_args(["session", "create"])
        assert ns.session_command == "create"

    def test_session_show(self):
        ns = self.parser.parse_args(["session", "show", "sess-abc"])
        assert ns.session_command == "show"
        assert ns.session_id == "sess-abc"

    def test_session_export_format(self):
        ns = self.parser.parse_args(["session", "export", "sess-abc", "--format", "runbook"])
        assert ns.format == "runbook"


# ---------------------------------------------------------------------------
# edit subcommand
# ---------------------------------------------------------------------------

class TestEditSubparser:
    def setup_method(self):
        self.parser = build_parser()

    def test_edit_basic(self):
        ns = self.parser.parse_args(["edit", "file.py"])
        assert ns.command == "edit"
        assert ns.path == "file.py"

    def test_edit_dry_run(self):
        ns = self.parser.parse_args(["edit", "file.py", "--dry-run"])
        assert ns.dry_run is True

    def test_edit_append(self):
        ns = self.parser.parse_args(["edit", "file.py", "--append"])
        assert ns.append is True

    def test_edit_replace(self):
        ns = self.parser.parse_args(["edit", "file.py", "--replace", "old", "new"])
        assert ns.replace == ["old", "new"]
