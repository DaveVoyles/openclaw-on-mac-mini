"""
Document Editing Cog — /doc and /sheet slash commands for Word and Excel files.

Commands:
  /doc read      — Read a .docx file and display its content
  /doc edit      — Edit a .docx file using natural language instructions (AI-assisted)
  /doc create    — Create a new .docx file from a description

  /sheet read    — Read an .xlsx file and display as a markdown table
  /sheet edit    — Edit an .xlsx file using natural language instructions (AI-assisted)
  /sheet create  — Create a new .xlsx file from a description
"""

import io
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, truncate_for_embed
from discord_error import build_error_embed
from document_skills import (
    create_excel,
    create_word,
    edit_excel,
    edit_word,
    read_excel,
    read_word,
)

log = logging.getLogger("openclaw")

NAS_OPENCLAW_FOLDER = "/volume1/documents/OpenClaw"


class _SaveToNASView(discord.ui.View):
    """Button to save a generated file to the Synology NAS."""

    def __init__(self, file_bytes: bytes, filename: str, *, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.file_bytes = file_bytes
        self.filename = filename

    @discord.ui.button(label="💾 Save to NAS", style=discord.ButtonStyle.green)
    async def save_nas(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            from nas import nas_create_folder, nas_write_file

            await nas_create_folder(NAS_OPENCLAW_FOLDER)
            result = await nas_write_file(
                content=self.file_bytes,
                remote_folder=NAS_OPENCLAW_FOLDER,
                filename=self.filename,
            )
            await interaction.followup.send(f"💾 {result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Save failed: {e}", ephemeral=True)
        audit_log(interaction.user, "save_to_nas", self.filename)
        self.stop()

log = logging.getLogger("openclaw.doc_cog")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _llm_chat(prompt: str) -> str:
    """Send a prompt to the LLM and return the response text."""
    from llm.chat import chat

    response_text, _history, _model = await chat(prompt, model_preference="auto")
    return response_text


def _parse_json(raw: str):
    """Extract JSON from an LLM response, stripping markdown fences."""
    text = raw.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


async def _parse_edit_instructions(
    content: str, instructions: str, doc_type: str
) -> dict | list:
    """Use the LLM to convert natural language instructions into structured edits."""
    if doc_type == "word":
        format_spec = (
            "Respond with a JSON object of find-and-replace pairs:\n"
            '{"find_text_1": "replace_text_1", "find_text_2": "replace_text_2"}'
        )
    else:
        format_spec = (
            "Respond with a JSON array of cell edits:\n"
            '[{"cell": "A1", "value": "new value"}, {"cell": "B3", "value": "42"}]'
        )

    prompt = (
        f"Given this {doc_type} document content:\n---\n{content[:3000]}\n---\n\n"
        f"The user wants to: {instructions}\n\n{format_spec}\n\n"
        "Respond ONLY with valid JSON, no explanation."
    )
    raw = await _llm_chat(prompt)
    return _parse_json(raw)


async def _generate_doc_content(instructions: str) -> tuple[str, str, list[str]]:
    """Ask the LLM to generate Word document content.

    Returns (title, body, headers).
    """
    prompt = (
        f"Create a Word document based on this request: {instructions}\n\n"
        "Respond with a JSON object:\n"
        '{"title": "Document Title", "headers": ["Section 1", "Section 2"], '
        '"body": "Full document text with section headers on their own lines."}\n\n'
        "Respond ONLY with valid JSON, no explanation."
    )
    raw = await _llm_chat(prompt)
    data = _parse_json(raw)
    return data["title"], data["body"], data.get("headers", [])


async def _generate_sheet_content(instructions: str) -> tuple[str, list[str], list[list]]:
    """Ask the LLM to generate Excel spreadsheet content.

    Returns (title, headers, rows).
    """
    prompt = (
        f"Create an Excel spreadsheet based on this request: {instructions}\n\n"
        "Respond with a JSON object:\n"
        '{"title": "Spreadsheet Title", "headers": ["Col1", "Col2"], '
        '"rows": [["val1", "val2"], ["val3", "val4"]]}\n\n'
        "Respond ONLY with valid JSON, no explanation."
    )
    raw = await _llm_chat(prompt)
    data = _parse_json(raw)
    return data["title"], data["headers"], data.get("rows", [])


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class DocCog(commands.Cog, name="Documents"):
    """Read, edit, and create Word and Excel documents."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- command groups -----------------------------------------------------
    doc = app_commands.Group(name="doc", description="Word document commands (.docx)")
    sheet = app_commands.Group(name="sheet", description="Excel spreadsheet commands (.xlsx)")

    # ======================================================================
    # /doc commands
    # ======================================================================

    @doc.command(name="read", description="Read a Word document and display its content")
    @app_commands.describe(file="The .docx file to read")
    @require_auth()
    async def doc_read(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.lower().endswith(".docx"):
            await interaction.response.send_message(
                "❌ Please attach a `.docx` file.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            file_bytes = await file.read()
            text = await read_word(file_bytes)

            if not text.strip():
                await interaction.followup.send("📄 The document appears to be empty.")
                return

            if len(text) > 4000:
                txt_file = discord.File(
                    io.BytesIO(text.encode()), filename=f"{file.filename}.txt"
                )
                await interaction.followup.send(
                    "📄 Document content (too long for embed):", file=txt_file
                )
            else:
                embed = discord.Embed(
                    title=f"📄 {file.filename}",
                    description=truncate_for_embed(text),
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed)

            audit_log(interaction, "doc_read", file.filename)
        except Exception as exc:
            log.exception("doc_read failed")
            await interaction.followup.send(embed=build_error_embed(exc, context="/doc read"), ephemeral=True)

    @doc.command(name="edit", description="Edit a Word document using AI instructions")
    @app_commands.describe(
        file="The .docx file to edit",
        instructions="What changes to make (e.g., 'Replace Q1 with Q2')",
    )
    @require_auth()
    async def doc_edit(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        instructions: str,
    ):
        if not file.filename.lower().endswith(".docx"):
            await interaction.response.send_message(
                "❌ Please attach a `.docx` file.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            file_bytes = await file.read()
            text = await read_word(file_bytes)

            edits = await _parse_edit_instructions(text, instructions, "word")
            if not isinstance(edits, dict) or not edits:
                await interaction.followup.send(
                    "⚠️ Could not determine edits from your instructions. "
                    "Try being more specific."
                )
                return

            modified = await edit_word(file_bytes, edits)
            out_file = discord.File(io.BytesIO(modified), filename=file.filename)

            summary = "\n".join(
                f"• `{k}` → `{v}`" for k, v in edits.items()
            )
            embed = discord.Embed(
                title="✏️ Document Edited",
                description=f"**Changes applied:**\n{truncate_for_embed(summary, 3000)}",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, file=out_file)
            audit_log(interaction, "doc_edit", f"{file.filename}: {instructions}")

        except json.JSONDecodeError:
            await interaction.followup.send(
                "❌ AI returned invalid edit instructions. Please try rephrasing.",
                ephemeral=True,
            )
        except Exception as exc:
            log.exception("doc_edit failed")
            await interaction.followup.send(embed=build_error_embed(exc, context="/doc edit"), ephemeral=True)

    @doc.command(name="create", description="Create a new Word document from a description")
    @app_commands.describe(instructions="Describe the document to create")
    @require_auth()
    async def doc_create(self, interaction: discord.Interaction, instructions: str):
        await interaction.response.defer()
        try:
            title, body, headers = await _generate_doc_content(instructions)
            doc_bytes = await create_word(title, body, headers)

            safe_title = "".join(c for c in title if c.isalnum() or c in " _-")[:50]
            filename = f"{safe_title.strip() or 'document'}.docx"
            out_file = discord.File(io.BytesIO(doc_bytes), filename=filename)

            embed = discord.Embed(
                title=f"📝 Created: {title}",
                description=truncate_for_embed(body[:500] + ("…" if len(body) > 500 else "")),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, file=out_file, view=_SaveToNASView(doc_bytes, filename))
            audit_log(interaction, "doc_create", instructions)

        except json.JSONDecodeError:
            await interaction.followup.send(
                "❌ AI returned invalid content. Please try rephrasing.",
                ephemeral=True,
            )
        except Exception as exc:
            log.exception("doc_create failed")
            await interaction.followup.send(embed=build_error_embed(exc, context="/doc create"), ephemeral=True)

    # ======================================================================
    # /sheet commands
    # ======================================================================

    @sheet.command(name="read", description="Read an Excel spreadsheet and display its content")
    @app_commands.describe(file="The .xlsx file to read")
    @require_auth()
    async def sheet_read(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.lower().endswith(".xlsx"):
            await interaction.response.send_message(
                "❌ Please attach an `.xlsx` file.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            file_bytes = await file.read()
            text = await read_excel(file_bytes)

            if not text.strip():
                await interaction.followup.send("📊 The spreadsheet appears to be empty.")
                return

            if len(text) > 4000:
                txt_file = discord.File(
                    io.BytesIO(text.encode()), filename=f"{file.filename}.txt"
                )
                await interaction.followup.send(
                    "📊 Spreadsheet content (too long for embed):", file=txt_file
                )
            else:
                embed = discord.Embed(
                    title=f"📊 {file.filename}",
                    description=truncate_for_embed(text),
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed)

            audit_log(interaction, "sheet_read", file.filename)
        except Exception as exc:
            log.exception("sheet_read failed")
            await interaction.followup.send(embed=build_error_embed(exc, context="/sheet read"), ephemeral=True)

    @sheet.command(name="edit", description="Edit an Excel spreadsheet using AI instructions")
    @app_commands.describe(
        file="The .xlsx file to edit",
        instructions="What changes to make (e.g., 'Set cell B3 to 42')",
    )
    @require_auth()
    async def sheet_edit(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        instructions: str,
    ):
        if not file.filename.lower().endswith(".xlsx"):
            await interaction.response.send_message(
                "❌ Please attach an `.xlsx` file.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            file_bytes = await file.read()
            text = await read_excel(file_bytes)

            edits = await _parse_edit_instructions(text, instructions, "excel")
            if not isinstance(edits, list) or not edits:
                await interaction.followup.send(
                    "⚠️ Could not determine edits from your instructions. "
                    "Try being more specific."
                )
                return

            modified = await edit_excel(file_bytes, edits)
            out_file = discord.File(io.BytesIO(modified), filename=file.filename)

            summary = "\n".join(
                f"• `{e['cell']}` → `{e['value']}`" for e in edits
            )
            embed = discord.Embed(
                title="✏️ Spreadsheet Edited",
                description=f"**Changes applied:**\n{truncate_for_embed(summary, 3000)}",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, file=out_file)
            audit_log(interaction, "sheet_edit", f"{file.filename}: {instructions}")

        except json.JSONDecodeError:
            await interaction.followup.send(
                "❌ AI returned invalid edit instructions. Please try rephrasing.",
                ephemeral=True,
            )
        except Exception as exc:
            log.exception("sheet_edit failed")
            await interaction.followup.send(embed=build_error_embed(exc, context="/sheet edit"), ephemeral=True)

    @sheet.command(name="create", description="Create a new Excel spreadsheet from a description")
    @app_commands.describe(instructions="Describe the spreadsheet to create")
    @require_auth()
    async def sheet_create(self, interaction: discord.Interaction, instructions: str):
        await interaction.response.defer()
        try:
            title, headers, rows = await _generate_sheet_content(instructions)
            sheet_bytes = await create_excel(title, headers, rows)

            safe_title = "".join(c for c in title if c.isalnum() or c in " _-")[:50]
            filename = f"{safe_title.strip() or 'spreadsheet'}.xlsx"
            out_file = discord.File(io.BytesIO(sheet_bytes), filename=filename)

            preview = " | ".join(headers)
            embed = discord.Embed(
                title=f"📊 Created: {title}",
                description=f"**Columns:** {preview}\n**Rows:** {len(rows)}",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, file=out_file, view=_SaveToNASView(sheet_bytes, filename))
            audit_log(interaction, "sheet_create", instructions)

        except json.JSONDecodeError:
            await interaction.followup.send(
                "❌ AI returned invalid content. Please try rephrasing.",
                ephemeral=True,
            )
        except Exception as exc:
            log.exception("sheet_create failed")
            await interaction.followup.send(embed=build_error_embed(exc, context="/sheet create"), ephemeral=True)

    # -- error handler ------------------------------------------------------

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)
        else:
            log.exception("Unhandled error in DocCog: %s", error)
            msg = f"❌ An unexpected error occurred: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DocCog(bot))
