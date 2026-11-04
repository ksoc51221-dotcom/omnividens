"""Installer tests: fake user, temp HOME, no network, no real launchctl, no real Telegram."""

import os
import plistlib
import stat
import subprocess
import tomllib
import urllib.error
from pathlib import Path

import pytest

from reelay.core.config import load_config
from reelay.installer import service
from reelay.installer.checks import Check, tool_checks
from reelay.installer.cli import main as cli_main
from reelay.installer.config_file import dumps, save_config
from reelay.installer.doctor import config_checks, run_doctor
from reelay.installer.telegram import NetworkProblem, TokenRejected, get_bot_username
from reelay.installer.ui import Console
from reelay.installer.wizard import Deps, Wizard

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0"
API_KEY = "sk-ant-api03-SECRET-KEY-VALUE"
USER = "reelay_test_bot"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REELAY_HOME", str(tmp_path / "dot-reelay"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


class FakeUser:
    """Feeds scripted answers and records everything the wizard prints."""

    def __init__(self, answers):
        self.answers, self.out, self.asked = list(answers), [], []

    def read(self, prompt):
        self.asked.append(prompt)
        return self.answers.pop(0)

    def console(self):
        return Console(read=self.read, write=self.out.append, read_secret=self.read)

    @property
    def screen(self):
        return "\n".join(self.out)


def make_deps(home, which_missing=(), username=USER, **over):
    codes = iter(["ABCD2345", "WXYZ6789"])
    ran = []
    d = Deps(
        which=lambda name: None if name in which_missing else f"/fake/bin/{name}",
        run_command=lambda argv: ran.append(argv) or True,
        get_username=lambda token: username,
        check_network=lambda: Check("Can reach Telegram", True, "yes"),
        make_code=lambda: next(codes),
        make_qr=lambda data: "<QR of " + data + ">",
        install_service=lambda: "",
        service_installed=lambda: False,
        paired_count=lambda: 0,
        run_bot=lambda: ran.append("bot"),
        sleep=lambda s: None,
        home=home,
    )
    for k, v in over.items():
        setattr(d, k, v)
    d.ran = ran
    return d


def project(home, name="proj"):
    p = home / name
    p.mkdir()
    return p


CONFIG = lambda home: home / "dot-reelay" / "config.toml"


# ---------- config file ----------

