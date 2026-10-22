"""One-time local helper: mint a Google refresh token for Drive + YouTube.

Run on your own machine (not in CI):

    python scripts/auth_setup.py

It starts a throwaway localhost listener, opens the consent screen, and prints
the refresh token you paste into the GOOGLE_REFRESH_TOKEN secret.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser

import requests

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
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
    client_id = input("Google OAuth client_id: ").strip()
    client_secret = input("Google OAuth client_secret: ").strip()

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": " ".join(SCOPES),
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

    print("\n" + "=" * 60)
    print("GOOGLE_REFRESH_TOKEN:")
    print(refresh)
    print("=" * 60)
    print("Store it as a GitHub Actions secret. Do not commit it.")


if __name__ == "__main__":
    main()
