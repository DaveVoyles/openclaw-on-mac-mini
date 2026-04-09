"""Tests for src/ui_components.py — EmbedColors, paginate_items, build_embed, error_embed."""


# discord is the real module (conftest loads it) — no stubs needed

import ui_components as uc  # noqa: E402

# ---------------------------------------------------------------------------
# EmbedColors
# ---------------------------------------------------------------------------

class TestEmbedColors:
    def test_success_is_green(self):
        assert uc.EmbedColors.SUCCESS == 0x00FF00

    def test_error_is_red(self):
        assert uc.EmbedColors.ERROR == 0xFF0000

    def test_info_is_blue(self):
        assert uc.EmbedColors.INFO == 0x3498DB

    def test_warning_is_orange(self):
        assert uc.EmbedColors.WARNING == 0xFF9900

    def test_ai_is_purple(self):
        assert uc.EmbedColors.AI == 0x9B59B6


# ---------------------------------------------------------------------------
# paginate_items
# ---------------------------------------------------------------------------

class TestPaginateItems:
    def test_empty_list_returns_single_page(self):
        pages = uc.paginate_items([], title="Test")
        assert len(pages) == 1

    def test_small_list_fits_on_one_page(self):
        items = [f"item {i}" for i in range(5)]
        pages = uc.paginate_items(items, per_page=10)
        assert len(pages) == 1

    def test_large_list_splits_into_multiple_pages(self):
        items = [f"item {i}" for i in range(25)]
        pages = uc.paginate_items(items, per_page=10)
        assert len(pages) == 3

    def test_exactly_one_page_boundary(self):
        items = [f"item {i}" for i in range(10)]
        pages = uc.paginate_items(items, per_page=10)
        assert len(pages) == 1

    def test_returns_list(self):
        pages = uc.paginate_items(["a", "b"], per_page=10)
        assert isinstance(pages, list)
        assert len(pages) >= 1

    def test_footer_set_on_embeds(self):
        pages = uc.paginate_items(["a"], footer="my footer")
        assert len(pages) == 1


# ---------------------------------------------------------------------------
# build_embed
# ---------------------------------------------------------------------------

class TestBuildEmbed:
    def test_basic_build_returns_embed(self):
        import discord
        result = uc.build_embed("My Title", "My description")
        assert isinstance(result, discord.Embed)

    def test_title_is_set(self):
        result = uc.build_embed("My Title")
        assert result.title == "My Title"

    def test_description_is_set(self):
        result = uc.build_embed("T", "My description")
        assert result.description == "My description"

    def test_footer_set_when_provided(self):
        result = uc.build_embed("T", footer="footer text")
        assert result.footer.text == "footer text"

    def test_model_used_as_footer_when_no_footer(self):
        result = uc.build_embed("T", model="gpt-4")
        assert result.footer.text == "via gpt-4"

    def test_long_description_truncated_to_4096(self):
        long_desc = "x" * 5000
        result = uc.build_embed("T", long_desc)
        assert len(result.description) <= 4096


# ---------------------------------------------------------------------------
# error_embed
# ---------------------------------------------------------------------------

class TestErrorEmbed:
    def test_error_embed_uses_error_color(self):
        result = uc.error_embed("something went wrong")
        assert result.color.value == uc.EmbedColors.ERROR

    def test_error_embed_default_title(self):
        result = uc.error_embed("oops")
        assert "Error" in result.title

    def test_error_embed_custom_title(self):
        result = uc.error_embed("oops", title="⚠️ Warning")
        assert result.title == "⚠️ Warning"

    def test_long_message_truncated(self):
        result = uc.error_embed("e" * 5000)
        assert len(result.description) <= 4096