def test_config_is_private_and_round_trips(home):
    path = CONFIG(home)
    data = {"telegram_bot_token": TOKEN, "allowed_projects": ['/a "b"', "/c\\d"], "n": 3, "flag": True}
    save_config(data, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_config(path) == data


def test_config_merge_keeps_other_keys_and_backs_up(home):
    path = CONFIG(home)
    save_config({"groq_api_key": "g", "factcheck_timeout_s": 60}, path)
    backup = save_config({"telegram_bot_token": TOKEN}, path)
    assert load_config(path) == {"groq_api_key": "g", "factcheck_timeout_s": 60, "telegram_bot_token": TOKEN}
    assert backup and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert load_config(backup) == {"groq_api_key": "g", "factcheck_timeout_s": 60}


def test_config_escapes_control_characters_and_unicode():
    value = "line\nbreak\x7f é 🎉"
    assert tomllib.loads(dumps({"k": value})) == {"k": value}


# ---------- Telegram lookup ----------

class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.body


def test_get_bot_username_ok_and_errors():
    ok = lambda url, timeout: FakeResponse(b'{"ok": true, "result": {"username": "my_bot"}}')
    assert get_bot_username(TOKEN, opener=ok) == "my_bot"

    def http(code):
        def opener(url, timeout):
            raise urllib.error.HTTPError(url, code, "x", {}, None)
        return opener

    with pytest.raises(TokenRejected):
        get_bot_username(TOKEN, opener=http(401))
    with pytest.raises(NetworkProblem):
        get_bot_username(TOKEN, opener=http(502))

    def offline(url, timeout):
        raise urllib.error.URLError("no internet")
    with pytest.raises(NetworkProblem):
        get_bot_username(TOKEN, opener=offline)

    junk = lambda url, timeout: FakeResponse(b'["not", "a", "dict"]')
    with pytest.raises(TokenRejected):
        get_bot_username(TOKEN, opener=junk)


def test_telegram_errors_never_contain_the_token():
    def opener(url, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    with pytest.raises(TokenRejected) as info:
        get_bot_username(TOKEN, opener=opener)
    assert TOKEN not in repr(info.value) and info.value.__suppress_context__


# ---------- tool checks ----------

def test_tool_checks_say_exactly_what_to_do():
    checks = {c.name.split()[0]: c for c in tool_checks(lambda n: None)}
    assert not any(c.ok for c in checks.values())
    assert "brew.sh" in checks["ffmpeg"].fix
    assert checks["yt-dlp"].fix == "uv tool install yt-dlp"
    assert "claude.ai/install.sh" in checks["Claude"].fix
    brew = {c.name.split()[0]: c for c in tool_checks(lambda n: "/x/brew" if n == "brew" else None)}
    assert brew["ffmpeg"].fix == "brew install ffmpeg"
    assert all(c.ok for c in tool_checks(lambda n: "/x/" + n))


def test_ffmpeg_needs_ffprobe_too():
    assert not tool_checks(lambda n: None if n == "ffprobe" else "/x/" + n)[0].ok


# ---------- wizard ----------

def happy(folder):
    return [
        "",               # step 2: Enter after reading the BotFather steps
        TOKEN,            # hidden token
        str(folder), "",  # one folder, then finish
        "n",              # no API key
        "n",              # not at login
    ]


def test_happy_path_writes_private_config_and_shows_pairing(home):
    folder = project(home)
    user = FakeUser(happy(folder))
    deps = make_deps(home)
    assert Wizard(user.console(), deps).run() == 0
    path = CONFIG(home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cfg = load_config(path)
    assert cfg["telegram_bot_token"] == TOKEN and cfg["telegram_bot_username"] == USER
    assert cfg["allowed_projects"] == [str(folder.resolve())]
    assert "anthropic_api_key" not in cfg
    assert f"https://t.me/{USER}?start=ABCD2345" in user.screen
    assert f"<QR of https://t.me/{USER}?start=ABCD2345>" in user.screen
    assert "/start ABCD2345" in user.screen
    assert deps.ran == ["bot"]  # no service chosen: runs in the window
    for n in range(1, 7):
        assert f"Step {n} of 6" in user.screen


def test_secrets_never_appear_on_screen_or_in_prompts(home):
    folder = project(home)
    user = FakeUser(["", TOKEN, str(folder), "", "y", API_KEY, "n"])
    assert Wizard(user.console(), make_deps(home)).run() == 0
    assert API_KEY not in user.screen and TOKEN not in user.screen
    assert not any(TOKEN in p or API_KEY in p for p in user.asked)
    assert load_config(CONFIG(home))["anthropic_api_key"] == API_KEY


def test_secrets_use_hidden_input(home):
    folder = project(home)
    shown, hidden = [], []
    answers = iter(["", str(folder), "", "y", "n"])
    io = Console(read=lambda p: shown.append(p) or next(answers),
                 write=lambda t: None,
                 read_secret=lambda p: hidden.append(p) or (TOKEN if len(hidden) == 1 else API_KEY))
    Wizard(io, make_deps(home)).run()
    assert len(hidden) == 2 and all("hidden" in p for p in hidden)
    assert not any(TOKEN in p for p in shown)


def test_api_key_step_warns_about_cost_and_spend_limit(home):
    user = FakeUser(happy(project(home)))
    Wizard(user.console(), make_deps(home)).run()
    assert "costs real money" in user.screen and "spend limit" in user.screen
    assert "free" in user.screen


def test_bad_token_shape_then_rejected_then_good(home):
    deps = make_deps(home)
    calls = []

    def get_username(token):
        calls.append(token)
        if len(calls) == 1:
            raise TokenRejected()
        return USER
    deps.get_username = get_username
    user = FakeUser(["", "not-a-token", TOKEN, TOKEN, "", "n", "n"])
    assert Wizard(user.console(), deps).run() == 0
    assert "doesn't look like a bot token" in user.screen
    assert "isn't valid" in user.screen
    assert len(calls) == 2


def test_three_bad_tokens_stops_cleanly_without_writing(home):
    user = FakeUser(["", "x", "y", "z"])
    assert Wizard(user.console(), make_deps(home)).run() == 1
    assert not CONFIG(home).exists()
    assert "run  reelay setup  again" in user.screen


def test_network_problem_lets_user_type_username(home):
    deps = make_deps(home)

    def offline(token):
        raise NetworkProblem()
    deps.get_username = offline
    user = FakeUser(["", TOKEN, "@my_bot", "", "n", "n"])
    assert Wizard(user.console(), deps).run() == 0
    assert load_config(CONFIG(home))["telegram_bot_username"] == "my_bot"
    assert "t.me/my_bot?start=" in user.screen


def test_folder_rules(home):
    good = project(home, "good")
    user = FakeUser(["", TOKEN, "/no/such/folder", str(home), "/", str(good), str(good), "", "n", "n"])
    Wizard(user.console(), make_deps(home)).run()
    assert user.screen.count("doesn't exist") == 1
    assert user.screen.count("too wide") == 2
    assert load_config(CONFIG(home))["allowed_projects"] == [str(good.resolve())]


def test_no_folders_is_allowed_with_explanation(home):
    user = FakeUser(["", TOKEN, "", "n", "n"])
    assert Wizard(user.console(), make_deps(home)).run() == 0
    assert load_config(CONFIG(home))["allowed_projects"] == []
    assert "won't change any project" in user.screen


def test_bad_api_key_is_refused_then_skipped(home):
    user = FakeUser(["", TOKEN, "", "y", "hunter2", "", "n"])
    Wizard(user.console(), make_deps(home)).run()
    assert "anthropic_api_key" not in load_config(CONFIG(home))
    assert "hunter2" not in user.screen


def test_missing_tools_explain_and_never_run_remote_scripts(home):
    deps = make_deps(home, which_missing={"ffmpeg", "ffprobe", "yt-dlp", "claude", "brew", "uv"})
    user = FakeUser(["", TOKEN, "", "n", "n"])
    Wizard(user.console(), deps).run()
    assert "brew.sh" in user.screen and "claude.ai/install.sh" in user.screen
    assert "uv tool install yt-dlp" in user.screen
    assert deps.ran == ["bot"]  # nothing was installed behind the user's back


def test_offers_brew_install_and_runs_only_on_yes(home):
    deps = make_deps(home, which_missing={"ffmpeg", "ffprobe"})
    Wizard(FakeUser(["y", "", TOKEN, "", "n", "n"]).console(), deps).run()
    assert deps.ran[0] == ["brew", "install", "ffmpeg"]

    deps = make_deps(home, which_missing={"ffmpeg", "ffprobe"})
    Wizard(FakeUser(["n", "", TOKEN, "", "n", "n"]).console(), deps).run()
    assert ["brew", "install", "ffmpeg"] not in deps.ran


def test_service_only_with_explicit_yes_then_waits_for_pairing(home):
    installed = []
    counts = iter([0, 0, 1])
    deps = make_deps(home, install_service=lambda: installed.append(1) or "", paired_count=lambda: next(counts))
    user = FakeUser(["", TOKEN, "", "n", "y"])
    assert Wizard(user.console(), deps).run() == 0
    assert installed == [1] and deps.ran == []  # service, so no foreground bot
    assert "Paired!" in user.screen and "reelay service remove" in user.screen

    installed.clear()  # Enter = the default = no
    Wizard(FakeUser(["", TOKEN, "", "n", ""]).console(),
           make_deps(home, install_service=lambda: installed.append(1) or "")).run()
    assert installed == []


def test_service_failure_falls_back_to_window(home):
    deps = make_deps(home, install_service=lambda: "macOS refused to start the background service.")
    user = FakeUser(["", TOKEN, "", "n", "y"])
    Wizard(user.console(), deps).run()
    assert "refused" in user.screen and deps.ran == ["bot"]


def test_existing_service_is_restarted_without_asking(home):
    installed = []
    counts = iter([0, 1])
    deps = make_deps(home, service_installed=lambda: True, install_service=lambda: installed.append(1) or "",
                     paired_count=lambda: next(counts))
    assert Wizard(FakeUser(["", TOKEN, "", "n"]).console(), deps).run() == 0
    assert installed == [1]


def test_existing_bot_can_be_kept(home):
    save_config({"telegram_bot_token": TOKEN, "telegram_bot_username": USER, "groq_api_key": "g"})
    user = FakeUser(["y", "", "n", "n"])  # keep bot; no folders; no API key; no service
    Wizard(user.console(), make_deps(home)).run()
    cfg = load_config(CONFIG(home))
    assert cfg["telegram_bot_token"] == TOKEN and cfg["groq_api_key"] == "g"
    assert any("already have a bot" in p for p in user.asked)


def test_broken_existing_config_is_not_overwritten(home):
    CONFIG(home).parent.mkdir()
    CONFIG(home).write_text("this is = = not toml")
    user = FakeUser([])
    assert Wizard(user.console(), make_deps(home)).run() == 1
    assert CONFIG(home).read_text() == "this is = = not toml"
    assert "broken" in user.screen


def test_ctrl_c_stops_politely(home):
    def boom(prompt):
        raise KeyboardInterrupt
    io = Console(read=boom, write=lambda t: None, read_secret=boom)
    assert Wizard(io, make_deps(home)).run() == 1


def test_default_pairing_code_is_a_real_one_the_bot_accepts(home):
    """The default make_code writes into the pairing file the bot reads at start-up."""
    from reelay.bot.pairing import Pairing
    from reelay.installer.wizard import _make_code
    code = _make_code()
    assert Pairing().try_pair("42", code)
    assert stat.S_IMODE((home / "dot-reelay" / "paired.json").stat().st_mode) == 0o600


# ---------- QR ----------

def test_qr_renders_when_segno_is_installed():
    pytest.importorskip("segno")
    from reelay.installer.qr import qr_text
    out = qr_text("https://t.me/x_bot?start=ABCD2345")
    assert out and "█" in out and len(out.splitlines()) > 10


def test_qr_missing_library_returns_none(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_segno(name, *a, **k):
        if name == "segno":
            raise ImportError
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_segno)
    from reelay.installer.qr import qr_text
    assert qr_text("x") is None


# ---------- doctor ----------

def doctor_text(home, which=lambda n: "/x/" + n):
    user = FakeUser([])
    code = run_doctor(user.console(), which=which, path=CONFIG(home), extra=[])
    return code, user.screen


def test_doctor_fresh_machine_points_to_setup(home):
    code, text = doctor_text(home, which=lambda n: None)
    assert code == 1
    assert "FIX" in text and "reelay setup" in text and "What to do next" in text


def test_doctor_all_good(home):
    folder = project(home)
    save_config({"telegram_bot_token": TOKEN, "allowed_projects": [str(folder)], "anthropic_api_key": API_KEY})
    code, text = doctor_text(home)
    assert code == 0 and "Everything looks good" in text
    assert TOKEN not in text and API_KEY not in text


def test_doctor_optional_items_do_not_fail(home):
    save_config({"telegram_bot_token": TOKEN})
    code, text = doctor_text(home)
    assert code == 0 and "TIP" in text and "Nothing is required" in text


def test_doctor_flags_loose_permissions_and_gone_folders(home):
    save_config({"telegram_bot_token": TOKEN, "allowed_projects": ["/gone/folder"]})
    os.chmod(CONFIG(home), 0o644)
    checks = {c.name: c for c in config_checks(CONFIG(home))}
    assert not checks["Settings file is private"].ok and "chmod 600" in checks["Settings file is private"].fix
    assert "no longer exist" in checks["Project folders Reelay may work in"].detail


def test_doctor_broken_config_does_not_crash(home):
    CONFIG(home).parent.mkdir()
    CONFIG(home).write_text("= = =")
    assert not config_checks(CONFIG(home))[0].ok


# ---------- launchd service ----------

def fake_launchctl(calls, fail_on=None):
    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 5 if argv[1] == fail_on else 0, "", "")
    return run


def test_plist_has_path_and_logs_but_no_secrets(home):
    save_config({"telegram_bot_token": TOKEN, "anthropic_api_key": API_KEY})
    calls = []
    problem = service.install(run=fake_launchctl(calls), home=home, command=["/x/reelay", "run"],
                              path_env="/opt/homebrew/bin:/usr/bin")
    assert problem == ""
    raw = service.plist_path(home).read_bytes()
    data = plistlib.loads(raw)
    assert data["Label"] == "com.reelay.bot" and data["RunAtLoad"] is True
    assert data["ProgramArguments"] == ["/x/reelay", "run"]
    assert data["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:/usr/bin")
    assert TOKEN.encode() not in raw and API_KEY.encode() not in raw
    assert [c[1] for c in calls] == ["bootout", "bootstrap"]


def test_service_install_reports_launchctl_failure(home):
    problem = service.install(run=fake_launchctl([], fail_on="bootstrap"), home=home, command=["x"], path_env="")
    assert "refused" in problem


def test_service_remove_deletes_file_and_stops(home):
    calls = []
    run = fake_launchctl(calls)
    service.install(run=run, home=home, command=["x"], path_env="")
    assert service.is_installed(home)
    assert service.remove(run=run, home=home) == ""
    assert not service.is_installed(home) and calls[-1][1] == "bootout"
    assert service.remove(run=run, home=home) == ""  # removing twice is fine


# ---------- command line ----------

def test_cli_help_and_unknown(capsys):
    assert cli_main(["--help"]) == 0
    assert "reelay setup" in capsys.readouterr().out
    assert cli_main(["frobnicate"]) == 1


def test_cli_pair_needs_setup_first(capsys):
    assert cli_main(["pair"]) == 1
    assert "reelay setup" in capsys.readouterr().out


def test_cli_pair_prints_link_and_code(capsys):
    save_config({"telegram_bot_token": TOKEN, "telegram_bot_username": USER})
    assert cli_main(["pair"]) == 0
    out = capsys.readouterr().out
    assert f"https://t.me/{USER}?start=" in out and "/start " in out and TOKEN not in out


# ---------- install.sh ----------

SCRIPT = Path(__file__).resolve().parent.parent / "installer" / "install.sh"


def test_install_script_is_valid_sh_and_safe_by_design():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    text = SCRIPT.read_text()
    assert "sudo" not in text.replace("not with sudo", "")
    assert "github.com/ksoc51221-dotcom/omnividens" in text
    assert "</dev/tty" in text and "--with-executables-from yt-dlp" in text


def test_install_script_refuses_while_repo_is_placeholder():
    env = {**os.environ, "REELAY_REPO": "https://github.com/REPLACE-ME/reelay"}
    result = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 1 and "placeholder" in result.stdout


# ---------- T7 review fixes ----------

@pytest.mark.parametrize("sub", [".ssh", ".ssh/keys", ".aws", ".gnupg", ".config/gh", "Library/Keychains", "dot-reelay"])
def test_secret_folders_are_refused(home, sub):
    from reelay.installer.folders import forbidden_reason
    (home / sub).mkdir(parents=True, exist_ok=True)
    assert forbidden_reason(home / sub, home) != ""


@pytest.mark.parametrize("path", ["/", "/etc", "/etc/ssh", "/usr", "/usr/local", "/bin", "/var", "/private", "/System", "/Library", "/Users"])
def test_system_and_wide_folders_are_refused(home, path):
    from reelay.installer.folders import forbidden_reason
    assert forbidden_reason(Path(path), home) != ""


def test_parent_of_home_is_refused_but_normal_projects_are_fine(home):
    from reelay.installer.folders import forbidden_reason
    assert forbidden_reason(home.parent, home) != ""
    assert forbidden_reason(home, home) != ""
    assert forbidden_reason(project(home, "my-app"), home) == ""
    (home / "Documents" / "code").mkdir(parents=True)
    assert forbidden_reason(home / "Documents" / "code", home) == ""


def test_symlink_into_ssh_is_refused(home):
    from reelay.installer.folders import forbidden_reason
    (home / ".ssh").mkdir()
    (home / "innocent").symlink_to(home / ".ssh")
    assert forbidden_reason(home / "innocent", home) != ""


def test_wizard_rejects_secret_folder_end_to_end(home):
    (home / ".ssh").mkdir()
    user = FakeUser(["", TOKEN, str(home / ".ssh"), "", "n", "n"])
    Wizard(user.console(), make_deps(home)).run()
    assert "passwords, keys" in user.screen
    assert load_config(CONFIG(home))["allowed_projects"] == []


def test_backup_is_never_looser_than_0600_even_over_an_old_one(home):
    path = CONFIG(home)
    path.parent.mkdir(mode=0o755)
    path.write_text('telegram_bot_token = "old"\n')
    os.chmod(path, 0o644)  # hand-made file
    backup = path.with_name("config.toml.bak")
    backup.write_text("stale")
    os.chmod(backup, 0o666)
    save_config({"telegram_bot_token": TOKEN}, path)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert load_config(backup) == {"telegram_bot_token": "old"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700  # existing folder tightened too
    assert not path.with_name("config.toml.tmp").exists()


def test_wizard_warns_that_backup_holds_old_secrets(home):
    save_config({"telegram_bot_token": "old"})
    user = FakeUser(["", TOKEN, "", "n", "n"])
    Wizard(user.console(), make_deps(home)).run()
    assert "config.toml.bak" in user.screen and "delete it" in user.screen
    assert "Comments" in user.screen


def test_doctor_reminds_to_delete_backup(home):
    save_config({"telegram_bot_token": "old"})
    save_config({"telegram_bot_token": TOKEN})
    checks = {c.name: c for c in config_checks(CONFIG(home))}
    assert not checks["Old settings backup"].ok and "rm " in checks["Old settings backup"].fix
    assert not checks["Old settings backup"].required
    CONFIG(home).with_name("config.toml.bak").unlink()
    assert "Old settings backup" not in {c.name for c in config_checks(CONFIG(home))}


def test_config_is_written_as_utf8(home):
    save_config({"allowed_projects": ["/Users/é/项目"]})
    assert "项目".encode("utf-8") in CONFIG(home).read_bytes()


def test_service_log_is_private_and_path_has_no_relative_entries(home):
    service.install(run=fake_launchctl([]), home=home, command=["x"], path_env=".:relative/bin:/opt/homebrew/bin")
    log = home / "dot-reelay" / "reelay.log"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    path = plistlib.loads(service.plist_path(home).read_bytes())["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert all(os.path.isabs(p) for p in path) and "/opt/homebrew/bin" in path


def test_pairing_screens_say_dont_share(home, capsys):
    user = FakeUser(happy(project(home)))
    Wizard(user.console(), make_deps(home)).run()
    assert "Don't share this screen" in user.screen
    save_config({"telegram_bot_token": TOKEN, "telegram_bot_username": USER})
    cli_main(["pair"])
    assert "don't share this screen" in capsys.readouterr().out


# ---------- network check (live-test follow-up: school filter breaks Telegram TLS) ----------

import http.client
import socket
import ssl

from reelay.installer.netcheck import (DNS_BLOCKED, FILTERED, INTERFERING, NEUTRAL_URL, NO_CERTS, TIMEOUT,
                                       UNREACHABLE, check_telegram)


def raising(exc):
    def opener(url, timeout):
        raise exc
    return opener


def only_telegram_fails(exc):
    """Telegram's address raises `exc`; the neutral test site works."""
    def opener(url, timeout):
        if url == NEUTRAL_URL:
            return OpenedOK()
        raise exc
    return opener


class OpenedOK:
    def close(self):
        pass


def test_netcheck_ok_when_telegram_answers_even_with_an_http_error():
    assert check_telegram(opener=lambda url, timeout: OpenedOK()).ok
    err = urllib.error.HTTPError("https://api.telegram.org/", 302, "Found", {}, None)
    assert check_telegram(opener=raising(err)).ok


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")),
    ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"),
])
def test_netcheck_certificate_error_only_for_telegram_says_network_is_filtering(exc):
    result = check_telegram(opener=only_telegram_fails(exc))
    assert not result.ok and result.required and result.fix == FILTERED
    assert "school/work Wi-Fi" in result.fix and "phone hotspot" in result.fix


def test_netcheck_certificate_error_everywhere_blames_the_mac_not_the_network():
    bad = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
    result = check_telegram(opener=raising(bad))  # the neutral site fails too
    assert not result.ok and result.fix == NO_CERTS and "date and time" in result.fix
    assert "filtering" not in result.fix
    wrapped = check_telegram(opener=raising(urllib.error.URLError(bad)))
    assert wrapped.fix == NO_CERTS


def test_netcheck_neutral_site_being_offline_still_reports_filtering():
    bad = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")

    def opener(url, timeout):
        raise bad if url != NEUTRAL_URL else urllib.error.URLError(socket.timeout("t"))
    assert check_telegram(opener=opener).fix == FILTERED


def test_netcheck_non_certificate_tls_failure_says_interfering():
    result = check_telegram(opener=only_telegram_fails(ssl.SSLError("handshake failed")))
    assert not result.ok and result.fix == INTERFERING


@pytest.mark.parametrize("exc", [
    http.client.BadStatusLine("garbage"),
    http.client.IncompleteRead(b"x"),
    UnicodeError("label too long"),
    ValueError("odd"),
    RuntimeError("anything"),
])
def test_netcheck_never_raises_on_strange_failures(exc):
    result = check_telegram(opener=raising(exc))
    assert not result.ok and result.fix == UNREACHABLE


def test_netcheck_does_not_swallow_ctrl_c():
    with pytest.raises(KeyboardInterrupt):
        check_telegram(opener=raising(KeyboardInterrupt()))


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(socket.timeout("timed out")),
    TimeoutError("timed out"),
    socket.timeout("timed out"),
])
def test_netcheck_timeout_says_check_internet(exc):
    result = check_telegram(opener=raising(exc))
    assert not result.ok and result.fix == TIMEOUT and "internet connection" in result.fix


