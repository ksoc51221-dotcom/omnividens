"""Download a video with yt-dlp and pick up its native subtitles + caption.

Approach adapted from claude-video (MIT), see NOTICE.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from reelay.video.errors import DownloadError

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


@dataclass
class Downloaded:
    video: Path
    subtitle: Path | None = None
    caption: str = ""


def is_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def download(url: str, out_dir: Path, timeout_s: int = 180) -> Downloaded:
    """Fetch `url` into `out_dir`. Raises DownloadError on any failure."""
    if shutil.which("yt-dlp") is None:
        raise DownloadError("yt-dlp is not installed (brew install yt-dlp)")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "--no-playlist", "--ignore-errors", "--no-warnings",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json", "--write-subs", "--write-auto-subs",
        "--sub-langs", "en.*,en", "--sub-format", "vtt", "--convert-subs", "vtt",
        "-o", str(out_dir / "video.%(ext)s"), url,
    ]
    try:
        # A failed subtitle (e.g. HTTP 429) can make yt-dlp exit non-zero even
        # though the video arrived, so we judge success by the file, not the code.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(f"download timed out after {timeout_s}s") from exc
    video = _find_video(out_dir)
    if video is None:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise DownloadError(f"yt-dlp produced no video: {tail[0]}")
    subs = sorted(out_dir.glob("video*.vtt"))
    return Downloaded(video, subs[0] if subs else None, _read_caption(out_dir))


def _find_video(out_dir: Path) -> Path | None:
    for path in sorted(out_dir.glob("video.*")):
        if path.suffix.lower() in VIDEO_EXTS:
            return path
    return None


def _read_caption(out_dir: Path) -> str:
    """Title + description from yt-dlp's info json (this is the post's caption)."""
    try:
        info = json.loads((out_dir / "video.info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    parts = [info.get("title") or "", info.get("description") or ""]
    return "\n".join(p.strip() for p in parts if p and p.strip())
