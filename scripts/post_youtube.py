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


def upload(
    token: str,
    path: pathlib.Path,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = "22",
) -> str:
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:15],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
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
        params={"uploadType": "resumable", "part": "snippet,status"},
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
