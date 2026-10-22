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
    r"|sk-ant-[\w\-]{20,}"
    r"|AIza[\w\-]{30,}"
    r"|(?i:bearer)\s+[\w.\-]{20,}"
    r"|(?i:(?:access_token|refresh_token|client_secret|token|signature|key)=)[^&\s\"']+)"
)


def _scrub(err: object, limit: int = 400) -> str:
    """Redact credential-shaped substrings, then truncate for storage."""
    return _SECRETISH.sub("[REDACTED]", str(err))[:limit]


# A video that keeps failing (bad codec, wrong aspect ratio, ...) must not block
# the queue forever. After this many failed attempts on a platform it is
# skipped for that platform and the next video gets its turn.
MAX_ATTEMPTS = 3
KNOWN_PLATFORMS = {"youtube", "instagram"}


def _safe_local_name(name: str, file_id: str) -> str:
    """Drive names may contain '/', '..', or be absolute-looking. Never let a
    remote-controlled string pick the path on disk."""
    base = re.sub(r"[^\w.\-]+", "_", pathlib.PurePath(name).name).strip("._")
    suffix = pathlib.PurePath(base).suffix or ".mp4"
    stem = pathlib.PurePath(base).stem[:80] or "video"
    return f"{stem}-{re.sub(r'[^A-Za-z0-9_-]', '', file_id)[:16]}{suffix}"


def _done(entry: dict) -> bool:
    """A platform is finished for a video if it succeeded or gave up."""
    return bool(entry.get("ok")) or entry.get("attempts", 0) >= MAX_ATTEMPTS


def pick(videos: list[dict], state: dict, platforms: list[str]) -> dict | None:
    """First video (oldest-created) not yet done on every enabled platform."""
    for v in videos:
        done = state["posted"].get(v["id"], {}).get("platforms", {})
        if all(_done(done.get(p, {})) for p in platforms):
            continue
        return v
    return None


def _record_failure(plat: dict, name: str, err: BaseException, now: str) -> None:
    prev = plat.get(name, {})
    attempts = int(prev.get("attempts", 0)) + 1
    plat[name] = {"ok": False, "error": _scrub(err), "at": now, "attempts": attempts}
    # Keep a known IG container id so the next run can check/resume it.
    cid = prev.get("container_id")
    if cid:
        plat[name]["container_id"] = cid
    if attempts >= MAX_ATTEMPTS:
        LOG.error("%s: giving up on this video after %d attempts", name, attempts)


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="youtube,instagram",
                    help="comma separated: youtube,instagram")
    ap.add_argument("--dry-run", action="store_true",
                    help="pick + caption, but do not upload anywhere")
    args = ap.parse_args()

    platforms = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    unknown = set(platforms) - KNOWN_PLATFORMS
    if not platforms or unknown:
        ap.error(f"--platforms must be a non-empty subset of {sorted(KNOWN_PLATFORMS)}; "
                 f"got {args.platforms!r}")
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

    pending = [p for p in platforms if not _done(plat.get(p, {}))]

    with tempfile.TemporaryDirectory() as tmp:
        try:
            local = download_drive_file(
                token, target["id"],
                pathlib.Path(tmp) / _safe_local_name(target["name"], target["id"]),
                root=pathlib.Path(tmp),
            )
        except (SystemExit, Exception) as e:
            # Count it as an attempt everywhere so an undownloadable file
            # cannot block the queue forever.
            LOG.error("Download failed: %s", _scrub(e))
            for p in pending:
                _record_failure(plat, p, e, now)
            save_state(state)
            return 1

        if "youtube" in pending:
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
            # Exception, not just SystemExit: a requests.Timeout escaping here
            # would skip save_state and the video would be re-posted tomorrow.
            except (SystemExit, Exception) as e:
                LOG.error("YouTube failed: %s", _scrub(e))
                _record_failure(plat, "youtube", e, now)
                failures.append("youtube")
            # Persist immediately: if the Instagram step kills the process,
            # the YouTube success must already be on disk for the commit step.
            save_state(state)

        if "instagram" in pending:
            try:
                ig_user, ig_token = env("IG_USER_ID"), env("IG_ACCESS_TOKEN")
                prev = plat.get("instagram", {})
                media_id = None
                if prev.get("container_id"):
                    # A previous run created a container; it may already be
                    # published (client-side timeout) -- never publish twice.
                    media_id = post_instagram.resume(ig_user, ig_token, prev["container_id"])

                if media_id is None:
                    def remember(cid: str) -> None:
                        plat["instagram"] = {**plat.get("instagram", {}), "container_id": cid}
                        save_state(state)

                    with PublicAsset(env("STAGING_TOKEN"), env("STAGING_REPO"), local) as asset:
                        media_id = post_instagram.post_reel(
                            ig_user, ig_token, asset.url,
                            caption_mod.compose_ig(meta["caption"], meta["hashtags"]),
                            on_container=remember,
                        )
                plat["instagram"] = {"ok": True, "id": media_id, "at": now}
            except (SystemExit, Exception) as e:
                LOG.error("Instagram failed: %s", _scrub(e))
                _record_failure(plat, "instagram", e, now)
                failures.append("instagram")

    save_state(state)

    if failures:
        LOG.error("Completed with failures on: %s", ", ".join(failures))
        return 1
    LOG.info("Done. Posted '%s' to %s", target["name"], ", ".join(platforms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
