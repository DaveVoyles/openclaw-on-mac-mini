"""
OpenClaw Email Skill — Phase 6
Read and send email via Gmail or Outlook using IMAP/SMTP with App Passwords.
No OAuth2 required — works with a standard App Password (2FA must be enabled).

Gmail setup:
  1. Enable 2-Step Verification on your Google account.
  2. myaccount.google.com → Security → App Passwords → Create one (any label).
  3. Add to .env:
       GMAIL_USER=you@gmail.com
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Outlook / Microsoft 365 setup:
  1. Enable 2-Step Verification on your Microsoft account.
  2. account.live.com → Security → Advanced Security → App Passwords → Create.
  3. Add to .env:
       OUTLOOK_USER=you@outlook.com
       OUTLOOK_APP_PASSWORD=xxxxxxxxxxxx
  Note: For M365 (work/school) tenants, an admin must allow IMAP/SMTP AUTH.
"""

import asyncio
import email
import email.header
import imaplib
import logging
import re
import smtplib
import ssl
from email.mime.text import MIMEText

from config import cfg as _cfg

log = logging.getLogger(__name__)

# --- Gmail ---
GMAIL_USER = _cfg.gmail_user
GMAIL_APP_PASSWORD = _cfg.gmail_app_password
GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587

# --- Outlook / Microsoft 365 ---
OUTLOOK_USER = _cfg.outlook_user
OUTLOOK_APP_PASSWORD = _cfg.outlook_app_password
OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_SMTP_HOST = "smtp.office365.com"
OUTLOOK_SMTP_PORT = 587

# Gmail sent folder names to try in order
_GMAIL_SENT_FOLDERS = ['"[Gmail]/Sent Mail"', '"[Google Mail]/Sent Mail"']
# Outlook sent folder names to try in order
_OUTLOOK_SENT_FOLDERS = ['"Sent Items"', "Sent"]


def _decode_header(raw: str) -> str:
    """Decode an RFC 2047 encoded email header to a plain string."""
    parts = email.header.decode_header(raw or "")
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return "".join(decoded)


from utils import truncate as _truncate


def _provider_creds(provider: str) -> tuple[str, str, str, str, int] | None:
    """Return (user, password, imap_host, smtp_host, smtp_port) or None."""
    p = provider.lower()
    if p == "gmail":
        if GMAIL_USER and GMAIL_APP_PASSWORD:
            return GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST, GMAIL_SMTP_HOST, GMAIL_SMTP_PORT
    elif p in ("outlook", "hotmail", "microsoft", "ms365", "office365"):
        if OUTLOOK_USER and OUTLOOK_APP_PASSWORD:
            return (
                OUTLOOK_USER,
                OUTLOOK_APP_PASSWORD,
                OUTLOOK_IMAP_HOST,
                OUTLOOK_SMTP_HOST,
                OUTLOOK_SMTP_PORT,
            )
    return None


def _config_hint(provider: str) -> str:
    p = provider.lower()
    if p == "gmail":
        return "❌ Gmail not configured. Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` in `.env`."
    return "❌ Outlook not configured. Set `OUTLOOK_USER` and `OUTLOOK_APP_PASSWORD` in `.env`."


# ---------------------------------------------------------------------------
# Blocking helpers (wrapped via asyncio.to_thread)
# ---------------------------------------------------------------------------

_DEFAULT_SSL_CONTEXT = ssl.create_default_context()


def _imap_read_inbox(user: str, password: str, imap_host: str, count: int) -> list[dict]:
    """Fetch the last `count` message headers from INBOX via IMAP SSL."""
    messages: list[dict] = []
    with imaplib.IMAP4_SSL(imap_host, 993, ssl_context=_DEFAULT_SSL_CONTEXT) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "ALL")
        msg_nums = data[0].split()[-count:] if data[0] else []
        for num in reversed(msg_nums):
            _, msg_data = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if msg_data and msg_data[0]:
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                messages.append(
                    {
                        "from": _decode_header(msg.get("From", "")),
                        "subject": _decode_header(msg.get("Subject", "(no subject)")),
                        "date": msg.get("Date", ""),
                    }
                )
    return messages


def _imap_search(user: str, password: str, imap_host: str, query: str, provider: str) -> list[dict]:
    """Search INBOX for messages containing `query` in subject or body."""
    # Sanitize to prevent IMAP command injection
    safe_query = query.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")
    results: list[dict] = []
    with imaplib.IMAP4_SSL(imap_host, 993, ssl_context=_DEFAULT_SSL_CONTEXT) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, f'TEXT "{safe_query}"')
        msg_nums = (data[0].split() if data[0] else [])[-15:]
        for num in reversed(msg_nums):
            _, msg_data = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if msg_data and msg_data[0]:
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                results.append(
                    {
                        "from": _decode_header(msg.get("From", "")),
                        "subject": _decode_header(msg.get("Subject", "(no subject)")),
                        "date": msg.get("Date", ""),
                    }
                )
    return results[:15]


