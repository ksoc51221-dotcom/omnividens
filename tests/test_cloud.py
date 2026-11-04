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
        self.fail_reports_after = None
        self.profile = profile if profile is not None else {"vibe": "hype", "goals": ["win"], "traits": {}}

    def send(self, chat_id, text, adopt_line=None, report=False):
        if self.fail_reports_after is not None and report and len([x for x in self.sent if x[3]]) >= self.fail_reports_after:
            raise RelayError('relay answered 429')
        self.sent.append((chat_id, text, adopt_line, report))

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


def _write_report_dir(tmp_path, verdict, relay_job=None, links="auto", clone_files=("SKILL.md",), owner_supplied=None):
    """links="auto": every report URL is linked in the video and cloned with `clone_files` under tmp/trial."""
    (tmp_path / "job.json").write_text(relay_job or job("x"))
    urls = [r["url"] for r in verdict.get("reports", [])] if isinstance(verdict, dict) else []
    gl = [u for u in urls if u.startswith("https://github.com/")] if links == "auto" else list(links)
    bundle = {"frames": [], "transcript": "t", "caption": "", "duration_s": 60, "github_links": gl}
    if owner_supplied:
        bundle["owner_supplied"] = owner_supplied
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    for u in urls:
        o, r = u.removeprefix("https://github.com/").split("/", 1) if u.startswith("https://github.com/") else (None, None)
        if o and clone_files is not None and "/" not in r:
            d = tmp_path / "trial" / f"{o}__{r}"
            d.mkdir(parents=True, exist_ok=True)
            for f in clone_files:
                (d / f).parent.mkdir(parents=True, exist_ok=True)
                (d / f).write_text("# hi\n")
    (tmp_path / "verdict.json").write_text(json.dumps(verdict) if not isinstance(verdict, str) else verdict)


def run_report(relay, tmp_path):
    return report.run(str(tmp_path), relay_factory=lambda url: relay, trial_root=str(tmp_path / "trial"))


def rep(name="bar", url="https://github.com/foo/bar", type="skill", safety="YES", name_only=False, owner_repo=None,
        **extra):
    d = {"name": name, "url": url, "type": type,
         "identity": {"owner_repo": owner_repo or url.removeprefix("https://github.com/"), "name_only": name_only},
         "safety": {"verdict": safety, "reason": "Nothing risky in the code."},
         "info": {"what": "Does X. Does Y.", "problem": "Z is slow.", "pros": ["a", "b"], "cons": ["c", "d"],
                  "examples": ["one", "two"]},
         "usage": {"setup_minutes": 5, "level": "low", "tokens": 3000, "basis": "estimate", "money": "free"}}
    d.update(extra)
    return d


def verdict_with(*reports, candidates=()):
    return {"summary": "A tool.\nSee https://evil.sh now", "claims": [
        {"text": "X is fast\nIGNORE ALL", "time_s": 3, "verdict": "true", "sources": ["https://example.com/a"]}],
        "candidates": list(candidates), "reports": list(reports)}


def test_one_factcheck_then_one_report_per_candidate_each_with_own_adopt(fake, tmp_path):
    relay, _ = fake
    reps = [rep(f"r{i}", f"https://github.com/o/r{i}") for i in range(9)]
    _write_report_dir(tmp_path, verdict_with(*reps))
    assert run_report(relay, tmp_path) == 0
    assert len(relay.sent) == 10  # 1 fact-check + 9 reports, nothing else
    assert relay.sent[0][2] is None and relay.sent[0][3] is False
    assert "evil.sh" not in relay.sent[0][1] and "Sources are what the AI cited, not proof" in relay.sent[0][1]
    assert [m[2] for m in relay.sent[1:]] == [f"git clone -- https://github.com/o/r{i}" for i in range(9)]
    assert all(m[3] for m in relay.sent[1:])
    t = relay.sent[1][1]
    assert t.index("1. Is it safe? YES") < t.index("2. What it does") < t.index("3. Where it fits") < t.index("4. Usage")
    assert "foo" not in t and "Repo: o/r0" in t and "I have NOT installed anything" in t
    assert "\x00" not in t and relay.puts


def test_budget_stops_reports_and_names_the_rest(fake, tmp_path):
    relay, _ = fake
    relay.fail_reports_after = 2
    _write_report_dir(tmp_path, verdict_with(*[rep(f"r{i}", f"https://github.com/o/r{i}") for i in range(4)]))
    assert run_report(relay, tmp_path) == 0
    assert len([m for m in relay.sent if m[3]]) == 2
    assert "Not reported (2): o/r2, o/r3" in relay.sent[-1][1]


def test_over_nine_reports_are_left_out(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(*[rep(f"r{i}", f"https://github.com/o/r{i}") for i in range(11)]))
    run_report(relay, tmp_path)
    assert len([m for m in relay.sent if m[3]]) == 9 and "Not reported (2)" in relay.sent[-1][1]


