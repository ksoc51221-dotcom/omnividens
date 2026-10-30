"""Tests for reelay.video. Tiny ffmpeg-generated clips, no network."""

import shutil
import subprocess
from pathlib import Path

import pytest

from reelay.video import VideoError, DownloadError, FrameError, NetworkError, TooLongError, load_video
from reelay.video import download as dl
from reelay.video import pipeline
from reelay.video.frames import plan_times
from reelay.video.transcript import parse_vtt

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")

VTT = """WEBVTT

00:00:01.000 --> 00:00:02.000
hello <c>there</c>

00:00:02.000 --> 00:00:03.000
hello there

00:00:03.000 --> 00:00:05.000
hello there friend
"""


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """6 s test pattern with a sine tone."""
    path = tmp_path_factory.mktemp("clip") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=6",
         "-f", "lavfi", "-i", "sine=duration=6", "-shortest", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def test_parse_vtt_dedupes_rolling_captions():
    assert parse_vtt(VTT) == "[0:01] hello there friend"


def test_plan_times_regular_plus_cuts_capped():
    assert plan_times(6, 2, [], 30) == [0.0, 2.0, 4.0, 6.0]
    assert 3.1 in plan_times(6, 2, [3.1], 30)
    assert len(plan_times(600, 2, [], 30)) == 30


@needs_ffmpeg
def test_local_file_gives_bundle(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "load_groq_key", lambda: None)  # no Whisper
    bundle = load_video(clip, tmp_path)
    assert bundle.source_url is None
    assert 5.5 < bundle.duration_s < 6.5
    assert len(bundle.frames) >= 3
    assert all(f.image.exists() for f in bundle.frames)
    assert bundle.frames[0].time_s == 0.0
    assert bundle.transcript == ""


@needs_ffmpeg
def test_frame_cap(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "load_groq_key", lambda: None)
    assert len(load_video(clip, tmp_path, interval=0.5, max_frames=4).frames) == 4


@needs_ffmpeg
def test_whisper_used_only_without_subtitles(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "load_groq_key", lambda: "k")
    monkeypatch.setattr(pipeline, "whisper_transcribe", lambda *a: "[0:00] spoken")
    assert load_video(clip, tmp_path / "a").transcript == "[0:00] spoken"
    assert load_video(clip, tmp_path / "b", use_whisper=False).transcript == ""


@needs_ffmpeg
def test_url_uses_native_subtitles_and_caption(clip, tmp_path, monkeypatch):
    sub = tmp_path / "video.en.vtt"
    sub.write_text(VTT)
    monkeypatch.setattr(pipeline, "download", lambda url, d: dl.Downloaded(clip, sub, "Title\nDesc"))
    monkeypatch.setattr(pipeline, "whisper_transcribe", lambda *a: pytest.fail("no whisper needed"))
    bundle = load_video("https://example.com/v", tmp_path / "w")
    assert bundle.source_url == "https://example.com/v"
    assert bundle.transcript == "[0:01] hello there friend"
    assert bundle.caption == "Title\nDesc"


def test_missing_file_is_download_error(tmp_path):
    with pytest.raises(DownloadError):
        load_video(tmp_path / "nope.mp4", tmp_path)


def test_failed_download_raises_with_user_message(tmp_path):
    with pytest.raises(DownloadError) as err:
        dl.download("http://127.0.0.1:9/none", tmp_path, timeout_s=30)
    assert "screen recording" in err.value.user_message


@needs_ffmpeg
def test_garbage_file_is_frame_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(FrameError):
        load_video(bad, tmp_path / "w")


@pytest.mark.parametrize("url", [
    "http://localhost:8000/v", "http://127.0.0.1/v", "http://192.168.1.5/v",
    "http://10.0.0.2/v", "http://169.254.169.254/latest", "http://[::1]/v",
])
def test_private_addresses_rejected_before_ytdlp(url, tmp_path):
    with pytest.raises(DownloadError, match="local or private"):
        dl.download(url, tmp_path)