def test_netcheck_other_failures_say_check_internet_and_dns_mentions_blocking():
    refused = check_telegram(opener=raising(ConnectionRefusedError()))
    assert not refused.ok and refused.fix == UNREACHABLE
    dns = check_telegram(opener=raising(urllib.error.URLError(socket.gaierror("no DNS"))))
    assert not dns.ok and dns.fix == DNS_BLOCKED and "blocking Telegram" in dns.fix


def test_netcheck_sends_no_token_and_keeps_certificate_checks_on(home):
    save_config({"telegram_bot_token": TOKEN})
    seen = []
    check_telegram(opener=lambda url, timeout: seen.append((url, timeout)) or OpenedOK())
    assert seen == [("https://api.telegram.org/", 5)]  # bare host, short timeout, no token
    import inspect
    from reelay.installer import netcheck
    source = inspect.getsource(netcheck)
    assert "CERT_NONE" not in source and "_create_unverified" not in source and "check_hostname" not in source


def test_doctor_shows_network_problem_as_required_fix(home):
    save_config({"telegram_bot_token": TOKEN})
    blocked = check_telegram(opener=only_telegram_fails(ssl.SSLCertVerificationError("x")))
    user = FakeUser([])
    code = run_doctor(user.console(), which=lambda n: "/x/" + n, path=CONFIG(home), extra=[blocked])
    assert code == 1
    assert "[FIX ] Can reach Telegram: certificate check failed" in user.screen
    assert "filtering Telegram" in user.screen and "Start with: This network" in user.screen


