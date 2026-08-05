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
        # Asset names must be unique within a release.
        name = f"{self.path.stem[:60]}-{abs(hash(self.path.name)) % 10**8}{self.path.suffix}"

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
