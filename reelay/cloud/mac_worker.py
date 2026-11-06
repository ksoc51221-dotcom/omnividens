"""Mac fallback worker: fetch one blocked video for the cloud, upload frames + transcript. Nothing else.

It runs no model and no routine. A model that reads hostile video or repo text stays on the
disposable cloud VM; the video file never leaves this Mac, only small frames and the transcript do.
`reelay mac-worker --once` polls the relay once (launchd runs it every 60 s); `install` / `remove`
manage that launchd agent (own label, one-shot process, no KeepAlive).

Config in ~/.reelay/config.toml: `relay_url` and `mac_secret`. The secret is never printed or logged.
"""

import base64
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from reelay.cloud.job import first_url
from reelay.cloud.speech import MAX_CHARS, MODEL, find_media
from reelay.cloud.relay import RelayError, _NoRedirect, check_relay_url
from reelay.core.config import ConfigError, load_config, reelay_home
from reelay.installer import service
from reelay.installer.config_file import private_dir
from reelay.video import load_video
from reelay.video.download import is_private_host, is_url, safe_host
from reelay.video.errors import (
    BadLinkError, BlockedError, NetworkError, RateLimitedError, TooLongError, VideoError,
)
from reelay.video.proc import run_tool

LABEL = "com.reelay.macworker"
MAX_FRAMES = 20
MAX_FRAME_BYTES = 50_000
MAX_TRANSCRIPT = 24_000
MAX_CAPTION = 3_000
TIMEOUT_S = 60
# (width, ffmpeg -q:v) tried in order until a frame fits the size cap; 512 px is enough to read text.
_SHRINK_STEPS = ((512, 8), (448, 10), (384, 12), (320, 15), (256, 18))
_JPEG_MAGIC = b"\xff\xd8\xff"


class MacClient:
    """The three worker routes. Auth is MAC_SECRET only; urllib verifies TLS as usual."""

    def __init__(self, relay_url: str, secret: str):
        self.base = check_relay_url(relay_url)
        self._secret = secret
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _call(self, method: str, path: str, body: dict | None = None):
        headers = {"authorization": f"Bearer {self._secret}", "user-agent": "reelay-mac-worker"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read(200_000)
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raise RelayError(f"relay answered {e.code} for {method} {path}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RelayError(f"relay unreachable ({type(e).__name__})") from None

    def next_job(self) -> dict | None:
        status, data = self._call("GET", "/api/mac/next")
        return data if status == 200 and isinstance(data, dict) else None

    def upload(self, update_id: int, lease: str, meta: dict, frames: list[dict]) -> None:
        self._call("POST", "/api/mac/bundle",
                   {"update_id": update_id, "lease": lease, "meta": meta, "frames": frames})

    def fail(self, update_id: int, lease: str, reason: str, host: str = "") -> None:
        self._call("POST", "/api/mac/fail",
                   {"update_id": update_id, "lease": lease, "reason": reason, "host": host})


def fail_reason(err: Exception) -> tuple[str, str]:
    """(reason, host) the relay turns into a plain message for the user."""
    if isinstance(err, BlockedError):
        return "blocked", ""
    if isinstance(err, RateLimitedError):
        return "rate_limited", ""
    if isinstance(err, BadLinkError):
        return "bad_link", ""
    if isinstance(err, TooLongError):
        return "too_long", ""
    if isinstance(err, NetworkError):
        return "network", getattr(err, "host", "")
    return "other", ""


def shrink_frame(path: Path) -> bool:
    """Make the JPEG at most MAX_FRAME_BYTES, re-encoding smaller if needed. False if it can't fit."""
    if path.stat().st_size <= MAX_FRAME_BYTES:
        return True
    small = path.with_suffix(".small.jpg")
    for width, quality in _SHRINK_STEPS:
        try:
            run_tool(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                      "-vf", f"scale='min({width},iw)':-2", "-q:v", str(quality), str(small)],
                     30, capture_output=True)
        except VideoError:
            return False
        if small.exists() and small.stat().st_size <= MAX_FRAME_BYTES:
            small.replace(path)
            return True
    small.unlink(missing_ok=True)
    return False


def _installed_whisper() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


WHISPER_TIMEOUT_S = 300


def local_only_transcript(work: Path, model_factory=None, timeout_s: int = WHISPER_TIMEOUT_S) -> str:
    """faster-whisper if already installed with its model already downloaded. Never a cloud service.

    `work` is the temp folder; the biggest media file in it is transcribed. The model call runs in a
    thread and is abandoned after `timeout_s`, so a hung model cannot hold the one-shot launchd process.
    """
    media = find_media(work)
    if media is None or (model_factory is None and not _installed_whisper()):
        return ""
    if model_factory is None:
        def model_factory():
            from faster_whisper import WhisperModel
            return WhisperModel(MODEL, device="cpu", compute_type="int8", local_files_only=True)

    def run() -> str:
        segments, _ = model_factory().transcribe(str(media), vad_filter=True)
        lines = [f"[{int(s.start) // 60}:{int(s.start) % 60:02d}] {s.text.strip()}"
                 for s in segments if s.text.strip()]
        return "\n".join(lines)[:MAX_CHARS]

    box: dict[str, str] = {}

    def target() -> None:
        try:
            box["text"] = run()
        except Exception:  # noqa: BLE001 - model not cached, bad audio: just no transcript
            pass

    # A daemon thread is not joined at interpreter exit, so a hung model cannot keep the process alive.
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout_s)
    return "" if t.is_alive() else box.get("text", "")


