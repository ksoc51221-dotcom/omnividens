"""Tests for the sandbox. No network, no real Claude: fake fetcher/scanner/runner."""

import json
import subprocess

import pytest

from reelay.core.types import Candidate, TrialReport
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


@pytest.mark.parametrize("body", [
    "---\nname: x\n---\nContext: !`git log`\n",
    "---\nname: x\n---\n!`touch canary`\n",
    "no frontmatter\n  ```!\nls\n```\n",
])
def test_dynamic_shell_line_refused(tmp_path, body):
    (tmp_path / "SKILL.md").write_text(body)
    assert "shell commands" in safety.find_problem(tmp_path)


@pytest.mark.parametrize("fm", [
    "allowed-tools: Bash",
    "allowed-tools: Read, bash(git:*)",
    "ALLOWED-TOOLS: [Read, WebFetch]",
    "allowed-tools:\n  - Read\n  - WebSearch",
    "allowed-tools: mcp__server__tool",
    "allowed-tools: \"Read BASH\"",
])
def test_risky_allowed_tools_refused(tmp_path, fm):
    (tmp_path / "SKILL.md").write_text(f"---\nname: x\n{fm}\n---\nbody")
    assert "tools" in safety.find_problem(tmp_path)


@pytest.mark.parametrize("raw", [
    "---\nname: x\ndescription: a --- b\nallowed-tools: Bash(*)\n---\nbody",
    "---\nname: x\ndescription: a --- b\nhooks:\n  Stop: []\n---\nbody",
    "\ufeff---\nname: x\nallowed-tools: Bash\n---\nbody",
    "\n\n  ---\nname: x\nallowed-tools: Bash\n---\nbody",
    "---\r\nname: x\r\nallowed-tools: Bash\r\n---\r\nbody",
    "---\nname: x\n\"allowed-tools\": Bash\n---\nbody",
    "---\nname: x\n'hooks': {}\n---\nbody",
    "---\nname: x\nallowed-tools:\n\n  - Read\n\n  - Bash\n---\nbody",
    "name: x\nallowed-tools: Bash\nno frontmatter at all",
    "---\nname: x\nallowed-tools: Bash\nnever closed",
    "intro\n~~~!\nls\n~~~\n",
])
def test_frontmatter_bypasses_refused(tmp_path, raw):
    (tmp_path / "SKILL.md").write_bytes(raw.encode())
    assert safety.find_problem(tmp_path) is not None


def test_benign_skill_passes(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\nallowed-tools: Read, Grep\ndescription: uses bash-like words\n---\n"
        "Say hello! Use `code` freely. Mention Bash in prose.\n")
    assert safety.find_problem(tmp_path) is None


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


def test_nothing_available_reports_scan_only(monkeypatch, tmp_path):
    r = run_trial(CAND, None, fake_fetch, scanner_returning("review"), root=tmp_path,
                  probe=lambda: NONE, launch=lambda *a: pytest.fail("must not run"))
    assert not r.ran and r.scan_verdict == "review"
    assert r.fit.startswith("Scan-only") and "note" in r.logs


def test_trial_model_default_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    assert runner.trial_model() == "claude-haiku-5-5"
    (tmp_path / "config.toml").write_text('trial_model = "claude-sonnet-5-5"\n')
    assert runner.trial_model() == "claude-sonnet-5-5"
    argv = runner.build_argv("claude", {}, runner.trial_model())
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"


@pytest.mark.parametrize("bad", ["--dangerously-skip-permissions", "a b", "x;rm", "-m", ""])
def test_invalid_trial_model_falls_back(monkeypatch, tmp_path, bad):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(f'trial_model = "{bad}"\n')
    assert runner.trial_model() == "claude-haiku-5-5"


# ---------------------------------------------------------------- T15: backends and routing
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.error import URLError

from reelay.sandbox import routing

SECRET = "sk-ant-oat01-SECRETSECRETSECRET"
NONE = {"subscription": False, "api": False, "local": False}
ALL = {"subscription": True, "api": True, "local": True}


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path_factory):
    h = tmp_path_factory.mktemp("reelay_home")
    monkeypatch.setenv("REELAY_HOME", str(h))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return h


def light_repo(path: Path, lines: int = 20) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("---\nname: demo\n---\n" + "step\n" * lines)
    return path


