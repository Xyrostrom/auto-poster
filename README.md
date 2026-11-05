# auto-poster

Picks one un-posted video per day from a Google Drive folder, generates a
caption, and publishes it to **YouTube Shorts** and **Instagram Reels**.

Runs entirely on GitHub Actions. No server, no cost.

```
Drive folder ──► pick oldest unposted ──► caption ──┬──► YouTube (resumable upload)
                                                    │
                                                    └──► stage public URL ──► Instagram Reels
                                                              │
                                                    state/posted.json committed back
```

## Why the video lives in Drive, not on your PC

GitHub's runners are ephemeral cloud VMs. They cannot reach `D:\...` on your
machine. Drive is the shared source both you and the runner can read.

## Why there is a second, public repo

Instagram's Graph API will not accept a file upload or a Drive share link — it
requires a public HTTPS URL it can fetch itself. Each run uploads the video as
a release asset on a **public** staging repo, hands that URL to Instagram, and
deletes the asset immediately after. Release assets on a *private* repo need an
auth header, so Instagram would get a 404 — hence the split.

Nothing sensitive goes in the staging repo. It holds video blobs for ~60s.

## Setup

### 1. Google (Drive read + YouTube upload)

1. Create an OAuth **Desktop app** client at
   <https://console.cloud.google.com/apis/credentials>
2. Enable both **Google Drive API** and **YouTube Data API v3**
3. On the OAuth consent screen, add yourself as a test user, then
   **publish the app** — while it is in *Testing*, refresh tokens die after 7 days
4. Mint the refresh token:

```bash
python scripts/auth_setup.py
```

### 2. Instagram

Requires a **Business** or **Creator** account linked to a Facebook Page.
You need a long-lived token with `instagram_content_publish`,
`instagram_basic`, and `pages_read_engagement`.

`IG_USER_ID` is the Instagram *Business account* ID, not your @handle.

> Long-lived IG tokens expire after **60 days**. Refresh it before then or the
> Instagram leg starts failing silently while YouTube keeps working.

### 3. Secrets

Repo → Settings → Secrets and variables → Actions.

| Secret | What it is |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth desktop client id |
| `GOOGLE_CLIENT_SECRET` | OAuth desktop client secret |
| `GOOGLE_REFRESH_TOKEN` | from `auth_setup.py` |
| `YOUTUBE_REFRESH_TOKEN` | optional, from `auth_setup.py --youtube`; uploads go to that (brand) channel |
| `DRIVE_FOLDER_ID` | the id in the folder URL |
| `IG_USER_ID` | IG Business account id |
| `IG_ACCESS_TOKEN` | long-lived Graph token |
| `STAGING_TOKEN` | PAT with `repo` scope on the staging repo only |
| `ANTHROPIC_API_KEY` | optional — without it captions use a filename template |

| Variable | What it is |
|---|---|
| `STAGING_REPO` | e.g. `Xyrostrom/auto-poster-media` (must be public) |
| `CONTENT_CONTEXT` | one line describing your niche, improves captions |
| `YT_CHANNEL_ID` | optional; refuse to upload unless the YouTube token is for this channel |
| `YT_PRIVACY` | `public`, `unlisted`, or `private` (default `public`) |

> **YouTube API audit:** videos uploaded through the API from an *unverified*
> Google Cloud project are forced to **private**, whatever `YT_PRIVACY` says.
> To post publicly, submit the project for the
> [YouTube API Services audit](https://support.google.com/youtube/contact/yt_api_form).
> Until it's approved, expect private uploads.
>
> **Instagram token:** the long-lived `IG_ACCESS_TOKEN` expires after about 60 days
> and nothing here refreshes it. Set a reminder to rotate it.

Use a **fine-grained PAT** for `STAGING_TOKEN`, scoped to the staging repo with
Contents: read/write. It does not need access to anything else.

## Running it

Dry run first — picks a video and shows the caption, uploads nothing:

```bash
gh workflow run auto-post -f dry_run=true
```

Then for real:

```bash
gh workflow run auto-post
```

Schedule lives in `.github/workflows/repost.yml`. It posts at peak audience time: weekdays 18:17 IST (`47 12 * * 1-5` UTC), weekends 12:17 IST (`47 6 * * 0,6` UTC). GitHub may start a scheduled run up to ~1 hour late. Once the channel has viewers, check YouTube Studio → Analytics → Audience → *When your viewers are on YouTube* and adjust the times.

## State

`state/posted.json` records success **per platform**:

```json
{
  "posted": {
    "1AbC...": {
      "name": "clip_01.mp4",
      "platforms": {
        "youtube":   { "ok": true, "id": "dQw4...", "at": "2026-08-05T05:00:12+00:00" },
        "instagram": { "ok": false, "error": "...", "at": "2026-08-05T05:01:44+00:00" }
      }
    }
  }
}
```

A video is skipped only once **every** enabled platform succeeded. If YouTube
worked and Instagram failed, the next run retries Instagram only — it will not
double-post to YouTube.

To force a re-post, delete that file's entry.

## Limits worth knowing

- **YouTube**: uploads via API cost 1600 quota units; the default 10,000/day cap
  allows ~6 uploads/day. One per day is comfortable.
- **Instagram**: 50 publishes per rolling 24h.
- **Shorts**: vertical and ≤3 minutes, or YouTube treats it as a normal video.

## Content ownership

This posts videos from *your* Drive folder to *your* accounts. Re-uploading
other people's videos without permission violates the YouTube and Instagram
terms of service and is copyright infringement — accounts doing it get struck
and terminated. Point `DRIVE_FOLDER_ID` at content you made or have a licence
for.
