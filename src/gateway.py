"""
OpenClaw API Gateway Skill — Maton managed OAuth proxy
Connects to 100+ third-party APIs (Slack, Google Workspace, Notion, GitHub,
HubSpot, Stripe, etc.) through a single MATON_API_KEY using managed OAuth.

Based on the ClawHub skill: https://clawhub.ai/byungkyu/api-gateway

Setup:
  1. Sign in at https://maton.ai/ and go to https://maton.ai/settings
  2. Copy your API key and add it to .env:
       MATON_API_KEY=your_api_key_here
  3. Create an OAuth connection for each app you want to use:
       gateway_create_connection("slack")
     Open the returned URL in a browser to complete OAuth.

Gateway base URL:  https://gateway.maton.ai/{app}/{native-api-path}
Control plane URL: https://ctrl.maton.ai/connections

Rate limit: 10 requests/second per account.
"""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote as urlquote

import aiohttp

from config import TIMEOUT_SLOW
from config import cfg as _cfg

log = logging.getLogger("openclaw.gateway")

MATON_API_KEY = _cfg.maton_api_key
GATEWAY_BASE = "https://gateway.maton.ai"
CTRL_BASE = "https://ctrl.maton.ai"

_TIMEOUT = TIMEOUT_SLOW

from http_session import SessionManager

_sessions = SessionManager(timeout=TIMEOUT_SLOW, name="gateway")
_get_gateway_session = _sessions.get
close_gateway_session = _sessions.close


def _headers(connection_id: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {MATON_API_KEY}",
        "Content-Type": "application/json",
    }
    if connection_id:
        h["Maton-Connection"] = connection_id
    return h


def _api_key_hint() -> str:
    return (
        "❌ MATON_API_KEY is not set. "
        "Sign in at https://maton.ai/settings, copy your API key, "
        "and add `MATON_API_KEY=...` to your .env file."
    )


# ---------------------------------------------------------------------------
# Async HTTP helper
# ---------------------------------------------------------------------------

async def _http_request(
    url: str,
    method: str = "GET",
    body: dict | None = None,
    extra_headers: dict | None = None,
    retries: int = 2,
) -> dict | list:
    """Make an authenticated async request to Maton with retry on transient failures."""
    session = await _get_gateway_session()
    headers = _headers()
    if extra_headers:
        headers.update(extra_headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with session.request(method, url, json=body, headers=headers) as resp:
                if resp.status >= 500 and attempt < retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status} from {url}: {text[:300]}")
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise RuntimeError(str(e)) from e
    # Unreachable in practice, but satisfies type checkers
    raise RuntimeError(str(last_exc))  # pragma: no cover


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

async def gateway_request(
    app: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
    connection_id: str | None = None,
) -> str:
    """
    Call a third-party API through the Maton gateway using managed OAuth.

    Args:
        app: Service name, e.g. "slack", "github", "google-sheets", "notion".
        path: Native API path (without the leading app prefix), e.g.
              "api/chat.postMessage" for Slack or "repos/owner/repo/issues" for GitHub.
        method: HTTP method — GET, POST, PUT, PATCH, DELETE (default: GET).
        body: Optional JSON request body as a dict.
        connection_id: Optional specific connection UUID if you have multiple
                       OAuth connections for the same app.

    Returns a formatted JSON string of the API response, or an error message.

    Examples:
      # List GitHub repos
      gateway_request("github", "user/repos")

      # Post Slack message
      gateway_request("slack", "api/chat.postMessage", "POST",
                      {"channel": "C0123456", "text": "Hello!"})

      # Read Google Sheet values
      gateway_request("google-sheets",
                      "v4/spreadsheets/SHEET_ID/values/Sheet1!A1:B10")
    """
    if not MATON_API_KEY:
        return _api_key_hint()

    # Guard: reject oversized request bodies (1 MB max)
    _MAX_BODY_SIZE = 1_048_576
    if body:
        try:
            encoded = json.dumps(body)
            if len(encoded) > _MAX_BODY_SIZE:
                return f"❌ Request body too large ({len(encoded):,} bytes, max {_MAX_BODY_SIZE:,})."
        except (TypeError, ValueError) as e:
            return f"❌ Request body is not JSON-serializable: {e}"

    # Validate app name: only lowercase alphanumeric and hyphens, max 100 chars
    if len(app) > 100:
        return "❌ App name too long (max 100 characters)."
    import re as _re
    if not _re.match(r"^[a-z0-9][a-z0-9-]*$", app.lower()):
        return "❌ Invalid app name. Only lowercase letters, digits, and hyphens are allowed."

    # Normalise: strip leading slash from path so we can build {app}/{path}
    clean_path = path.lstrip("/")
    url = f"{GATEWAY_BASE}/{app}/{clean_path}"
    method = method.upper()

    try:
        extra: dict[str, str] = {}
        if connection_id:
            extra["Maton-Connection"] = connection_id
        await _http_request(url, method, body, extra)
    except RuntimeError as e:
        return f"❌ Gateway error: {e}"
    except asyncio.TimeoutError:
        return f"❌ Gateway request timed out after {_TIMEOUT}s."
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        log.warning("Gateway request network/parse error: %s", e)
        return f"❌ Gateway error: {e}"
    except Exception as e:
        log.warning("Unexpected gateway_request error: %s", e)
        return f"❌ Unexpected error: {e}"