def test_doctor_tells_user_it_is_checking_the_connection(home, monkeypatch):
    import reelay.installer.doctor as doctor
    order = []
    user = FakeUser([])
    io = user.console()
    real_say = io.say
    io.say = lambda text="": (order.append(text), real_say(text))[1]
    monkeypatch.setattr(doctor, "check_telegram", lambda: order.append("CHECK") or Check("Can reach Telegram", True, "yes"))
    monkeypatch.setattr(doctor, "pairing_check", lambda: Check("Telegram pairing", True, "1"))
    monkeypatch.setattr(doctor, "service_check", lambda: Check("Starts at login", True, "on"))
    run_doctor(io, which=lambda n: "/x/" + n, path=CONFIG(home))
    assert order.index("Checking your connection to Telegram...") < order.index("CHECK")


def test_default_opener_verifies_certificates_with_certifi_bundle():
    from reelay.installer import netcheck
    ctx = netcheck._context()
    if ctx is not None:  # certifi installed (it comes with httpx)
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_doctor_default_runs_the_network_check(home, monkeypatch):
    import reelay.installer.doctor as doctor
    called = []
    monkeypatch.setattr(doctor, "check_telegram", lambda: called.append(1) or Check("Can reach Telegram", True, "yes"))
    monkeypatch.setattr(doctor, "pairing_check", lambda: Check("Telegram pairing", True, "1"))
    monkeypatch.setattr(doctor, "service_check", lambda: Check("Starts at login", True, "on"))
    run_doctor(FakeUser([]).console(), which=lambda n: "/x/" + n, path=CONFIG(home))
    assert called == [1]


