"""Temporarily expose a local video at a public HTTPS URL.

Instagram's Graph API refuses local uploads and will not follow a Google Drive
share link -- it needs a direct, unauthenticated, resolvable media URL. The
cheapest reliable option inside CI is a GitHub Release asset on this repo:
public over HTTPS, no extra vendor, and deletable the moment IG has ingested
the file.

The release is created as a draft-less throwaway tagged `staging`, and the
asset is removed in a finally block so the repo does not accumulate videos.
"""
from __future__ import annotations

import logging
import pathlib
import uuid

import requests

LOG = logging.getLogger("autoposter.stage")

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
STAGING_TAG = "staging"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "auto-poster",
    }


def _ensure_release(token: str, repo: str) -> int:
    r = requests.get(f"{API}/repos/{repo}/releases/tags/{STAGING_TAG}",
                     headers=_headers(token), timeout=30)
    if r.status_code == 200:
        return r.json()["id"]

    r = requests.post(
        f"{API}/repos/{repo}/releases",
        headers=_headers(token),
        json={
            "tag_name": STAGING_TAG,
            "name": "Media staging (auto-managed)",
            "body": "Transient upload targets for Instagram ingestion. Safe to ignore.",
            "prerelease": True,
        },
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(f"Could not create staging release ({r.status_code}): {r.text[:400]}")
    return r.json()["id"]


class PublicAsset:
    """Context manager: uploads on enter, always deletes on exit."""

    def __init__(self, token: str, repo: str, path: pathlib.Path):
        self.token = token
        self.repo = repo
        self.path = path
        self.asset_id: int | None = None
        self.url: str | None = None

    def __enter__(self) -> "PublicAsset":
        # A release asset on a PRIVATE repo needs an auth header to download,
        # so Instagram would receive a 404. Fail loudly instead of posting
        # nothing and looking like a network flake.
        meta = requests.get(f"{API}/repos/{self.repo}", headers=_headers(self.token), timeout=30)
        if meta.status_code != 200:
            raise SystemExit(f"Cannot read staging repo {self.repo} ({meta.status_code})")
        if meta.json().get("private", True):
            raise SystemExit(
                f"Staging repo {self.repo} is PRIVATE. Instagram cannot fetch assets from it.\n"
                "STAGING_REPO must point at a public repo (it holds no code, only "
                "transient video blobs that are deleted seconds later)."
            )

        release_id = _ensure_release(self.token, self.repo)
        # Asset names must be unique within a release. uuid4 rather than hash():
        # str hashing is salted per process (PYTHONHASHSEED), so a name built
        # from it can never be recomputed to find an asset stranded by an
        # earlier crashed run.
        name = f"{self.path.stem[:60]}-{uuid.uuid4().hex[:8]}{self.path.suffix}"

        with self.path.open("rb") as fh:
            r = requests.post(
                f"{UPLOADS}/repos/{self.repo}/releases/{release_id}/assets",
                headers={**_headers(self.token), "Content-Type": "application/octet-stream"},
                params={"name": name},
                data=fh,
                timeout=(30, 1800),
            )
        if r.status_code not in (200, 201):
            raise SystemExit(f"Asset upload failed ({r.status_code}): {r.text[:400]}")

        body = r.json()
        self.asset_id = body["id"]
        self.url = body["browser_download_url"]
        LOG.info("Staged public URL: %s", self.url)
        return self

    def __exit__(self, *exc) -> None:
        if self.asset_id is None:
            return
        try:
            requests.delete(
                f"{API}/repos/{self.repo}/releases/assets/{self.asset_id}",
                headers=_headers(self.token),
                timeout=30,
            )
            LOG.info("Removed staged asset %s", self.asset_id)
        except requests.RequestException as e:
            # Never mask the real error from the body of the with-block.
            LOG.warning("Could not clean up staged asset %s: %s", self.asset_id, e)


def sweep(token: str, repo: str) -> int:
    """Delete every asset on the staging release.

    __exit__ handles the happy path, but it never runs if the process is killed
    outright -- job timeout, cancelled workflow, runner OOM. Without this a
    video stays publicly downloadable on the staging repo forever. Run it as an
    `if: always()` step so cleanup survives the thing that killed the upload.
    """
    r = requests.get(f"{API}/repos/{repo}/releases/tags/{STAGING_TAG}",
                     headers=_headers(token), timeout=30)
    if r.status_code == 404:
        LOG.info("No staging release on %s; nothing to sweep.", repo)
        return 0
    if r.status_code != 200:
        LOG.warning("Could not read staging release (%s): %s", r.status_code, r.text[:200])
        return 0

    assets = r.json().get("assets", [])
    removed = 0
    for a in assets:
        d = requests.delete(f"{API}/repos/{repo}/releases/assets/{a['id']}",
                            headers=_headers(token), timeout=30)
        if d.status_code in (204, 404):
            removed += 1
            LOG.info("Swept stranded asset %s (%s)", a["name"], a["id"])
        else:
            LOG.warning("Could not sweep asset %s (%s)", a["name"], d.status_code)
    if not assets:
        LOG.info("Staging release clean; nothing stranded.")
    return removed


if __name__ == "__main__":
    import argparse
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Staging-repo asset maintenance.")
    ap.add_argument("--sweep", action="store_true",
                    help="delete all assets on the staging release")
    args = ap.parse_args()
    if not args.sweep:
        ap.error("nothing to do; pass --sweep")

    tok = os.environ.get("STAGING_TOKEN")
    rep = os.environ.get("STAGING_REPO")
    if not tok or not rep:
        # A cleanup step must never fail the job over missing config.
        LOG.warning("STAGING_TOKEN/STAGING_REPO not set; skipping sweep.")
        raise SystemExit(0)
    sweep(tok, rep)
