"""
test_scripts_unit.py — Unit tests for scripts/validate_env.py and scripts/validate_schema.py.

Uses tmp_path to create isolated temp files; never touches real .env or schema.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_validate_env(
    *extra_args: str, env_content: str = "", example_content: str = "", tmp_path: Path
) -> subprocess.CompletedProcess:
    """Create temp env/example files, run validate_env.py, return the result."""
    env_file = tmp_path / ".env"
    example_file = tmp_path / ".env.example"
    env_file.write_text(env_content)
    example_file.write_text(example_content)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_env.py"),
            "--env",
            str(env_file),
            "--example",
            str(example_file),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )


# ---------------------------------------------------------------------------
# TestValidateEnv
# ---------------------------------------------------------------------------


class TestValidateEnv:
    def test_all_present_exits_0(self, tmp_path: Path) -> None:
        """All required vars present → exit 0."""
        result = run_validate_env(
            tmp_path=tmp_path,
            example_content="# REQUIRED\nFOO=\n# REQUIRED\nBAR=\n",
            env_content="FOO=1\nBAR=2\n",
        )
        assert result.returncode == 0

    def test_missing_required_without_strict_exits_0(self, tmp_path: Path) -> None:
        """Missing required var without --strict → exit 0 (warning only)."""
        result = run_validate_env(
            tmp_path=tmp_path,
            example_content="# REQUIRED\nFOO=\nBAR=\n",
            env_content="BAR=2\n",
        )
        assert result.returncode == 0
        assert "FOO" in result.stdout

    def test_missing_required_with_strict_exits_1(self, tmp_path: Path) -> None:
        """Missing required var with --strict → exit 1."""
        result = run_validate_env(
            "--strict",
            tmp_path=tmp_path,
            example_content="# REQUIRED\nFOO=\n# REQUIRED\nBAR=\n",
            env_content="BAR=2\n",
        )
        assert result.returncode == 1
        assert "FOO" in result.stdout

    def test_strict_all_present_exits_0(self, tmp_path: Path) -> None:
        """--strict: all required vars present (incl. schema-required) → exit 0."""
        # The script also loads config/env_schema.yaml which marks DISCORD_BOT_TOKEN
        # and DISCORD_GUILD_ID as required, so both must be in .env too.
        result = run_validate_env(
            "--strict",
            tmp_path=tmp_path,
            example_content="# REQUIRED\nFOO=\n# REQUIRED\nBAR=\n",
            env_content="FOO=hello\nBAR=world\nDISCORD_BOT_TOKEN=tok\nDISCORD_GUILD_ID=123\n",
        )
        assert result.returncode == 0

    def test_missing_env_file_no_strict_exits_0(self, tmp_path: Path) -> None:
        """Non-existent .env with no required vars → exit 0."""
        env_file = tmp_path / ".env"
        example_file = tmp_path / ".env.example"
        example_file.write_text("OPTIONAL_THING=\n")
        # env_file intentionally not created
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "validate_env.py"),
                "--env",
                str(env_file),
                "--example",
                str(example_file),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0

    def test_missing_env_file_with_strict_exits_1(self, tmp_path: Path) -> None:
        """Non-existent .env with required vars + --strict → exit 1."""
        env_file = tmp_path / ".env"
        example_file = tmp_path / ".env.example"
        example_file.write_text("# REQUIRED\nFOO=\n")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "validate_env.py"),
                "--env",
                str(env_file),
                "--example",
                str(example_file),
                "--strict",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 1

    def test_missing_example_file_exits_1(self, tmp_path: Path) -> None:
        """Non-existent .env.example → exit 1."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=1\n")
        example_file = tmp_path / ".env.example"  # intentionally not created
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "validate_env.py"),
                "--env",
                str(env_file),
                "--example",
                str(example_file),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 1
        assert ".env.example" in result.stdout

    def test_empty_example_exits_0(self, tmp_path: Path) -> None:
        """Empty .env.example → no example-derived vars → exit 0 (no strict)."""
        # Note: without --strict the script always exits 0, even if schema adds required vars.
        result = run_validate_env(
            tmp_path=tmp_path,
            example_content="",
            env_content="",
        )
        assert result.returncode == 0

    def test_extra_keys_in_env_exits_0(self, tmp_path: Path) -> None:
        """Extra keys in .env not in .env.example → still exits 0."""
        result = run_validate_env(
            tmp_path=tmp_path,
            example_content="FOO=\n",
            env_content="FOO=1\nUNKNOWN_KEY=secret\n",
        )
        assert result.returncode == 0

    def test_comments_only_example_exits_0(self, tmp_path: Path) -> None:
        """Example with only comments → no vars → exit 0 (no strict)."""
        # Without --strict the script always exits 0.
        result = run_validate_env(
            tmp_path=tmp_path,
            example_content="# This is a comment\n# Another comment\n",
            env_content="",
        )
        assert result.returncode == 0

    def test_required_inline_comment_detected(self, tmp_path: Path) -> None:
        """REQUIRED in inline comment on same line as key → detected as required."""
        result = run_validate_env(
            "--strict",
            tmp_path=tmp_path,
            example_content="FOO= # REQUIRED\n",
            env_content="",
        )
        # Script handles inline REQUIRED comments on the key line
        # The key FOO should be detected as required and exit 1
        assert result.returncode == 1

    def test_real_files_integration(self) -> None:
        """Smoke: running against real repo files does not crash."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_env.py")],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        # Script may exit 0 or 1 depending on local .env, but must not crash (returncode != 2)
        assert result.returncode in (0, 1)


# ---------------------------------------------------------------------------
# TestValidateSchema — test importable functions with tmp_path
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPTS_DIR.parent))


class TestValidateSchema:
    """Tests for validate_schema.py using direct function imports + subprocess."""

    def _import(self):
        """Import validate_schema module (deferred to avoid import-time side-effects)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("validate_schema", SCRIPTS_DIR / "validate_schema.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_load_example_vars_basic(self, tmp_path: Path) -> None:
        """load_example_vars extracts variable names from .env.example."""
        mod = self._import()
        example = tmp_path / ".env.example"
        example.write_text("FOO=\nBAR=hello\n# comment\n\nBAZ=\n")
        result = mod.load_example_vars(example)
        assert result == {"FOO", "BAR", "BAZ"}

    def test_load_example_vars_skips_comments(self, tmp_path: Path) -> None:
        """load_example_vars ignores comment lines."""
        mod = self._import()
        example = tmp_path / ".env.example"
        example.write_text("# FOO=\n# BAR=\nREAL_VAR=\n")
        result = mod.load_example_vars(example)
        assert result == {"REAL_VAR"}

    def test_load_schema_vars_basic(self, tmp_path: Path) -> None:
        """load_schema_vars extracts variable names from schema YAML."""
        mod = self._import()
        schema = tmp_path / "env_schema.yaml"
        schema.write_text(
            "schema_version: '1.0'\nvariables:\n  FOO:\n    required: true\n  BAR:\n    required: false\n"
        )
        result = mod.load_schema_vars(schema)
        assert result == {"FOO", "BAR"}

    def test_load_schema_vars_empty(self, tmp_path: Path) -> None:
        """load_schema_vars returns empty set for schema with no variables key."""
        mod = self._import()
        schema = tmp_path / "env_schema.yaml"
        schema.write_text("schema_version: '1.0'\n")
        result = mod.load_schema_vars(schema)
        assert result == set()

    def test_validate_schema_real_files_exits_cleanly(self) -> None:
        """Smoke: running against real repo files does not crash."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_schema.py"), "--warn-only"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0

    def test_validate_schema_warn_only_exits_0(self) -> None:
        """--warn-only always exits 0 regardless of gaps."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_schema.py"), "--warn-only"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0

    def test_validate_schema_fix_hints_output(self) -> None:
        """--fix-hints produces YAML stub output when undocumented vars exist."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_schema.py"), "--warn-only", "--fix-hints"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0
        # --fix-hints should either print YAML stubs or confirm in-sync; either is fine
        assert result.stdout  # something was printed

    def test_load_example_vars_empty_file(self, tmp_path: Path) -> None:
        """load_example_vars handles empty file gracefully."""
        mod = self._import()
        example = tmp_path / ".env.example"
        example.write_text("")
        result = mod.load_example_vars(example)
        assert result == set()

    def test_load_schema_vars_multiple_vars(self, tmp_path: Path) -> None:
        """load_schema_vars extracts all variable names correctly."""
        mod = self._import()
        schema = tmp_path / "env_schema.yaml"
        schema.write_text(
            "schema_version: '1.0'\nvariables:\n"
            "  ALPHA:\n    required: true\n"
            "  BETA:\n    required: false\n"
            "  GAMMA:\n    required: true\n"
        )
        result = mod.load_schema_vars(schema)
        assert result == {"ALPHA", "BETA", "GAMMA"}

    def test_undocumented_vars_detected(self, tmp_path: Path) -> None:
        """load_example_vars - load_schema_vars detects undocumented vars."""
        mod = self._import()
        schema = tmp_path / "env_schema.yaml"
        example = tmp_path / ".env.example"
        schema.write_text("schema_version: '1.0'\nvariables:\n  FOO:\n    required: true\n")
        example.write_text("FOO=\nBAR=\n")  # BAR is undocumented
        schema_vars = mod.load_schema_vars(schema)
        example_vars = mod.load_example_vars(example)
        undocumented = example_vars - schema_vars
        assert "BAR" in undocumented
        assert "FOO" not in undocumented

    def test_missing_from_example_detected(self, tmp_path: Path) -> None:
        """schema vars not in .env.example are detected as missing."""
        mod = self._import()
        schema = tmp_path / "env_schema.yaml"
        example = tmp_path / ".env.example"
        schema.write_text("schema_version: '1.0'\nvariables:\n  FOO:\n    required: true\n  BAR:\n    required: true\n")
        example.write_text("FOO=\n")  # BAR missing from example
        schema_vars = mod.load_schema_vars(schema)
        example_vars = mod.load_example_vars(example)
        missing = schema_vars - example_vars
        assert "BAR" in missing
        assert "FOO" not in missing
