"""Strip metadata from a video before it is uploaded anywhere.

Removes container tags (title, creation date, encoder, location/GPS,
device make/model, comments), per-stream tags, chapters, and extra data /
subtitle / thumbnail tracks. Video and audio are stream-copied, so there is
no re-encode and no quality loss.
"""

from __future__ import annotations

import logging
import pathlib
import subprocess

from caption import _ffmpeg

LOG = logging.getLogger("autoposter.clean")


def _tags(ff: str, video: pathlib.Path) -> list[str]:
    """Metadata keys ffmpeg reports for a file (best effort, for logging)."""
    proc = subprocess.run([ff, "-hide_banner", "-i", str(video)],
                          capture_output=True, text=True, errors="replace", timeout=60)
    keys, in_meta = [], False
    for line in proc.stderr.splitlines():
        s = line.strip()
        if s.startswith("Metadata:"):
            in_meta = True
            continue
        if in_meta and ":" in s and line.startswith("      "):
            keys.append(s.split(":", 1)[0].strip())
        else:
            in_meta = False
    return keys


def strip_metadata(video: pathlib.Path) -> pathlib.Path:
    """Return a metadata-free copy of ``video`` next to it.

    If stripping fails, the original is returned and a warning is logged,
    so a broken ffmpeg never blocks the day's post.
    """
    out = video.with_name(video.stem + ".clean" + (video.suffix or ".mp4"))
    try:
        ff = _ffmpeg()
        before = _tags(ff, video)
        cmd = [
            ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            # Keep only the main video track and audio (if any): drops GPS /
            # timecode data tracks, subtitles and embedded cover images.
            "-map", "0:V:0", "-map", "0:a?",
            "-c", "copy",
            "-map_metadata", "-1",          # container tags
            "-map_metadata:s:v", "-1",      # video stream tags
            "-map_metadata:s:a", "-1",      # audio stream tags
            "-map_chapters", "-1",
            "-fflags", "+bitexact",         # no "encoder=Lavf..." tag
            "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-movflags", "+faststart",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=300)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(proc.stderr.strip()[-300:] or f"ffmpeg exit {proc.returncode}")
        after = _tags(ff, out)
        LOG.info("Metadata stripped (%d tag(s) before, %d after: %s)",
                 len(before), len(after), ", ".join(sorted(set(after))) or "none")
        return out
    except Exception as exc:  # noqa: BLE001 - never block the post on this
        LOG.warning("Could not strip metadata (%s); uploading the original file", exc)
        out.unlink(missing_ok=True)
        return video
