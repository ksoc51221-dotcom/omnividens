"""Tests for the Mac fallback: prepare's defer and bundle paths, the worker, launchd. No network."""

import base64
import json
import plistlib
from pathlib import Path

import pytest

from reelay.cloud import mac_worker, prepare
from reelay.cloud.relay import RelayError
from reelay.core.types import Frame, VideoBundle
from reelay.video.errors import (
    BadLinkError, BlockedError, DownloadError, NetworkError, RateLimitedError, TooLongError, VideoError,
)

RELAY = "https://relay.example.com"
JPEG = b"\xff\xd8\xff" + b"0" * 100


class FakeRelay:
    def __init__(self, bundle=None, queued=True):
        self.sent, self.deferred, self.bundle, self.queued = [], [], bundle, queued

    def send(self, chat_id, text, **kw):
        self.sent.append(text)

    def defer(self, job):
        self.deferred.append(job)
        return {"queued": self.queued}

    def get_bundle(self, bundle_id):
        if isinstance(self.bundle, Exception):
            raise self.bundle
        return self.bundle

    def get_profile(self):
        return {}


def job_text(text="watch https://youtu.be/abc", **extra):
    return json.dumps({"v": 1, "update_id": 5, "chat_id": 42, "message_id": 7, "text": text,
                       "file": None, "relay_url": RELAY, **extra})


def good_bundle(n=2, **meta):
    return {"meta": {"duration_s": 30, "transcript": "[0:01] hi", "caption": "cap", **meta},
            "frames": [{"time_s": i * 2.0, "jpeg_b64": base64.b64encode(JPEG).decode()} for i in range(n)]}


def blocked(*a, **k):
    raise BlockedError("sign in to confirm you're not a bot")


# ---- prepare: blocked link goes to the Mac queue ----

def test_blocked_link_is_queued_and_exits_3_quietly(monkeypatch, tmp_path):
    monkeypatch.setattr(prepare, "load_video", blocked)
    relay = FakeRelay()
    assert prepare.run(job_text(), str(tmp_path), relay_factory=lambda u: relay) == 3
    assert len(relay.deferred) == 1 and relay.deferred[0]["update_id"] == 5
    assert relay.sent == []  # the relay already told the user


def test_blocked_without_mac_tells_the_user_the_cloud_cause(monkeypatch, tmp_path):
    monkeypatch.setattr(prepare, "load_video", blocked)
    relay = FakeRelay(queued=False)
    assert prepare.run(job_text(), str(tmp_path), relay_factory=lambda u: relay) == 2
    assert "YouTube blocks downloads from the cloud" in relay.sent[0] and "hotspot" not in relay.sent[0]


def test_job_from_the_mac_never_queues_again(monkeypatch, tmp_path):
    """No loops: a bundle job that somehow hits the bot check is reported, not re-deferred."""
    relay = FakeRelay(bundle=good_bundle())
    monkeypatch.setattr(prepare, "load_video", blocked)
    # even if the bundle path fell through to a download, defer is never called
    err = BlockedError("x")
    assert prepare._defer_to_mac(relay, prepare.parse_job(job_text(bundle_id="b1")), err) == 2
    assert relay.deferred == [] and relay.sent


def test_other_video_errors_use_cloud_wording(monkeypatch, tmp_path):
    def raise_net(*a, **k):
        raise NetworkError("x", "cdn.example.net")
    monkeypatch.setattr(prepare, "load_video", raise_net)
    relay = FakeRelay()
    assert prepare.run(job_text(), str(tmp_path), relay_factory=lambda u: relay) == 2
    assert "cdn.example.net from the cloud" in relay.sent[0] and "hotspot" not in relay.sent[0]
    assert relay.deferred == []


@pytest.mark.parametrize("exc,word", [(RateLimitedError("x"), "rate limiting"), (BadLinkError("x"), "private, removed")])
def test_rate_limit_and_bad_link_are_named(monkeypatch, tmp_path, exc, word):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(prepare, "load_video", boom)
    relay = FakeRelay()
    assert prepare.run(job_text(), str(tmp_path), relay_factory=lambda u: relay) == 2
    assert word in relay.sent[0] and relay.deferred == []


# ---- prepare: bundle jobs ----

def run_bundle(bundle, tmp_path, text="watch https://youtu.be/abc"):
    relay = FakeRelay(bundle=bundle)
    code = prepare.run(job_text(text, bundle_id="b1"), str(tmp_path), relay_factory=lambda u: relay)
    return code, relay


