"""prepare: job -> video -> frames + transcript -> bundle.json. Exit 0 ok, 2 failure, 3 nothing to do."""

import json
import mimetypes
import os
from pathlib import Path

from reelay.cloud.job import Job, first_url, github_links, parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.cloud.speech import find_media, local_transcript
from reelay.video import load_video
from reelay.video.errors import VideoError

NO_LINK_REPLY = "Send me a video or a link to a Reel/TikTok/Short."
ERROR_FALLBACK = "Sorry, I couldn't process that. Please try again or send the video file."
_EXT_OK = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def _ext(mime) -> str:
    ext = mimetypes.guess_extension(mime.split(";")[0].strip()) if isinstance(mime, str) else None
    return ext if ext in _EXT_OK else ".mp4"


def _bundle_dict(b, caption_text: str, links: list[str]) -> dict:
    return {
        "source_url": b.source_url,
        "duration_s": b.duration_s,
        "frames": [{"time_s": f.time_s, "path": str(Path(f.image).resolve())} for f in b.frames],
        "transcript": b.transcript,
        "caption": b.caption,
        "message_text": caption_text,
        "github_links": links,
    }


def run(job_text: str, workdir: str, relay_factory=Relay, transcribe=local_transcript) -> int:
    try:
        job = parse_job(job_text)
    except RelayError as e:
        print(f"reelay: {e}")
        return 2
    relay = relay_factory(job.relay_url)
    work = Path(workdir)
    try:
        work.mkdir(parents=True, exist_ok=True)
        url = first_url(job.text)
        if not url and not job.file_id:
            relay.send(job.chat_id, NO_LINK_REPLY)
            return 3
        if job.file_id:
            source = relay.get_file(job.file_id, work / "in" / ("upload" + _ext((job.raw.get("file") or {}).get("mime_type"))))
        else:
            source = url
        bundle = load_video(source, work / "video")
        data = _bundle_dict(bundle, job.text, [])
        if not data["transcript"]:
            data["transcript"] = transcribe(find_media(work / "video", Path(source) if job.file_id else work / "video"))
        profile = relay.get_profile()
        data["github_links"] = github_links(job.text, bundle.caption, data["transcript"])
        (work / "job.json").write_text(json.dumps(job.raw), encoding="utf-8")
        (work / "bundle.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (work / "profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        return 0
    except VideoError as e:
        return _fail(relay, job, e.user_message)
    except RelayError as e:
        print(f"reelay: {e}")
        return _fail(relay, job, ERROR_FALLBACK)
    except Exception as e:  # noqa: BLE001 - never a traceback to the user
        print(f"reelay: prepare failed ({type(e).__name__})")
        return _fail(relay, job, ERROR_FALLBACK)


def _fail(relay, job: Job, message: str) -> int:
    try:
        relay.send(job.chat_id, " ".join(str(message).split())[:300])
    except Exception:  # noqa: BLE001
        pass
    return 2
