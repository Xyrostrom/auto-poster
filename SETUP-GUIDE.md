# Auto-Poster: Complete Setup Guide (step by step)

This guide sets up the same system from zero on a new account. Put short videos
in a Google Drive folder and the program will, every day at peak time:

1. pick the oldest video it has not posted yet
2. watch it (6 frames) and use AI to write a title, caption, hashtags and tags,
   after checking what is trending on the web
3. upload it to **YouTube Shorts**, and to **Instagram Reels** if you set that up
4. **delete it from Drive** so it can never be posted twice
5. write down what it posted in `state/posted.json`

It runs on **GitHub Actions** (free for public repos, so your PC can stay off), or on
**your own PC** using Windows Task Scheduler (Part 8).

Allow about **1–2 hours** the first time. Do the parts in order.

> **Only post videos you own or have the rights to.** Re-uploading someone
> else's videos can get copyright strikes, and a channel with three strikes is deleted.

---

## What's in this zip

| Item | What it is |
|---|---|
| `SETUP-GUIDE.md` | This guide |
| `auto-poster/` | The program: all code, the GitHub workflow, the website pages |
| `auto-poster/.env.example` | Template for your private settings file |
| `auto-poster/run-local.ps1` | Runs one post from your PC (Part 8) |
| `drive-downloader/` | Bonus Windows app that downloads or copies a shared Drive folder (Part 11) |

### The code files

