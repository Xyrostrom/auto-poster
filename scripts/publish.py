"""Orchestrator: pick one unposted video from Drive, caption it, post it.

Design rules that matter:
  * One video per run. Bulk posting is the fastest way to get flagged.
  * State is written per-platform, so a YouTube success followed by an
    Instagram failure does NOT cause the video to be re-uploaded to YouTube
    on the next run.
  * A video is only skipped once EVERY enabled platform has succeeded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import pathlib
import re
import tempfile

import caption as caption_mod
import post_instagram
import post_youtube
from common import (
    download_drive_file,
    env,
    google_access_token,
    list_drive_videos,
    load_state,
    save_state,
    setup_logging,
)
from stage_public import PublicAsset

LOG = logging.getLogger("autoposter")


# Long opaque blobs in an API error body are almost always credentials --
# bearer tokens, refresh tokens, signed URL query strings. state/posted.json is
# committed to the repo every run, so anything landing in it is permanent.
_SECRETISH = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}"
    r"|ya29\.[\w.\-]{20,}"
    r"|1//[\w.\-]{20,}"
    r"|IGQ[\w.\-]{20,}"
    r"|EA[A-Za-z0-9]{40,}"
    r"|(?i:(?:access_token|refresh_token|client_secret|token|signature)=)[^&\s\"']+)"
)


def _scrub(err: object, limit: int = 400) -> str:
    """Redact credential-shaped substrings, then truncate for storage."""
    return _SECRETISH.sub("[REDACTED]", str(err))[:limit]


def pick(videos: list[dict], state: dict, platforms: list[str]) -> dict | None:
    """First video (oldest-created) not yet done on every enabled platform."""
    for v in videos:
        done = state["posted"].get(v["id"], {}).get("platforms", {})
        if all(done.get(p, {}).get("ok") for p in platforms):
            continue
        return v
    return None


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="youtube,instagram",
                    help="comma separated: youtube,instagram")
    ap.add_argument("--dry-run", action="store_true",
                    help="pick + caption, but do not upload anywhere")
    args = ap.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    LOG.info("Enabled platforms: %s%s", platforms, "  (DRY RUN)" if args.dry_run else "")

    folder_id = env("DRIVE_FOLDER_ID")
    niche = env("CONTENT_CONTEXT", required=False, default="")

    token = google_access_token()
    videos = list_drive_videos(token, folder_id)
    if not videos:
        LOG.warning("No videos in the Drive folder. Nothing to do.")
        return 0

    state = load_state()
    target = pick(videos, state, platforms)
    if target is None:
        LOG.info("Every video has already been posted to %s. Nothing to do.", platforms)
        return 0

    LOG.info("Selected: %s (%s)", target["name"], target["id"])

    meta = caption_mod.generate(target["name"], niche)
    LOG.info("Title:   %s", meta["title"])
    LOG.info("Caption: %s", meta["caption"].replace("\n", " ")[:120])
    LOG.info("Tags:    %s", " ".join("#" + t for t in meta["hashtags"]))

    if args.dry_run:
        LOG.info("Dry run complete; nothing uploaded and state left untouched.")
        return 0

    record = state["posted"].setdefault(
        target["id"], {"name": target["name"], "platforms": {}}
    )
    record["name"] = target["name"]
    plat = record.setdefault("platforms", {})
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        local = download_drive_file(
            token, target["id"], pathlib.Path(tmp) / target["name"]
        )

        if "youtube" in platforms and not plat.get("youtube", {}).get("ok"):
            try:
                vid = post_youtube.upload(
                    token,
                    local,
                    title=meta["title"],
                    description=caption_mod.compose_yt_description(
                        meta["caption"], meta["hashtags"]
                    ),
                    tags=meta["hashtags"],
                    privacy=env("YT_PRIVACY", required=False, default="public"),
                )
                plat["youtube"] = {"ok": True, "id": vid, "at": now,
                                   "url": f"https://youtu.be/{vid}"}
            except SystemExit as e:
                LOG.error("YouTube failed: %s", _scrub(e))
                plat["youtube"] = {"ok": False, "error": _scrub(e), "at": now}
                failures.append("youtube")

        if "instagram" in platforms and not plat.get("instagram", {}).get("ok"):
            try:
                staging_repo = env("STAGING_REPO")
                staging_token = env("STAGING_TOKEN")
                with PublicAsset(staging_token, staging_repo, local) as asset:
                    media_id = post_instagram.post_reel(
                        env("IG_USER_ID"),
                        env("IG_ACCESS_TOKEN"),
                        asset.url,
                        caption_mod.compose_ig(meta["caption"], meta["hashtags"]),
                    )
                plat["instagram"] = {"ok": True, "id": media_id, "at": now}
            except SystemExit as e:
                LOG.error("Instagram failed: %s", _scrub(e))
                plat["instagram"] = {"ok": False, "error": _scrub(e), "at": now}
                failures.append("instagram")

    save_state(state)

    if failures:
        LOG.error("Completed with failures on: %s", ", ".join(failures))
        return 1
    LOG.info("Done. Posted '%s' to %s", target["name"], ", ".join(platforms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
