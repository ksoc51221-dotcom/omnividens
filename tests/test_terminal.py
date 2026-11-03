"""Tests for reelay.terminal: `reelay try` with fake deps and fake stdin. No network, keys or video."""

import io
import socket
import ssl
from pathlib import Path

import pytest

from reelay.adapters import AdapterResult
from reelay.core.pipeline import Deps
from reelay.core.types import Candidate, Claim, Frame, Instruction, TrialReport, Verdict, VideoBundle
from reelay.terminal import classify, main, run_try
from reelay.terminal.netfail import explain
from reelay.video import VideoError

CAND = Candidate("cool-skill", "skill", "https://github.com/a/cool-skill")


class Fakes:
    def __init__(self):
        self.calls = {k: [] for k in ("video", "route", "adapter")}
        self.decisions = []
        self.load_error: Exception | None = None
        self.route_result = Instruction(task="summarise the README", project=Path("/proj"))

    def deps(self) -> Deps:
        return Deps(load_video=self.load_video, fact_check=self.fact_check, run_trial=self.run_trial,
                    route=self.route, run_adapter=self.run_adapter, adapter_name="fake-ai",
                    record_decision=lambda *a: self.decisions.append(a))

    def load_video(self, source, work):
        self.calls["video"].append(source)
        if self.load_error:
            raise self.load_error
        return VideoBundle(source, [Frame(0.0, Path("f.jpg"))], "hello", "cap", 30.0)

    def fact_check(self, bundle, profile):
        return Verdict("Mostly fine.", [Claim("water is wet", 1.0, "true")], [CAND])

    def run_trial(self, candidate):
        return TrialReport(candidate, True, True, "clean", 100, 2.0, "Ran in isolation.")

    def route(self, text):
        self.calls["route"].append(text)
        return self.route_result

    def run_adapter(self, instruction):
        self.calls["adapter"].append(instruction)
        return AdapterResult(ok=True, text="It says hi.")


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path / "home"))
    return Fakes()


def run(fakes, arg, stdin="", **kw):
    out = io.StringIO()
    code = run_try(arg, deps=fakes.deps(), stdin=io.StringIO(stdin), stdout=out, **kw)
    return code, out.getvalue()


# ---- argument types

