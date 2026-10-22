"""Shared helpers: config, logging, Google auth, Drive access, state."""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("autoposter")


def _make_session() -> requests.Session:
    """Shared session. Retries transient failures (429/5xx, connection resets)
    with backoff -- but only for idempotent methods. Uploads and publishes are
    POST/PUT and are never blindly replayed: a replay can mean a double post."""
    retry = Retry(
        total=4,
        connect=4,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "DELETE"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    return s


http = _make_session()

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "posted.json"

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
# v21.0 is retired 2027-01-21. Overridable so a future bump needs no code change.
GRAPH_API = f"https://graph.facebook.com/{os.environ.get('GRAPH_API_VERSION') or 'v26.0'}"

VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/x-m4v",
    "video/webm",
}


def setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def env(name: str, *, required: bool = True, default: str | None = None) -> str:
    """Read an env var. Fails loudly at startup rather than mid-upload."""
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(
            f"Missing required environment variable: {name}\n"
            f"Set it as a GitHub Actions secret (see README.md)."
        )
    return val or ""


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"posted": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOG.error("state/posted.json is corrupt; refusing to run rather than repost everything")
        raise
    data.setdefault("posted", {})
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mark_posted(state: dict[str, Any], file_id: str, record: dict[str, Any]) -> None:
    state["posted"][file_id] = record


# --------------------------------------------------------------------------
# google auth
# --------------------------------------------------------------------------

def google_access_token() -> str:
    """Exchange the long-lived refresh token for a short-lived access token.

    One Google account covers both Drive (read the source folder) and
    YouTube (upload), so a single refresh token with both scopes is enough.
    """
    # Token refresh is safe to replay; connection errors are retried by `http`.
    resp = http.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": env("GOOGLE_CLIENT_ID"),
            "client_secret": env("GOOGLE_CLIENT_SECRET"),
            "refresh_token": env("GOOGLE_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        # Do not dump the body verbatim: it can echo back client_secret.
        raise SystemExit(
            f"Google token refresh failed ({resp.status_code}). "
            f"error={resp.json().get('error', 'unknown') if resp.headers.get('content-type','').startswith('application/json') else 'unparseable'}. "
            "The refresh token is usually revoked when the Google account password "
            "changes or the OAuth app is still in 'Testing' mode (7-day expiry)."
        )
    return resp.json()["access_token"]


def gheaders(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# drive
# --------------------------------------------------------------------------

def list_drive_videos(token: str, folder_id: str) -> list[dict[str, Any]]:
    """Return every video directly inside folder_id, oldest-created first."""
    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, videoMediaMetadata)",
            "orderBy": "createdTime",
            "pageSize": "100",
            # Required for folders that live in a Shared Drive.
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = http.get(f"{DRIVE_API}/files", headers=gheaders(token), params=params, timeout=60)
        if resp.status_code != 200:
            raise SystemExit(f"Drive list failed ({resp.status_code}): {resp.text[:400]}")

        payload = resp.json()
        files.extend(payload.get("files", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    videos = [f for f in files if f.get("mimeType") in VIDEO_MIMES]
    LOG.info("Drive folder holds %d files, %d of them video", len(files), len(videos))
    return videos


def download_drive_file(token: str, file_id: str, dest: pathlib.Path,
                        root: pathlib.Path | None = None) -> pathlib.Path:
    """Stream a Drive file to disk. Streams so a 500MB video never lands in RAM."""
    # Defence in depth: callers must sanitise the name, but never write outside
    # `root` (an attacker-named Drive file could otherwise overwrite a script
    # that a later workflow step runs with secrets in its environment).
    if root is not None and not dest.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing download path outside {root}: {dest!r}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with http.get(
        f"{DRIVE_API}/files/{file_id}",
        headers=gheaders(token),
        params={"alt": "media", "supportsAllDrives": "true"},
        stream=True,
        timeout=(30, 600),
    ) as resp:
        if resp.status_code != 200:
            raise SystemExit(f"Drive download failed ({resp.status_code}): {resp.text[:400]}")
        written = 0
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
    LOG.info("Downloaded %s (%.1f MB)", dest.name, written / 1_048_576)
    return dest


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------

def poll(fn, *, ok, timeout_s: int, interval_s: int, what: str):
    """Poll fn() until ok(result) is truthy. Used for IG container processing."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if ok(last):
            return last
        LOG.info("Waiting on %s ... (%s)", what, last)
        time.sleep(interval_s)
    raise SystemExit(f"Timed out after {timeout_s}s waiting on {what}. Last state: {last}")
