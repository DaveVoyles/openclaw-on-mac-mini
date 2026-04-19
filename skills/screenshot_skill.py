"""Website screenshot skill — Playwright/Chromium."""
import logging
from typing import Literal

log = logging.getLogger("openclaw.screenshot")

ViewportKind = Literal["desktop", "mobile", "tablet"]

_VIEWPORTS: dict[str, dict] = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 390, "height": 844},
    "tablet": {"width": 768, "height": 1024},
}


async def take_website_screenshot(
    url: str,
    *,
    viewport: ViewportKind = "desktop",
    full_page: bool = True,
    timeout_ms: int = 30_000,
) -> bytes:
    """Capture a full-page PNG screenshot of url using headless Chromium.

    Returns raw PNG bytes.
    """
    from playwright.async_api import async_playwright

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    vp = _VIEWPORTS.get(viewport, _VIEWPORTS["desktop"])

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context(viewport=vp)
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            png_bytes: bytes = await page.screenshot(full_page=full_page, type="png")
        finally:
            await browser.close()

    log.info("screenshot: captured %d bytes from %s (viewport=%s)", len(png_bytes), url, viewport)
    return png_bytes