def test_classify(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    kind, job = classify(str(clip))
    assert kind == "file" and job.file == clip.resolve()
    kind, job = classify("https://example.com/v")
    assert kind == "link" and job.url == "https://example.com/v"
    assert classify("summarise the README, use opus")[0] == "instruction"
    kind, job = classify("look at https://example.com/v please")  # same rule as the bot
    assert kind == "link" and job.url == "https://example.com/v" and job.text == "look at  please"


def test_link_runs_video_pipeline(fakes):
    code, out = run(fakes, "https://example.com/v", interactive=False)
    assert code == 0 and fakes.calls["video"] == ["https://example.com/v"]
    assert "Mostly fine." in out and "🧾" in out
    assert "1) Adopt  2) Skip" in out and "<b>" not in out


def test_file_runs_video_pipeline(fakes, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    run(fakes, str(clip), interactive=False)
    assert fakes.calls["video"] == [str(clip.resolve())]
    assert clip.exists()  # a user's own file is never deleted


def test_instruction_routes_and_confirms(fakes):
    code, out = run(fakes, "summarise the README, use opus", stdin="1\n", interactive=True)
    assert fakes.calls["route"] == ["summarise the README, use opus"]
    assert "1) Run it  2) Cancel" in out
    assert len(fakes.calls["adapter"]) == 1 and "It says hi." in out


def test_missing_video_path_is_friendly(fakes):
    code, out = run(fakes, "nope/clip.mp4")
    assert code == 1 and "can't find a file" in out and not fakes.calls["route"]


# ---- buttons

def test_choice_adopt_records_decision_and_prints_clone(fakes):
    run(fakes, "https://example.com/v", stdin="1\n", interactive=True)
    assert [d[0] for d in fakes.decisions] == ["terminal"] and fakes.decisions[0][3] == "adopt"


def test_choice_skip(fakes):
    _, out = run(fakes, "https://example.com/v", stdin="2\n", interactive=True)
    assert fakes.decisions[0][3] == "skip" and "Nothing was installed" in out


def test_cancel_runs_nothing(fakes):
    _, out = run(fakes, "do a thing", stdin="2\n", interactive=True)
    assert not fakes.calls["adapter"] and "Cancelled" in out


def test_two_candidates_each_answerable(fakes):
    fakes.fact_check = lambda b, p: Verdict("ok", [], [CAND, Candidate("other", "tool", "https://github.com/b/other")])
    _, out = run(fakes, "https://example.com/v", stdin="2\n3\n", interactive=True)
    assert "1) Adopt  2) Skip" in out and "3) Adopt  4) Skip" in out
    assert [d[3] for d in fakes.decisions] == ["skip", "adopt"]
    assert {d[2].name for d in fakes.decisions} == {"cool-skill", "other"}


def test_exit_code_one_on_failures(fakes):
    assert run(fakes, "https://example.com/v", interactive=False)[0] == 0
    fakes.load_error = socket.gaierror("x")
    assert run(fakes, "https://example.com/v", interactive=False)[0] == 1
    fakes.load_error = VideoError("bad")
    assert run(fakes, "https://example.com/v", interactive=False)[0] == 1


def test_exit_code_one_on_adapter_refusal(fakes):
    fakes.run_adapter = lambda i: AdapterResult(ok=False, error="refused: not allowed")
    code, _ = run(fakes, "do a thing", stdin="1\n", interactive=True)
    assert code == 1


def test_new_network_error_type_names_host(fakes):
    class NetworkError(VideoError):  # shape of T1's reelay.video.errors.NetworkError
        user_message = "I couldn't reach cdn.example.net from this network."
    fakes.load_error = NetworkError("dns")
    _, out = run(fakes, "https://example.com/v", interactive=False)
    assert "cdn.example.net" in out and "Something went wrong" not in out


def test_bidi_overrides_stripped(fakes):
    fakes.fact_check = lambda b, p: Verdict("a\u202eb\u2066c", [], [])
    _, out = run(fakes, "https://example.com/v", interactive=False)
    assert "\u202e" not in out and "\u2066" not in out


def test_bad_or_empty_choice_stops(fakes):
    _, out = run(fakes, "https://example.com/v", stdin="9\n", interactive=True)
    assert not fakes.decisions and "isn't one of the choices" in out
    run(fakes, "https://example.com/v", stdin="\n", interactive=True)
    assert not fakes.decisions


def test_no_input_lists_and_does_not_read(fakes):
    class Boom(io.StringIO):
        def readline(self, *a):
            raise AssertionError("must not read stdin")
    out = io.StringIO()
    run_try("https://example.com/v", deps=fakes.deps(), stdin=Boom("1\n"), stdout=out, no_input=True, interactive=True)
    assert "1) Adopt  2) Skip" in out.getvalue() and not fakes.decisions


def test_non_tty_stdin_does_not_wait(fakes):
    _, out = run(fakes, "https://example.com/v", stdin="1\n")  # StringIO is not a TTY
    assert not fakes.decisions and "choices not answered" in out


# ---- safety and errors

def test_control_characters_stripped(fakes):
    fakes.fact_check = lambda b, p: Verdict("hi\x1b[2Jthere", [], [])
    _, out = run(fakes, "https://example.com/v", interactive=False)
    assert "\x1b" not in out


def test_network_error_names_host_no_traceback(fakes):
    fakes.load_error = socket.gaierror("Name or service not known")
    _, out = run(fakes, "https://blocked.example.org/v", interactive=False)
    assert "blocked.example.org" in out and "Traceback" not in out
    assert "Something went wrong" not in out


def test_cert_error_message_has_no_bypass_advice():
    msg = explain(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"), "cdn.example.com")
    assert "cdn.example.com" in msg and "certificate" in msg
    for bad in ("verify=False", "disable", "insecure", "proxy", "VPN"):
        assert bad.lower() not in msg.lower()


def test_other_errors_use_generic_message(fakes):
    fakes.load_error = ValueError("boom")
    _, out = run(fakes, "https://example.com/v", interactive=False)
    assert "Something went wrong" in out


def test_main_usage_and_flags(fakes, capsys):
    assert main([]) == 1
    assert "Usage" in capsys.readouterr().out
    assert main(["--debug"]) == 1  # a flag alone is not an argument


def test_non_video_file_is_refused(fakes, tmp_path):
    doc = tmp_path / "README.md"
    doc.write_text("hi")
    assert classify(str(doc))[0] == "not_video"
    code, out = run(fakes, str(doc))
    assert code == 1 and "isn't a video file" in out and not fakes.calls["video"] and not fakes.calls["route"]


def test_no_traceback_on_stderr_unless_debug(fakes, capsys):
    fakes.load_error = socket.gaierror("Name or service not known")
    run(fakes, "https://blocked.example.org/v", interactive=False)
    assert "Traceback" not in capsys.readouterr().err
    run(fakes, "https://blocked.example.org/v", interactive=False, debug=True)
    assert "Traceback" in capsys.readouterr().err


def test_t22_long_instruction_is_not_a_file():
    assert classify("write a story " * 30)[0] == "instruction"