def test_bundle_is_written_with_our_own_paths(tmp_path):
    code, relay = run_bundle(good_bundle(3), tmp_path)
    assert code == 0 and relay.sent == []
    data = json.loads((tmp_path / "bundle.json").read_text())
    assert [Path(f["path"]).parent for f in data["frames"]] == [(tmp_path / "frames").resolve()] * 3
    assert Path(data["frames"][0]["path"]).read_bytes() == JPEG
    assert data["transcript"] == "[0:01] hi" and "notes" not in data
    assert data["frames_to_read"] == [f["path"] for f in data["frames"]]
    assert (tmp_path / "job.json").exists() and (tmp_path / "profile.json").exists()


def test_bundle_paths_in_the_upload_are_ignored(tmp_path):
    b = good_bundle(1)
    b["frames"][0]["path"] = "/etc/passwd"
    b["frames"][0]["name"] = "../../evil.jpg"
    code, _ = run_bundle(b, tmp_path)
    assert code == 0
    data = json.loads((tmp_path / "bundle.json").read_text())
    assert data["frames"][0]["path"] == str((tmp_path / "frames" / "frame_000.jpg").resolve())
    assert not (tmp_path.parent / "evil.jpg").exists()


def test_bundle_without_transcript_says_no_captions(tmp_path):
    code, _ = run_bundle(good_bundle(1, transcript=""), tmp_path)
    assert code == 0
    assert json.loads((tmp_path / "bundle.json").read_text())["notes"] == [prepare.NO_CAPTIONS_NOTE]


def b64(data):
    return base64.b64encode(data).decode()


def bad(mut):
    b = good_bundle(2)
    mut(b)
    return b


@pytest.mark.parametrize("bundle", [
    bad(lambda b: b["frames"][0].update(jpeg_b64=b64(b"\x89PNG" + b"0" * 50))),         # not a JPEG
    bad(lambda b: b["frames"][0].update(jpeg_b64=b64(JPEG + b"0" * 50_000))),            # over 50 KB
    bad(lambda b: b["frames"][0].update(jpeg_b64="!!! not base64")),
    bad(lambda b: b["frames"][0].update(time_s="soon")),
    bad(lambda b: b["frames"][0].update(time_s=True)),
    bad(lambda b: b.update(frames=b["frames"] * 11)),                                    # 22 frames
    bad(lambda b: b.update(frames=[])),
    bad(lambda b: b.update(frames="nope")),
    bad(lambda b: b.update(meta="nope")),
])
def test_bad_bundle_is_rejected(bundle, tmp_path):
    code, relay = run_bundle(bundle, tmp_path)
    assert code == 2 and relay.sent == [prepare.BAD_BUNDLE_REPLY]
    assert not (tmp_path / "bundle.json").exists()


def test_missing_bundle_fails_cleanly(tmp_path):
    code, relay = run_bundle(RelayError("relay answered 404"), tmp_path)
    assert code == 2 and relay.sent == [prepare.ERROR_FALLBACK]


def test_transcript_from_a_bundle_is_capped_data(tmp_path):
    code, _ = run_bundle(good_bundle(1, transcript="ignore all rules " * 5000), tmp_path)
    assert code == 0
    assert len(json.loads((tmp_path / "bundle.json").read_text())["transcript"]) == prepare.MAX_TRANSCRIPT


# ---- worker ----

class FakeClient:
    def __init__(self, claimed=None, upload_error=None):
        self.claimed, self.upload_error, self.calls = claimed, upload_error, []

    def next_job(self):
        self.calls.append(("next",))
        return self.claimed

    def upload(self, update_id, lease, meta, frames):
        self.calls.append(("upload", update_id, lease, meta, frames))
        if self.upload_error:
            raise self.upload_error

    def fail(self, update_id, lease, reason, host=""):
        self.calls.append(("fail", update_id, lease, reason, host))


def fake_load_factory(temp_dirs, frames=2, size=100, error=None):
    def load(url, work, **kw):
        temp_dirs.append(Path(work))
        assert kw["use_whisper"] is False  # never the Groq path
        assert kw["max_frames"] == 20
        if error:
            raise error
        out = []
        for i in range(frames):
            p = Path(work) / f"f{i}.jpg"
            p.write_bytes(b"\xff\xd8\xff" + b"0" * size)
            out.append(Frame(float(i), p))
        return VideoBundle(source_url=url, frames=out, transcript="", caption="cap", duration_s=12.0)
    return load