| File | Job |
|---|---|
| `.github/workflows/repost.yml` | Schedule and steps GitHub runs (the "cloud computer" instructions) |
| `scripts/publish.py` | Main program: pick → download → caption → strip metadata → upload → delete → record |
| `scripts/clean_video.py` | Removes the video's hidden metadata (date, device, GPS, title, encoder) before upload |
| `scripts/caption.py` | AI captions: frames from the video + Claude + web search, plus all caption rules |
| `scripts/post_youtube.py` | Uploads to YouTube with the settings that help views (Part 9) |
| `scripts/post_instagram.py` | Publishes the Reel through Instagram's API |
| `scripts/stage_public.py` | Gives Instagram a temporary public link to the video, then removes it |
| `scripts/common.py` | Google sign-in, Drive listing, downloading, deleting, saving state |
| `scripts/auth_setup.py` | One-time helper that creates your Google "refresh token" |
| `state/posted.json` | Record of what was posted (don't edit by hand) |
| `docs/index.html`, `docs/privacy.html` | Home page and privacy policy that Google requires |

---

## Part 0: Accounts and software you need

### Accounts (all free to create)
1. **Google account** (Gmail). This is the account whose Drive holds the videos and which owns the YouTube channel.
2. **YouTube channel** on that Google account:
   - Go to https://www.youtube.com → sign in → click your picture (top right) → **Create a channel** → follow the steps.
3. **GitHub account**: https://github.com/signup
4. **Anthropic account** for the AI captions: https://console.anthropic.com. This costs money: about $5 of credit lasts a very long time, since each caption costs roughly half a US cent.
5. *(Optional, Part 6)* **Instagram** Professional account plus a **Facebook Page**.

### Software on your Windows PC
1. **Python 3.12 or newer**: https://www.python.org/downloads/
   - In the installer, **tick "Add python.exe to PATH"** at the bottom, then click *Install Now*.
2. **Git**: https://git-scm.com/download/win → run the installer and keep clicking *Next* (defaults are fine).
3. **GitHub CLI** (`gh`), which makes Part 5 much easier: https://cli.github.com → download → install.
4. Check everything works. Press `Win` key → type `powershell` → open it → run:
   ```powershell
   python --version
   git --version
   gh --version
   ```
   Each should print a version number. If one says "not recognized", close PowerShell, open it again and retry. If it still fails, reinstall that tool.

---

## Part 1: Put the code on your PC and on GitHub

1. Unzip this zip. Move the `auto-poster` folder somewhere simple, for example `D:\Projects\auto-poster`.
2. Open PowerShell **in that folder**: open the folder in File Explorer → click the address bar → type `powershell` → Enter.
3. Install the Python packages:
   ```powershell
   pip install -r requirements.txt
   ```
4. Sign in to GitHub from the PC:
   ```powershell
   gh auth login
   ```
   Choose: **GitHub.com** → **HTTPS** → **Yes** (authenticate Git) → **Login with a web browser**. Copy the 8-character code, press Enter, paste the code in the browser, and click **Authorize**.
5. Set your name for Git (one time):
   ```powershell
   git config --global user.name  "Your Name"
   git config --global user.email "you@example.com"
   ```
6. Create the GitHub repo and upload the code:
   ```powershell
   git init -b main
   git add .
   git commit -m "first commit"
   gh repo create auto-poster --public --source . --push
   ```
   - **Use `--public`.** GitHub Actions is **free with no limit for public repos**. Private repos have a small free quota and then need a working payment card.
   - Your passwords and tokens are **not** in the code. They go in GitHub *Secrets* (Part 5), which stay hidden even on a public repo. The `.env` file is ignored by Git, so it is never uploaded.
7. Open `https://github.com/<your-username>/auto-poster` in a browser. You should see the files there.

---

## Part 2: Google Drive folder for your videos

1. Go to https://drive.google.com (signed in with the **same** Google account as the YouTube channel).
2. **New** → **New folder** → name it, for example, `auto-poster videos` → **Create**.
3. Upload your videos into it (drag and drop). Vertical, **3 minutes or shorter**, so YouTube treats them as Shorts. `.mp4` is best.
4. Open the folder. The link looks like
   `https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456`
   The part after `/folders/` (here `1AbCdEfGhIjKlMnOpQrStUvWxYz123456`) is your **DRIVE_FOLDER_ID**. Save it in Notepad for now.

Notes:
- The folder must be **owned by your account**. The program deletes each video after posting, and Google only lets the owner (or an editor) delete files.
- To use videos someone shared with you, copy them into your own folder first. The Drive Downloader in Part 11 can do this with "Copy into my Google Drive".
- Videos inside sub-folders are **not** picked up. Put them directly in the folder.
- Posting order is **oldest uploaded first**.

---

## Part 3: Google Cloud (permission to read Drive and upload to YouTube)

This gives *your* program permission to use *your* Drive and YouTube. It's the longest part, so go slowly.

### 3.1 Create a project
1. Go to https://console.cloud.google.com and sign in with the same Google account.
2. If asked, accept the Terms of Service and choose your country.
3. At the top left, click the **project picker** (next to "Google Cloud") → **New project**.
4. Project name: `auto-poster` → **Create**. Wait a few seconds, then select that project with the same picker. **Always check the project name at the top** before you change anything.

### 3.2 Turn on the two APIs
1. Go to https://console.cloud.google.com/apis/library
2. Search **Google Drive API** → open it → **Enable**.
3. Go back, search **YouTube Data API v3** → open it → **Enable**.

### 3.3 Website pages Google requires (home page and privacy policy)
Google won't put your app "In production" without a home page and a privacy policy. The `docs/` folder already contains both, and GitHub Pages hosts them for free.
1. **First, edit the pages so they show YOUR name and email.** Open `docs/index.html` and `docs/privacy.html` in Notepad, replace the previous owner's name, email and GitHub links with yours, then save. Push the change:
   ```powershell
   git add docs
   git commit -m "docs: my name and email"
   git push
   ```
2. On GitHub, open your repo → **Settings** → **Pages** (left menu).
3. Under *Build and deployment*: **Source = Deploy from a branch**, **Branch = `main`**, folder **`/docs`** → **Save**.
4. Wait 1–2 minutes, then refresh. It shows "Your site is live at `https://<your-username>.github.io/auto-poster/`".
5. Your two links are:
   - Home page: `https://<your-username>.github.io/auto-poster/`
   - Privacy policy: `https://<your-username>.github.io/auto-poster/privacy.html`
   Open both to check they load.

### 3.4 Branding and consent screen
1. Go to https://console.cloud.google.com/auth/overview → **Get started**.
2. **App information**: App name `Auto-poster`, User support email = your Gmail → **Next**.
3. **Audience**: choose **External** → **Next**.
4. **Contact information**: your Gmail → **Next**.
5. Tick **I agree to the Google API Services: User Data Policy** → **Continue** → **Create**.
6. Left menu → **Branding**. Fill in:
   - *Application home page*: your home page link from 3.3
   - *Application privacy policy link*: your privacy link from 3.3
   - *Application Terms of Service link*: you can use the privacy link again
   - *Authorised domains* → **Add domain** → `<your-username>.github.io`
   - → **Save**
7. Left menu → **Audience** → under *Publishing status* click **Publish app** → **Confirm**. The status should now say **In production**.
   - **Why this matters:** while the app is in *Testing*, Google's sign-in **expires after 7 days** and the program stops. In production it keeps working.
   - You do **not** need to submit for verification. Your own account can use the app; you'll just see an "unverified app" warning once (3.6).

### 3.5 Create the sign-in ID (OAuth client)
1. Left menu → **Clients** → **+ Create client**.
2. *Application type*: **Desktop app**. Name: `auto-poster desktop` → **Create**.
3. A box shows the **Client ID** and **Client secret**. Copy both into Notepad **now**, because the secret is shown in full only once. If you lose it, open the client, click **Add secret**, copy the new one, and disable the old one.

### 3.6 Create the refresh token (run once on your PC)
The refresh token lets the program sign in as you without a password.
1. In PowerShell, in the `auto-poster` folder:
   ```powershell
   python scripts/auth_setup.py
   ```
2. Paste the **Client ID** and press Enter, then paste the **Client secret** and press Enter.
3. A browser opens. Pick the Google account that owns the Drive folder **and** the YouTube channel.
4. You'll see **"Google hasn't verified this app"**. This is expected because it's your own app. Click **Advanced** → **Go to Auto-poster (unsafe)**.
5. **Tick every checkbox** (Drive and YouTube) → **Continue**.
6. The browser says you can close it. Back in PowerShell, the program prints
   `GOOGLE_REFRESH_TOKEN:` followed by a long line starting with `1//`. Copy that line into Notepad.
   - It asks for full Drive access because it **deletes** each video after posting. Read-only access can't delete.
   - If YouTube shows several channels, pick the channel you want the videos on.

You now have: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `DRIVE_FOLDER_ID`.

---

## Part 4: Anthropic API key (AI captions)

1. Go to https://console.anthropic.com → sign up or sign in.
2. **Settings → Billing** → add a card → buy credits ($5 is plenty to start).
3. **Settings → API Keys** → **Create Key** → name it `auto-poster` → **Create** → copy the key (starts with `sk-ant-`). It's shown only once.
4. That's your `ANTHROPIC_API_KEY`.

Without a key the program still works, but the captions are simple templates made from the file name (no AI, no trending hashtags).

---

## Part 5: GitHub Secrets and Variables (where the program reads your keys)

**Secrets** are hidden passwords. **Variables** are normal, non-secret settings.

### Easiest: from PowerShell in the `auto-poster` folder
Each `gh secret set` command asks you to paste the value, which is then hidden. Paste and press Enter.
```powershell
gh secret set GOOGLE_CLIENT_ID
gh secret set GOOGLE_CLIENT_SECRET
gh secret set GOOGLE_REFRESH_TOKEN
gh secret set DRIVE_FOLDER_ID
gh secret set ANTHROPIC_API_KEY

gh variable set PLATFORMS --body "youtube"
gh variable set YT_PRIVACY --body "public"
gh variable set CONTENT_CONTEXT --body "Indian cricket and stand-up comedy clips"
```
- `PLATFORMS`: `youtube` for now. Change it to `youtube,instagram` after Part 6.
- `YT_PRIVACY`: `public`, `unlisted` (only people with the link) or `private`.
- `CONTENT_CONTEXT`: one sentence about your channel. The AI uses it to write better captions.

### Or in the browser
Repo → **Settings** → **Secrets and variables** → **Actions**:
- **Secrets** tab → **New repository secret** → Name (exactly as above) + value → **Add secret**. Repeat for each.
- **Variables** tab → **New repository variable** → same idea.

### Check Actions is allowed
Repo → **Settings** → **Actions** → **General** → *Actions permissions*: **Allow all actions and reusable workflows** → **Save**.

---

## Part 6 (optional): Instagram Reels

> This part wasn't tested end to end in the original setup, because YouTube was done
> first. Follow it carefully. If Instagram fails, YouTube still posts.

Instagram's API can't take a file directly. It needs a **public web link** to the video. The program uploads each video for a moment to a second, public GitHub repo (the "staging repo"), gives that link to Instagram, then deletes it.

### 6.1 Make Instagram a Professional account
In the Instagram app: profile → ☰ menu → **Settings and privacy** → **Account type and tools** → **Switch to professional account** → choose **Creator** or **Business** → finish.

### 6.2 Link a Facebook Page
1. Create a Page: https://www.facebook.com/pages/create → name it after your channel → **Create**.
2. Link it: on the Page → **Settings** → **Linked accounts** → **Instagram** → **Connect account** → sign in to Instagram → allow.

### 6.3 Create a Meta developer app and get the token
1. Go to https://developers.facebook.com → **Get started** (register as a developer with your Facebook account).
2. **My Apps** → **Create app** → use case **Other** → type **Business** → name `auto-poster` → **Create app**.
3. Go to **Tools → Graph API Explorer** (https://developers.facebook.com/tools/explorer).
   - *Meta App*: choose `auto-poster`.
   - *User or Page*: **User Token**.
   - *Permissions*: add `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`.
   - Click **Generate Access Token** → log in → select your Page and Instagram account → allow.
4. Find your **IG_USER_ID**: in the Explorer box type
   `me/accounts?fields=name,instagram_business_account` → **Submit**.
   The number inside `"instagram_business_account": {"id": "1784..."}` is your `IG_USER_ID`.
5. Make the token last 60 days: **Tools → Access Token Debugger** (https://developers.facebook.com/tools/debug/accesstoken) → paste the token → **Debug** → **Extend Access Token** → copy the new token. That's your `IG_ACCESS_TOKEN`.
   - **Put a reminder in your calendar for 50 days from now.** When the token expires, Instagram posting stops and YouTube keeps working. Repeat steps 3 and 5 and update the secret.

### 6.4 Staging repo and its token
1. Create an empty public repo for the temporary video links:
   ```powershell
   gh repo create auto-poster-media --public --add-readme
   ```
2. Create a token that can **only** touch that repo:
   https://github.com/settings/personal-access-tokens/new
   - Name: `auto-poster staging` · Expiration: 1 year
   - *Repository access*: **Only select repositories** → `auto-poster-media`
   - *Permissions* → *Repository permissions* → **Contents: Read and write**
   - **Generate token** → copy it (starts with `github_pat_`).
3. Save everything in GitHub:
   ```powershell
   gh secret set IG_USER_ID
   gh secret set IG_ACCESS_TOKEN
   gh secret set STAGING_TOKEN
   gh variable set STAGING_REPO --body "<your-username>/auto-poster-media"
   gh variable set PLATFORMS --body "youtube,instagram"
   ```

---

## Part 7: Test it

### 7.1 Test on your PC first
1. In the `auto-poster` folder, copy `.env.example` to `.env`:
   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```
   Fill in your values, then save. **Never share or upload this file.**
2. **Dry run** (writes the caption but uploads, deletes and saves nothing):
   ```powershell
   Get-Content .env | ForEach-Object { if ($_ -match '^\s*([A-Za-z_]\w*)\s*=\s*(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
   python scripts/publish.py --platforms youtube --dry-run
   ```
   You should see `Selected: ...`, `Title: ...`, `Caption: ...`, `Tags: ...`, `Dry run complete`.
3. **Real run** (uploads one video, then deletes it from Drive):
   ```powershell
   python scripts/publish.py --platforms youtube
   ```
   The log prints `YouTube published: https://youtu.be/...`. Open the link to check it.
4. Save the record so GitHub knows that video is done:
   ```powershell
   git add state/posted.json
   git commit -m "chore: record post run [skip ci]"
   git push
   ```

### 7.2 Test on GitHub
1. Repo → **Actions** tab. If it asks, click **I understand my workflows, go ahead and enable them**.
2. Left: **auto-post** → right: **Run workflow** → set *dry_run* to `true` → **Run workflow**.
3. Click the run that appears and open **publish** to read the log. Green ✓ means it worked.
4. Run it again with *dry_run* `false` to do a real post.

---

## Part 8: When it runs automatically

### On GitHub (default)
Set in `.github/workflows/repost.yml`:
```yaml
  schedule:
    # Weekdays: 12:47 UTC = 18:17 IST (evening peak, ~7-10 PM IST).
    - cron: "47 12 * * 1-5"
    # Weekends: 06:47 UTC = 12:17 IST (people are free from midday).
    - cron: "47 6 * * 0,6"
```
- **Rule:** post just before your audience is most active. A new Short that gets views and likes in its first hour is shown to more people.
- The times are in **UTC** (India is UTC + 5:30). They are set 30–45 minutes early because GitHub often starts scheduled jobs late.
- GitHub **switches off the schedule if the repo has no activity for 60 days**. Each post commits `state/posted.json`, which counts as activity, so it stays on as long as it keeps posting.
- **After a few weeks**, look at YouTube Studio → **Analytics** → **Audience** → *When your viewers are on YouTube*, then change the times to match your real viewers.
  To convert an IST time to UTC, subtract 5:30. Example: 8:00 PM IST = 14:30 UTC = `cron: "30 14 * * *"`.

### On your own PC instead (if GitHub Actions is locked or you don't want it)
`run-local.ps1` does one post (same as GitHub), saves logs in `logs\`, and pushes `state/posted.json`.

1. Make sure `.env` is filled in (Part 7.1) and that `PLATFORMS=youtube` is set in it.
2. Open PowerShell **as Administrator** (right-click → *Run as administrator*) and run this. Change the path if your folder is somewhere else:
   ```powershell
   $dir = 'D:\Projects\auto-poster'
   $a  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\run-local.ps1`"" -WorkingDirectory $dir
   $t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '18:30'
   $t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday,Sunday -At '12:30'
   $s  = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
   Register-ScheduledTask -TaskName 'auto-poster' -Action $a -Trigger $t1,$t2 -Settings $s
   ```
   (These times use your PC's clock, so set Windows to India time.)
3. Test it now: `Start-ScheduledTask -TaskName auto-poster`, wait a minute, then open the newest file in `logs\`.
4. The PC must be **on**, or asleep (it wakes up to post). If it was off, the post runs as soon as the PC starts.
5. To remove it: `Unregister-ScheduledTask -TaskName auto-poster -Confirm:$false`
6. **Use only one of the two.** If you use the PC schedule, disable the GitHub one: repo → Actions → auto-post → **⋯** → **Disable workflow**. Otherwise both post at around the same time.

---

## Part 9: All the rules the program follows

### Picking a video
- Only video files placed directly in `DRIVE_FOLDER_ID` (sub-folders are ignored).
- **Oldest uploaded first**, one video per run.
- Skips anything already in `state/posted.json`.
- If a video fails **3 times** (`MAX_ATTEMPTS = 3` in `publish.py`), it's skipped for good so one broken file can't block the queue.

### Writing the caption (`scripts/caption.py`)
- Takes **6 frames** spread across the video and sends them to Claude (`CAPTION_MODEL`, default `claude-sonnet-5-5`), so the caption describes what actually **happens** in the clip. It doesn't rely on the file name.
- Claude may **search the web up to 2 times** for who or what is in the clip and which hashtags and search phrases are trending right now. Set `CAPTION_WEB_SEARCH=0` to turn this off.
- **Title**: 40–70 characters, the main search keyword first, a curiosity hook or bold true statement, at most 1 emoji, no hashtags, no file names or IDs. YouTube's hard limit is 100.
- **Caption / description**: 2–4 short lines. Line 1 (under 125 characters) is a hook with the keyword. Then one line of context, then a question to get comments. No clickbait or misleading claims, because YouTube and Instagram punish those.
- **Hashtags**: **exactly 5** (Instagram's limit, and YouTube's recommended 3–5): 1 broad, 2 niche, 2 specific. **No generic spam tags** such as `#viral #fyp #trending #explore` (removed automatically).
- **YouTube tags (keywords)**: 8–15 search phrases, including Hindi/Hinglish spellings when they fit, kept under YouTube's 500-character limit (`YT_TAGS_MAX = 450`).
- **Category picked automatically** to match the clip (Sports, Comedy, Entertainment, Music, Gaming, …). Default is Entertainment. Set `YT_CATEGORY_ID` to force one.
- **Language detected** from speech and on-screen text (for example `hi` for Hindi, `en` for English).
- If there's no API key, or the AI fails, a **simple template** caption from the file name is used, so a post is never skipped because of captions.

The exact instructions given to the AI are copied at the end of this guide (Appendix A).

### Removing metadata before upload (`scripts/clean_video.py`)
- Before anything is uploaded, the program makes a copy of the video **without its hidden metadata**: original title, recording date, GPS location, phone make/model, comments, encoder name, chapters, and extra tracks (cover image, subtitles, location data).
- Picture and sound are **copied, not re-encoded**, so quality and size stay the same and it takes a second or two.
- The same clean copy goes to YouTube and Instagram.
- If cleaning fails, a warning is logged and the original file is uploaded, so a post is never blocked.
- The log line `Metadata stripped (N tag(s) before, 2 after: handler_name)` confirms it worked. The 2 left are generic `VideoHandler`/`SoundHandler` labels every MP4 has.

### YouTube upload settings (`scripts/post_youtube.py`), chosen for reach
| Setting | Value | Why |
|---|---|---|
| Visibility | `YT_PRIVACY` (default public) | |
| Made for kids | **No** | "Made for kids" turns off comments and notifications |
| Notify subscribers | **Yes** | Subscribers see it in their feed |
| Embeddable | **Yes** | Can be shared on websites and apps |
| Public like count | **Yes** | |
| Category / Language | auto (see above) | Shown to people who watch that kind of video |
| Altered or synthetic content | **No** | Real clips |
| Shorts | automatic | Vertical and ≤3 minutes. `#shorts` isn't required |

### Instagram
Posted as a **Reel** with the same caption and 5 hashtags (caption limit 2,200 characters).

### After posting
- When **every enabled platform** succeeds, the video is **permanently deleted from Drive** (it skips the Bin) so it can't be posted twice.
- If the delete fails, that's only logged. `state/posted.json` still stops a repost.
- The record (`posted.json`) is committed back to GitHub automatically.

---

## Part 10: Problems and fixes

| Problem | Fix |
|---|---|
| Every GitHub run fails in 2–3 seconds with **startup_failure**, or "recent account payments have failed" | GitHub billing is locked. Make the repo public (Part 1), then pay the balance at https://github.com/settings/billing. Indian cards: in your bank app, turn on **international** and **online / e-commerce** transactions, or try PayPal. You can also ask https://support.github.com → Billing to waive a tiny balance. Until then, use the PC schedule (Part 8). |
| `invalid_grant` in the log | The Google refresh token expired or was revoked. Is the app still **In production** (3.4)? Run Part 3.6 again and update `GOOGLE_REFRESH_TOKEN` in GitHub and `.env`. |
| `Missing required environment variable: X` | Add secret or variable `X` (Part 5) or the line to `.env`. |
| `Could not strip metadata ...; uploading the original file` | ffmpeg couldn't read the file. The post still goes out, but with the original metadata. Run `pip install -r requirements.txt` again (it installs ffmpeg). |
| `Drive delete failed (403)` | The video isn't owned by your account. Copy it into your own folder (Part 2). |
| `Drive folder holds 0 files` | Wrong `DRIVE_FOLDER_ID`, the videos are in a sub-folder, or the folder belongs to another account. |
| YouTube `quotaExceeded` | The free API allows about 6 uploads a day. One a day is fine. |
| YouTube video stuck **private** | Google can lock videos uploaded by an unaudited API project as private. Request an audit: https://support.google.com/youtube/contact/yt_api_form |
| Instagram `OAuthException` / expired token | Make a new token (6.3 steps 3 and 5) and update `IG_ACCESS_TOKEN`. |
| Captions look like the file name | `ANTHROPIC_API_KEY` is missing, wrong, or has no credit (Part 4). |
| PC schedule did nothing | Open the newest file in `logs\`. Check `.env` exists and that Python/Git work in a new PowerShell window. |

### Reading a GitHub run
Repo → **Actions** → click a run → **publish** → expand the **Publish** step. Every line explains what happened.

### Running by hand
Actions → **auto-post** → **Run workflow** (choose platforms, dry_run).
Or from the PC: `gh workflow run repost.yml -f dry_run=true -f platforms=youtube`

---

## Part 11 (bonus): Drive Downloader app

The `drive-downloader` folder holds a small Windows app for friends:
- Paste a shared Drive folder or file link and **download** it to the PC, or
- **Copy it into your own Google Drive** (nothing is downloaded; this is useful for filling the auto-poster folder).

Use: double-click **`Drive Downloader.cmd`** and follow the window. See `drive-downloader/README.txt`.

**Its Google sign-in ID isn't included**, because it's private to the person who created it. To make your own (about 5 minutes):
1. In Google Cloud (Part 3), create a **new project** named `Drive Downloader`.
2. Enable **Google Drive API** (3.2), then fill in Branding and the consent screen the same way (3.4), using `docs/drive-downloader/index.html` and `privacy.html` as the links. Publish the app.
3. Create a **Desktop app** client (3.5).
4. Create `drive-downloader\google-client.txt` with:
   ```
   client_id=YOUR_CLIENT_ID
   client_secret=YOUR_CLIENT_SECRET
   ```
   The app finds this file automatically. Without the file, it falls back to rclone's built-in sign-in, which may stop working.

---

## Checklist

- [ ] Python, Git and GitHub CLI installed
- [ ] Code pushed to a **public** GitHub repo
- [ ] Drive folder made and filled with videos; `DRIVE_FOLDER_ID` copied
- [ ] Google Cloud project: Drive API + YouTube Data API v3 enabled
- [ ] `docs` pages edited with your name and email; GitHub Pages on
- [ ] Branding filled in, authorised domain added, app **In production**
- [ ] Desktop client created; refresh token made with `auth_setup.py`
- [ ] Anthropic key with credit
- [ ] Secrets and variables added in GitHub
- [ ] *(optional)* Instagram: Professional account, Page, token, IG user ID, staging repo and token
- [ ] Dry run OK → real run OK → video opens on YouTube
- [ ] Schedule on (GitHub **or** PC, not both)
- [ ] Reminder set for the Instagram token (every 50 days)

---

## Appendix A: Exact instructions given to the AI (from `scripts/caption.py`)

Edit `PROMPT` in `scripts/caption.py` to change the rules, then push.

```text
You are a short-form video growth expert writing the copy for one YouTube Short / Instagram Reel.

Above are {n} frames sampled in order from start to end of the video. Work out what actually happens in it:
the subject, the moment/punchline, any on-screen text, the mood, and who would want to watch it.
{search_hint}
Original filename (often just an export ID -- ignore it unless it clearly names the topic): {filename}
Channel niche / context: {context}

Write copy designed to maximise views, watch time, likes, comments and shares, WITHOUT misleading viewers
(YouTube and Instagram both punish misleading metadata, and viewers who feel baited swipe away or click
"Not interested", which hurts reach far more than a plain title).

1. "title": 40-70 characters. Start with the main keyword people would actually search for (who/what/where),
   then the hook: a curiosity gap or bold, TRUE statement about what happens. The first ~40 characters
   must make sense on their own, because feeds cut long titles off. No hashtags in the title. At most one emoji, at the end.
   Never copy the filename, never use IDs or dates.
2. "caption": 2-4 short lines, used as the YouTube description and Instagram caption.
   Line 1 (under 125 characters, shown before "more"): a hook that repeats the main keyword naturally,
   because both platforms index this text for search. Line 2: concrete context with 1-2 more search
   phrases (names, place, event, what kind of moment). Last line: one specific question that is easy to
   answer, to get comments (e.g. "Who was your player of the match?", not "Thoughts?").
   No hashtags inside the caption, and no "like and subscribe" spam.
3. "hashtags": EXACTLY 5 lowercase tags without '#', most important first, and every one must match
   the video: 1 broad category (e.g. cricket), 2 niche (e.g. teamindia, worldcup2023), 2 specific
   (e.g. narendramodistadium, indvsaus). Never use generic tags like viral, trending, fyp, foryou,
   explore, reels or shorts (the code adds #shorts itself). Instagram allows only 5 hashtags, and YouTube
   shows just 3 by the title.
4. "keywords": 8-15 search phrases (plain text, no '#') a viewer might type to find this video,
   including common spellings and Hindi/Hinglish variants where relevant. These go in the YouTube tags field.
5. "category": the ONE YouTube category whose viewers are most likely to watch and like this video, chosen
   by what the video is about (a cricket crowd clip is Sports, not People & Blogs). One of:
   Sports, Entertainment, Comedy, Music, Gaming, Education, Howto & Style, News & Politics,
   Travel & Events, Autos & Vehicles, Pets & Animals, Film & Animation, Science & Technology, People & Blogs.
6. "language": ISO 639-1 code of the spoken/on-screen language ("hi" for Hindi/Hinglish, "en" if none).

Write in the same language as any speech/on-screen text in the video (English if none).
Return ONLY valid JSON, no markdown fence:
{"title": "...", "caption": "...", "hashtags": ["t1", "t2", "t3", "t4", "t5"], "keywords": ["...", "..."], "category": "...", "language": "en"}

Before writing, you may use web search (at most twice) to identify the event/people/place shown and which specific hashtags and search phrases people use for it right now.
```

### YouTube categories the AI can choose

| Category | ID |
|---|---|
| Film & Animation | 1 |
| Autos & Vehicles | 2 |
| Music | 10 |
| Pets & Animals | 15 |
| Sports | 17 |
| Travel & Events | 19 |
| Gaming | 20 |
| People & Blogs | 22 |
| Comedy | 23 |
| Entertainment | 24 |
| News & Politics | 25 |
| How-to & Style | 26 |
| Education | 27 |
| Science & Technology | 28 |

Generic tags the AI is not allowed to use (`#shorts` is added by the code itself): `#explore #follow #foryou #foryoupage #fyp #instagood #like #reels #reelsinstagram #shorts #subscribe #trending #viral #youtubeshorts`
