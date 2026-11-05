"""Upload a video to YouTube as a Short via resumable upload."""
from __future__ import annotations

import json
import logging
import pathlib

import requests

from common import YOUTUBE_UPLOAD, gheaders

LOG = logging.getLogger("autoposter.youtube")

# A video is treated as a Short automatically when it is vertical and <= 3 min.
# There is no API flag for it; #Shorts in the title/description is the only hint.

CHANNELS_API = "https://www.googleapis.com/youtube/v3/channels"


def check_channel(token: str, expected_id: str) -> None:
    """Refuse to upload unless the token belongs to channel `expected_id`.

    Needs the youtube.readonly scope (auth_setup.py --youtube asks for it).
    """
    resp = requests.get(
        CHANNELS_API,
        headers=gheaders(token),
        params={"part": "snippet", "mine": "true"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"Could not confirm the YouTube channel ({resp.status_code}). Mint the "
            "YouTube token with `python scripts/auth_setup.py --youtube`, or clear "
            "YT_CHANNEL_ID to skip this check."
        )
    items = resp.json().get("items", [])
    got = items[0]["id"] if items else None
    if got != expected_id:
        title = items[0]["snippet"].get("title") if items else "none"
        raise SystemExit(
            f"YouTube token is for channel {got} ({title}), not YT_CHANNEL_ID "
            f"{expected_id}. Refusing to upload to the wrong channel."
        )
    LOG.info("YouTube channel confirmed: %s (%s)", items[0]["snippet"].get("title"), got)


def upload(
    token: str,
    path: pathlib.Path,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = "24",
    language: str = "en",
) -> str:
    # Every setting here is chosen for reach: the right category and language put
    # the Short in front of viewers who watch that kind of video, "not made for
    # kids" keeps comments/likes/notifications on, embedding lets it spread off
    # YouTube, and subscribers get notified. Tags were already sized to YouTube's
    # 500-char limit by caption._fit_keywords.
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
            "publicStatsViewable": True,
            "license": "youtube",
            "containsSyntheticMedia": False,
        },
    }

    size = path.stat().st_size

    # Step 1: open a resumable session.
    init = requests.post(
        YOUTUBE_UPLOAD,
        headers={
            **gheaders(token),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/*",
        },
        params={"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "true"},
        data=json.dumps(metadata).encode("utf-8"),
        timeout=60,
    )
    if init.status_code not in (200, 201):
        raise SystemExit(f"YouTube session init failed ({init.status_code}): {init.text[:500]}")

    session_url = init.headers.get("Location")
    if not session_url:
        raise SystemExit("YouTube did not return a resumable session URL")

    # Step 2: send the bytes in one PUT. Runners have fast uplink and Shorts
    # are small; chunking only matters for multi-GB files.
    LOG.info("Uploading %.1f MB to YouTube", size / 1_048_576)
    with path.open("rb") as fh:
        put = requests.put(
            session_url,
            headers={"Content-Type": "video/*", "Content-Length": str(size)},
            data=fh,
            timeout=(30, 1800),
        )

    if put.status_code not in (200, 201):
        raise SystemExit(f"YouTube upload failed ({put.status_code}): {put.text[:500]}")

    video_id = put.json()["id"]
    LOG.info("YouTube published: https://youtu.be/%s", video_id)
    return video_id