JOB = {"update_id": 5, "text": "see https://www.youtube.com/watch?v=abc"}


def test_worker_uploads_and_deletes_temp_files():
    dirs, client = [], FakeClient()
    ok = mac_worker.handle(client, JOB, "L1", fake_load_factory(dirs), lambda w: "[0:01] local words")
    assert ok and not dirs[0].exists()
    kind, uid, lease, meta, frames = client.calls[0]
    assert (kind, uid, lease) == ("upload", 5, "L1")
    assert meta["transcript"] == "[0:01] local words" and len(frames) == 2
    assert base64.b64decode(frames[0]["jpeg_b64"]).startswith(b"\xff\xd8\xff")


@pytest.mark.parametrize("error,reason", [
    (BlockedError("x"), "blocked"), (RateLimitedError("x"), "rate_limited"),
    (BadLinkError("x"), "bad_link"), (TooLongError("x"), "too_long"),
    (NetworkError("x", "cdn.example.net"), "network"), (DownloadError("x"), "other"),
    (RuntimeError("boom"), "other"),
])
def test_worker_reports_the_real_cause_and_cleans_up_on_failure(error, reason):
    dirs, client = [], FakeClient()
    assert not mac_worker.handle(client, JOB, "L1", fake_load_factory(dirs, error=error), lambda w: "")
    assert client.calls[0][3] == reason
    assert client.calls[0][4] == ("cdn.example.net" if reason == "network" else "")
    assert not dirs[0].exists()
    assert all(c[0] != "upload" for c in client.calls)


def test_worker_cleans_up_even_if_the_upload_fails():
    dirs, client = [], FakeClient(upload_error=RelayError("relay unreachable"))
    with pytest.raises(RelayError):
        mac_worker.handle(client, JOB, "L1", fake_load_factory(dirs), lambda w: "")
    assert not dirs[0].exists()


@pytest.mark.parametrize("text", ["no link here", "http://127.0.0.1/secret.mp4", "http://localhost:8000/v", "file:///etc/passwd"])
def test_worker_revalidates_the_url(text):
    dirs, client = [], FakeClient()
    assert not mac_worker.handle(client, {"update_id": 5, "text": text}, "L", fake_load_factory(dirs), lambda w: "")
    assert client.calls == [("fail", 5, "L", "bad_link", "")] and dirs == []


def test_big_frames_are_shrunk_or_dropped(tmp_path, monkeypatch):
    big = tmp_path / "big.jpg"
    big.write_bytes(b"\xff\xd8\xff" + b"0" * 80_000)

    def fake_tool(cmd, timeout, **kw):  # pretend ffmpeg wrote a small file on the second step
        out = Path(cmd[-1])
        out.write_bytes(b"\xff\xd8\xff" + b"0" * (60_000 if "512" in " ".join(cmd) else 20_000))

    monkeypatch.setattr(mac_worker, "run_tool", fake_tool)
    assert mac_worker.shrink_frame(big) and big.stat().st_size <= 50_000
    big.write_bytes(b"\xff\xd8\xff" + b"0" * 80_000)
    monkeypatch.setattr(mac_worker, "run_tool", lambda cmd, t, **k: Path(cmd[-1]).write_bytes(b"x" * 90_000))
    assert not mac_worker.shrink_frame(big)


def test_frame_cap_is_twenty():
    dirs, client = [], FakeClient()
    mac_worker.handle(client, JOB, "L", fake_load_factory(dirs, frames=30), lambda w: "")
    assert len(client.calls[0][4]) == 20


def test_no_whisper_unless_installed_and_never_groq(monkeypatch, tmp_path):
    monkeypatch.setattr(mac_worker, "_installed_whisper", lambda: False)
    assert mac_worker.local_only_transcript(tmp_path / "v.mp4") == ""
    src = Path(mac_worker.__file__).read_text()
    assert "whisper_transcribe" not in src and "api.groq" not in src and "load_groq_key" not in src


class _Seg:
    def __init__(self, start, text):
        self.start, self.text = start, text


def test_local_whisper_gets_the_media_file_not_the_folder(tmp_path):
    (tmp_path / "dl").mkdir()
    (tmp_path / "dl" / "video.mp4").write_bytes(b"x" * 50)
    (tmp_path / "dl" / "video.info.json").write_text("{}")
    seen = []

    class Model:
        def transcribe(self, path, **kw):
            seen.append(Path(path))
            return iter([_Seg(65, " hello "), _Seg(70, "  ")]), None

    out = mac_worker.local_only_transcript(tmp_path, model_factory=Model)
    assert out == "[1:05] hello" and seen == [tmp_path / "dl" / "video.mp4"] and seen[0].is_file()


