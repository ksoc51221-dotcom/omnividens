"""Transcripts: native subtitles first, Whisper (Groq) as the fallback.

VTT parsing and the Groq call follow the claude-video approach (MIT), see NOTICE.
"""

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from reelay.core.config import ConfigError, load_config
from reelay.video.proc import run_tool

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

_CUE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,]\d{3}\s+-->")
_TAG = re.compile(r"<[^>]+>")


def parse_vtt(text: str) -> str:
    """Turn a WebVTT file into '[m:ss] line' text, collapsing YouTube's rolling duplicates."""
    lines: list[tuple[int, str]] = []
    start = 0
    cue_lines: list[str] = []

    def flush() -> None:
        cue = " ".join(cue_lines).strip()
        cue_lines.clear()
        if not cue:
            return
        if lines and cue == lines[-1][1]:
            return
        if lines and cue.startswith(lines[-1][1] + " "):
            lines[-1] = (lines[-1][0], cue)  # rolling caption grew; keep the longer one
            return
        lines.append((start, cue))

    for raw in text.splitlines():
        m = _CUE.match(raw)
        if m:
            flush()
            h, mi, s = (int(x or 0) for x in m.groups())
            start = h * 3600 + mi * 60 + s
        elif raw.strip() and "-->" not in raw and not raw.startswith(("WEBVTT", "Kind:", "Language:")):
            cleaned = _TAG.sub("", raw).strip()
            if cleaned and not cleaned.isdigit():
                cue_lines.append(cleaned)
    flush()
    return "\n".join(f"[{t // 60}:{t % 60:02d}] {line}" for t, line in lines)


def read_subtitle(path: Path) -> str:
    try:
        return parse_vtt(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return ""


def load_groq_key() -> str | None:
    """GROQ_API_KEY from the environment, else `groq_api_key` in ~/.reelay/config.toml."""
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"].strip()
    try:
        key = load_config().get("groq_api_key")
    except ConfigError:
        return None
    return key.strip() if isinstance(key, str) and key.strip() else None


def has_audio(video: Path) -> bool:
    out = run_tool(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(video)],
        30, capture_output=True, text=True,
    ).stdout
    return bool(out.strip())


def whisper_transcribe(video: Path, work_dir: Path, api_key: str) -> str:
    """Extract small mono mp3, send to Groq Whisper, return '[m:ss] text' lines. '' on failure."""
    audio = work_dir / "audio.mp3"
    run_tool(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-t", "600", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio)],
        120, capture_output=True,
    )
    if not audio.exists():
        return ""
    boundary = uuid.uuid4().hex
    fields = {"model": GROQ_MODEL, "response_format": "verbose_json", "temperature": "0"}
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    )
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             f'filename="audio.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n').encode()
    body += audio.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(GROQ_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "reelay/0.1",  # Groq's firewall rejects the default urllib UA
    })
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return ""
    segments = data.get("segments") or []
    if not segments and data.get("text"):
        return data["text"].strip()
    return "\n".join(
        f"[{int(s['start']) // 60}:{int(s['start']) % 60:02d}] {s['text'].strip()}"
        for s in segments if s.get("text", "").strip()
    )
