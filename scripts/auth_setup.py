"""One-time local helper: mint a Google refresh token for Drive + YouTube.

Run on your own machine (not in CI):

    python scripts/auth_setup.py            # Drive (+ YouTube) -> GOOGLE_REFRESH_TOKEN
    python scripts/auth_setup.py --youtube  # YouTube only     -> YOUTUBE_REFRESH_TOKEN

Use --youtube when videos should go to a brand channel (e.g. @tech.nexvora):
on the consent screen pick that channel, not your personal account.

It starts a throwaway localhost listener, opens the consent screen, and prints
the refresh token to store as a secret.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"

SCOPES = [
    # Full Drive (not readonly): the source video is deleted after a successful post.
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube.upload",
]

# --youtube: a brand channel cannot see your Drive, so it only gets YouTube
# scopes. readonly lets the bot confirm the channel before every upload;
# youtube (manage) lets setup_channel.py set the description, keywords, banner.
YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]

_code: str | None = None
_error: str | None = None
_done = threading.Event()
_state = secrets.token_urlsafe(16)
# PKCE: binds the code to this process even if the redirect is intercepted.
_verifier = secrets.token_urlsafe(64)
_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(_verifier.encode()).digest()
).rstrip(b"=").decode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        global _code, _error
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if qs.get("state", [""])[0] != _state:
            # Ignore stray requests (e.g. /favicon.ico) rather than aborting.
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch - possible CSRF. Start over.")
            return
        _code = qs.get("code", [None])[0]
        _error = qs.get("error", [None])[0]
        _done.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Done. Close this tab and return to the terminal.</h2>")

    def log_message(self, *a):  # silence default stderr spam
        pass


def main() -> None:
    youtube_only = "--youtube" in sys.argv[1:]
    scopes = YT_SCOPES if youtube_only else SCOPES
    var = "YOUTUBE_REFRESH_TOKEN" if youtube_only else "GOOGLE_REFRESH_TOKEN"
    if youtube_only:
        print("On the Google screen, choose the channel the videos should go to "
              "(e.g. Tech Nexvora), not your personal account.")
    client_id = input("Google OAuth client_id: ").strip()
    client_secret = input("Google OAuth client_secret: ").strip()

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        # Without prompt=consent Google omits refresh_token on re-authorisation.
        "prompt": "consent",
        "state": _state,
        "code_challenge": _challenge,
        "code_challenge_method": "S256",
    })

    # Bind to loopback only, never all interfaces.
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"{AUTH}?{params}"
    print(f"\nOpening consent screen. If it does not open, visit:\n{url}\n")
    webbrowser.open(url)

    got = _done.wait(timeout=300)
    server.shutdown()
    if not got:
        raise SystemExit("Timed out after 5 minutes waiting for the OAuth redirect.")
    if _error or not _code:
        raise SystemExit(f"Consent was not granted: {_error or 'no code returned'}")

    resp = requests.post(TOKEN, data={
        "code": _code,
        "code_verifier": _verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    body = resp.json()

    refresh = body.get("refresh_token")
    if not refresh:
        print("No refresh_token returned. Revoke the app at "
              "https://myaccount.google.com/permissions and run this again.")
        return

    if youtube_only:
        ch = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            headers={"Authorization": f"Bearer {body['access_token']}"},
            params={"part": "snippet", "mine": "true"},
            timeout=30,
        )
        items = ch.json().get("items", []) if ch.ok else []
        if items:
            print(f"\nChannel: {items[0]['snippet']['title']}")
            print(f"YT_CHANNEL_ID: {items[0]['id']}")
        else:
            print("\nWARNING: could not read the channel for this token.")

    print("\n" + "=" * 60)
    print(f"{var}:")
    print(refresh)
    print("=" * 60)
    print("Store it as a GitHub Actions secret. Do not commit it.")


if __name__ == "__main__":
    main()
