"""Free on-VM speech-to-text when a video has no subtitles (most TikToks). Never raises.

Uses faster-whisper on CPU if `routine/setup.sh` installed it; otherwise returns "".
Same `[m:ss] text` lines as `reelay.video.transcript`. The result is untrusted data.
"""

from pathlib import Path

MODEL = "base"  # small enough for a 16 GB CPU VM; a 1-minute clip takes seconds
MAX_CHARS = 12_000
_MEDIA_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".m4a", ".mp3", ".wav"}


def find_media(*places: Path) -> Path | None:
    """The largest media file among `places` (files or folders)."""
    found = []
    for place in places:
        if place.is_file():
            found.append(place)
        elif place.is_dir():
            found.extend(p for p in place.rglob("*") if p.is_file())
    media = [p for p in found if p.suffix.lower() in _MEDIA_EXT]
    return max(media, key=lambda p: p.stat().st_size, default=None)


def local_transcript(media: Path | None, model_factory=None) -> str:
    if media is None:
        return ""
    try:
        if model_factory is None:
            from faster_whisper import WhisperModel
            model_factory = lambda: WhisperModel(MODEL, device="cpu", compute_type="int8")  # noqa: E731
        segments, _ = model_factory().transcribe(str(media), vad_filter=True)
        lines = [f"[{int(s.start) // 60}:{int(s.start) % 60:02d}] {s.text.strip()}"
                 for s in segments if s.text.strip()]
        return "\n".join(lines)[:MAX_CHARS]
    except Exception:  # noqa: BLE001 - missing package, bad audio, model download blocked
        return ""