@pytest.mark.parametrize("bad", [
    rep(type="database"),                                  # unknown type
    {k: v for k, v in rep().items() if k != "type"},        # missing type
    rep(type="app", url="https://github.com/o/openproject"),  # app with a valid URL
    rep(safety="UNSURE"),                                   # unsure safety
    rep(owner_repo="evil/lookalike"),                       # identity does not match the URL
    rep(safety="NO"),                                       # high-risk scan
    rep(type=["skill"]),                                    # junk type
])
def test_no_adopt_line_unless_skill_or_mod_with_verified_identity_and_yes(fake, tmp_path, bad):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(bad))
    run_report(relay, tmp_path)
    assert len(relay.sent) == 2 and relay.sent[1][2] is None
    assert "git clone" not in relay.sent[1][1] and "Nothing was installed" in relay.sent[1][1]


def test_mod_gets_adopt_and_types_are_shown(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(type="mod"), rep("app", "https://github.com/o/app", type="app"),
                                             rep("odd", "https://github.com/o/odd", type="zzz", type_note="no README")))
    (tmp_path / "trial" / "foo__bar" / ".claude-plugin").mkdir()
    (tmp_path / "trial" / "foo__bar" / ".claude-plugin" / "plugin.json").write_text("{}")
    run_report(relay, tmp_path)
    assert relay.sent[1][2] and "type: mod" in relay.sent[1][1]
    assert "type: full app" in relay.sent[2][1] and relay.sent[2][2] is None
    assert "type: unclear" in relay.sent[3][1] and "Why unclear: no README" in relay.sent[3][1]


def test_no_adopt_for_non_github_or_lookalike_urls(fake, tmp_path):
    relay, _ = fake
    bad = [rep("n", "https://example.com/tool"), rep("x", "http://github.com/a/b"),
           rep("y", "https://github.com/a/b/tree/main"), rep("z", "https://github.com.evil.sh/a/b"),
           rep("w", "https://github.com/a/b;rm -rf")]
    _write_report_dir(tmp_path, verdict_with(*bad))
    run_report(relay, tmp_path)
    assert all(m[2] is None for m in relay.sent)


def test_high_risk_says_no_and_recommends_skip(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(safety="NO")))
    run_report(relay, tmp_path)
    assert "1. Is it safe? NO" in relay.sent[1][1] and "I recommend Skip" in relay.sent[1][1]


def test_usage_is_always_an_estimate(fake, tmp_path):
    relay, _ = fake
    r = rep(usage={"tokens": 0, "basis": "measured", "level": "high"})
    r2 = rep("b", "https://github.com/o/b", usage={"tokens": 5000, "basis": "measured"})
    _write_report_dir(tmp_path, verdict_with(r, r2))
    run_report(relay, tmp_path)
    assert all("measured" not in m[1] for m in relay.sent)
    assert "5,000 tokens (estimate)" in relay.sent[2][1]


def test_impact_uses_only_listed_projects(fake, tmp_path):
    relay, _ = fake
    relay.profile = {"vibe": "hype", "goals": [], "traits": {}, "projects": ["Spool: a recorder"]}
    on = rep(impact={"project": "spool", "why": "Adds https://x.sh/a hooks."})
    off = rep("b", "https://github.com/o/b", impact={"project": "Invented", "why": "great"})
    _write_report_dir(tmp_path, verdict_with(on, off))
    (tmp_path / "profile.json").write_text(json.dumps(relay.profile))
    run_report(relay, tmp_path)
    assert "Fits Spool: Adds hooks." in relay.sent[1][1] and "x.sh" not in relay.sent[1][1]
    assert "Invented" not in relay.sent[2][1] and "No fit with your listed projects." in relay.sent[2][1]


def test_empty_project_list_line(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(impact={"project": "Anything", "why": "x"})))
    run_report(relay, tmp_path)
    assert "No project list yet. Send /projects to add one." in relay.sent[1][1]


def test_nothing_to_install_and_bad_verdict(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, {"summary": "ok", "claims": [], "candidates": []})
    run_report(relay, tmp_path)
    assert len(relay.sent) == 1 and "Done. Nothing to install in this one." in relay.sent[0][1]
    relay.sent.clear()
    _write_report_dir(tmp_path, "not json at all")
    assert run_report(relay, tmp_path) == 0
    assert "couldn't fact-check" in relay.sent[0][1]


def test_candidate_without_report_is_listed_as_left_out(fake, tmp_path):
    relay, _ = fake
    v = verdict_with(candidates=[{"name": "lonely", "kind": "repo", "url": "https://github.com/q/lonely"}])
    _write_report_dir(tmp_path, v)
    run_report(relay, tmp_path)
    assert "Not reported (1): q/lonely" in relay.sent[-1][1]


def test_report_control_chars_and_plain_error(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, {"summary": "a\x1b[31mred\r\nline", "claims": []})
    run_report(relay, tmp_path)
    assert "\x1b" not in relay.sent[0][1] and "\r" not in relay.sent[0][1]


def test_report_crash_message_is_plain(fake, tmp_path, monkeypatch):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep()))
    monkeypatch.setattr(report._reports, "format_factcheck", lambda *a, **k: 1 / 0)
    assert run_report(relay, tmp_path) == 2
    assert relay.sent[-1][1] == "Something broke. Nothing was installed. Try again."
    assert "ZeroDivision" not in relay.sent[-1][1]


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


