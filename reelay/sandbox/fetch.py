"""Step 1: shallow git clone of an https URL into a fresh folder."""

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from reelay.video.download import is_private_host


class FetchError(Exception):
    pass


def check_url(url: str) -> None:
    """Allow only plain https URLs to public hosts."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise FetchError("only https:// URLs are allowed")
    if parsed.username or parsed.password:
        raise FetchError("URLs with embedded credentials are not allowed")
    if url.startswith("-") or is_private_host(url):
        raise FetchError("refusing to fetch a local or private address")


def clone(url: str, dest: Path, timeout_s: int = 120) -> None:
    """Shallow-clone `url` into `dest` (which must not exist yet)."""
    check_url(url)
    cmd = [
        "git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
        "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "clone", "--depth", "1", "--no-tags", "--single-branch", "--", url, str(dest),
    ]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        raise FetchError("git clone timed out") from None
    except FileNotFoundError:
        raise FetchError("git is not installed") from None
    if proc.returncode != 0:
        raise FetchError("git clone failed: " + proc.stderr.strip()[-300:])
