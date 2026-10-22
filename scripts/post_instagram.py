"""Publish a Reel via the Instagram Graph API.

Three-step flow, and every step can fail differently:
  1. POST /{ig_user_id}/media          -> creates a container, returns id
  2. GET  /{container_id}?status_code  -> poll until FINISHED (IG transcodes)
  3. POST /{ig_user_id}/media_publish  -> actually publishes

Requires an Instagram *Business* or *Creator* account linked to a Facebook
Page, and a long-lived token with instagram_content_publish.

Idempotency: the container id is handed to `on_container` as soon as it
exists, so the caller can persist it. On the next run, `post_reel` is given
that id back and checks its status first -- a container that already reached
PUBLISHED (e.g. media_publish timed out client-side but succeeded) is never
published a second time.
"""
from __future__ import annotations

import logging
from typing import Callable

import requests

from common import GRAPH_API, http, poll

LOG = logging.getLogger("autoposter.instagram")

# IG rejects publishes beyond 50 per rolling 24h.
PUBLISH_LIMIT_HINT = 50

# Container states that will never become FINISHED.
_TERMINAL_BAD = {"ERROR", "EXPIRED"}


def _auth(token: str) -> dict[str, str]:
    # Header, not ?access_token=: query strings end up in exception messages
    # and proxy logs.
    return {"Authorization": f"Bearer {token}"}


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
        headers=_auth(token),
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise SystemExit(f"IG container creation failed -> {_err(resp)}")
    container_id = resp.json()["id"]
    LOG.info("IG container created: %s", container_id)
    return container_id


def container_status(container_id: str, token: str) -> tuple[str, str]:
    r = http.get(
        f"{GRAPH_API}/{container_id}",
        headers=_auth(token),
        params={"fields": "status_code,status"},
        timeout=60,
    )
    if r.status_code == 200:
        body = r.json()
        return body.get("status_code", "UNKNOWN"), str(body.get("status", ""))
    if 400 <= r.status_code < 500:
        # Expired/revoked token (code 190), bad id, permissions: polling for
        # 15 minutes will not fix any of these.
        raise SystemExit(f"IG status check failed ({r.status_code}) -> {_err(r)}")
    return f"HTTP_{r.status_code}", ""


def wait_ready(container_id: str, token: str, timeout_s: int = 600) -> None:
    def check() -> str:
        code, detail = container_status(container_id, token)
        if code in _TERMINAL_BAD:
            # `status` carries the human-readable reason (bad codec, aspect, etc.)
            raise SystemExit(f"IG container {code}: {detail}")
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
        headers=_auth(token),
        data={"creation_id": container_id},
        timeout=120,
    )
    if resp.status_code != 200:
        raise SystemExit(f"IG publish failed -> {_err(resp)}")
    media_id = resp.json()["id"]
    LOG.info("Instagram published: media_id=%s", media_id)
    return media_id


def resume(ig_user_id: str, token: str, container_id: str) -> str | None:
    """Try to finish a container left over from a previous run.

    Returns a media/container id if the reel is (now) published, or None if
    the old container is unusable and a fresh one must be created.
    """
    try:
        code, _ = container_status(container_id, token)
    except SystemExit as e:
        LOG.warning("Previous IG container %s not usable: %s", container_id, e)
        return None
    LOG.info("Previous IG container %s is %s", container_id, code)
    if code == "PUBLISHED":
        return container_id
    if code == "FINISHED":
        return publish(ig_user_id, token, container_id)
    return None


def post_reel(ig_user_id: str, token: str, video_url: str, caption: str,
              on_container: Callable[[str], None] | None = None) -> str:
    container_id = create_container(ig_user_id, token, video_url, caption)
    if on_container:
        on_container(container_id)
    wait_ready(container_id, token)
    return publish(ig_user_id, token, container_id)
