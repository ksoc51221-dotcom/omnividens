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


# --- safety checks, argv, settings (T5 review blockers) ---
import os

from reelay.sandbox import runner, safety


def test_symlink_candidate_is_high_and_not_run(tmp_path):
    def fetch_with_link(url, dest):
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("# x")
        os.symlink("/etc", dest / "notes")

    r = run_trial(CAND, lambda *a: pytest.fail("no run"), fetch_with_link, scanner_returning("clean"), root=tmp_path)
    assert r.scan_verdict == "high" and not r.ran and "symlink" in r.logs


def test_hooks_in_frontmatter_or_settings_refused(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: x\nhooks:\n  PreToolUse: []\n---\nbody")
    assert "hooks" in safety.find_problem(tmp_path)
    (tmp_path / "SKILL.md").write_text("---\nname: x\n---\nbody mentions hooks: in prose")
    assert safety.find_problem(tmp_path) is None
    (tmp_path / "settings.json").write_text('{"hooks": {}}')
    assert "hooks" in safety.find_problem(tmp_path)


def test_too_many_files_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "MAX_FILES", 3)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    assert "too large" in safety.find_problem(tmp_path)


def test_clone_size_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "is_private_host", lambda u: False)
    monkeypatch.setattr(fetch, "MAX_CLONE_BYTES", 10)

    def fake_run(cmd, **kw):
        d = tmp_path / "x"
        d.mkdir()
        (d / "big").write_bytes(b"0" * 100)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    with pytest.raises(fetch.FetchError):
        fetch.clone("https://github.com/a/b", tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_run_folder_is_private_and_outside_reelay_home(tmp_path):
    seen = {}

    def runner_fn(repo, run_dir, timeout):
        seen["mode"] = run_dir.stat().st_mode & 0o777
        return RunResult(True, 1, 1, "")

    run_trial(CAND, runner_fn, fake_fetch, scanner_returning("clean"), root=tmp_path)
    assert seen["mode"] == 0o700


def test_settings_are_path_scoped_and_hooks_disabled(tmp_path):
    s = runner.build_settings(tmp_path)
    assert s["disableAllHooks"] is True and s["permissions"]["defaultMode"] == "dontAsk"
    allow = s["permissions"]["allow"]
    assert not {"Read", "Write", "Edit"} & set(allow)  # no bare blanket rules
    assert all(a == "Skill" or a.startswith(("Read(//", "Edit(//", "Write(//")) for a in allow)
    assert any(".ssh" in d for d in s["permissions"]["deny"])


def test_argv_has_no_bash_or_blanket_allow():
    argv = runner.build_argv("claude", runner.build_settings(__import__("pathlib").Path("/tmp/w")))
    assert "--allowedTools" not in argv and "--bare" in argv
    tools = argv[argv.index("--tools") + 1].split(",")
    assert tools == ["Read", "Write", "Edit", "Glob", "Grep", "Skill"]


def test_no_api_key_means_not_run(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    monkeypatch.setattr(runner.shutil, "which", lambda n: "/bin/claude")
    res = runner.claude_runner(tmp_path, tmp_path, 5)
    assert not res.ok and "API key" in res.output


def test_no_api_key_skips_runner_and_reports_scan_only(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    monkeypatch.setattr(runner, "claude_runner", lambda *a: pytest.fail("must not run"))
    from reelay.sandbox import trial
    monkeypatch.setattr(trial, "claude_runner", runner.claude_runner)
    r = trial.run_trial(CAND, None, fake_fetch, scanner_returning("review"), root=tmp_path)
    assert not r.ran and r.scan_verdict == "review"
    assert r.fit.startswith("Scan-only") and "note" in r.logs


def test_trial_model_default_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    assert runner.trial_model() == "claude-haiku-5-5"
    (tmp_path / "config.toml").write_text('trial_model = "claude-sonnet-5-5"\n')
    assert runner.trial_model() == "claude-sonnet-5-5"
    argv = runner.build_argv("claude", {}, runner.trial_model())
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