def heavy_repo(path: Path) -> Path:
    light_repo(path)
    (path / "tool.py").write_text("print('hi')\n")
    return path


def make_fetch(builder):
    return lambda url, dest: builder(dest)


class Launcher:
    """Fake `launch`: records calls and returns canned results by backend."""

    def __init__(self, ok=None, cost=None):
        self.ok = ok or {}
        self.calls = []
        self.timeouts = []
        self.cost = cost

    def __call__(self, backend, repo, run_dir, timeout_s, model, budget):
        assert run_dir.is_dir() and run_dir.stat().st_mode & 0o777 == 0o700
        self.calls.append((backend, model, budget))
        self.timeouts.append(timeout_s)
        ok = self.ok.get(backend, True)
        return RunResult(ok, 2.0, 100, "worked" if ok else "broke", backend, model, self.cost)


def trial(tmp_path, builder, avail, launcher, config=""):
    if config:
        (Path(__import__("os").environ["REELAY_HOME"]) / "config.toml").write_text(config)
    return run_trial(CAND, None, make_fetch(builder), scanner_returning("clean"), root=tmp_path,
                     probe=lambda: avail, launch=launcher)


@pytest.mark.parametrize("build, why", [
    (lambda p: [light_repo(p), [(p / f"n{i}.md").write_text("x") for i in range(5)]], "files"),
    (lambda p: (light_repo(p), (p / "big.md").write_text("x" * 21 * 1024)), "KB"),
    (heavy_repo, "code"),
    (lambda p: light_repo(p, lines=148), "has 151 lines"),
    (lambda p: (p.mkdir(), (p / "README.md").write_text("x")), "SKILL.md"),
    (lambda p: (light_repo(p), (p / "sub").mkdir(), (p / "sub" / "SKILL.md").write_text("x")), "several"),
])
def test_classify_heavy(tmp_path, build, why):
    build(tmp_path / "r")
    light, reason = routing.classify(tmp_path / "r")
    assert not light and why in reason


def test_classify_light_and_boundaries(tmp_path):
    assert routing.classify(light_repo(tmp_path / "a", lines=147))[0]  # 3 frontmatter lines + 147 = 150
    assert not routing.classify(light_repo(tmp_path / "b", lines=148))[0]


def test_light_goes_local(tmp_path):
    launcher = Launcher()
    r = trial(tmp_path, light_repo, ALL, launcher)
    assert [c[0] for c in launcher.calls] == ["local"] and launcher.calls[0][1] == "qwen3:8b"
    assert r.ran and "local (qwen3:8b)" in r.fit and "light skill" in r.fit and "$" not in r.fit


def test_heavy_prefers_subscription_then_api(tmp_path):
    launcher = Launcher()
    trial(tmp_path, heavy_repo, ALL, launcher)
    r = trial(tmp_path, heavy_repo, {**ALL, "subscription": False}, launcher)
    assert [c[0] for c in launcher.calls] == ["subscription", "api"]
    assert "heavy skill" in r.fit


def test_api_shows_cost_and_passes_budget(tmp_path):
    launcher = Launcher(cost=0.0123)
    r = trial(tmp_path, heavy_repo, {**NONE, "api": True}, launcher, 'max_trial_usd = 0.2\n')
    assert launcher.calls[0] == ("api", "claude-haiku-5-5", 0.2)
    assert "$0.0123" in r.fit and "cap $0.20" in r.fit
    sub = trial(tmp_path, heavy_repo, {**NONE, "subscription": True}, Launcher(cost=0.5))
    assert "$" not in sub.fit  # money is shown for api only


def test_heavy_without_claude_runs_local_with_caveat(tmp_path):
    launcher = Launcher()
    r = trial(tmp_path, heavy_repo, {**NONE, "local": True}, launcher)
    assert launcher.calls[0][0] == "local" and "light model, may understate" in r.fit


def test_light_with_ollama_down_uses_claude(tmp_path):
    launcher = Launcher()
    r = trial(tmp_path, light_repo, {**ALL, "local": False}, launcher)
    assert launcher.calls[0][0] == "subscription" and "Ollama is not usable" in r.fit


def test_nothing_available_is_scan_only_with_reason(tmp_path):
    r = trial(tmp_path, heavy_repo, NONE, Launcher())
    assert not r.ran and r.fit.startswith("Scan-only") and "Ollama" in r.fit


