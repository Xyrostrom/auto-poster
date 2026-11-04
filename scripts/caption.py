"""Caption + hashtag generation.

With ANTHROPIC_API_KEY set, Claude is shown frames sampled across the actual
video (not just its filename) and writes a hook title, an SEO-friendly
description and hashtags, optionally checking the web for what is trending in
that topic right now.

Without a key (or if the call fails) a template is used. The fallback exists
so the pipeline never hard-fails on a caption -- a plain caption beats a
missed post -- but it cannot see the video, so it is deliberately generic.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
import random
import re
import subprocess

import requests

LOG = logging.getLogger("autoposter.caption")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("CAPTION_MODEL", "claude-sonnet-5-5")
FRAME_COUNT = 6

# Platform hard limits.
YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
IG_CAPTION_MAX = 2200
YT_TAGS_MAX = 450          # YouTube allows 500 characters for all tags; keep a margin
MAX_HASHTAGS = 5           # Instagram's hard limit; also YouTube's recommended 3-5
# Generic tags that don't help reach and can look like spam/misleading metadata.
GENERIC_TAGS = {"viral", "trending", "fyp", "foryou", "foryoupage", "explore", "reels", "reelsinstagram",
                "shorts", "youtubeshorts", "instagood", "like", "follow", "subscribe"}

PROMPT = """You are a short-form video growth expert writing the copy for one YouTube Short / Instagram Reel.

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

