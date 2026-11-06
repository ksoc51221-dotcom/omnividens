"""Run ffmpeg/ffprobe with a time limit so a hung tool can't block the queue."""

import subprocess

from reelay.video.errors import ToolTimeoutError


def run_tool(cmd: list[str], timeout_s: int, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout; a timeout becomes ToolTimeoutError.

    Text output never raises on bad bytes: ffmpeg echoes a file's metadata, in whatever encoding it has.
    """
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    try:
        return subprocess.run(cmd, timeout=timeout_s, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise ToolTimeoutError(f"{cmd[0]} timed out after {timeout_s}s") from exc
