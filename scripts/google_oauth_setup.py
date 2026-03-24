#!/usr/bin/env python3
"""
Google OAuth2 Setup — OpenClaw
Performs a one-time authorization to obtain a refresh token for the
Google Calendar API (and optionally Gmail). No external dependencies required.

Usage:
    python scripts/google_oauth_setup.py

After running, add the three lines printed at the end to your .env file.
You only need to do this once — the refresh token persists until revoked.

Prerequisites:
  1. Create a project at console.cloud.google.com
  2. Enable "Google Calendar API" (and optionally "Gmail API")
  3. OAuth consent screen → External → add yourself as a test user
  4. Credentials → Create OAuth 2.0 Client ID → Desktop app
  5. Note the Client ID and Client Secret, then run this script
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def main() -> None:
    print("=" * 50)
    print(" OpenClaw — Google OAuth2 Setup")
    print("=" * 50)
    print()
    print("Scopes that will be requested:")
    for s in SCOPES:
        print(f"  • {s}")
    print()

    client_id = input("Enter your Google OAuth2 Client ID: ").strip()
    client_secret = input("Enter your Google OAuth2 Client Secret: ").strip()

    if not client_id or not client_secret:
        print("\nError: Client ID and Client Secret are required.")
        sys.exit(1)

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force consent screen so refresh token is always issued
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("\nOpening the authorization URL in your browser...")
    print("If it does not open automatically, paste this URL manually:\n")
    print(auth_url)
    print()
    webbrowser.open(auth_url)

    code = input(
        "After authorizing, paste the authorization code shown by Google here:\n> "
    ).strip()

    if not code:
        print("No code provided. Exiting.")
        sys.exit(1)

    # Exchange the authorization code for tokens
    token_payload = urllib.parse.urlencode(
        {
            "code": code,
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

    print("\n" + "=" * 50)
    print(" SUCCESS — add these lines to your .env file:")
    print("=" * 50)
    print()
    print(f"GOOGLE_OAUTH_CLIENT_ID={client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={refresh_token}")
    print()
    print(f"Scopes granted: {', '.join(SCOPES)}")


if __name__ == "__main__":
    main()