def _smtp_send(
    user: str,
    password: str,
    smtp_host: str,
    smtp_port: int,
    to: str,
    subject: str,
    body: str,
) -> None:
    """Send an email via SMTP with STARTTLS."""
    # Strip header-injection characters
    safe_to = to.strip().replace("\n", "").replace("\r", "")
    safe_subject = subject.replace("\n", " ").replace("\r", " ")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = safe_subject
    msg["From"] = user
    msg["To"] = safe_to

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls(context=_DEFAULT_SSL_CONTEXT)
        smtp.login(user, password)
        smtp.sendmail(user, [safe_to], msg.as_bytes())


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def read_inbox(provider: str = "gmail", count: int = 10) -> str:
    """Read the most recent emails from a Gmail or Outlook inbox."""
    creds = _provider_creds(provider)
    if not creds:
        return _config_hint(provider)

    user, password, imap_host, _, _ = creds
    count = min(max(count, 1), 25)

    try:
        messages = await asyncio.wait_for(
            asyncio.to_thread(_imap_read_inbox, user, password, imap_host, count),
            timeout=25,
        )
    except asyncio.TimeoutError:
        return "❌ Email read timed out — mail server may be slow."
    except imaplib.IMAP4.error as e:
        return f"❌ IMAP error (check App Password): {e}"
    except (OSError, ConnectionError) as e:
        return f"❌ Network error reading inbox: {e}"
    except Exception as e:  # broad: intentional
        return f"❌ Email error: {e}"

    if not messages:
        return f"✅ No messages in {provider.title()} inbox."

    lines = [f"**{provider.title()} Inbox** (last {len(messages)} messages)"]
    for m in messages:
        date_short = m["date"][:16] if m["date"] else "?"
        lines.append(f"• **{m['subject']}** — {m['from']} ({date_short})")

    return _truncate("\n".join(lines), 1900)


async def search_emails(query: str, provider: str = "gmail") -> str:
    """Search for emails by keyword in the Gmail or Outlook inbox."""
    creds = _provider_creds(provider)
    if not creds:
        return _config_hint(provider)

    user, password, imap_host, _, _ = creds

    try:
        messages = await asyncio.wait_for(
            asyncio.to_thread(_imap_search, user, password, imap_host, query, provider),
            timeout=25,
        )
    except asyncio.TimeoutError:
        return "❌ Email search timed out — mail server may be slow."
    except imaplib.IMAP4.error as e:
        return f"❌ IMAP search error: {e}"
    except (OSError, ConnectionError) as e:
        return f"❌ Network error searching inbox: {e}"
    except Exception as e:  # broad: intentional
        return f"❌ Search error: {e}"

    if not messages:
        return f"✅ No messages matching '{query}' in {provider.title()}."

    lines = [f"**{provider.title()} Search**: '{query}' ({len(messages)} result(s))"]
    for m in messages:
        date_short = m["date"][:16] if m["date"] else "?"
        lines.append(f"• **{m['subject']}** — {m['from']} ({date_short})")

    return _truncate("\n".join(lines), 1900)


async def send_email(to: str, subject: str, body: str, provider: str = "gmail") -> str:
    """Send an email via Gmail or Outlook."""
    creds = _provider_creds(provider)
    if not creds:
        return _config_hint(provider)

    # Validate recipient email address
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", to.strip()):
        return "❌ Invalid recipient email address."

    user, password, _, smtp_host, smtp_port = creds

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_smtp_send, user, password, smtp_host, smtp_port, to, subject, body),
            timeout=25,
        )
    except asyncio.TimeoutError:
        return "❌ Email send timed out — mail server may be slow."
    except smtplib.SMTPAuthenticationError:
        return "❌ Authentication failed — check your App Password in `.env`."
    except smtplib.SMTPRecipientsRefused:
        return f"❌ Recipient refused by mail server: {to}"
    except (OSError, ConnectionError) as e:
        return f"❌ Network error sending email: {e}"
    except Exception as e:  # broad: intentional
        return f"❌ Failed to send email: {e}"

    return f"✅ Email sent to **{to}** via {provider.title()}."


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sync helper and async skill for reading a single email by IMAP ID
# ---------------------------------------------------------------------------


def _imap_fetch_one(provider: str, msg_id: str) -> str:
    """Fetch the full RFC822 message for *msg_id* from INBOX and return a
    formatted string with From, Subject, Date, and plain-text body."""
    creds = _provider_creds(provider)
    if not creds:
        return _config_hint(provider)

    user, password, imap_host, _, _ = creds
    try:
        with imaplib.IMAP4_SSL(imap_host, 993, ssl_context=_DEFAULT_SSL_CONTEXT) as imap:
            imap.login(user, password)
            imap.select("INBOX", readonly=True)
            _, msg_data = imap.fetch(msg_id.encode(), "(RFC822)")
            if not msg_data or not msg_data[0]:
                return f"❌ No message found with ID {msg_id}."

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            from_addr = _decode_header(msg.get("From", ""))
            subject = _decode_header(msg.get("Subject", "(no subject)"))
            date = msg.get("Date", "")

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                        charset = part.get_content_charset() or "utf-8"
                        body = part.get_payload(decode=True).decode(charset, errors="replace")
                        break
            else:
                if msg.get_content_type() == "text/plain":
                    charset = msg.get_content_charset() or "utf-8"
                    body = msg.get_payload(decode=True).decode(charset, errors="replace")

            return f"**From:** {from_addr}\n**Subject:** {subject}\n**Date:** {date}\n\n{body.strip()}"
    except imaplib.IMAP4.error as e:
        return f"❌ IMAP error: {e}"
    except (OSError, ConnectionError) as e:
        return f"❌ Network error: {e}"
    except Exception as e:  # broad: intentional
        return f"❌ Error fetching email: {e}"


async def read_email_by_id(msg_id: str, provider: str = "gmail") -> str:
    """Fetch the full body of a single email by its sequential IMAP ID."""
    return await asyncio.to_thread(_imap_fetch_one, provider, msg_id)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

EMAIL_SKILLS = {
    "read_inbox": read_inbox,
    "search_emails": search_emails,
    "send_email": send_email,
    "read_email_by_id": read_email_by_id,
}