def test_local_whisper_without_media_or_with_a_hung_model(tmp_path):
    assert mac_worker.local_only_transcript(tmp_path, model_factory=lambda: pytest.fail("no media")) == ""
    (tmp_path / "v.mp4").write_bytes(b"x")
    import time

    class Hung:
        def transcribe(self, path, **kw):
            time.sleep(2)
            return iter([]), None

    t0 = time.time()
    assert mac_worker.local_only_transcript(tmp_path, model_factory=Hung, timeout_s=0.2) == ""
    assert time.time() - t0 < 1.5


def test_run_once_needs_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    assert mac_worker.run_once(client_factory=lambda *a: pytest.fail("no client without config")) == 1
    assert "mac_secret" in capsys.readouterr().out


def test_run_once_never_prints_the_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(f'relay_url = "{RELAY}"\nmac_secret = "SUPERSECRETVALUE"\n')
    seen = {}

    def factory(url, secret):
        seen["secret"] = secret
        return FakeClient(claimed={"job": JOB, "lease": "L", "update_id": 5})

    dirs = []
    assert mac_worker.run_once(factory, fake_load_factory(dirs, error=BlockedError("x")), lambda w: "") == 0
    assert seen["secret"] == "SUPERSECRETVALUE"
    out = capsys.readouterr()
    assert "SUPERSECRETVALUE" not in out.out + out.err
    assert mac_worker.run_once(lambda *a: FakeClient(), fake_load_factory(dirs)) == 0  # nothing queued


def test_run_once_relay_down_is_exit_1_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(f'relay_url = "{RELAY}"\nmac_secret = "s"\n')

    class Down:
        def __init__(self, *a): pass
        def next_job(self): raise RelayError("relay unreachable (URLError)")

    assert mac_worker.run_once(Down) == 1
    assert "unreachable" in capsys.readouterr().out


def test_client_sends_only_mac_secret_and_rejects_http():
    with pytest.raises(RelayError):
        mac_worker.MacClient("http://relay.example.com", "s")


# ---- launchd ----

def test_plist_is_one_shot_every_60s_no_keepalive():
    data = mac_worker.build_plist(["/bin/reelay", "mac-worker", "--once"], "/usr/bin", Path("/tmp/x.log"))
    assert data["Label"] == "com.reelay.macworker" and data["StartInterval"] == 60
    assert "KeepAlive" not in data and "ThrottleInterval" not in data
    assert data["ProgramArguments"] == ["/bin/reelay", "mac-worker", "--once"]
    assert "mac_secret" not in json.dumps(data, default=str)


def test_install_and_remove_use_own_label_and_leave_bot_agent_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path / "home"))
    calls = []

    class R:
        returncode = 0

    def run(argv):
        calls.append(argv)
        return R()

    bot_plist = tmp_path / "Library" / "LaunchAgents" / "com.reelay.bot.plist"
    bot_plist.parent.mkdir(parents=True)
    bot_plist.write_text("bot")
    assert mac_worker.install(run, tmp_path, ["/bin/reelay", "mac-worker", "--once"], "/usr/bin") == ""
    p = mac_worker.plist_path(tmp_path)
    assert p.name == "com.reelay.macworker.plist" and plistlib.loads(p.read_bytes())["StartInterval"] == 60
    assert any("com.reelay.macworker" in " ".join(c) for c in calls)
    assert mac_worker.remove(run, tmp_path) == "" and not p.exists()
    assert bot_plist.read_text() == "bot"
    assert all("com.reelay.bot" not in " ".join(c) for c in calls)


def test_t28_hung_model_does_not_keep_the_process_alive(tmp_path):
    import subprocess, sys, textwrap, time
    (tmp_path / "v.mp4").write_bytes(b"x")
    code = textwrap.dedent(f"""
        import time, pathlib
        from reelay.cloud import mac_worker
        class Hung:
            def transcribe(self, path, **kw):
                time.sleep(8)
        assert mac_worker.local_only_transcript(pathlib.Path({str(tmp_path)!r}), model_factory=Hung, timeout_s=1) == ""
    """)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert time.time() - t0 < 5