async def gateway_list_connections(app: str = "") -> str:
    """
    List all active Maton OAuth connections.

    Args:
        app: Optional service name to filter by (e.g. "slack"). Leave empty for all.

    Returns one connection per line with its ID, app name, and status.
    """
    if not MATON_API_KEY:
        return _api_key_hint()

    url = f"{CTRL_BASE}/connections"
    if app:
        url += f"?app={urlquote(app, safe='')}&status=ACTIVE"

    try:
        result = await _http_request(url)
    except RuntimeError as e:
        return f"❌ Could not list connections: {e}"
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        log.warning("Gateway list_connections network/parse error: %s", e)
        return f"❌ Connection list error: {e}"
    except Exception as e:
        log.warning("Unexpected gateway_list_connections error: %s", e)
        return f"❌ Unexpected error: {e}"

    connections: list[dict[str, Any]] = result.get("connections", [])  # type: ignore[union-attr]
    if not connections:
        filter_note = f" for `{app}`" if app else ""
        return f"ℹ️ No active connections found{filter_note}. Use `gateway_create_connection` to add one."

    lines = ["**Maton Connections**"]
    for conn in connections:
        cid = conn.get("connection_id", "?")[:8]
        status = conn.get("status", "?")
        service = conn.get("app", "?")
        updated = conn.get("last_updated_time", "")[:10]
        lines.append(f"• **{service}** `{cid}…` — {status} (updated {updated})")

    return "\n".join(lines)


async def gateway_create_connection(app: str) -> str:
    """
    Create a new Maton OAuth connection for a third-party app.
    Returns a URL that the user must open in a browser to complete the OAuth flow.

    Args:
        app: Service name to connect, e.g. "slack", "github", "google-calendar",
             "notion", "hubspot", "stripe". See the full list at
             https://clawhub.ai/byungkyu/api-gateway#supported-services
    """
    if not MATON_API_KEY:
        return _api_key_hint()

    url = f"{CTRL_BASE}/connections"
    try:
        result = await _http_request(url, "POST", {"app": app})
    except RuntimeError as e:
        return f"❌ Could not create connection for `{app}`: {e}"
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        log.warning("Gateway create_connection network/parse error: %s", e)
        return f"❌ Connection creation error: {e}"
    except Exception as e:
        log.warning("Unexpected gateway_create_connection error: %s", e)
        return f"❌ Unexpected error: {e}"

    conn = result.get("connection", result)  # type: ignore[union-attr]
    auth_url = conn.get("url", "")
    cid = conn.get("connection_id", "?")

    if auth_url:
        return (
            f"✅ Connection created for **{app}** (ID: `{cid}`).\n"
            f"Open this URL to complete OAuth authorization:\n{auth_url}"
        )
    return f"✅ Connection created for **{app}** (ID: `{cid}`)."


