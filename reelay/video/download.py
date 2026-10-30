"""Download a video with yt-dlp and pick up its native subtitles + caption.

Approach adapted from claude-video (MIT), see NOTICE.
"""

import ipaddress
import json
import os
import re
import signal
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from reelay.video.errors import DownloadError, NetworkError, TooLongError

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
MAX_FILESIZE = "200M"
MAX_FILESIZE_BYTES = 200 * 1024 * 1024
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
    host = (urlparse(url).hostname or "").lower()  # raw host: safe_host() would mangle IPv6
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except (socket.gaierror, ValueError):
            return False  # unknown name: yt-dlp will fail on its own
    addrs = [getattr(a, "ipv4_mapped", None) or a for a in addrs]  # ::ffff:10.0.0.1 -> 10.0.0.1
    return any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
               or a.is_multicast or a.is_unspecified for a in addrs)


def safe_host(url: str) -> str:
    """Hostname limited to [A-Za-z0-9.-] and 60 chars, safe to put in a chat message."""
    return re.sub(r"[^A-Za-z0-9.-]", "", urlparse(url).hostname or "")[:60]


def _network_error_line(stderr: str) -> str | None:
    """The first yt-dlp ERROR line that looks like a network failure (URL text elsewhere is ignored)."""
    for line in stderr.splitlines():
        if line.startswith("ERROR") and any(h in line.lower() for h in _NETWORK_HINTS):
            return line
    return None


def _run_group(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess:
    """Run in its own process group so a timeout also kills yt-dlp's ffmpeg children."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # already gone
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def download(url: str, out_dir: Path, timeout_s: int = 180) -> Downloaded:
    """Fetch `url` into `out_dir`. Raises DownloadError on any failure (and removes partial files)."""
    try:
        return _download(url, out_dir, timeout_s)
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


def _download(url: str, out_dir: Path, timeout_s: int) -> Downloaded:
    if shutil.which("yt-dlp") is None:
        raise DownloadError("yt-dlp is not installed (brew install yt-dlp)")
    if is_private_host(url):
        raise DownloadError("refusing to fetch a local or private address")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "--ignore-config", "--no-playlist", "--ignore-errors", "--no-warnings",
        "--max-filesize", MAX_FILESIZE,
        # "<=?" lets videos with no duration metadata (plain .mp4 links) through;
        # pipeline.py re-checks the real length with ffprobe after download.
        "--match-filter", f"duration<=?{MAX_DURATION_S} & !is_live",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json", "--write-subs", "--write-auto-subs",
        "--sub-langs", "en.*,en", "--sub-format", "vtt", "--convert-subs", "vtt",
        "-o", str(out_dir / "video.%(ext)s"), "--", url,
    ]
    try:
        # A failed subtitle (e.g. HTTP 429) can make yt-dlp exit non-zero even
        # though the video arrived, so we judge success by the file, not the code.
        proc = _run_group(cmd, timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise NetworkError(f"download timed out after {timeout_s}s", safe_host(url)) from exc
    video = _find_video(out_dir)
    if video is None:
        err = proc.stderr or ""
        if "does not pass filter" in err or "larger than max-filesize" in err:
            raise TooLongError(f"video is over {MAX_DURATION_S // 60} min or {MAX_FILESIZE}")
        net_line = _network_error_line(err)
        if net_line:
            raise NetworkError("host unreachable: " + net_line, safe_host(url))
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise DownloadError(f"yt-dlp produced no video: {tail[0]}")
    if video.stat().st_size > MAX_FILESIZE_BYTES:  # yt-dlp's own cap misses some streams
        raise TooLongError(f"file is over {MAX_FILESIZE}")
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