def test_wizard_stops_before_starting_bot_when_telegram_is_filtered(home):
    blocked = check_telegram(opener=only_telegram_fails(ssl.SSLCertVerificationError("x")))
    deps = make_deps(home, check_network=lambda: blocked)
    user = FakeUser(["", TOKEN, "", "n"])  # token step still works offline-style: username comes from fake
    assert Wizard(user.console(), deps).run() == 1
    assert "filtering Telegram" in user.screen and "phone hotspot" in user.screen
    assert deps.ran == [] and "t.me/" not in user.screen  # no bot started, no pairing link shown
    assert load_config(CONFIG(home))["telegram_bot_token"] == TOKEN  # settings were still saved
    assert TOKEN not in user.screen


def test_wizard_token_step_explains_filter_instead_of_no_internet(home):
    blocked = check_telegram(opener=only_telegram_fails(ssl.SSLCertVerificationError("x")))
    deps = make_deps(home, check_network=lambda: blocked)

    def offline(token):
        raise NetworkProblem()
    deps.get_username = offline
    user = FakeUser(["", TOKEN, "@my_bot", "", "n"])
    assert Wizard(user.console(), deps).run() == 1
    assert user.screen.count("filtering Telegram") == 2  # at the token step and again at the end
    assert "no internet?" not in user.screen


