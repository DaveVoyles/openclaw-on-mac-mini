"""OCR skill — extract text from images and PDFs using LLM vision or pypdf."""

from __future__ import annotations

import re

# Keywords that signal the user wants OCR / text extraction
_OCR_PATTERNS = re.compile(
    r"\b(ocr|extract\s+text|read\s+this|what\s+does\s+this\s+say|receipt|invoice)\b",
    re.IGNORECASE,
)


def is_ocr_request(text: str) -> bool:
    """Return True when *text* contains an OCR/text-extraction intent."""
    return bool(_OCR_PATTERNS.search(text))


async def extract_text_from_image(image_bytes: bytes, mime_type: str, hint: str = "") -> str:
    """Use LLM vision to extract text from an image."""
    from llm import analyze_image as llm_analyze_image  # lazy import

    prompt = f"Extract all text from this image verbatim. {hint}".strip()
    return await llm_analyze_image(image_bytes, mime_type, prompt)


async def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from a digital (non-scanned) PDF using pypdf."""
    import io
    from pypdf import PdfReader  # lazy import

    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        parts.append(page_text)
    return "\n".join(parts).strip()


async def ocr_file(file_bytes: bytes, mime_type: str, hint: str = "") -> str:
    """Route to image OCR or PDF text extraction based on mime_type.

    For scanned PDFs (no text layer), falls back to a message asking the user
    to send the document as an image so vision-based OCR can be used.
    """
    mime_lower = mime_type.lower()

    if mime_lower.startswith("image/"):
        return await extract_text_from_image(file_bytes, mime_type, hint)

    if mime_lower == "application/pdf":
        text = await extract_text_from_pdf(file_bytes)
        if not text:
            return (
                "⚠️ This PDF appears to be a scanned document with no selectable text layer. "
                "Please send the page(s) as an image (PNG/JPEG) so I can use vision-based OCR."
            )
        return text

    return f"⚠️ OCR is not supported for files of type '{mime_type}'. Please send an image or PDF."