def test_escalation_light_local_failure_retries_once_on_claude(tmp_path):
    launcher = Launcher(ok={"local": False})
    r = trial(tmp_path, light_repo, ALL, launcher)
    assert [c[0] for c in launcher.calls] == ["local", "subscription"]
    assert r.ran and "escalated" in r.fit and r.tokens_used == 200
    assert routing.claude_used_today() == 1


def test_escalation_not_for_heavy_or_without_claude_or_when_both_fail(tmp_path):
    heavy = Launcher(ok={"local": False})
    r = trial(tmp_path, heavy_repo, {**NONE, "local": True}, heavy)
    assert [c[0] for c in heavy.calls] == ["local"] and not r.ran
    no_claude = Launcher(ok={"local": False})
    trial(tmp_path, light_repo, {**NONE, "local": True}, no_claude)
    assert len(no_claude.calls) == 1
    both = Launcher(ok={"local": False, "subscription": False})
    r = trial(tmp_path, light_repo, ALL, both)
    assert len(both.calls) == 2 and not r.ran and "did not run cleanly" in r.fit


def test_escalation_respects_daily_cap(tmp_path, home):
    for _ in range(4):
        routing.record_claude_trial()
    launcher = Launcher(ok={"local": False})
    trial(tmp_path, light_repo, ALL, launcher, "max_claude_trials_per_day = 4\n")
    assert [c[0] for c in launcher.calls] == ["local"]  # cap already reached: no retry


def test_daily_cap_pushes_heavy_to_local_then_scan_only(tmp_path):
    launcher = Launcher()
    for _ in range(5):
        trial(tmp_path, heavy_repo, ALL, launcher)
    assert [c[0] for c in launcher.calls] == ["subscription"] * 5
    r = trial(tmp_path, heavy_repo, ALL, launcher)  # 6th: over the default cap of 5
    assert launcher.calls[-1][0] == "local" and "cap of 5" in r.fit
    r = trial(tmp_path, heavy_repo, {**ALL, "local": False}, launcher)
    assert len(launcher.calls) == 6 and r.fit.startswith("Scan-only") and "cap of 5" in r.fit


def test_forced_backends(tmp_path):
    launcher = Launcher()
    trial(tmp_path, light_repo, ALL, launcher, 'trial_backend = "api"\n')
    assert launcher.calls[0][0] == "api"
    r = trial(tmp_path, light_repo, {**ALL, "subscription": False}, Launcher(), 'trial_backend = "subscription"\n')
    assert not r.ran and "not set up" in r.fit
    assert trial(tmp_path, heavy_repo, ALL, Launcher(), 'trial_backend = "local"\n').ran


def test_invalid_trial_backend_falls_back_to_auto(home):
    (home / "config.toml").write_text('trial_backend = "--evil"\n')
    assert runner.trial_backend() == "auto"


def test_choose_backend_is_pure():
    assert routing.choose_backend(Path("/nonexistent-x"), NONE, 0)[0] == "none"


def test_usage_counter_file(home):
    d = date(2026, 11, 1)
    assert routing.claude_used_today(d) == 0
    assert routing.record_claude_trial(d) == 1 and routing.record_claude_trial(d) == 2
    f = home / "trial_usage.json"
    assert f.stat().st_mode & 0o777 == 0o600
    assert routing.claude_used_today(d + timedelta(days=1)) == 0  # new day resets
    f.write_text("{not json")
    assert routing.claude_used_today(d) == 0  # corrupt file never blocks or crashes


def test_launch_crash_is_reported_not_raised(tmp_path):
    def boom(*a):
        raise RuntimeError("kaboom")
    r = trial(tmp_path, light_repo, ALL, boom)
    assert not r.ran