def test_public_ip_is_not_private():
    assert not dl.is_private_host("https://8.8.8.8/v")


def test_filtered_download_raises_too_long(tmp_path, monkeypatch):
    class Proc:
        returncode, stdout = 0, ""
        stderr = "video.mp4 does not pass filter (duration<=600), skipping .."
    monkeypatch.setattr(dl, "_run_group", lambda *a, **k: Proc())
    monkeypatch.setattr(dl, "is_private_host", lambda u: False)
    with pytest.raises(TooLongError) as err:
        dl.download("https://example.com/v", tmp_path)
    assert "too long" in err.value.user_message


def test_download_passes_caps_to_ytdlp(tmp_path, monkeypatch):
    seen = {}
    def fake(cmd, timeout_s):
        seen["cmd"] = cmd
        raise dl.subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(dl, "_run_group", fake)
    monkeypatch.setattr(dl, "is_private_host", lambda u: False)
    with pytest.raises(DownloadError):
        dl.download("https://example.com/v", tmp_path)
    assert "200M" in seen["cmd"] and any(a.startswith("duration<=?600") for a in seen["cmd"])


@needs_ffmpeg
def test_long_local_file_rejected(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_DURATION_S", 3)
    with pytest.raises(TooLongError):
        load_video(clip, tmp_path)


def test_groq_key_from_env_then_toml(tmp_path, monkeypatch):
    from reelay.video.transcript import load_groq_key
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert load_groq_key() is None  # no file
    (tmp_path / "config.toml").write_text('groq_api_key = "from-toml"\n')
    assert load_groq_key() == "from-toml"
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert load_groq_key() == "from-env"
    monkeypatch.delenv("GROQ_API_KEY")
    (tmp_path / "config.toml").write_text("not = valid = toml")
    assert load_groq_key() is None  # broken config means no key, no crash


def _fake_ytdlp(monkeypatch, stderr):
    class Proc:
        returncode, stdout = 1, ""
    Proc.stderr = stderr
    monkeypatch.setattr(dl, "_run_group", lambda *a, **k: Proc())
    monkeypatch.setattr(dl, "is_private_host", lambda u: False)


@pytest.mark.parametrize("stderr", [
    "ERROR: [generic] Unable to download webpage: SSL: CERTIFICATE_VERIFY_FAILED",
    "ERROR: <urlopen error [Errno 8] nodename nor servname provided, or not known>",
    "ERROR: Connection refused",
])
def test_network_failure_names_host_and_never_suggests_insecure(stderr, tmp_path, monkeypatch):
    _fake_ytdlp(monkeypatch, stderr)
    with pytest.raises(NetworkError) as err:
        dl.download("https://videos.example.com/a.mp4", tmp_path)
    msg = err.value.user_message
    assert "videos.example.com" in msg and "another network" in msg
    assert "certificate" not in msg.lower() and "insecure" not in msg.lower()


def test_unsupported_page_keeps_screen_recording_advice(tmp_path, monkeypatch):
    _fake_ytdlp(monkeypatch, "ERROR: Unsupported URL: https://example.com/page")
    with pytest.raises(DownloadError) as err:
        dl.download("https://example.com/page", tmp_path)
    assert not isinstance(err.value, NetworkError)
    assert "screen recording" in err.value.user_message


def test_missing_duration_is_not_rejected(clip, tmp_path, monkeypatch):
    """A link whose metadata has no duration still downloads; length is checked afterwards."""
    sub = None
    monkeypatch.setattr(pipeline, "download", lambda url, d: dl.Downloaded(clip, sub, ""))
    monkeypatch.setattr(pipeline, "load_groq_key", lambda: None)
    assert load_video("https://example.com/plain.mp4", tmp_path).duration_s > 5


def test_filter_ignore_config_and_double_dash_in_argv(tmp_path, monkeypatch):
    seen = {}
    def fake(cmd, timeout_s):
        seen["cmd"] = cmd
        raise dl.subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(dl, "_run_group", fake)
    monkeypatch.setattr(dl, "is_private_host", lambda u: False)
    with pytest.raises(NetworkError):
        dl.download("https://example.com/-v", tmp_path)
    cmd = seen["cmd"]
    assert "--ignore-config" in cmd
    assert "duration<=?600 & !is_live" in cmd
    assert cmd[-2:] == ["--", "https://example.com/-v"]


def test_timeout_names_host_and_cleans_partial_dir(tmp_path, monkeypatch):
    out = tmp_path / "dl"
    def fake(cmd, timeout_s):
        out.mkdir(exist_ok=True); (out / "video.mp4.part").write_text("x")
        raise dl.subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(dl, "_run_group", fake)
    monkeypatch.setattr(dl, "is_private_host", lambda u: False)
    with pytest.raises(NetworkError) as err:
        dl.download("https://cdn.example.com/a.mp4", out)
    assert "cdn.example.com" in err.value.user_message
    assert not out.exists()


def test_oversize_file_after_download_rejected(tmp_path, monkeypatch):
    def fake(cmd, timeout_s):
        (tmp_path / "video.mp4").write_bytes(b"x")
        class P: returncode, stderr = 0, ""
        return P()
    monkeypatch.setattr(dl, "_run_group", fake)
    monkeypatch.setattr(dl, "is_private_host", lambda u: False)
    monkeypatch.setattr(dl, "MAX_FILESIZE_BYTES", 0)
    with pytest.raises(TooLongError):
        dl.download("https://example.com/a.mp4", tmp_path)


@pytest.mark.parametrize("duration", [0.0, float("nan"), -1.0, float("inf"), 601.0])
def test_unknown_or_long_duration_fails_closed(duration, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "download", lambda url, d: dl.Downloaded(f))
    monkeypatch.setattr(pipeline, "probe_duration", lambda v: duration)
    monkeypatch.setattr(pipeline, "extract_frames", lambda *a: pytest.fail("must not extract"))
    with pytest.raises((FrameError, TooLongError)):
        load_video("https://example.com/a.mp4", tmp_path / "w")


def test_probe_duration_rejects_nan_and_missing_ffprobe(tmp_path, monkeypatch):
    from reelay.video import frames
    class R: stdout = "nan\n"
    monkeypatch.setattr(frames.subprocess, "run", lambda *a, **k: R())
    assert frames.probe_duration(tmp_path / "x") == 0.0
    monkeypatch.setattr(frames.shutil, "which", lambda n: None)
    assert frames.probe_duration(tmp_path / "x") == 0.0


def test_network_hints_only_from_error_lines_and_host_sanitised(tmp_path, monkeypatch):
    # URL text mentioning "timed out" on a non-ERROR line must not count as a network failure
    _fake_ytdlp(monkeypatch, "[generic] Extracting URL: https://a.com/timed out\nERROR: Unsupported URL")
    with pytest.raises(DownloadError) as err:
        dl.download("https://a.com/x", tmp_path)
    assert not isinstance(err.value, NetworkError)
    assert dl.safe_host("https://" + "a" * 80 + ".com/") == ("a" * 60)


def test_hourless_vtt_cues_keep_their_times():
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nfirst\n\n01:05.000 --> 01:06.000\nsecond\n"
    assert parse_vtt(vtt) == "[0:01] first\n[1:05] second"


def test_hung_ffmpeg_becomes_clean_video_error(tmp_path, monkeypatch):
    from reelay.video import ToolTimeoutError, frames, proc, transcript
    def hang(cmd, **kw):
        raise proc.subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(proc.subprocess, "run", hang)
    monkeypatch.setattr(frames.shutil, "which", lambda n: "/usr/bin/" + n)
    with pytest.raises(ToolTimeoutError) as err:
        frames.probe_duration(tmp_path / "x.mp4")
    assert "too long" in err.value.user_message
    with pytest.raises(ToolTimeoutError):
        frames.extract_frames(tmp_path / "x.mp4", tmp_path / "f")
    with pytest.raises(ToolTimeoutError):
        transcript.has_audio(tmp_path / "x.mp4")
    assert isinstance(err.value, VideoError)