async def create_google_doc(title: str, content: str) -> str:
    """
    Create a Google Doc with the given title and populate it with content.
    Requires an active Maton 'google-docs' OAuth connection.

    Args:
        title: Title for the new document.
        content: Full text content to insert into the document body.
    """
    if not MATON_API_KEY:
        return _api_key_hint()

    # Step 1: Create empty document
    try:
        doc = await _http_request(
            f"{GATEWAY_BASE}/google-docs/v1/documents",
            "POST",
            {"title": title},
        )
    except RuntimeError as e:
        return f"❌ Could not create Google Doc: {e}"

    doc_id = doc.get("documentId")  # type: ignore[union-attr]
    if not doc_id:
        return "❌ Google Docs API did not return a document ID. Is 'google-docs' connected via Maton?"

    # Step 2: Insert content via batchUpdate
    try:
        await _http_request(
            f"{GATEWAY_BASE}/google-docs/v1/documents/{doc_id}:batchUpdate",
            "POST",
            {
                "requests": [
                    {
                        "insertText": {
                            "text": content,
                            "location": {"index": 1},
                        }
                    }
                ]
            },
        )
    except RuntimeError as e:
        # Doc was created but content insert failed — return partial success
        return (
            f"⚠️ Google Doc created but content insert failed: {e}\n"
            f"Doc ID: `{doc_id}` — open at https://docs.google.com/document/d/{doc_id}/edit"
        )

    return (
        f"✅ Google Doc created: **{title}**\n"
        f"🔗 https://docs.google.com/document/d/{doc_id}/edit"
    )


async def create_onedrive_file(
    filename: str,
    content: str,
    folder_path: str = "OpenClaw",
) -> str:
    """
    Save a text or markdown file to OneDrive.
    Requires an active Maton 'microsoft-onedrive' OAuth connection.

    Args:
        filename: File name including extension, e.g. 'report.md'.
        content: Text content to write.
        folder_path: Destination folder in OneDrive (default: 'OpenClaw').
                     Use '/' for the root, or 'Documents/Reports' for subdirectories.
    """
    if not MATON_API_KEY:
        return _api_key_hint()

    # Microsoft Graph: PUT /v1.0/me/drive/root:/{folder}/{file}:/content
    # The Maton proxy forwards the body; we send it as plain text via a raw request.
    clean_folder = folder_path.strip("/")
    clean_file = filename.lstrip("/")
    remote_path = f"{clean_folder}/{clean_file}" if clean_folder else clean_file

    session = await _get_gateway_session()
    url = f"{GATEWAY_BASE}/microsoft-onedrive/v1.0/me/drive/root:/{remote_path}:/content"
    headers = {
        "Authorization": f"Bearer {MATON_API_KEY}",
        "Content-Type": "text/plain",
    }

    try:
        async with session.put(
            url,
            data=content.encode("utf-8"),
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                if resp.status == 401:
                    return "❌ OneDrive: authentication failed. Connect via: `/ask Connect me to microsoft-onedrive via Maton`"
                if resp.status == 404:
                    return "❌ OneDrive: path not found. Check the folder path or connect microsoft-onedrive via Maton."
                return f"❌ OneDrive upload failed (HTTP {resp.status}): {body[:300]}"
            result = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        return f"❌ OneDrive upload timed out after {_TIMEOUT}s."
    except aiohttp.ClientError as e:
        log.warning("OneDrive upload error: %s", e)
        return f"❌ OneDrive upload error: {e}"

    file_url = result.get("webUrl", "")
    name = result.get("name", filename)
    return (
        f"✅ Saved to OneDrive: **{name}** in `{folder_path}`\n"
        + (f"🔗 {file_url}" if file_url else "")
    )


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

GATEWAY_SKILLS: dict[str, Any] = {
    "gateway_request": gateway_request,
    "gateway_list_connections": gateway_list_connections,
    "gateway_create_connection": gateway_create_connection,
    "create_google_doc": create_google_doc,
    "create_onedrive_file": create_onedrive_file,
}
