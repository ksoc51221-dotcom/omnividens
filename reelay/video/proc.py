"""Run ffmpeg/ffprobe with a time limit so a hung tool can't block the queue."""

import subprocess

from reelay.video.errors import ToolTimeoutError


def run_tool(cmd: list[str], timeout_s: int, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout; a timeout becomes ToolTimeoutError."""
    try:
        return subprocess.run(cmd, timeout=timeout_s, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise ToolTimeoutError(f"{cmd[0]} timed out after {timeout_s}s") from exc
