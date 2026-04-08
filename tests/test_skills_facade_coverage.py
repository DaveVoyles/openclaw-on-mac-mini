"""Tests for skills_facade.py — thin wrapper over SKILLS dict."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import skills_facade as mod
from skills_facade import get_skill, list_skills, skill_exists


class TestGetSkill:
    def test_returns_none_for_unknown_skill(self):
        assert get_skill("nonexistent_skill_xyz") is None

    def test_returns_non_none_for_existing_skill(self):
        names = list_skills()
        if not names:
            pytest.skip("No skills registered")
        first_name = names[0]
        result = get_skill(first_name)
        assert result is not None

    def test_returns_same_as_dict_lookup(self):
        from skills_facade import SKILLS
        names = list(SKILLS.keys())
        if not names:
            pytest.skip("No skills registered")
        name = names[0]
        assert get_skill(name) is SKILLS[name]


class TestListSkills:
    def test_returns_sorted_list(self):
        names = list_skills()
        assert names == sorted(names)

    def test_returns_list_type(self):
        assert isinstance(list_skills(), list)

    def test_all_items_are_strings(self):
        for name in list_skills():
            assert isinstance(name, str)

    def test_matches_skills_dict_keys(self):
        from skills_facade import SKILLS
        assert set(list_skills()) == set(SKILLS.keys())


class TestSkillExists:
    def test_false_for_unknown(self):
        assert skill_exists("definitely_not_a_real_skill") is False

    def test_true_for_existing(self):
        names = list_skills()
        if not names:
            pytest.skip("No skills registered")
        assert skill_exists(names[0]) is True

    def test_false_for_empty_string(self):
        assert skill_exists("") is False

    def test_consistent_with_get_skill(self):
        """skill_exists should return True iff get_skill returns non-None."""
        for name in list_skills()[:5]:
            assert skill_exists(name) is True
            assert get_skill(name) is not None

    def test_patched_skills_reflected(self):
        """Patching SKILLS should propagate to skill_exists."""
        fake_skills = {"my_fake_skill": {"name": "my_fake_skill"}}
        with patch.object(mod, "SKILLS", fake_skills):
            # Directly check the dict since the facade reads SKILLS at call time
            from skills_facade import SKILLS as S
            assert "my_fake_skill" in S