def test_corrupt_paired_json_gives_one_clear_line_in_doctor_and_wizard(home):
    from reelay.core.config import ConfigError
    from reelay.installer import doctor
    paired = home / "dot-reelay" / "paired.json"
    paired.parent.mkdir(parents=True, exist_ok=True)
    paired.write_text("{broken")
    check = doctor.pairing_check()
    assert not check.ok and "unreadable" in check.detail

    def boom():
        raise ConfigError("paired.json is corrupt. Delete it and run `reelay setup`.")
    user = FakeUser(["", TOKEN, "", "n", "y"])
    Wizard(user.console(), make_deps(home, paired_count=boom)).run()
    assert "paired.json is corrupt" in user.screen and "Traceback" not in user.screen


# ---------- T22 bug sweep ----------

def test_t22_offline_with_service_says_to_reinstall_it(home):
    deps = make_deps(home, check_network=lambda: Check("Telegram", False, "no network"),
                     service_installed=lambda: True)
    user = FakeUser([])
    assert Wizard(user.console(), deps).step_pair("bot") is False
    assert "reelay service install" in user.screen


def test_t22_already_paired_skips_code_and_wait(home):
    deps = make_deps(home, paired_count=lambda: 1, make_code=lambda: pytest.fail("no code needed"))
    user = FakeUser([])
    assert Wizard(user.console(), deps).step_pair("bot") is True
    assert "already paired" in user.screen and "expired" not in user.screen


def test_t22_bootstrap_retried_once_after_bootout(home):
    calls, slept = [], []
    results = iter([0, 5, 0])  # bootout, bootstrap fails, retry works

    def run(argv):
        calls.append(argv[1])
        return subprocess.CompletedProcess(argv, next(results), "", "")

    assert service.install(run=run, home=home, command=["x"], path_env="", sleep=slept.append) == ""
    assert calls == ["bootout", "bootstrap", "bootstrap"] and slept == [1]
