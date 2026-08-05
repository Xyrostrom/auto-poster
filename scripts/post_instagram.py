"""Publish a Reel via the Instagram Graph API.

Three-step flow, and every step can fail differently:
  1. POST /{ig_user_id}/media          -> creates a container, returns id
  2. GET  /{container_id}?status_code  -> poll until FINISHED (IG transcodes)
  3. POST /{ig_user_id}/media_publish  -> actually publishes

Requires an Instagram *Business* or *Creator* account linked to a Facebook
Page, and a long-lived token with instagram_content_publish.
"""
from __future__ import annotations

import logging

import requests

from common import GRAPH_API, poll

LOG = logging.getLogger("autoposter.instagram")

# IG rejects publishes beyond 50 per rolling 24h.
PUBLISH_LIMIT_HINT = 50


def _err(resp: requests.Response) -> str:
    try:
        e = resp.json().get("error", {})
        return f"{e.get('code')}/{e.get('error_subcode')}: {e.get('message')}"
    except ValueError:
        return resp.text[:300]


def create_container(ig_user_id: str, token: str, video_url: str, caption: str,
                     share_to_feed: bool = True) -> str:
    resp = requests.post(
        f"{GRAPH_API}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
            "access_token": token,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise SystemExit(f"IG container creation failed -> {_err(resp)}")
    container_id = resp.json()["id"]
    LOG.info("IG container created: %s", container_id)
    return container_id


def wait_ready(container_id: str, token: str, timeout_s: int = 900) -> None:
    def check() -> str:
        r = requests.get(
            f"{GRAPH_API}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=60,
        )
        if r.status_code != 200:
            return f"ERROR {_err(r)}"
        body = r.json()
        code = body.get("status_code", "UNKNOWN")
        if code == "ERROR":
            # `status` carries the human-readable reason (bad codec, aspect, etc.)
            raise SystemExit(f"IG rejected the media: {body.get('status')}")
        return code

    poll(
        check,
        ok=lambda s: s == "FINISHED",
        timeout_s=timeout_s,
        interval_s=15,
        what="Instagram transcode",
    )


def publish(ig_user_id: str, token: str, container_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_API}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=120,
    )
    if resp.status_code != 200:
        raise SystemExit(f"IG publish failed -> {_err(resp)}")
    media_id = resp.json()["id"]
    LOG.info("Instagram published: media_id=%s", media_id)
    return media_id


def post_reel(ig_user_id: str, token: str, video_url: str, caption: str) -> str:
    container_id = create_container(ig_user_id, token, video_url, caption)
    wait_ready(container_id, token)
    return publish(ig_user_id, token, container_id)
