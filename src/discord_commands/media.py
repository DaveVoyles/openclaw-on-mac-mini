"""Media commands: /analyze-image, /analyze-file, /briefing, /imagine."""

import asyncio
import io
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from constants import DOCUMENT_MAX_CHARS, MAX_FILE_SIZE, PDF_MAX_PAGES
from image_gen import generate_image
from image_gen import is_available as sd_is_available
from llm import SUPPORTED_IMAGE_MIMES
from llm import analyze_document as llm_analyze_document
from llm import analyze_image as llm_analyze_image
from llm import is_configured as llm_is_configured
from permissions import require_auth

from ._helpers import _get_http_session, truncate_for_embed

log = logging.getLogger(__name__)


def _register_media_commands(bot: commands.Bot, send_morning_briefing: Callable[..., Any]) -> None:
    """Register /analyze-image, /analyze-file, /briefing, and /imagine."""

    # ------------------------------------------------------------------
    # /analyze-image
    # ------------------------------------------------------------------

    @bot.tree.command(name="analyze-image", description="Analyze an image using Gemini AI vision")
    @app_commands.describe(
        image="Image file to analyze (PNG, JPEG, WebP, GIF, HEIC)",
        question="What to ask about the image (optional)",
    )
    @require_auth
    async def analyze_image_cmd(
        interaction: discord.Interaction,
        image: discord.Attachment,
        question: str = "Describe this image in detail. Note any text, errors, or important information.",
    ):

        mime = (image.content_type or "").split(";")[0].strip()
        if mime not in SUPPORTED_IMAGE_MIMES:
            await interaction.response.send_message(
                f"❌ Unsupported file type `{mime or 'unknown'}`. Supported: PNG, JPEG, WebP, GIF, HEIC",
                ephemeral=True,
            )
            return

        if image.size > MAX_FILE_SIZE:
            await interaction.response.send_message("❌ Image too large (max 20 MB).", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            session = await _get_http_session()
            async with session.get(image.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Could not download image (HTTP {resp.status}).")
                    return
                image_bytes = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            await interaction.followup.send(f"❌ Failed to fetch image: {e}")
            return

        result = await llm_analyze_image(image_bytes, mime, question)
        result = truncate_for_embed(result)

        embed = discord.Embed(
            title="🖼️ Image Analysis",
            description=result,
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"📎 {image.filename} • via Gemini Vision")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "analyze-image", detail=f"{image.filename} q={question[:60]}")

    # ------------------------------------------------------------------
    # /analyze-file
    # ------------------------------------------------------------------

    @bot.tree.command(name="analyze-file", description="Analyze a document or file using Gemini AI")
    @app_commands.describe(
        file="File to analyze (PDF, TXT, JSON, CSV, YAML, log files, etc.)",
        question="What to ask about the document (optional)",
    )
    @require_auth
    async def analyze_file_cmd(
        interaction: discord.Interaction,
        file: discord.Attachment,
        question: str = "Summarize this document and highlight the most important information.",
    ):

        if file.size > MAX_FILE_SIZE:
            await interaction.response.send_message("❌ File too large (max 20 MB).", ephemeral=True)
            return

        filename = file.filename.lower()
        mime = (file.content_type or "").split(";")[0].strip()

        await interaction.response.defer()

        try:
            session = await _get_http_session()
            async with session.get(file.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Could not download file (HTTP {resp.status}).")
                    return
                file_bytes = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            await interaction.followup.send(f"❌ Failed to download file: {e}")
            return

        extracted_text: str | None = None
        file_type_label = "text"

        if filename.endswith(".pdf") or mime == "application/pdf":
            file_type_label = "PDF"
            try:
                import pypdf

                reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                pages_text = []
                for page in reader.pages[:PDF_MAX_PAGES]:
                    page_text = page.extract_text()
                    if page_text:
                        pages_text.append(page_text)
                extracted_text = "\n\n".join(pages_text)
                if not extracted_text.strip():
                    await interaction.followup.send(
                        "⚠️ Could not extract text from this PDF (may be scanned/image-based)."
                    )
                    return
            except ImportError:
                await interaction.followup.send("❌ pypdf not installed. Add `pypdf>=4.0` to requirements.txt.")
                return
            except (OSError, ValueError, TypeError) as e:
                await interaction.followup.send(f"❌ Failed to parse PDF: {e}")
                return
        else:
            file_type_label = filename.rsplit(".", 1)[-1].upper() if "." in filename else "text"
            try:
                extracted_text = file_bytes.decode("utf-8", errors="replace")
            except (UnicodeDecodeError, ValueError) as e:
                await interaction.followup.send(f"❌ Could not decode file as text: {e}")
                return

        del file_bytes

        MAX_CHARS = DOCUMENT_MAX_CHARS
        truncated = False
        if len(extracted_text) > MAX_CHARS:
            extracted_text = extracted_text[:MAX_CHARS]
            truncated = True

        result = await llm_analyze_document(extracted_text, question)
        result = truncate_for_embed(result)

        embed = discord.Embed(
            title=f"📄 {file_type_label} Analysis",
            description=result,
            color=discord.Color.dark_blue(),
        )
        footer = f"📎 {file.filename} ({file.size // 1024} KB)"
        if truncated:
            footer += " • ⚠️ truncated to 50,000 chars"
        embed.set_footer(text=footer + " • via Gemini")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "analyze-file", detail=f"{file.filename} q={question[:60]}")

    # ------------------------------------------------------------------
    # /briefing
    # ------------------------------------------------------------------

    @bot.tree.command(
        name="briefing", description="Generate an on-demand morning briefing (weather, health, downloads, calendar)"
    )
    @require_auth
    async def briefing_cmd(interaction: discord.Interaction):
        if not llm_is_configured():
            await interaction.response.send_message("⚠️ LLM not configured.", ephemeral=True)
            return
        await interaction.response.defer()
        await send_morning_briefing(bot, channel_override=interaction.channel)
        try:
            await interaction.edit_original_response(content="✅ Briefing posted above.")
        except Exception as exc:  # broad: intentional — edit_original_response can fail in many ways
            log.debug("Briefing edit_original_response failed: %s", exc)
        audit_log(interaction.user, "briefing")

    # ------------------------------------------------------------------
    # /imagine
    # ------------------------------------------------------------------

    @bot.tree.command(name="imagine", description="Generate an image using local Stable Diffusion (free, on-device)")
    @app_commands.describe(
        prompt="Describe the image you want to generate",
        negative="Things to avoid in the image (optional)",
        width="Image width in pixels (default: 1024, max: 1536)",
        height="Image height in pixels (default: 1024, max: 1536)",
        steps="Inference steps — higher = better quality, slower (default: 20)",
    )
    @require_auth
    async def imagine_cmd(
        interaction: discord.Interaction,
        prompt: str,
        negative: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
    ):
        await interaction.response.defer()

        if not await sd_is_available():
            await interaction.edit_original_response(
                content=(
                    "⚠️ **Stable Diffusion service is not running.**\n"
                    "Start it on the host with: `python scripts/sd_server.py`\n"
                    "Or set `SD_URL` env var to point to your SD API."
                )
            )
            return

        await interaction.edit_original_response(
            content=f"🎨 *Generating image…* ({width}×{height}, {steps} steps)\nPrompt: `{prompt[:100]}`"
        )

        image_bytes, img_status = await generate_image(
            prompt,
            negative_prompt=negative,
            width=width,
            height=height,
            steps=steps,
        )

        if image_bytes is None:
            await interaction.edit_original_response(content=f"❌ Image generation failed: {img_status}")
            return

        embed = discord.Embed(
            title="🎨 Generated Image",
            description=f"**Prompt:** {prompt[:200]}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{width}×{height} · {steps} steps · local Stable Diffusion")
        img_file = discord.File(io.BytesIO(image_bytes), filename="openclaw_generated.png")
        embed.set_image(url="attachment://openclaw_generated.png")

        await interaction.edit_original_response(content=None, embed=embed, attachments=[img_file])
        audit_log(interaction.user, "imagine", detail=prompt[:200])