def build_upload(bundle, transcript: str) -> tuple[dict, list[dict]]:
    """Frames that fit the cap (thinned already by plan_times in extract_frames), as base64."""
    frames = []
    for fr in bundle.frames[:MAX_FRAMES]:
        path = Path(fr.image)
        if path.exists() and shrink_frame(path):
            data = path.read_bytes()
            if data.startswith(_JPEG_MAGIC):
                frames.append({"time_s": fr.time_s, "jpeg_b64": base64.b64encode(data).decode()})
    meta = {"duration_s": bundle.duration_s, "transcript": transcript[:MAX_TRANSCRIPT],
            "caption": (bundle.caption or "")[:MAX_CAPTION]}
    return meta, frames


def handle(client, job: dict, lease: str, load=load_video, transcribe=local_only_transcript) -> bool:
    """One claimed job. Always reports to the relay; always deletes its temp files."""
    update_id = job.get("update_id")
    work = Path(tempfile.mkdtemp(prefix="reelay-mac-"))
    try:
        url = first_url(str(job.get("text", "")))
        if not url or not is_url(url) or is_private_host(url):
            client.fail(update_id, lease, "bad_link")
            return False
        try:
            # use_whisper=False: the Groq path would send audio to a third party.
            bundle = load(url, work, max_frames=MAX_FRAMES, use_whisper=False)
            transcript = bundle.transcript or transcribe(work)
            meta, frames = build_upload(bundle, transcript)
            if not frames:
                raise VideoError("no frame fit the size cap")
        except VideoError as e:
            reason, host = fail_reason(e)
            print(f"mac-worker: {type(e).__name__}")
            client.fail(update_id, lease, reason, host)
            return False
        except Exception as e:  # noqa: BLE001 - never a traceback; the user still gets told
            print(f"mac-worker: failed ({type(e).__name__})")
            client.fail(update_id, lease, "other")
            return False
        client.upload(update_id, lease, meta, frames)
        print("mac-worker: uploaded one video")
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_once(client_factory=MacClient, load=load_video, transcribe=local_only_transcript) -> int:
    """Poll once. 0 = nothing to do or done, 1 = not configured or the relay failed."""
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e)
        return 1
    url, secret = cfg.get("relay_url"), cfg.get("mac_secret")
    if not isinstance(url, str) or not isinstance(secret, str) or not secret.strip():
        print("mac-worker: put relay_url and mac_secret in ~/.reelay/config.toml (see docs/SETUP-CLOUD.md).")
        return 1
    try:
        client = client_factory(url, secret.strip())
        claimed = client.next_job()
        if claimed is None:
            return 0
        job, lease = claimed.get("job"), claimed.get("lease")
        if not isinstance(job, dict) or not isinstance(lease, str):
            return 1
        handle(client, job, lease, load, transcribe)
        return 0
    except RelayError as e:
        print(f"mac-worker: {e}")
        return 1


# ---- launchd: own label, one-shot process every 60 s, no KeepAlive (avoids the bot service's restart loops) ----

def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(command: list[str], path_env: str, log: Path) -> dict:
    data = service.build_plist(command, path_env, log)  # same PATH handling as the bot's agent
    data.pop("KeepAlive", None)
    data.pop("ThrottleInterval", None)
    data.update({"Label": LABEL, "StartInterval": 60, "RunAtLoad": True})
    return data


def worker_command() -> list[str]:
    exe = shutil.which("reelay")
    return [exe, "mac-worker", "--once"] if exe else [sys.executable, "-m", "reelay.installer.cli", "mac-worker", "--once"]


def install(run=service._run, home: Path | None = None, command: list[str] | None = None,
            path_env: str | None = None) -> str:
    """Write the agent and load it. Returns "" on success or a friendly error."""
    plist = plist_path(home)
    log = reelay_home() / "macworker.log"
    private_dir(log.parent)
    log.touch(mode=0o600)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(build_plist(
        command or worker_command(), path_env if path_env is not None else os.environ.get("PATH", ""), log)))
    domain = f"gui/{os.getuid()}"
    try:
        run(["launchctl", "bootout", f"{domain}/{LABEL}"])
        result = run(["launchctl", "bootstrap", domain, str(plist)])
    except (OSError, subprocess.SubprocessError):
        return "I couldn't talk to launchctl (the macOS service manager)."
    return "" if result.returncode == 0 else "macOS refused to start the Mac worker."


def remove(run=service._run, home: Path | None = None) -> str:
    problem = ""
    try:
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    except (OSError, subprocess.SubprocessError):
        problem = "I couldn't stop it with launchctl; it will stop at your next logout."
    plist_path(home).unlink(missing_ok=True)
    return problem


def main(argv: list[str]) -> int:
    action = argv[0] if argv else ""
    if action == "--once":
        return run_once()
    if action in ("install", "remove"):
        problem = (install if action == "install" else remove)()
        print(problem or ("The Mac worker now checks the relay every minute." if action == "install"
                          else "Removed. The Mac worker no longer runs."))
        return 1 if problem else 0
    print("Usage:  reelay mac-worker --once | install | remove")
    return 1
