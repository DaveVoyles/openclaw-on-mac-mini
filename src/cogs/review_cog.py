"""
Review Cog — document review and critique from Discord.

Commands:
  /review text [mode]  — paste text into a modal for AI review
  /review file [mode]  — upload a file for AI review
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import truncate_for_embed

log = logging.getLogger("openclaw")

# ── Mode choices ──────────────────────────────────────────────────────────────

MODE_CHOICES = [
    app_commands.Choice(name="Writing (clarity, tone, structure)", value="writing"),
    app_commands.Choice(name="Technical (completeness, accuracy, readability)", value="technical"),
    app_commands.Choice(name="Quick (3-bullet summary)", value="quick"),
]

# ── Prompts ───────────────────────────────────────────────────────────────────

_WRITING_PROMPT = """You are a professional writing editor. Review the following text and provide structured feedback.

Format your response EXACTLY as:
**✅ Strengths**
- [strength 1]
- [strength 2]
- [strength 3]

**🔧 Areas to Improve**
- [issue 1 with specific example from the text]
- [issue 2 with specific example]
- [issue 3 with specific example]

**💡 Specific Suggestions**
- [actionable suggestion 1]
- [actionable suggestion 2]
- [actionable suggestion 3]

**📊 Overall**
[2-3 sentence overall assessment with grade: Excellent / Good / Needs Work / Major Revision]

Text to review:
{text}"""

_TECHNICAL_PROMPT = """You are a senior technical writer and engineer. Review the following technical document.

Format your response EXACTLY as:
**✅ What Works Well**
- [strength 1]
- [strength 2]

**🔧 Gaps & Issues**
- [issue 1: missing or unclear section]
- [issue 2: accuracy concern or ambiguity]
- [issue 3: readability or structure issue]

**💡 Recommendations**
- [concrete improvement 1]
- [concrete improvement 2]
- [concrete improvement 3]

**📊 Overall**
[2-3 sentence technical assessment. Note: completeness, accuracy, target audience fit.]

Document to review:
{text}"""

_QUICK_PROMPT = """Review this text in exactly 3 bullet points. Be direct and actionable.
Format:
✅ **Best aspect**: [one sentence]
⚠️ **Main issue**: [one sentence]
💡 **Top suggestion**: [one sentence]

Text:
{text}"""

_PROMPTS = {
    "writing": _WRITING_PROMPT,
    "technical": _TECHNICAL_PROMPT,
    "quick": _QUICK_PROMPT,
}

# ── Vault save view ───────────────────────────────────────────────────────────


class _ReviewView(discord.ui.View):
    def __init__(self, review_text: str, filename: str):
        super().__init__(timeout=300)
        self.review_text = review_text
        self.filename = filename

    @discord.ui.button(label="💾 Save Review to Vault", style=discord.ButtonStyle.green)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            from obsidian_writer import save_to_vault

            result = await save_to_vault(
                title=f"Review - {self.filename}",
                content=self.review_text,
                content_type="review",
                tags=["review"],
                source_url="",
            )
            await interaction.followup.send(f"✅ Saved to vault: {result}", ephemeral=True)
        except Exception as e:
            log.exception("review vault save failed")
            await interaction.followup.send(f"❌ Failed to save: {e}", ephemeral=True)


# ── Core review helper ────────────────────────────────────────────────────────


async def _do_review(interaction: discord.Interaction, text: str, mode: str, filename: str) -> None:
    from llm.chat import chat

    prompt_template = _PROMPTS.get(mode, _WRITING_PROMPT)

    if len(text) > 8000:
        text = text[:8000] + "\n\n[Text truncated to 8000 characters]"

    response_text, _, model = await chat(
        prompt_template.format(text=text), model_preference="auto"
    )

    embed = discord.Embed(
        title=f"📋 Review: {filename[:50]}",
        description=truncate_for_embed(response_text, 4000),
        color=discord.Color.blue(),
    )
    embed.set_footer(text=f"Mode: {mode} | via {model}")

    view = _ReviewView(review_text=response_text, filename=filename)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ── Modal ─────────────────────────────────────────────────────────────────────


class ReviewTextModal(discord.ui.Modal, title="Paste Text for Review"):
    text_input = discord.ui.TextInput(
        label="Paste your text here",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await _do_review(
                interaction,
                text=self.text_input.value,
                mode=self.mode,
                filename="pasted-text",
            )
        except Exception as e:
            log.exception("review text modal submit failed")
            await interaction.followup.send(f"❌ Review failed: {e}", ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────


class ReviewCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    review = app_commands.Group(name="review", description="Document review and critique")

    # ── /review text ──────────────────────────────────────────────────────

    @review.command(name="text", description="Paste text for AI review")
    @app_commands.describe(mode="Review mode (default: writing)")
    @app_commands.choices(mode=MODE_CHOICES)
    async def review_text(self, interaction: discord.Interaction, mode: str = "writing"):
        modal = ReviewTextModal(mode=mode)
        await interaction.response.send_modal(modal)

    # ── /review file ──────────────────────────────────────────────────────

    @review.command(name="file", description="Upload a file for AI review")
    @app_commands.describe(
        file="File to review (.docx, .xlsx, .txt, .md, .py, .json, .csv)",
        mode="Review mode (default: writing)",
    )
    @app_commands.choices(mode=MODE_CHOICES)
    async def review_file(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        mode: str = "writing",
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            raw_bytes = await file.read()
            name_lower = file.filename.lower()

            if name_lower.endswith(".docx"):
                from document_skills import read_word
                extracted = read_word(raw_bytes)
            elif name_lower.endswith(".xlsx"):
                from document_skills import read_excel
                extracted = read_excel(raw_bytes)
            elif any(name_lower.endswith(ext) for ext in (".txt", ".md", ".py", ".json", ".csv")):
                extracted = raw_bytes.decode("utf-8", errors="replace")
            elif name_lower.endswith(".pdf"):
                try:
                    import io

                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                        extracted = "\n".join(
                            page.extract_text() or "" for page in pdf.pages
                        )
                except ImportError:
                    extracted = raw_bytes.decode("utf-8", errors="replace")
            else:
                await interaction.followup.send(
                    "❌ Unsupported format. Supported: .docx, .xlsx, .txt, .md, .py, .json, .csv",
                    ephemeral=True,
                )
                return

            await _do_review(
                interaction,
                text=extracted,
                mode=mode,
                filename=file.filename,
            )
        except Exception as e:
            log.exception("review file failed")
            await interaction.followup.send(f"❌ Review failed: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ReviewCog(bot))
