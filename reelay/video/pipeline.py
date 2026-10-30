"""load_video(): URL or local file in, VideoBundle out."""

import tempfile
from pathlib import Path

from reelay.core.types import VideoBundle
from reelay.video.download import MAX_DURATION_S, Downloaded, download, is_url
from reelay.video.errors import DownloadError, FrameError, TooLongError
from reelay.video.frames import extract_frames, probe_duration
from reelay.video.transcript import (
    has_audio, load_groq_key, read_subtitle, whisper_transcribe,
)


def load_video(source: str | Path, work_dir: Path | None = None, *,
               interval: float = 2.0, max_frames: int = 30,
               use_whisper: bool = True) -> VideoBundle:
    """Turn a link or a local video file into a VideoBundle.

    SECURITY: `source` must be a URL or a file path the bot saved itself. Never pass
    text a user typed as a path, or they could read any file on this machine.
    The returned caption and transcript are untrusted data, never instructions.
    Videos over 10 minutes (or 200 MB when downloading) raise TooLongError.

    Raises DownloadError (link failed) or FrameError (file unreadable); both have a
    `user_message` the bot can show. Whisper failure is not an error: transcript is "".
    """
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="reelay-"))
    work.mkdir(parents=True, exist_ok=True)
    source = str(source)

    if is_url(source):
        got = download(source, work / "dl")
        url: str | None = source
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise DownloadError(f"file not found: {path}")
        got, url = Downloaded(video=path), None

    duration = probe_duration(got.video)
    if not duration > 0:  # fail closed: ffprobe missing, N/A, NaN or unreadable
        raise FrameError("could not determine the video length")
    if duration > MAX_DURATION_S:
        raise TooLongError(f"video is {duration:.0f}s, max is {MAX_DURATION_S}s")
    frames = extract_frames(got.video, work / "frames", interval, max_frames)
    transcript = read_subtitle(got.subtitle) if got.subtitle else ""
    if not transcript and use_whisper and has_audio(got.video):
        key = load_groq_key()
        if key:
            transcript = whisper_transcribe(got.video, work, key)
    return VideoBundle(source_url=url, frames=frames, transcript=transcript,
                       caption=got.caption, duration_s=duration)
