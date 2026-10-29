"""Optional: start Reelay at login with a launchd agent. Only installed if the user says yes.

The agent is one file, ~/Library/LaunchAgents/com.reelay.bot.plist. `remove()` deletes it.
launchd gives programs a tiny PATH, so we save the PATH of the terminal that ran setup
(it is how ffmpeg, yt-dlp and claude are found). The PATH is not a secret.
"""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from reelay.core.config import reelay_home

from .config_file import private_dir

LABEL = "com.reelay.bot"
EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "/usr/bin", "/bin"]

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def reelay_command() -> list[str]:
    """How launchd should start the bot: the `reelay` command, or `python -m reelay`."""
    exe = shutil.which("reelay")
    return [exe, "run"] if exe else [sys.executable, "-m", "reelay"]


def build_plist(command: list[str], path_env: str, log: Path) -> dict:
    dirs = [d for d in path_env.split(os.pathsep) if os.path.isabs(d)]  # drop relative entries like '.'
    dirs += [str(Path(d).expanduser()) for d in EXTRA_PATH if str(Path(d).expanduser()) not in dirs]
    return {
        "Label": LABEL,
        "ProgramArguments": command,
        "RunAtLoad": True,
        # Restart after a crash, but not after a clean exit (e.g. Ctrl+C) or a config error loop.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "EnvironmentVariables": {"PATH": os.pathsep.join(dirs)},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def is_installed(home: Path | None = None) -> bool:
    return plist_path(home).exists()


def is_running(run: Runner = _run) -> bool:
    try:
        return run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install(run: Runner = _run, home: Path | None = None, command: list[str] | None = None,
            path_env: str | None = None) -> str:
    """Write the agent and start it. Returns "" on success or a friendly error message."""
    plist = plist_path(home)
    log = reelay_home() / "reelay.log"
    private_dir(log.parent)
    log.touch(mode=0o600)  # logs may hold video links and message text
    plist.parent.mkdir(parents=True, exist_ok=True)
    data = build_plist(command or reelay_command(), path_env if path_env is not None else os.environ.get("PATH", ""), log)
    plist.write_bytes(plistlib.dumps(data))
    domain = f"gui/{os.getuid()}"
    try:
        run(["launchctl", "bootout", f"{domain}/{LABEL}"])  # restart cleanly if it was already loaded
        result = run(["launchctl", "bootstrap", domain, str(plist)])
    except (OSError, subprocess.SubprocessError):
        return "I couldn't talk to launchctl (the macOS service manager)."
    if result.returncode != 0:
        return "macOS refused to start the background service."
    return ""


def remove(run: Runner = _run, home: Path | None = None) -> str:
    """Stop the agent and delete its file. Returns "" on success, else a friendly message."""
    plist = plist_path(home)
    problem = ""
    try:
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    except (OSError, subprocess.SubprocessError):
        problem = "I couldn't stop it with launchctl; it will stop at your next logout."
    plist.unlink(missing_ok=True)
    return problem
