#!/usr/bin/env python3
"""
Google OAuth2 Setup — OpenClaw
Performs a one-time authorization to obtain a refresh token for the
Google Calendar API (and optionally Gmail). No external dependencies required.

Usage:
    python scripts/google_oauth_setup.py [--force] [--check] [--env PATH]

Credentials are read from .env automatically. After a successful flow,
GOOGLE_OAUTH_REFRESH_TOKEN is written back to .env.

Flags:
  --force   Always re-authorize even if a refresh token is already present.
  --check   Verify the current refresh token is still valid without re-auth.
  --env     Path to the .env file (default: .env at the repo root).

Prerequisites:
  1. Create a project at console.cloud.google.com
  2. Enable "Google Calendar API" (and optionally "Gmail API")
  3. OAuth consent screen → External → add yourself as a test user
  4. Credentials → Create OAuth 2.0 Client ID → Desktop app
  5. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env
"""

import argparse
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    # Google Drive — required for /drive list, /drive read, /drive upload
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    # Google Contacts — required for /contacts search, /contacts get
    "https://www.googleapis.com/auth/contacts.readonly",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_PORT = 8085
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"

_auth_code: str | None = None
_auth_error: str | None = None


def _load_dotenv(env_path: Path) -> dict[str, str]:
    """Parse .env file into a dict. Ignores comments and blank lines."""
    result = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    return result


def _update_dotenv(env_path: Path, key: str, value: str) -> None:
    """Update or append KEY=VALUE in .env file."""
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        updated = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                new_lines.append(f"{key}={value}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n")
    else:
        env_path.write_text(f"{key}={value}\n")


def _check_token(client_id: str, client_secret: str, refresh_token: str) -> bool:
    """Exchange refresh token for an access token; return True if valid."""
    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return "access_token" in data
    except Exception:
        return False


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth2 redirect callback on localhost."""

    def do_GET(self):
        global _auth_code, _auth_error
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authorization successful!</h2>"
                             b"<p>You can close this tab and return to the terminal.</p></body></html>")
        else:
            _auth_error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>Authorization failed: {_auth_error}</h2></body></html>".encode())

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


def main() -> None:
    global _auth_code, _auth_error

    parser = argparse.ArgumentParser(description="OpenClaw — Google OAuth2 Setup")
    parser.add_argument("--force", action="store_true",
                        help="Re-authorize even if a refresh token already exists in .env")
    parser.add_argument("--check", action="store_true",
                        help="Verify the current refresh token is valid without re-authorizing")
    parser.add_argument("--env", default=None,
                        help="Path to .env file (default: .env at repo root)")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    env_path = Path(args.env) if args.env else script_dir.parent / ".env"

    env = _load_dotenv(env_path)

    print("=" * 50)
    print(" OpenClaw — Google OAuth2 Setup")
    print("=" * 50)
    print()

    # --- Read credentials ---
    client_id = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    existing_token = env.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")

    if client_id and client_secret:
        print("Using credentials from .env")
    else:
        print("Scopes that will be requested:")
        for s in SCOPES:
            print(f"  • {s}")
        print()
        if not client_id:
            client_id = input("Enter your Google OAuth2 Client ID: ").strip()
        if not client_secret:
            client_secret = input("Enter your Google OAuth2 Client Secret: ").strip()

    if not client_id or not client_secret:
        print("\nError: Client ID and Client Secret are required.")
        sys.exit(1)

    # --- --check mode ---
    if args.check:
        if not existing_token:
            print("No GOOGLE_OAUTH_REFRESH_TOKEN found in .env — cannot check.")
            sys.exit(1)
        print("Checking refresh token validity...")
        if _check_token(client_id, client_secret, existing_token):
            print("✅ Refresh token is valid.")
            sys.exit(0)
        else:
            print("❌ Refresh token is invalid or expired.")
            sys.exit(1)

    # --- Skip if already authorized ---
    if existing_token and not args.force:
        print("Already authorized (use --force to re-authorize)")
        sys.exit(0)

    # --- Full OAuth flow ---
    print()
    print("Scopes that will be requested:")
    for s in SCOPES:
        print(f"  • {s}")
    print()

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    server = HTTPServer(("localhost", REDIRECT_PORT), _OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print("\nOpening the authorization URL in your browser...")
    print(f"(Listening for callback on localhost:{REDIRECT_PORT})")
    print("If it does not open automatically, paste this URL manually:\n")
    print(auth_url)
    print()
    webbrowser.open(auth_url)

    print("Waiting for authorization...")
    server_thread.join(timeout=120)
    server.server_close()

    if _auth_error:
        print(f"\nAuthorization failed: {_auth_error}")
        sys.exit(1)

    if not _auth_code:
        print("\nTimed out waiting for authorization (120s). Try again.")
        sys.exit(1)

    print("Authorization code received!")

    token_payload = urllib.parse.urlencode(
        {
            "code": _auth_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"\nError exchanging authorization code: HTTP {e.code}")
        print(body)
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("\nNo refresh token in the response.")
        print(
            "Make sure 'access_type=offline' and 'prompt=consent' are in the request.\n"
            "Raw response:", tokens,
        )
        sys.exit(1)

    # Write token back to .env
    _update_dotenv(env_path, "GOOGLE_OAUTH_CLIENT_ID", client_id)
    _update_dotenv(env_path, "GOOGLE_OAUTH_CLIENT_SECRET", client_secret)
    _update_dotenv(env_path, "GOOGLE_OAUTH_REFRESH_TOKEN", refresh_token)
    print(f"\nRefresh token written to {env_path}")

    print("\n" + "=" * 50)
    print(" SUCCESS")
    print("=" * 50)
    print()
    print(f"GOOGLE_OAUTH_CLIENT_ID={client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={refresh_token}")
    print()
    print(f"Scopes granted: {', '.join(SCOPES)}")


if __name__ == "__main__":
    main()
