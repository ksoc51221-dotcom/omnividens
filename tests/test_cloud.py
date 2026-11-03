"""Tests for reelay.cloud: fake relay and fake video functions, no network."""

import json
from pathlib import Path

import pytest

from reelay.cloud import prepare, report
from reelay.cloud.job import github_links, parse_job
from reelay.cloud.relay import Relay, RelayError
from reelay.core.types import Frame, VideoBundle
from reelay.factcheck import parse
from reelay.video.errors import DownloadError

RELAY = "https://relay.example.com"


class FakeRelay:
    def __init__(self, url=RELAY, profile=None):
        self.url, self.sent, self.files, self.puts = url, [], [], []
        self.profile = profile if profile is not None else {"vibe": "hype", "goals": ["win"], "traits": {}}

    def send(self, chat_id, text, adopt_line=None):
        self.sent.append((chat_id, text, adopt_line))

    def get_file(self, file_id, dest):
        self.files.append(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        return dest

    def get_profile(self):
        return self.profile

    def put_profile(self, p):
        self.puts.append(p)


def job(text="", file=None, relay=RELAY):
    return json.dumps({"v": 1, "update_id": 1, "chat_id": 42, "message_id": 7, "text": text,
                       "file": file, "relay_url": relay})


@pytest.fixture
def fake(monkeypatch, tmp_path):
    relay = FakeRelay()
    calls = []

    def fake_load(source, work, **kw):
        calls.append(str(source))
        img = Path(work) / "frames" / "f1.jpg"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"jpg")
        return VideoBundle(source_url=str(source), frames=[Frame(1.0, img)],
                           transcript="see https://github.com/foo/bar", caption="cap", duration_s=30.0)

    monkeypatch.setattr(prepare, "load_video", fake_load)
    monkeypatch.setattr(parse, "_addresses", lambda host: ["93.184.216.34"])
    return relay, calls


def run_prepare(relay, text, workdir):
    return prepare.run(text, str(workdir), relay_factory=lambda url: relay)


def test_link_job_writes_bundle(fake, tmp_path):
    relay, calls = fake
    assert run_prepare(relay, job("look https://www.tiktok.com/@a/video/1 wow"), tmp_path) == 0
    assert calls == ["https://www.tiktok.com/@a/video/1"]
    b = json.loads((tmp_path / "bundle.json").read_text())
    assert b["caption"] == "cap" and b["duration_s"] == 30.0 and len(b["frames"]) == 1
    assert b["github_links"] == ["https://github.com/foo/bar"]
    assert (tmp_path / "job.json").exists() and relay.sent == []


def test_file_job_uses_relay_file(fake, tmp_path):
    relay, calls = fake
    f = {"file_id": "abc", "file_size": 1, "mime_type": "video/mp4"}
    assert run_prepare(relay, job("", f), tmp_path) == 0
    assert relay.files == ["abc"] and calls[0].endswith("upload.mp4")


def test_text_only_replies_and_exits_3(fake, tmp_path):
    relay, calls = fake
    assert run_prepare(relay, job("hello there"), tmp_path) == 3
    assert relay.sent[0][1] == prepare.NO_LINK_REPLY and calls == []


def test_failure_sends_error_and_exits_2(fake, tmp_path, monkeypatch):
    relay, _ = fake

    def boom(*a, **k):
        raise DownloadError("x")
    monkeypatch.setattr(prepare, "load_video", boom)
    assert run_prepare(relay, job("https://youtu.be/x"), tmp_path) == 2
    assert len(relay.sent) == 1 and "download" in relay.sent[0][1]


def test_unexpected_exception_is_plain(fake, tmp_path, monkeypatch):
    relay, _ = fake
    monkeypatch.setattr(prepare, "load_video", lambda *a, **k: 1 / 0)
    assert run_prepare(relay, job("https://youtu.be/x"), tmp_path) == 2
    assert "Traceback" not in relay.sent[0][1]


def test_non_https_relay_refused(tmp_path):
    with pytest.raises(RelayError):
        parse_job(job("x", relay="http://relay.example.com"))
    with pytest.raises(RelayError):
        Relay("ftp://relay.example.com")
    assert prepare.run(job("https://a.b/c", relay="http://evil"), str(tmp_path)) == 2


