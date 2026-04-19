"""Browser navigation skill — Playwright/Chromium (read-only)."""
import logging
import re

log = logging.getLogger("openclaw.browser")

_MAX_CHARS = 4000


async def navigate_and_extract(
    url: str,
    css_selector: str | None = None,
    extract_mode: str = "text",
    timeout_ms: int = 30_000,
) -> str:
    """Navigate to URL and extract content.

    Args:
        url: URL to visit.
        css_selector: Optional CSS selector to target a specific element; if None,
            extracts main content heuristically (body text).
        extract_mode: "text" (default), "html", or "links".
        timeout_ms: Navigation timeout in milliseconds.

    Returns:
        Extracted text / html / newline-joined links as a string,
        truncated to _MAX_CHARS to avoid context overflow.
    """
    from playwright.async_api import async_playwright  # lazy import

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="networkidle")

            if extract_mode == "links":
                hrefs: list[str] = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href)",
                )
                result = "\n".join(hrefs)
            elif extract_mode == "html":
                if css_selector:
                    elem = page.locator(css_selector).first
                    result = await elem.inner_html()
                else:
                    result = await page.content()
            else:  # "text"
                if css_selector:
                    elem = page.locator(css_selector).first
                    result = await elem.inner_text()
                else:
                    result = await page.inner_text("body")
        finally:
            await browser.close()

    # Collapse excessive whitespace and limit length
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    if len(result) > _MAX_CHARS:
        result = result[:_MAX_CHARS] + f"\n\n…[truncated at {_MAX_CHARS} chars]"

    log.info("browser: extracted %d chars from %s (mode=%s)", len(result), url, extract_mode)
    return result


async def extract_links(url: str, filter_pattern: str | None = None, timeout_ms: int = 30_000) -> list[str]:
    """Navigate to URL and return all hyperlinks, optionally filtered.

    Args:
        url: URL to visit.
        filter_pattern: Optional regex pattern; only links matching it are returned.
        timeout_ms: Navigation timeout in milliseconds.

    Returns:
        List of absolute href strings.
    """
    from playwright.async_api import async_playwright  # lazy import

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            hrefs: list[str] = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href)",
            )
        finally:
            await browser.close()

    if filter_pattern:
        pat = re.compile(filter_pattern, re.IGNORECASE)
        hrefs = [h for h in hrefs if pat.search(h)]

    log.info("browser: extracted %d links from %s (filter=%r)", len(hrefs), url, filter_pattern)
    return hrefs