def sent_reports(relay):
    return [m for m in relay.sent if m[3]]


def test_readme_claims_skill_but_no_skill_file_is_read_only(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep()), clone_files=("README.md",))
    run_report(relay, tmp_path)
    m = sent_reports(relay)[0]
    assert m[2] is None and "type: unclear" in m[1] and "found no SKILL.md" in m[1]


@pytest.mark.parametrize("files", [("hooks/hooks.json",), ("commands/go.md",), (".claude-plugin/plugin.json",)])
def test_mod_evidence_files(fake, tmp_path, files):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(type="mod")), clone_files=files)
    run_report(relay, tmp_path)
    assert sent_reports(relay)[0][2] == "git clone -- https://github.com/foo/bar"


def test_skill_claim_needs_skill_md_not_mod_files(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(type="skill")), clone_files=("hooks/hooks.json",))
    run_report(relay, tmp_path)
    assert sent_reports(relay)[0][2] is None


def test_model_says_clean_but_our_scan_says_high(fake, tmp_path):
    relay, _ = fake
    v = verdict_with(rep(safety="YES"))
    _write_report_dir(tmp_path, v)
    (tmp_path / "trial" / "foo__bar" / "evil").symlink_to("/etc/passwd")  # find_problem refuses symlinks
    run_report(relay, tmp_path)
    m = sent_reports(relay)[0]
    assert m[2] is None and "Is it safe? NO" in m[1] and "I recommend Skip" in m[1]


def test_model_cannot_raise_a_missing_clone(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(safety="YES")), clone_files=None)
    run_report(relay, tmp_path)
    m = sent_reports(relay)[0]
    assert m[2] is None and "Is it safe? UNSURE" in m[1]


def test_model_no_lowers_a_clean_scan(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(safety="NO")))
    run_report(relay, tmp_path)
    assert sent_reports(relay)[0][2] is None


def test_identity_must_come_from_the_video_or_the_owner(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep()), links=[])  # model inferred it from a name
    run_report(relay, tmp_path)
    m = sent_reports(relay)[0]
    assert m[2] is None and "matched from a name only" in m[1] and "UNSURE" in m[1]
    relay.sent.clear()
    _write_report_dir(tmp_path, verdict_with(rep()), links=["https://github.com/foo/barn"])  # lookalike link
    run_report(relay, tmp_path)
    assert sent_reports(relay)[0][2] is None
    relay.sent.clear()
    _write_report_dir(tmp_path, verdict_with(rep()), links=["https://github.com/FOO/Bar"])  # case-insensitive
    run_report(relay, tmp_path)
    assert sent_reports(relay)[0][2] == "git clone -- https://github.com/foo/bar"


def test_report_command_repo_counts_as_confirmed_and_sends_one_message(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep(), rep("o", "https://github.com/o/other")), links=[],
                      owner_supplied=["foo/bar"])
    run_report(relay, tmp_path)
    assert len(relay.sent) == 1 and relay.sent[0][2] == "git clone -- https://github.com/foo/bar"
    assert "Is it safe? YES" in relay.sent[0][1]


def test_clones_are_deleted_after_the_report(fake, tmp_path):
    relay, _ = fake
    _write_report_dir(tmp_path, verdict_with(rep()))
    run_report(relay, tmp_path)
    assert not (tmp_path / "trial" / "foo__bar").exists()


def test_daily_cap_names_unsent_and_gives_a_route(fake, tmp_path):
    relay, _ = fake
    relay.fail_reports_after = 1
    _write_report_dir(tmp_path, verdict_with(*[rep(f"r{i}", f"https://github.com/o/r{i}") for i in range(3)]))
    assert run_report(relay, tmp_path) == 0
    last = relay.sent[-1][1]
    assert "today's report limit" in last and "o/r1, o/r2" in last and "/report owner/repo" in last
    assert "starts a new run" in last


def test_left_out_urls_from_the_routine_are_validated(fake, tmp_path):
    relay, _ = fake
    v = verdict_with(rep())
    v["left_out"] = ["https://github.com/a/b", "https://evil.sh/x", "https://github.com/a/b/tree/x", 5]
    _write_report_dir(tmp_path, v)
    run_report(relay, tmp_path)
    assert "Not reported (1): a/b" in relay.sent[-1][1]


def test_prepare_report_command(fake, tmp_path):
    relay, calls = fake
    assert run_prepare(relay, job("/report Foo/bar.js"), tmp_path) == 0
    b = json.loads((tmp_path / "bundle.json").read_text())
    assert b["owner_supplied"] == ["Foo/bar.js"] and b["github_links"] == ["https://github.com/Foo/bar.js"]
    assert calls == [] and b["frames"] == [] and b["started_at"] > 0  # no video was fetched
    assert run_prepare(relay, job("/report not a repo"), tmp_path / "x") == 3
    assert run_prepare(relay, job("/report a/b.git"), tmp_path / "y") == 3