def test_ollama_down_and_model_missing_detection():
    def down(url, timeout):
        raise URLError("refused")
    assert runner.ollama_status("qwen3:8b", opener=down) == (False, "Ollama is not running")

    class Resp:
        def __init__(self, names):
            self.data = json.dumps({"models": [{"name": n} for n in names]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return self.data

    ok, note = runner.ollama_status("qwen3:8b", opener=lambda u, timeout: Resp(["llama3:8b"]))
    assert not ok and "not installed" in note and "yourself" in note
    assert runner.ollama_status("qwen3:8b", opener=lambda u, timeout: Resp(["qwen3:8b"]))[0]
    assert runner.ollama_status("qwen3", opener=lambda u, timeout: Resp(["qwen3:latest"]))[0]


def test_ollama_url_is_fixed_to_this_mac():
    assert routing.OLLAMA_URL == "http://localhost:11434"


def test_argv_per_backend():
    s = {"disableAllHooks": True}
    api = runner.build_argv("claude", s, "m", "api", 0.05)
    assert "--bare" in api and api[api.index("--max-budget-usd") + 1] == "0.05"
    for backend in ("subscription", "local"):
        argv = runner.build_argv("claude", s, "m", backend, 0.05)
        assert "--bare" not in argv and "--max-budget-usd" not in argv
        assert argv[argv.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in argv
        assert argv[argv.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep,Skill"


class FakeProc:
    def __init__(self, stdout, stderr="", returncode=0):
        self.out, self.err, self.returncode, self.pid = stdout, stderr, returncode, 1

    def communicate(self, timeout=None):
        return self.out, self.err


def run_with_fake_popen(monkeypatch, tmp_path, backend, stdout, stderr="", config=""):
    (Path(__import__("os").environ["REELAY_HOME"]) / "config.toml").write_text(config)
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["env"], seen["cwd"] = argv, kw["env"], kw["cwd"]
        return FakeProc(stdout, stderr)

    monkeypatch.setattr(runner.shutil, "which", lambda n: "/bin/claude")
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    repo = light_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    res = runner.run_backend(backend, repo, run_dir, 5)
    return res, seen, run_dir


def test_subscription_token_only_in_env_never_argv_files_or_output(monkeypatch, tmp_path):
    out = json.dumps({"is_error": False, "result": f"I saw {SECRET} in my env",
                      "usage": {"input_tokens": 5, "output_tokens": 5}})
    res, seen, run_dir = run_with_fake_popen(
        monkeypatch, tmp_path, "subscription", out, stderr=SECRET, config=f'claude_oauth_token = "{SECRET}"\n')
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == SECRET
    assert not any(SECRET in a for a in seen["argv"]) and "--bare" not in seen["argv"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert SECRET not in res.output and "[redacted]" in res.output and res.backend == "subscription"
    for f in run_dir.rglob("*"):
        if f.is_file():
            assert SECRET not in f.read_text(errors="ignore"), f
    assert SECRET not in repr(res)


def test_secret_redacted_when_output_is_not_json(monkeypatch, tmp_path):
    res, _, _ = run_with_fake_popen(monkeypatch, tmp_path, "subscription", f"garbage {SECRET}",
                                    config=f'claude_oauth_token = "{SECRET}"\n')
    assert not res.ok and SECRET not in res.output


def test_subscription_without_token_does_not_run(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.shutil, "which", lambda n: "/bin/claude")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run"))
    res = runner.run_backend("subscription", tmp_path, tmp_path, 5)
    assert not res.ok and "claude_oauth_token" in res.output


@pytest.mark.parametrize("bad", ["short", "has space token1234", "line\\nbreak12345", ""])
def test_malformed_token_is_ignored(home, bad):
    (home / "config.toml").write_text(f'claude_oauth_token = "{bad}"\n')
    assert runner.find_oauth_token() is None


def test_local_env_points_at_ollama_and_has_no_secrets(monkeypatch, tmp_path):
    out = json.dumps({"is_error": False, "result": "ok", "total_cost_usd": 0.02, "usage": {"output_tokens": 3}})
    res, seen, _ = run_with_fake_popen(monkeypatch, tmp_path, "local", out,
                                       config=f'claude_oauth_token = "{SECRET}"\nanthropic_api_key = "k-12345678"\n')
    env = seen["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434" and env["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert seen["argv"][seen["argv"].index("--model") + 1] == "qwen3:8b"
    assert res.cost_usd is None and res.backend == "local"  # Claude's made-up local "cost" is dropped
    assert all(env[k] != SECRET for k in env)


def test_local_model_from_config_and_validated(home):
    (home / "config.toml").write_text('local_model = "llama3.2:3b"\n')
    assert runner.local_model() == "llama3.2:3b"
    (home / "config.toml").write_text('local_model = "--x"\n')
    assert runner.local_model() == "qwen3:8b"


def test_api_run_reports_cost_and_budget_flag(monkeypatch, tmp_path):
    out = json.dumps({"is_error": False, "result": "ok", "total_cost_usd": 0.0123, "usage": {"output_tokens": 3}})
    res, seen, _ = run_with_fake_popen(monkeypatch, tmp_path, "api", out, config='anthropic_api_key = "k-12345678"\n')
    assert seen["env"]["ANTHROPIC_API_KEY"] == "k-12345678" and "--bare" in seen["argv"]
    assert res.cost_usd == 0.0123
    (tmp_path / "run2").mkdir()
    runner.claude_runner(tmp_path / "repo", tmp_path / "run2", 5)  # the plain-Runner entry point
    assert seen["argv"][seen["argv"].index("--max-budget-usd") + 1] == "0.05"  # default max_trial_usd


def test_report_text_never_contains_token(tmp_path):
    (Path(__import__("os").environ["REELAY_HOME"]) / "config.toml").write_text(f'claude_oauth_token = "{SECRET}"\n')
    r = run_trial(CAND, None, make_fetch(heavy_repo), scanner_returning("clean"), root=tmp_path,
                  probe=lambda: ALL, launch=Launcher())
    assert SECRET not in r.fit and SECRET not in r.logs


def test_local_gets_a_longer_timeout_claude_keeps_the_callers(tmp_path):
    local, claude = Launcher(), Launcher()
    trial(tmp_path, light_repo, ALL, local)
    trial(tmp_path, heavy_repo, ALL, claude)
    assert local.timeouts == [routing.LOCAL_MIN_TIMEOUT_S] and claude.timeouts == [180]


# ---------------------------------------------------------------- T15 review fixes
@pytest.mark.parametrize("inside_cut", [3, 10, 15])
def test_secret_straddling_the_500_char_cut_is_redacted(monkeypatch, tmp_path, inside_cut):
    # the last 500 chars of the raw text start `inside_cut` characters into the token
    noisy = "x" * 40 + SECRET + "y" * (500 - len(SECRET) + inside_cut)
    assert noisy[-500:].startswith(SECRET[inside_cut:]) and SECRET not in noisy[-500:]
    res, _, _ = run_with_fake_popen(monkeypatch, tmp_path, "subscription", noisy,
                                    config=f'claude_oauth_token = "{SECRET}"\n')
    assert SECRET[inside_cut:] not in res.output and "[redacted]" in res.output


def test_crash_text_is_redacted(tmp_path):
    (Path(__import__("os").environ["REELAY_HOME"]) / "config.toml").write_text(f'claude_oauth_token = "{SECRET}"\n')

    def boom(*a):
        raise RuntimeError(f"failed with {SECRET}")
    r = trial(tmp_path, heavy_repo, ALL, boom)
    assert SECRET not in r.logs and SECRET not in r.fit and "[redacted]" in r.logs


def test_allow_local_false_sends_light_to_claude_else_scan_only(tmp_path):
    launcher = Launcher()
    run = lambda avail, builder=light_repo: run_trial(
        CAND, None, make_fetch(builder), scanner_returning("clean"), root=tmp_path,
        probe=lambda: avail, launch=launcher, allow_local=False)
    assert run(ALL).ran and launcher.calls[-1][0] == "subscription"
    r = run({**NONE, "local": True})
    assert not r.ran and r.fit.startswith("Scan-only") and "already had its one local trial" in r.fit
    assert len(launcher.calls) == 1
    run({**NONE, "local": True}, heavy_repo)  # heavy + no Claude + local blocked: same
    assert len(launcher.calls) == 1


def test_heads_up_before_local_run_only(tmp_path):
    said = []
    kw = dict(root=tmp_path, probe=lambda: ALL, notify=said.append)
    run_trial(CAND, None, make_fetch(heavy_repo), scanner_returning("clean"), launch=Launcher(), **kw)
    assert said == []  # Claude run: no heads-up
    seen_before_launch = []

    def launch(*a):
        seen_before_launch.append(list(said))
        return RunResult(True, 1, 1, "ok", "local", "qwen3:8b")
    run_trial(CAND, None, make_fetch(light_repo), scanner_returning("clean"), launch=launch, **kw)
    assert len(said) == 1 and "~5–10 min" in said[0] and seen_before_launch == [said[:1]]

    def bad_notify(text):
        raise RuntimeError("telegram down")
    r = run_trial(CAND, None, make_fetch(light_repo), scanner_returning("clean"), launch=Launcher(),
                  root=tmp_path, probe=lambda: ALL, notify=bad_notify)
    assert r.ran  # a failed heads-up never stops the trial


def test_pipeline_runs_at_most_one_local_trial_per_video(tmp_path):
    import threading
    from types import SimpleNamespace

    from reelay.core import pipeline
    from reelay.core.types import Profile, Verdict

    seen, sent = [], []

    def real_like(candidate, allow_local=True, notify=None):
        seen.append(allow_local)
        return TrialReport(candidate, True, True, "clean", 1, 1.0, "x", local_ran=allow_local)

    stub = SimpleNamespace(deps=SimpleNamespace(run_trial=real_like), send=lambda *a: sent.append(a),
                           _say=lambda uid, text, profile: sent.append(text), _lock=threading.Lock(),
                           _reports=SimpleNamespace(put=lambda *a: None))
    stub._run_trial = lambda *a, **k: pipeline.Pipeline._run_trial(stub, *a, **k)
    cands = [Candidate(f"c{i}", "skill", f"https://github.com/o/r{i}") for i in range(3)]
    pipeline.Pipeline._trials(stub, "u", Verdict("s", [], cands), Profile(user_id="u"))
    assert seen == [True, False, False]

    plain = []  # a run_trial without the new parameters is still called the old way
    stub.deps.run_trial = lambda c: plain.append(c.name) or TrialReport(c, True, False, "clean", 0, 0.0, "x")
    pipeline.Pipeline._trials(stub, "u", Verdict("s", [], cands[:1]), Profile(user_id="u"))
    assert plain == ["c0"]


def test_local_ran_is_set_for_local_and_escalated_trials_only(tmp_path):
    plain = trial(tmp_path, light_repo, ALL, Launcher())
    escalated = trial(tmp_path, light_repo, ALL, Launcher(ok={"local": False}))
    claude = trial(tmp_path, heavy_repo, ALL, Launcher())
    scan_only = trial(tmp_path, heavy_repo, NONE, Launcher())
    assert plain.local_ran and escalated.local_ran and "escalated" in escalated.fit
    assert not claude.local_ran and not scan_only.local_ran


def test_pipeline_counts_an_escalated_local_trial_as_local_used():
    import threading
    from types import SimpleNamespace

    from reelay.core import pipeline
    from reelay.core.types import Profile, Verdict

    seen = []

    def run(candidate, allow_local=True, notify=None):
        seen.append(allow_local)  # the report says "subscription", but a local attempt ran first
        return TrialReport(candidate, True, True, "clean", 1, 1.0,
                           "Ran in isolation on subscription (m) — escalated from local", local_ran=True)

    stub = SimpleNamespace(deps=SimpleNamespace(run_trial=run), send=lambda *a: None,
                           _say=lambda *a: None, _lock=threading.Lock(), _reports=SimpleNamespace(put=lambda *a: None))
    stub._run_trial = lambda *a, **k: pipeline.Pipeline._run_trial(stub, *a, **k)
    cands = [Candidate(f"c{i}", "skill", f"https://github.com/o/r{i}") for i in range(3)]
    pipeline.Pipeline._trials(stub, "u", Verdict("s", [], cands), Profile(user_id="u"))
    assert seen == [True, False, False]


def test_terminal_guard_keeps_run_trial_signature_so_limits_reach_it():
    import inspect

    from reelay.terminal import runner as term_runner

    got = {}

    def real_like(candidate, allow_local=True, notify=None):
        got["args"] = (allow_local, notify)
        return TrialReport(candidate, True, False, "clean", 0, 0.0, "x")

    term = term_runner._Terminal.__new__(term_runner._Terminal)
    term.failed, term.debug, term.host, term.net_message = False, False, "h", ""
    guarded = term.guard(real_like)
    assert "allow_local" in inspect.signature(guarded).parameters
    from reelay.core import pipeline
    from types import SimpleNamespace
    stub = SimpleNamespace(deps=SimpleNamespace(run_trial=guarded), _say=lambda *a: None)
    pipeline.Pipeline._run_trial(stub, "u", CAND, None, allow_local=False)
    assert got["args"][0] is False and callable(got["args"][1])


def test_local_prompt_is_a_parameter():
    argv = runner.build_argv("claude", {}, "m", "local", prompt="hello")
    assert argv[argv.index("-p") + 1] == "hello"