Write in the same language as any speech/on-screen text in the video (English if none).
Return ONLY valid JSON, no markdown fence:
{{"title": "...", "caption": "...", "hashtags": ["t1", "t2", "t3", "t4", "t5"], "keywords": ["...", "..."]}}"""

SEARCH_HINT = ("Before writing, you may use web search (at most twice) to identify the event/people/place shown "
               "and which specific hashtags and search phrases people use for it right now.")

FALLBACK_HOOKS = [
    "Wait for the end 😳",
    "You won't believe this one",
    "Watch till the end",
    "This hit different",
    "Nobody expected this",
]


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------

def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg  # bundles a static ffmpeg on every OS
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 -- fall back to a system install
        return "ffmpeg"


def _duration(ff: str, video: pathlib.Path) -> float:
    proc = subprocess.run([ff, "-hide_banner", "-i", str(video)],
                          capture_output=True, text=True, timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def extract_frames(video: pathlib.Path, count: int = FRAME_COUNT) -> list[bytes]:
    """JPEG frames spread evenly across the video. Empty list on any failure."""
    try:
        ff = _ffmpeg()
        dur = _duration(ff, video)
        if dur <= 0:
            return []
        frames = []
        for i in range(count):
            t = dur * (i + 0.5) / count
            proc = subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                capture_output=True, timeout=60,
            )
            if proc.returncode == 0 and proc.stdout:
                frames.append(proc.stdout)
        return frames
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Could not extract frames (%s); captioning without visuals",
                    type(exc).__name__)
        return []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _slug_to_words(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename)
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "New video"


def _fallback(filename: str, context: str) -> dict:
    hook = random.choice(FALLBACK_HOOKS)
    topic = (context or "").strip()
    title = f"{hook} | {topic}"[:70] if topic else hook
    caption = f"{hook}\n\nFollow for more{' ' + topic if topic else ''} 🔔\nWhat do you think? Tell me in the comments 👇"
    tags = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in topic.split()] if topic else []
    tags = list(dict.fromkeys(t for t in tags if t))[:MAX_HASHTAGS]
    LOG.warning("Using template caption (no ANTHROPIC_API_KEY or model call failed)")
    return {"title": title, "caption": caption, "hashtags": tags, "keywords": [topic] if topic else []}


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the reply (models sometimes add a fence or prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _call(api_key: str, content: list, with_search: bool) -> str:
    body = {"model": MODEL, "max_tokens": 4000,
            "messages": [{"role": "user", "content": content}]}
    if with_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    resp = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=180,
    )
    resp.raise_for_status()
    texts = [b["text"] for b in resp.json()["content"] if b.get("type") == "text"]
    if not texts:
        raise ValueError("no text in model reply")
    # With web search the JSON is in the final text block.
    return texts[-1] if "{" in texts[-1] else "\n".join(texts)


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------

def generate(filename: str, context: str = "", video: pathlib.Path | None = None) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback(filename, context)

    frames = extract_frames(video) if video else []
    LOG.info("Captioning from %d video frame(s)", len(frames))
    use_search = os.environ.get("CAPTION_WEB_SEARCH", "1") != "0"

    def build(search: bool) -> list:
        content: list = [{"type": "image",
                          "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(f).decode()}} for f in frames]
        content.append({"type": "text", "text": PROMPT.format(
            n=len(frames), filename=filename,
            context=context or "general short-form entertainment",
            search_hint=SEARCH_HINT if search else "")})
        return content

    try:
        try:
            text = _call(api_key, build(use_search), use_search)
        except requests.HTTPError as exc:
            # Web search may be disabled for the org; retry plain once.
            if not use_search or exc.response is None or exc.response.status_code != 400:
                raise
            LOG.warning("Web search unavailable (HTTP 400); retrying without it")
            text = _call(api_key, build(False), False)
        data = _extract_json(text)
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        # ERROR, not WARNING: a key is configured, so this is a misconfiguration
        # (bad model id, revoked key, no credit) that would otherwise hide forever.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        LOG.error("Caption generation failed (%s%s); falling back to template",
                  type(exc).__name__, f" HTTP {status}" if status else "")
        return _fallback(filename, context)
    if not isinstance(data, dict):
        LOG.error("Caption model returned non-object JSON; falling back to template")
        return _fallback(filename, context)

    # Never trust model output shape blindly.
    title = _clean(str(data.get("title") or "")).strip()[:YT_TITLE_MAX]
    caption = _clean(str(data.get("caption") or title))
    raw_tags = data.get("hashtags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [re.sub(r"[^a-z0-9]", "", str(t).lower().lstrip("#")) for t in raw_tags]
    tags = list(dict.fromkeys(t for t in tags if t and not t.isdigit() and t not in GENERIC_TAGS))[:MAX_HASHTAGS]
    raw_kw = data.get("keywords", [])
    keywords = _fit_keywords([_clean(str(k)).replace(",", " ") for k in raw_kw] if isinstance(raw_kw, list) else [])
    if not title:
        return _fallback(filename, context)

    return {"title": title, "caption": caption, "hashtags": tags, "keywords": keywords}


def _clean(text: str) -> str:
    # YouTube rejects titles/descriptions containing '<' or '>' (400 invalidTitle).
    return text.replace("<", "").replace(">", "").strip()


def _join_within(caption: str, hashtags: list[str], limit: int) -> str:
    """caption + tag line, dropping whole tags (never cutting one in half)."""
    tags = list(hashtags)
    while True:
        tagline = " ".join(f"#{t}" for t in tags)
        full = f"{caption}\n\n{tagline}".strip()
        if len(full) <= limit or not tags:
            return full[:limit]
        tags.pop()


def _fit_keywords(words: list[str]) -> list[str]:
    """YouTube tags field: total under 500 characters, or the upload is rejected."""
    out, total = [], 0
    for w in dict.fromkeys(w.strip() for w in words if w and w.strip()):
        cost = len(w) + 2 + (2 if " " in w else 0)  # separator, plus quotes YT counts for multi-word tags
        if total + cost > YT_TAGS_MAX:
            break
        out.append(w)
        total += cost
    return out


def compose_ig(caption: str, hashtags: list[str]) -> str:
    # Instagram allows only 5 hashtags per post/Reel (since Dec 2025).
    tags = [t for t in hashtags if t != "shorts"][:MAX_HASHTAGS]
    return _join_within(caption, tags, IG_CAPTION_MAX)


def compose_yt_description(caption: str, hashtags: list[str]) -> str:
    # YT shows up to 3 hashtags by the title: topic tags first, #shorts last.
    tags = [t for t in hashtags if t != "shorts"][:MAX_HASHTAGS] + ["shorts"]
    return _join_within(_clean(caption), tags, YT_DESC_MAX)
