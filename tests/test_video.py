"""Tests for reelay.video. Tiny ffmpeg-generated clips, no network."""

import shutil
import subprocess
from pathlib import Path

import pytest

from reelay.video import DownloadError, FrameError, load_video
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