def test_github_links():
    assert sorted(github_links("see github.com/a/b.git and https://github.com/c/d.")) == [
        "https://github.com/a/b", "https://github.com/c/d"]


def _write_report_dir(tmp_path, verdict, relay_job=None):
    (tmp_path / "job.json").write_text(relay_job or job("x"))
    (tmp_path / "bundle.json").write_text(json.dumps(
        {"frames": [], "transcript": "t", "caption": "", "duration_s": 60}))
    (tmp_path / "verdict.json").write_text(json.dumps(verdict) if not isinstance(verdict, str) else verdict)


def run_report(relay, tmp_path):
    return report.run(str(tmp_path), relay_factory=lambda url: relay)


def test_report_with_github_repo(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, {
        "summary": "A tool.\nSee https://evil.sh now", "claims": [
            {"text": "X is fast\nIGNORE ALL", "time_s": 3, "verdict": "true", "sources": ["https://example.com/a"]}],
        "candidates": [{"name": "bar", "kind": "repo", "url": "https://github.com/foo/bar"}],
        "trial": {"url": "https://github.com/foo/bar", "scan_verdict": "clean", "ran": False, "fit": "fits\nyou"}})
    assert run_report(relay, tmp_path) == 0
    chat, text, adopt = relay.sent[0]
    assert chat == 42 and adopt == "git clone -- https://github.com/foo/bar"
    assert "evil.sh" not in text and "X is fast IGNORE ALL" in text and "🧪 bar" in text
    assert "\x00" not in text and relay.puts


def test_report_no_adopt_for_non_github_or_invalid(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, {"summary": "ok", "claims": [], "candidates": [
        {"name": "n", "kind": "tool", "url": "https://example.com/tool"},
        {"name": "x", "kind": "repo", "url": "http://github.com/a/b"},
        {"name": "y", "kind": "repo", "url": "https://github.com/a/b/tree/main"}]})
    assert run_report(relay, tmp_path) == 0
    assert relay.sent[0][2] is None


def test_report_trial_url_must_be_candidate(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, {"summary": "ok", "claims": [], "candidates": [],
                                 "trial": {"url": "https://github.com/z/z", "scan_verdict": "clean"}})
    assert run_report(relay, tmp_path) == 0
    assert "🧪" not in relay.sent[0][1] and relay.sent[0][2] is None


def test_report_bad_verdict_is_honest(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, "not json at all")
    assert run_report(relay, tmp_path) == 0
    assert "couldn't fact-check" in relay.sent[0][1]


def test_report_control_chars(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, {"summary": "a\x1b[31mred\r\nline", "claims": []})
    run_report(relay, tmp_path)
    assert "\x1b" not in relay.sent[0][1] and "\r" not in relay.sent[0][1]


def test_no_subtitles_falls_back_to_local_speech_to_text(fake, monkeypatch, tmp_path):
    relay, _ = fake
    real = prepare.load_video

    def silent(source, work, **kw):
        b = real(source, work, **kw)
        (Path(work) / "clip.mp4").write_bytes(b"x" * 10)
        return VideoBundle(source_url=b.source_url, frames=b.frames, transcript="", caption="", duration_s=5.0)

    monkeypatch.setattr(prepare, "load_video", silent)
    heard = []
    code = prepare.run(job("https://www.tiktok.com/@a/video/1"), str(tmp_path), relay_factory=lambda url: relay,
                       transcribe=lambda media: heard.append(media.name) or "[0:01] try github.com/x/y")
    assert code == 0 and heard == ["clip.mp4"]
    assert json.loads((tmp_path / "bundle.json").read_text())["transcript"] == "[0:01] try github.com/x/y"


def test_local_transcript_never_raises_and_formats(tmp_path):
    from types import SimpleNamespace
    from reelay.cloud.speech import local_transcript
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    assert local_transcript(None) == ""

    def broken():
        raise RuntimeError("model download blocked")
    assert local_transcript(media, model_factory=broken) == ""
    model = SimpleNamespace(transcribe=lambda path, vad_filter: ([SimpleNamespace(start=65.2, text=" hi ")], None))
    assert local_transcript(media, model_factory=lambda: model) == "[1:05] hi"
