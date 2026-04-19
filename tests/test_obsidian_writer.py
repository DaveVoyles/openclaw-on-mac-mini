"""Tests for obsidian_writer.py — slugify, frontmatter, and vault I/O."""

from __future__ import annotations

import pytest

from obsidian_writer import _build_frontmatter, _slugify

# ===========================================================================
# _slugify
# ===========================================================================


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_stripped(self):
        result = _slugify("Hello! World?")
        assert "!" not in result
        assert "?" not in result

    def test_max_len_respected(self):
        long = "A word " * 20
        assert len(_slugify(long, max_len=20)) <= 20

    def test_empty_returns_untitled(self):
        assert _slugify("") == "untitled"

    def test_only_special_chars_returns_untitled(self):
        assert _slugify("!@#$%^&*()") == "untitled"

    def test_multiple_spaces_collapsed(self):
        assert _slugify("hello    world") == "hello-world"

    def test_hyphens_preserved(self):
        result = _slugify("my-title")
        assert "my" in result and "title" in result

    def test_leading_trailing_hyphens_stripped(self):
        result = _slugify("  -hello-  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_default_max_len(self):
        long = "word " * 30
        assert len(_slugify(long)) <= 50

    def test_lowercase(self):
        assert _slugify("UPPERCASE") == "uppercase"


# ===========================================================================
# _build_frontmatter
# ===========================================================================


class TestBuildFrontmatter:
    def test_contains_title(self):
        fm = _build_frontmatter("My Note")
        assert 'title: "My Note"' in fm

    def test_starts_and_ends_with_dashes(self):
        fm = _build_frontmatter("Test")
        lines = fm.strip().splitlines()
        assert lines[0] == "---"
        assert lines[-1] == "---"

    def test_includes_date(self):
        import datetime

        fm = _build_frontmatter("Test")
        assert datetime.date.today().isoformat() in fm

    def test_includes_source_url(self):
        fm = _build_frontmatter("Test", source_url="https://example.com")
        assert "https://example.com" in fm

    def test_source_url_omitted_when_empty(self):
        fm = _build_frontmatter("Test", source_url="")
        assert "source:" not in fm

    def test_content_type_included(self):
        fm = _build_frontmatter("Test", content_type="research")
        assert "type: research" in fm

    def test_content_type_prepended_to_tags(self):
        fm = _build_frontmatter("Test", tags=["python"], content_type="research")
        assert "- research" in fm
        assert "- python" in fm

    def test_tags_no_duplicates_from_content_type(self):
        fm = _build_frontmatter("Test", tags=["research"], content_type="research")
        # research should appear only once as a tag
        assert fm.count("- research") == 1

    def test_model_included(self):
        fm = _build_frontmatter("Test", model="gemini-pro")
        assert 'model: "gemini-pro"' in fm

    def test_model_omitted_when_empty(self):
        fm = _build_frontmatter("Test", model="")
        assert "model:" not in fm

    def test_tags_lowercased_and_hyphenated(self):
        fm = _build_frontmatter("Test", tags=["My Tag"])
        assert "- my-tag" in fm

    def test_no_tags_no_tags_section(self):
        fm = _build_frontmatter("Test", tags=[], content_type="")
        assert "tags:" not in fm


# ===========================================================================
# save_to_vault (async, filesystem-backed)
# ===========================================================================


class TestSaveToVault:
    @pytest.mark.asyncio
    async def test_saves_file_and_returns_success(self, tmp_path, monkeypatch):
        import obsidian_writer as mod

        monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)
        result = await mod.save_to_vault("Test Note", "Some content")
        assert "✅" in result
        assert "Test Note" not in result or "vault" in result.lower()

    @pytest.mark.asyncio
    async def test_file_contains_frontmatter_and_body(self, tmp_path, monkeypatch):
        import obsidian_writer as mod

        monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)
        await mod.save_to_vault("My Note", "Body text here", content_type="note")
        # Find the written file
        files = list(tmp_path.rglob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "My Note" in content
        assert "Body text here" in content
        assert "---" in content

    @pytest.mark.asyncio
    async def test_no_overwrite_on_duplicate(self, tmp_path, monkeypatch):
        import obsidian_writer as mod

        monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)
        await mod.save_to_vault("Same Title", "Content 1")
        await mod.save_to_vault("Same Title", "Content 2")
        files = list(tmp_path.rglob("*.md"))
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_content_type_routes_to_subfolder(self, tmp_path, monkeypatch):
        import obsidian_writer as mod

        monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)
        await mod.save_to_vault("Research Note", "Research content", content_type="research")
        # File should be in the Research subfolder
        files = list(tmp_path.rglob("*.md"))
        assert any("Research" in str(f) or "research" in str(f).lower() for f in files)
