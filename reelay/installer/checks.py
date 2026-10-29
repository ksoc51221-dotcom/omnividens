"""Is everything Reelay needs on this Mac? Each check says what to do if not."""

import shutil
from dataclasses import dataclass
from typing import Callable

Which = Callable[[str], str | None]

CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str  # what we found, or what is wrong
    fix: str = ""  # exactly what to do (empty when ok)
    required: bool = True


def tool_checks(which: Which = shutil.which) -> list[Check]:
    """ffmpeg, yt-dlp and Claude Code: the three programs outside Reelay itself."""
    has_brew = which("brew") is not None
    brew_fix = ("brew install ffmpeg" if has_brew else
                "Install Homebrew from https://brew.sh (it asks for your Mac password once), "
                "then run: brew install ffmpeg")
    ffmpeg_ok = which("ffmpeg") is not None and which("ffprobe") is not None
    return [
        Check("ffmpeg (reads video frames and sound)", ffmpeg_ok,
              "found" if ffmpeg_ok else "not found", "" if ffmpeg_ok else brew_fix),
        Check("yt-dlp (downloads videos from links)", which("yt-dlp") is not None,
              "found" if which("yt-dlp") else "not found",
              "" if which("yt-dlp") else "uv tool install yt-dlp"),
        Check("Claude Code (does the thinking, uses your Claude login)", which("claude") is not None,
              "found" if which("claude") else "not found",
              "" if which("claude") else
              f"{CLAUDE_INSTALL}   then type  claude  once and log in with your Claude account"),
    ]
