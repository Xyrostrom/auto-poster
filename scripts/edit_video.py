"""Give every clip the channel's own look before it is uploaded.

What gets added (re-encoded once with ffmpeg, no extra tools or dependencies):
  * 9:16 (1080x1920) frame; a non-vertical clip sits on a blurred copy of itself
  * a light colour lift (a touch more contrast and saturation)
  * a hook line at the top for the first seconds, taken from the post title
  * the channel handle as a watermark for the whole clip
  * a "Subscribe" bar over the last seconds

Controlled by env vars (all optional):
  EDIT_VIDEO   "false" turns editing off (default: on)
  EDIT_HANDLE  watermark text, e.g. "@lotpotmastiiii"

Never blocks a post: on any failure the original file is returned.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap

from caption import _ffmpeg

LOG = logging.getLogger("autoposter.edit")

W, H = 1080, 1920
HOOK_SECONDS = 3.5
OUTRO_SECONDS = 2.5

# First existing file wins: GitHub's Ubuntu runner, then Windows, then macOS.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font() -> str | None:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _ff_path(p: str) -> str:
    """Path as ffmpeg filter options want it (forward slashes, escaped colon)."""
    return p.replace("\\", "/").replace(":", r"\:")


def _plain(text: str) -> str:
    """Keep letters, digits and basic punctuation: emoji and symbols render as boxes."""
    text = re.sub(r"[^\w\s.,!?'&:@#%/+\-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _duration(ff: str, video: pathlib.Path) -> float | None:
    proc = subprocess.run([ff, "-hide_banner", "-i", str(video)],
                          capture_output=True, text=True, errors="replace", timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _hook(title: str) -> str:
    """Title cut to two short lines (the part before a ':' reads best as a hook)."""
    t = _plain(title)
    if len(t) > 60:
        t = t[:57].rsplit(" ", 1)[0] + "..."
    return "\n".join(textwrap.wrap(t, 24)[:2])


def edit(video: pathlib.Path, title: str, handle: str | None = None) -> pathlib.Path:
    """Return an edited copy of ``video`` (or ``video`` itself if editing is off/fails)."""
    if os.environ.get("EDIT_VIDEO", "true").strip().lower() in ("0", "false", "no", "off"):
        LOG.info("Video editing is off (EDIT_VIDEO=false)")
        return video
    handle = (handle if handle is not None else os.environ.get("EDIT_HANDLE", "")).strip()
    out = video.with_name(video.stem + ".edit.mp4")
    tmpdir = tempfile.mkdtemp(prefix="edit_")
    try:
        ff = _ffmpeg()
        font = _font()
        if not font:
            raise RuntimeError("no bold font found for text overlays")
        dur = _duration(ff, video)

        def textfile(name: str, content: str) -> str:
            p = pathlib.Path(tmpdir) / name
            # newline="\n": on Windows the default turns "\n" into CRLF, which draws a blank line.
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            return _ff_path(str(p))

        base = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=30:5,eq=brightness=-0.08[bgb];"
            f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,"
            f"eq=contrast=1.05:saturation=1.15,format=yuv420p"
        )
        f = _ff_path(font)
        draws = []
        hook = _hook(title)
        if hook:
            draws.append(
                f"drawtext=fontfile='{f}':textfile='{textfile('hook.txt', hook)}':"
                f"fontsize=62:fontcolor=white:line_spacing=12:x=(w-text_w)/2:y=210:"
                f"box=1:boxcolor=black@0.55:boxborderw=26:enable='lt(t,{HOOK_SECONDS})'"
            )
        if handle:
            draws.append(
                f"drawtext=fontfile='{f}':textfile='{textfile('handle.txt', handle)}':"
                f"fontsize=40:fontcolor=white@0.9:x=(w-text_w)/2:y={H - 430}:"
                f"box=1:boxcolor=black@0.35:boxborderw=14"
            )
        if dur and dur > HOOK_SECONDS + OUTRO_SECONDS:
            draws.append(
                f"drawtext=fontfile='{f}':textfile='{textfile('sub.txt', 'SUBSCRIBE for daily masti')}':"
                f"fontsize=56:fontcolor=white:x=(w-text_w)/2:y={H - 560}:"
                f"box=1:boxcolor=0xE62117@0.92:boxborderw=24:"
                f"enable='gte(t,{dur - OUTRO_SECONDS:.2f})'"
            )
        graph = base + ("," + ",".join(draws) if draws else "") + "[v]"

        cmd = [
            ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", "30",
            "-c:a", "aac", "-b:a", "128k",
            "-map_metadata", "-1", "-movflags", "+faststart", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=900)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(proc.stderr.strip()[-400:] or f"ffmpeg exit {proc.returncode}")
        LOG.info("Edited video: hook %r, watermark %r, %.1f -> %.1f MB",
                 hook.replace("\n", " / "), handle or "none",
                 video.stat().st_size / 1_048_576, out.stat().st_size / 1_048_576)
        return out
    except Exception as exc:  # noqa: BLE001 - never block the post on editing
        LOG.warning("Could not edit video (%s); uploading the original", exc)
        out.unlink(missing_ok=True)
        return video
