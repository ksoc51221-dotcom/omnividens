"""load_video(): URL or local file in, VideoBundle out."""

import tempfile
from pathlib import Path

from reelay.core.types import VideoBundle
from reelay.video.download import Downloaded, download, is_url
from reelay.video.errors import DownloadError
from reelay.video.frames import extract_frames, probe_duration
from reelay.video.transcript import (
    has_audio, load_groq_key, read_subtitle, whisper_transcribe,
)


def load_video(source: str | Path, work_dir: Path | None = None, *,
               interval: float = 2.0, max_frames: int = 30,
               use_whisper: bool = True) -> VideoBundle:
    """Turn a link or a local video file into a VideoBundle.

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

    frames = extract_frames(got.video, work / "frames", interval, max_frames)
    transcript = read_subtitle(got.subtitle) if got.subtitle else ""
    if not transcript and use_whisper and has_audio(got.video):
        key = load_groq_key()
        if key:
            transcript = whisper_transcribe(got.video, work, key)
    return VideoBundle(source_url=url, frames=frames, transcript=transcript,
                       caption=got.caption, duration_s=probe_duration(got.video))
