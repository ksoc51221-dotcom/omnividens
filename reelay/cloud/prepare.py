"""prepare: job -> video -> frames + transcript -> bundle.json. Exit 0 ok, 2 failure, 3 nothing to do."""

import base64
import binascii
import json
import mimetypes
import os
import re
import time
from pathlib import Path

from reelay.cloud.job import Job, first_url, github_links, parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.cloud.speech import find_media, local_transcript
from reelay.video import load_video
from reelay.video.errors import BlockedError, VideoError

NO_LINK_REPLY = "Send me a video or a link to a Reel/TikTok/Short."
ERROR_FALLBACK = "Sorry, I couldn't process that. Please try again or send the video file."
NO_CAPTIONS_NOTE = "No captions were available for this video."
BAD_BUNDLE_REPLY = "Your Mac's download arrived damaged, so I dropped it. Please send the link again."
MAX_FRAMES = 20
MAX_FRAME_BYTES = 50_000
MAX_TRANSCRIPT = 24_000
_JPEG_MAGIC = b"\xff\xd8\xff"
_EXT_OK = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def _ext(mime) -> str:
    ext = mimetypes.guess_extension(mime.split(";")[0].strip()) if isinstance(mime, str) else None
    return ext if ext in _EXT_OK else ".mp4"


def _bundle_dict(b, caption_text: str, links: list[str], started: float = 0.0) -> dict:
    return {
        "source_url": b.source_url,
        "duration_s": b.duration_s,
        "frames": [{"time_s": f.time_s, "path": str(Path(f.image).resolve())} for f in b.frames],
        "transcript": b.transcript,
        "caption": b.caption,
        "message_text": caption_text,
        "github_links": links,
        "started_at": started,
    }


_REPORT_CMD = re.compile(r"/report(?:@\w+)?\s+([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*)\s*")


class BadBundle(Exception):
    """The Mac's upload failed validation. Never shown to the user in detail."""


def _text(v, cap: int) -> str:
    return v[:cap] if isinstance(v, str) else ""


def _write_bundle_frames(raw: dict, work: Path) -> tuple[dict, list[dict]]:
    """Validate the uploaded bundle and write its frames ourselves. Paths come from here, never from the upload."""
    meta, frames = raw.get("meta"), raw.get("frames")
    if not isinstance(meta, dict) or not isinstance(frames, list) or not 0 < len(frames) <= MAX_FRAMES:
        raise BadBundle("shape")
    out_dir = work / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for i, fr in enumerate(frames):
        t = fr.get("time_s") if isinstance(fr, dict) else None
        b64 = fr.get("jpeg_b64") if isinstance(fr, dict) else None
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t < 3600 or not isinstance(b64, str):
            raise BadBundle("frame fields")
        if len(b64) > MAX_FRAME_BYTES * 4 // 3 + 8:
            raise BadBundle("frame size")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise BadBundle("frame encoding") from None
        if len(data) > MAX_FRAME_BYTES or not data.startswith(_JPEG_MAGIC):
            raise BadBundle("frame bytes")
        path = out_dir / f"frame_{i:03d}.jpg"
        path.write_bytes(data)
        out.append({"time_s": float(t), "path": str(path.resolve())})
    return meta, out


def _from_bundle(job: Job, work: Path, relay, started: float) -> int:
    """Job already fetched by the owner's Mac: fetch the upload, validate it, write bundle.json."""
    meta, frames = _write_bundle_frames(relay.get_bundle(job.bundle_id), work)
    transcript = _text(meta.get("transcript"), MAX_TRANSCRIPT)
    caption = _text(meta.get("caption"), 3000)
    dur = meta.get("duration_s")
    data = {
        "source_url": first_url(job.text), "duration_s": float(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) else 0.0,
        "frames": frames, "transcript": transcript, "caption": caption, "message_text": job.text,
        "github_links": github_links(job.text, caption, transcript), "started_at": started,
        "fetched_by": "owner's Mac",
    }
    if not transcript:
        data["notes"] = [NO_CAPTIONS_NOTE]
    (work / "job.json").write_text(json.dumps(job.raw), encoding="utf-8")
    (work / "bundle.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "profile.json").write_text(json.dumps(relay.get_profile(), ensure_ascii=False), encoding="utf-8")
    return 0


def _defer_to_mac(relay, job: Job, err: BlockedError) -> int:
    """YouTube refused the cloud. Queue the job for the owner's Mac; the relay tells the user."""
    print(f"reelay: blocked by the site: {' '.join(str(err).split())[:200]}")
    if job.bundle_id:  # this job already came from the Mac: never queue again
        return _fail(relay, job, err.cloud_message or err.user_message)
    try:
        queued = relay.defer(job.raw).get("queued") is True
    except RelayError as e:
        print(f"reelay: {e}")
        queued = False
    if queued:
        return 3  # the relay already told the user
    return _fail(relay, job, err.cloud_message or err.user_message)


def _repo_only(job: Job, work: Path, relay, started: float) -> int:
    """`/report owner/repo`: no video; the owner typed the exact repo, which counts as confirmed."""
    repo = _REPORT_CMD.fullmatch(job.text).group(1)
    if repo.lower().endswith(".git") or repo.split("/")[1] in (".", ".."):
        relay.send(job.chat_id, "Send /report owner/repo, for example /report anthropics/skills")
        return 3
    data = {"source_url": None, "duration_s": 0, "frames": [], "transcript": "", "caption": "",
            "message_text": "", "github_links": [f"https://github.com/{repo}"], "owner_supplied": [repo],
            "started_at": started}
    (work / "job.json").write_text(json.dumps(job.raw), encoding="utf-8")
    (work / "bundle.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (work / "profile.json").write_text(json.dumps(relay.get_profile(), ensure_ascii=False), encoding="utf-8")
    return 0


def run(job_text: str, workdir: str, relay_factory=Relay, transcribe=local_transcript) -> int:
    try:
        job = parse_job(job_text)
    except RelayError as e:
        print(f"reelay: {e}")
        return 2
    relay = relay_factory(job.relay_url)
    work = Path(workdir)
    started = time.time()
    try:
        work.mkdir(parents=True, exist_ok=True)
        if _REPORT_CMD.fullmatch(job.text or ""):
            return _repo_only(job, work, relay, started)
        url = first_url(job.text)
        if not url and not job.file_id:
            relay.send(job.chat_id, NO_LINK_REPLY)
            return 3
        if job.bundle_id:
            return _from_bundle(job, work, relay, started)
        if job.file_id:
            source = relay.get_file(job.file_id, work / "in" / ("upload" + _ext((job.raw.get("file") or {}).get("mime_type"))))
        else:
            source = url
        bundle = load_video(source, work / "video")
        data = _bundle_dict(bundle, job.text, [], started)
        if not data["transcript"]:
            data["transcript"] = transcribe(find_media(work / "video", Path(source) if job.file_id else work / "video"))
        profile = relay.get_profile()
        if not data["transcript"]:
            data["notes"] = [NO_CAPTIONS_NOTE]
        data["github_links"] = github_links(job.text, bundle.caption, data["transcript"])
        (work / "job.json").write_text(json.dumps(job.raw), encoding="utf-8")
        (work / "bundle.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (work / "profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        return 0
    except BlockedError as e:
        return _defer_to_mac(relay, job, e)
    except VideoError as e:
        print(f"reelay: {type(e).__name__}: {' '.join(str(e).split())[:300]}")
        return _fail(relay, job, e.cloud_message or e.user_message)
    except BadBundle as e:
        print(f"reelay: bad bundle ({e})")
        return _fail(relay, job, BAD_BUNDLE_REPLY)
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
