"""Sample frames with ffmpeg: one every few seconds plus scene cuts, capped."""

import math
import re
import shutil
import subprocess
from pathlib import Path

from reelay.core.types import Frame
from reelay.video.errors import FrameError, ToolTimeoutError
from reelay.video.proc import run_tool

SCENE_THRESHOLD = 0.4
PROBE_TIMEOUT_S = 30
SCENE_TIMEOUT_S = 120
FRAME_TIMEOUT_S = 30


def probe_duration(video: Path) -> float:
    """Length in seconds via ffprobe; 0.0 if unknown (callers must treat 0.0 as unknown)."""
    if shutil.which("ffprobe") is None:
        return 0.0
    out = run_tool(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        PROBE_TIMEOUT_S, capture_output=True, text=True,
    ).stdout.strip()
    try:
        value = float(out)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def plan_times(duration: float, interval: float, cuts: list[float], max_frames: int) -> list[float]:
    """Timestamps to grab: regular interval + scene cuts, deduped, thinned to the cap."""
    regular = [i * interval for i in range(int(duration // interval) + 1)] if duration else [0.0]
    times: list[float] = []
    for t in sorted(regular + cuts):
        if not times or t - times[-1] >= 0.5:  # drop near-duplicates
            times.append(t)
    if len(times) > max_frames:  # keep an even spread
        step = len(times) / max_frames
        times = [times[int(i * step)] for i in range(max_frames)]
    return [round(t, 2) for t in times]


def find_scene_cuts(video: Path) -> list[float]:
    """Timestamps where the picture changes a lot (ffmpeg scene detection)."""
    proc = run_tool(
        ["ffmpeg", "-hide_banner", "-t", "600", "-i", str(video),
         "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-an", "-f", "null", "-"],
        SCENE_TIMEOUT_S, capture_output=True, text=True,
    )
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr)]


def extract_frames(video: Path, out_dir: Path, interval: float = 2.0,
                   max_frames: int = 30, width: int = 512) -> list[Frame]:
    """Save small JPEGs into `out_dir`; returns them with their timestamps."""
    if shutil.which("ffmpeg") is None:
        raise FrameError("ffmpeg is not installed (brew install ffmpeg)")
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video)
    times = plan_times(duration, interval, find_scene_cuts(video), max_frames)
    frames: list[Frame] = []
    for i, t in enumerate(times):
        if duration and t >= duration:
            continue
        path = out_dir / f"frame_{i:03d}.jpg"
        try:
            run_tool(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t),
                 "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2", str(path)],
                FRAME_TIMEOUT_S, capture_output=True,
            )
        except ToolTimeoutError:
            continue  # skip this frame, keep the rest
        if path.exists():
            frames.append(Frame(time_s=t, image=path))
    if not frames:
        raise FrameError(f"no frames could be read from {video.name}")
    return frames
