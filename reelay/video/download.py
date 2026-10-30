"""Download a video with yt-dlp and pick up its native subtitles + caption.

Approach adapted from claude-video (MIT), see NOTICE.
"""

import ipaddress
import json
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from reelay.video.errors import DownloadError, NetworkError, TooLongError

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
MAX_FILESIZE = "200M"
MAX_DURATION_S = 600

# Fragments of yt-dlp error text that mean "could not reach the host", not "unsupported page".
_NETWORK_HINTS = (
    "certificate_verify_failed", "ssl:", "name or service not known",
    "temporary failure in name resolution", "nodename nor servname",
    "getaddrinfo failed", "connection refused", "connection reset",
    "network is unreachable", "timed out", "failed to establish a new connection",
)


@dataclass
class Downloaded:
    video: Path
    subtitle: Path | None = None
    caption: str = ""


def is_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def is_private_host(url: str) -> bool:
    """True for localhost, private/loopback/link-local IPs, or names that resolve to them."""
    host = urlparse(url).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except (socket.gaierror, ValueError):
            return False  # unknown name: yt-dlp will fail on its own
    return any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved for a in addrs)


def download(url: str, out_dir: Path, timeout_s: int = 180) -> Downloaded:
    """Fetch `url` into `out_dir`. Raises DownloadError on any failure."""
    if shutil.which("yt-dlp") is None:
        raise DownloadError("yt-dlp is not installed (brew install yt-dlp)")
    if is_private_host(url):
        raise DownloadError("refusing to fetch a local or private address")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "--no-playlist", "--ignore-errors", "--no-warnings",
        "--max-filesize", MAX_FILESIZE,
        # "<=?" lets videos with no duration metadata (plain .mp4 links) through;
        # pipeline.py re-checks the real length with ffprobe after download.
        "--match-filter", f"duration<=?{MAX_DURATION_S}",
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
        raise NetworkError(f"download timed out after {timeout_s}s", urlparse(url).hostname or "") from exc
    video = _find_video(out_dir)
    if video is None:
        err = proc.stderr or ""
        if "does not pass filter" in err or "larger than max-filesize" in err:
            raise TooLongError(f"video is over {MAX_DURATION_S // 60} min or {MAX_FILESIZE}")
        if any(hint in err.lower() for hint in _NETWORK_HINTS):
            raise NetworkError("host unreachable: " + (err.strip().splitlines() or [""])[-1],
                               urlparse(url).hostname or "")
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
