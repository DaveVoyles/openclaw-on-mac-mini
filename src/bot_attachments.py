"""Attachment handling for OpenClaw bot - images and documents."""

import logging

import aiohttp
import discord

from constants import ATTACHMENT_TEXT_MAX_CHARS
from http_session import SessionManager
from llm import analyze_image as llm_analyze_image

log = logging.getLogger("openclaw.bot_attachments")

# Shared HTTP session for downloads
_attachment_sessions = SessionManager(timeout=30, name="attachments")


async def handle_image_attachment(
    attachment: discord.Attachment, question: str
) -> str:
    """Download and analyze an image attachment via Gemini vision."""
    try:
        session = await _attachment_sessions.get()
        async with session.get(
            attachment.url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status == 200:
                img_bytes = await resp.read()
                mime = (attachment.content_type or "").split(";")[0].strip()
                image_answer = await llm_analyze_image(img_bytes, mime, question)
                return f"{question}\n\n[Attachment analysis: {image_answer}]"
    except Exception as e:  # broad: intentional — aiohttp, discord, and mock-related errors
        log.warning("Failed to analyze image attachment: %s", e)
    return question


async def handle_doc_attachment(
    attachment: discord.Attachment, question: str
) -> str:
    """Download and analyze a document attachment via Gemini."""
    try:
        session = await _attachment_sessions.get()
        async with session.get(
            attachment.url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status == 200:
                raw = await resp.read()
                try:
                    doc_text = raw.decode("utf-8", errors="replace")[
                        :ATTACHMENT_TEXT_MAX_CHARS
                    ]
                    combined_query = (
                        f"{question}\n\n--- Attached Document (first {ATTACHMENT_TEXT_MAX_CHARS} chars) ---\n"
                        f"{doc_text}\n"
                        f"--- End Document ---"
                    )
                    return combined_query
                except (UnicodeDecodeError, ValueError) as decode_err:
                    log.warning("Failed to decode doc attachment: %s", decode_err)
    except Exception as e:  # broad: intentional — aiohttp, discord, and mock-related errors
        log.warning("Failed to download doc attachment: %s", e)
    return question
