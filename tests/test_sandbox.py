"""Tests for the sandbox. No network, no real Claude: fake fetcher/scanner/runner."""

import json
import subprocess

import pytest

from reelay.core.types import Candidate
from reelay.sandbox import fetch, scan, run_trial
from reelay.sandbox.runner import RunResult, parse_cli_json

CAND = Candidate("demo", "skill", "https://github.com/example/demo-skill")


def fake_fetch(url, dest):
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("# demo")


def scanner_returning(verdict):
    return lambda path: (verdict, "note")


def test_clean_candidate_runs_and_measures(tmp_path):
    calls = []

    def runner(repo, run_dir, timeout):
        calls.append((repo, timeout))
        return RunResult(True, 4.2, 1234, "it worked")

    r = run_trial(CAND, runner, fake_fetch, scanner_returning("clean"), timeout_s=30, root=tmp_path)
    assert r.ran and r.installed and r.scan_verdict == "clean"
    assert (r.tokens_used, r.seconds) == (1234, 4.2)
    assert "it worked" in r.logs and calls[0][1] == 30


def test_high_verdict_is_never_run(tmp_path):
    def runner(*a):
        raise AssertionError("must not run a high-risk candidate")

    r = run_trial(CAND, runner, fake_fetch, scanner_returning("high"), root=tmp_path)
    assert r.scan_verdict == "high" and not r.ran


def test_review_verdict_still_runs(tmp_path):
    r = run_trial(CAND, lambda *a: RunResult(True, 1.0, 5, "ok"), fake_fetch,
                  scanner_returning("review"), root=tmp_path)
    assert r.ran and r.scan_verdict == "review"


def test_failed_run_and_crashing_runner(tmp_path):
    r = run_trial(CAND, lambda *a: RunResult(False, 2.0, 0, "timed out"), fake_fetch,
                  scanner_returning("clean"), root=tmp_path)
    assert not r.ran and r.installed

    def boom(*a):
        raise RuntimeError("x")

    r = run_trial(CAND, boom, fake_fetch, scanner_returning("clean"), root=tmp_path)
    assert not r.ran and "RuntimeError" in r.logs


def test_fetch_failure_reports_without_running(tmp_path):
    def bad_fetch(url, dest):
        raise fetch.FetchError("nope")

    r = run_trial(CAND, lambda *a: pytest.fail("no run"), bad_fetch, scanner_returning("clean"), root=tmp_path)
    assert not r.installed and not r.ran and r.scan_verdict == "review"


def test_run_folder_is_cleaned_up_unless_kept(tmp_path):
    run_trial(CAND, lambda *a: RunResult(True, 1, 1, ""), fake_fetch, scanner_returning("clean"), root=tmp_path)
    assert list(tmp_path.iterdir()) == []
    run_trial(CAND, lambda *a: RunResult(True, 1, 1, ""), fake_fetch, scanner_returning("clean"),
              root=tmp_path, keep=True)
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("url", [
    "http://github.com/a/b", "git://github.com/a/b", "ssh://git@github.com/a/b",
    "file:///etc/passwd", "https://localhost/a/b", "https://127.0.0.1/a/b",
    "https://169.254.169.254/x", "https://user:pw@github.com/a/b", "--upload-pack=x",
])
def test_bad_urls_rejected(url):
    with pytest.raises(fetch.FetchError):
        fetch.check_url(url)


def test_good_url_accepted(monkeypatch):
    monkeypatch.setattr(fetch, "is_private_host", lambda u: False)
    fetch.check_url("https://github.com/a/b")


def test_clone_uses_safe_git_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "is_private_host", lambda u: False)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    fetch.clone("https://github.com/a/b", tmp_path / "x")
    assert "--depth" in seen["cmd"] and "protocol.allow=never" in seen["cmd"]
    assert "--" in seen["cmd"] and "HOME" not in seen["env"]


def test_verdict_mapping():
    assert scan.map_verdict([{"verdict": "CLEAN"}, {"verdict": "LOW"}]) == "clean"
    assert scan.map_verdict([{"verdict": "CLEAN"}, {"verdict": "REVIEW"}]) == "review"
    assert scan.map_verdict([{"verdict": "REVIEW"}, {"verdict": "HIGH"}]) == "high"
    assert scan.map_verdict([{"verdict": "???"}]) == "review"


def test_scan_calls_script_with_isolated_python(monkeypatch, tmp_path):
    script = tmp_path / "scan.py"
    script.write_text("")
    monkeypatch.setattr(scan, "SCAN_SCRIPT", script)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps([{"verdict": "HIGH", "findings": [1]}]), "")

    monkeypatch.setattr(scan.subprocess, "run", fake_run)
    assert scan.scan(tmp_path)[0] == "high"
    assert seen["cmd"][1] == "-I" and str(script) in seen["cmd"]


def test_scan_failures_never_look_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "SCAN_SCRIPT", tmp_path / "missing.py")
    assert scan.scan(tmp_path)[0] == "review"
    script = tmp_path / "scan.py"
    script.write_text("")
    monkeypatch.setattr(scan, "SCAN_SCRIPT", script)
    monkeypatch.setattr(scan.subprocess, "run",
                        lambda c, **k: subprocess.CompletedProcess(c, 0, "not json", ""))
    assert scan.scan(tmp_path)[0] == "review"


def test_parse_cli_json_sums_real_usage():
    out = json.dumps({"is_error": False, "result": "done", "usage": {
        "input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000}})
    assert parse_cli_json(out) == (True, 1115, "done")
    assert parse_cli_json(json.dumps({"is_error": True, "result": "x"}))[0] is False
