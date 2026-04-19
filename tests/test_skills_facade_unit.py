"""Unit tests for skills_facade.py — gaps not covered by test_skills_facade_coverage.py.

Coverage file tests: get_skill, list_skills, skill_exists with standard cases.
This file covers: edge cases with patched empty SKILLS, type guarantees, and
consistency across all three API functions.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import skills_facade as mod
from skills_facade import SKILLS, get_skill, list_skills, skill_exists


class TestGetSkillEdgeCases:
    def test_skills_facade_unit_returns_none_for_empty_string(self):
        assert get_skill("") is None

    def test_returns_none_when_skills_empty(self):
        with patch.object(mod, "SKILLS", {}):
            assert get_skill("anything") is None

    def test_case_sensitive_lookup(self):
        names = list(SKILLS.keys())
        if not names:
            pytest.skip("No skills registered")
        lower = names[0].lower()
        upper = lower.upper()
        if upper not in SKILLS:
            assert get_skill(upper) is None


class TestListSkillsEdgeCases:
    def test_empty_skills_returns_empty_list(self):
        with patch.object(mod, "SKILLS", {}):
            result = list_skills()
        assert result == []

    def test_sorted_order_is_stable(self):
        result = list_skills()
        assert result == sorted(result)

    def test_returns_list_not_dict_keys(self):
        result = list_skills()
        assert isinstance(result, list)

    def test_single_skill_list(self):
        with patch.object(mod, "SKILLS", {"zebra": {"description": "test"}}):
            result = list_skills()
        assert result == ["zebra"]


class TestSkillExistsEdgeCases:
    def test_empty_skills_always_false(self):
        with patch.object(mod, "SKILLS", {}):
            assert skill_exists("anything") is False

    def test_whitespace_skill_name_returns_false(self):
        assert skill_exists("   ") is False

    def test_consistent_with_get_skill_for_all_skills(self):
        for name in list_skills():
            assert skill_exists(name) == (get_skill(name) is not None)

    def test_patched_skill_detected(self):
        with patch.object(mod, "SKILLS", {"my_skill": {"description": "x"}}):
            assert skill_exists("my_skill") is True
            assert skill_exists("other") is False
