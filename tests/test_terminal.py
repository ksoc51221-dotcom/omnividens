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
    assert classify(str(clip)) == ("file", str(clip.resolve()))
    assert classify("https://example.com/v") == ("link", "https://example.com/v")
    assert classify("summarise the README, use opus")[0] == "instruction"
    assert classify("https://example.com/v with words")[0] == "instruction"


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
