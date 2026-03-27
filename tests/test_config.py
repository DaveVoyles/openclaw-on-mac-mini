"""
Tests for config.py — centralized configuration loading.

Validates YAML defaults, env-var overrides, and allowed-user-ID parsing.
"""

import importlib
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_config(monkeypatch, env_overrides: dict | None = None, yaml_text: str | None = None, tmp_path=None):
    """Re-import config with fresh env/yaml state.

    Returns the new ``cfg`` object.
    """
    env_overrides = env_overrides or {}

    if tmp_path is not None and yaml_text is not None:
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir(exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml_text)
        monkeypatch.setenv("CONFIG_DIR", str(cfg_dir))
    elif tmp_path is not None:
        # Point to an empty dir so no YAML is loaded
        cfg_dir = tmp_path / "empty_cfg"
        cfg_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("CONFIG_DIR", str(cfg_dir))

    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    # Remove cached module so the next import re-evaluates class attrs
    for mod_name in [k for k in sys.modules if k.startswith("config")]:
        del sys.modules[mod_name]

    import config
    importlib.reload(config)
    return config.cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_config_loads_defaults(self, monkeypatch, tmp_path):
        cfg = _reload_config(monkeypatch, tmp_path=tmp_path)
        assert hasattr(cfg, "discord_token")
        assert hasattr(cfg, "llm_model")
        assert hasattr(cfg, "bot_name")
        assert hasattr(cfg, "version")
        assert hasattr(cfg, "default_timeout")

    def test_config_env_override(self, monkeypatch, tmp_path):
        cfg = _reload_config(
            monkeypatch,
            env_overrides={"LLM_MODEL": "test-model-override"},
            tmp_path=tmp_path,
        )
        assert cfg.llm_model == "test-model-override"


class TestAllowedUserIds:
    def test_config_allowed_user_ids_parsing(self, monkeypatch, tmp_path):
        cfg = _reload_config(
            monkeypatch,
            env_overrides={"ALLOWED_USER_IDS": "111,222,333"},
            tmp_path=tmp_path,
        )
        assert cfg.allowed_user_ids == [111, 222, 333]

    def test_config_empty_allowed_users(self, monkeypatch, tmp_path):
        cfg = _reload_config(
            monkeypatch,
            env_overrides={"ALLOWED_USER_IDS": ""},
            tmp_path=tmp_path,
        )
        assert cfg.allowed_user_ids == []
