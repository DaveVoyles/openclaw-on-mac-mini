"""Image generation via local Stable Diffusion service on the host Mac Mini.

The bot calls a lightweight HTTP API running on the host at
``http://host.docker.internal:<SD_PORT>/generate``.

See ``scripts/sd_server.py`` for the host-side service that wraps the
``diffusers`` pipeline (Apple Silicon MPS backend).
"""

import asyncio
import io
import logging
import os

import aiohttp

log = logging.getLogger("openclaw.image_gen")

SD_URL = os.getenv("SD_URL", "http://host.docker.internal:7861")
SD_TIMEOUT = int(os.getenv("SD_TIMEOUT", "120"))  # seconds

_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def is_available() -> bool:
    """Check if the SD service is reachable."""
    try:
        session = await _get_session()
        async with session.get(
            f"{SD_URL}/health",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            return resp.status == 200
    except Exception as exc:
        log.debug("SD health check failed: %s", exc)
        return False


async def generate_image(
    prompt: str,
    *,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
    seed: int = -1,
) -> tuple[bytes | None, str]:
    """Generate an image and return ``(png_bytes, status_message)``.

    Returns ``(None, error_message)`` on failure.
    """
    # Clamp dimensions to safe values
    width = max(256, min(width, 1536))
    height = max(256, min(height, 1536))
    # Round to nearest 64 (required by most SD models)
    width = (width // 64) * 64
    height = (height // 64) * 64

    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt or "blurry, low quality, distorted, watermark",
        "width": width,
        "height": height,
        "steps": steps,
        "seed": seed,
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{SD_URL}/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=SD_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return None, f"SD service returned HTTP {resp.status}: {body[:200]}"
            image_bytes = await resp.read()
            if len(image_bytes) < 1000:
                return None, "SD service returned suspiciously small output."
            return image_bytes, "ok"
    except asyncio.TimeoutError:
        return None, f"Image generation timed out after {SD_TIMEOUT}s."
    except aiohttp.ClientError as e:
        return None, f"Cannot reach SD service at {SD_URL}: {e}"
    except Exception as e:
        log.error("Image generation error: %s", e)
        return None, f"Image generation failed: {e}"
