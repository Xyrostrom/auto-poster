"""Caption + hashtag generation.

Uses Claude if ANTHROPIC_API_KEY is present, otherwise falls back to a
deterministic template built from the filename. The fallback exists so the
pipeline never hard-fails on a caption -- a plain caption beats a missed post.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

LOG = logging.getLogger("autoposter.caption")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("CAPTION_MODEL", "claude-sonnet-5-5")

# Platform hard limits.
YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
IG_CAPTION_MAX = 2200

PROMPT = """You are writing social copy for a short-form vertical video.

The video's filename is: {filename}
Creator's niche / context: {context}

Write:
1. A YouTube Shorts title, max 80 characters, punchy, no clickbait lies, no emoji spam.
2. A caption of 2-3 sentences suitable for Instagram Reels.
3. Exactly 8 relevant hashtags, lowercase, no numbers-only tags.

Return ONLY valid JSON, no markdown fence:
{{"title": "...", "caption": "...", "hashtags": ["tag1", "tag2"]}}"""


def _slug_to_words(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename)
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "New video"


def _fallback(filename: str, context: str) -> dict:
    words = _slug_to_words(filename)
    title = words[:80]
    caption = f"{words}.\n\nNew one up now."
    tags = ["shorts", "reels", "viral", "trending", "fyp", "explore", "video", "creator"]
    LOG.warning("Using template caption")
    return {"title": title, "caption": caption, "hashtags": tags}


def _extract_json(text: str) -> dict:
    """Models occasionally wrap JSON in a fence despite instructions."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def generate(filename: str, context: str = "") -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback(filename, context)

    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 2000,
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT.format(
                            filename=filename, context=context or "general short-form content"
                        ),
                    }
                ],
            },
            timeout=90,
        )
        resp.raise_for_status()
        text = next(b["text"] for b in resp.json()["content"] if b.get("type") == "text")
        data = _extract_json(text)
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError,
            StopIteration) as exc:
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
    title = _clean(str(data.get("title") or _slug_to_words(filename)))[:YT_TITLE_MAX]
    caption = _clean(str(data.get("caption") or title))
    raw_tags = data.get("hashtags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [re.sub(r"[^a-z0-9]", "", str(t).lower().lstrip("#")) for t in raw_tags]
    tags = [t for t in tags if t and not t.isdigit()][:10]

    return {"title": title or "New video", "caption": caption, "hashtags": tags}


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


def compose_ig(caption: str, hashtags: list[str]) -> str:
    return _join_within(caption, hashtags, IG_CAPTION_MAX)


def compose_yt_description(caption: str, hashtags: list[str]) -> str:
    # YT shows the first 3 hashtags above the title.
    return _join_within(_clean(caption), hashtags[:3], YT_DESC_MAX)
