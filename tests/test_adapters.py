"""Adapter tests: a fake runner stands in for `claude`; nothing real is run."""

import json
import subprocess
from pathlib import Path

from reelay.adapters import Adapter, available_adapters
from reelay.adapters.base import check_project, subprocess_runner
from reelay.core.config import ConfigError
from reelay.adapters.claude_code import ClaudeCodeAdapter
from reelay.core.types import Instruction


def ok_proc(result="done", rc=0, is_error=False):
    out = json.dumps({"result": result, "is_error": is_error,
                      "usage": {"input_tokens": 10, "output_tokens": 5,
                                "cache_read_input_tokens": 100}})
    return subprocess.CompletedProcess([], rc, out, "")


class FakeRunner:
    def __init__(self, proc=None, exc=None):
        self.proc, self.exc, self.calls = proc or ok_proc(), exc, []

    def __call__(self, argv, cwd, stdin_text, timeout):
        self.calls.append((argv, cwd, stdin_text, timeout))
        if self.exc:
            raise self.exc
        return self.proc


def make(tmp_path, runner=None, **cfg):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    config = {"allowed_projects": [str(tmp_path)], **cfg}
    return ClaudeCodeAdapter(runner or FakeRunner(), config, binary="claude"), proj


def test_happy_path_flags_and_result(tmp_path):
    runner = FakeRunner()
    a, proj = make(tmp_path, runner)
    r = a.run(Instruction("summarise README", skill="pdf", model="opus", effort="high", project=proj))
    argv, cwd, stdin, timeout = runner.calls[0]
    assert r.ok and r.text == "done" and r.tokens == 115
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--dangerously-skip-permissions" not in argv
    assert stdin == "/pdf summarise README" and "summarise README" not in argv
    assert cwd == proj.resolve() and timeout == 600


def test_project_outside_allowlist_refused(tmp_path):
    a, _ = make(tmp_path)
    for bad in (Path("/"), Path.home() / ".ssh", tmp_path / ".." ):
        r = a.run(Instruction("hi there", project=bad))
        assert not r.ok and "refused" in r.error


def test_symlink_escape_refused(tmp_path):
    allowed = tmp_path / "ok"
    allowed.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (allowed / "link").symlink_to(outside)
    cfg = {"allowed_projects": [str(allowed)]}
    try:
        check_project(allowed / "link", cfg)
        assert False, "should refuse"
    except ValueError:
        pass


def test_no_allowlist_or_no_project_refused(tmp_path):
    runner = FakeRunner()
    a = ClaudeCodeAdapter(runner, {}, binary="claude")
    assert not a.run(Instruction("hi there", project=tmp_path)).ok
    a2, _ = make(tmp_path, runner)
    assert not a2.run(Instruction("hi there", project=None)).ok
    assert runner.calls == []


def test_bypass_mode_in_config_refused(tmp_path):
    runner = FakeRunner()
    a, proj = make(tmp_path, runner, permission_mode="bypassPermissions")
    r = a.run(Instruction("hi there", project=proj))
    assert not r.ok and runner.calls == []


def test_injection_attempts_refused(tmp_path):
    runner = FakeRunner()
    a, proj = make(tmp_path, runner)
    for ins in (Instruction("x y", skill="--dangerously-skip-permissions", project=proj),
                Instruction("x y", model="--add-dir", project=proj),
                Instruction("x y", effort="turbo", project=proj),
                Instruction("/login now", project=proj),
                Instruction("   ", project=proj)):
        assert not a.run(ins).ok
    assert runner.calls == []


def test_dash_task_goes_via_stdin(tmp_path):
    runner = FakeRunner()
    a, proj = make(tmp_path, runner)
    a.run(Instruction("--help me out", project=proj))
    assert runner.calls[0][2] == "--help me out" and "--help me out" not in runner.calls[0][0]


def test_timeout_and_config_timeout(tmp_path):
    runner = FakeRunner(exc=subprocess.TimeoutExpired("claude", 5))
    a, proj = make(tmp_path, runner, timeout_s=5)
    r = a.run(Instruction("hi there", project=proj))
    assert not r.ok and "timed out" in r.error and runner.calls[0][3] == 5


def test_failures_reported_not_raised(tmp_path):
    proj_a = make(tmp_path, FakeRunner(proc=ok_proc("boom", rc=1, is_error=True)))
    r = proj_a[0].run(Instruction("hi there", project=proj_a[1]))
    assert not r.ok and r.error == "boom"
    bad = subprocess.CompletedProcess([], 1, "not json", "crash")
    b = make(tmp_path, FakeRunner(proc=bad))
    assert b[0].run(Instruction("hi there", project=b[1])).error == "crash"
    c = make(tmp_path, FakeRunner(exc=FileNotFoundError("claude")))
    assert not c[0].run(Instruction("hi there", project=c[1])).ok


def test_broken_shared_config_gives_clean_error(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("telegram_bot_token = 123:abc\n")  # invalid TOML
    runner = FakeRunner()
    r = ClaudeCodeAdapter(runner, None, binary="claude").run(Instruction("hi there", project=tmp_path))
    assert not r.ok and "refused" in r.error and runner.calls == []


def test_shared_config_with_token_and_allowlist_works(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    proj = tmp_path / "p"
    proj.mkdir()
    (tmp_path / "config.toml").write_text(
        f'telegram_bot_token = "123:abc"\nallowed_projects = ["{tmp_path}"]\n')
    r = ClaudeCodeAdapter(FakeRunner(), None, binary="claude").run(Instruction("hi there", project=proj))
    assert r.ok


def test_parse_never_raises(tmp_path):
    for out in ("[]", '"x"', "null", "5"):
        a, proj = make(tmp_path, FakeRunner(proc=subprocess.CompletedProcess([], 0, out, "")))
        assert not a.run(Instruction("hi there", project=proj)).ok
    odd = subprocess.CompletedProcess([], 0, json.dumps({"result": "ok", "usage": {"input_tokens": "lots"}}), "")
    a, proj = make(tmp_path, FakeRunner(proc=odd))
    r = a.run(Instruction("hi there", project=proj))
    assert r.ok and r.tokens == 0
    odd2 = subprocess.CompletedProcess([], 0, json.dumps({"result": "ok", "usage": 7}), "")
    a, proj = make(tmp_path, FakeRunner(proc=odd2))
    assert a.run(Instruction("hi there", project=proj)).ok


def test_too_wide_allowlist_rejected(tmp_path):
    for bad in ("/", "~"):
        try:
            check_project(tmp_path, {"allowed_projects": [bad]})
            assert False, "should refuse"
        except ValueError:
            pass


def test_budget_flag_from_config(tmp_path):
    runner = FakeRunner()
    a, proj = make(tmp_path, runner, max_budget_usd=0.5)
    a.run(Instruction("hi there", project=proj))
    argv = runner.calls[0][0]
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5"


def test_timeout_kills_process_group(tmp_path):
    marker = tmp_path / "grandchild_alive"
    # child shell starts a background grandchild that would write the marker after 2s
    argv = ["sh", "-c", f"(sleep 2; touch {marker}) & sleep 30"]
    try:
        subprocess_runner(argv, tmp_path, "", 1)
        assert False, "should time out"
    except subprocess.TimeoutExpired:
        pass
    import time
    time.sleep(2.5)
    assert not marker.exists()


def test_registry_lists_only_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert available_adapters() == []
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    found = available_adapters()
    assert [a.name for a in found] == ["claude-code"] and isinstance(found[0], Adapter)
