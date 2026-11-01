"""`reelay doctor`: one screen with the status of everything, and what to do next. Prints no secrets."""

import shutil
import stat
from pathlib import Path
from typing import Callable

from reelay.core.config import ConfigError, config_path, load_config

from . import service
from .checks import Check, Which, tool_checks
from .netcheck import check_telegram
from .ui import Console


def config_checks(path: Path | None = None) -> list[Check]:
    path = path or config_path()
    if not path.exists():
        return [Check("Settings file", False, "not created yet", "reelay setup")]
    try:
        config = load_config(path)
    except ConfigError:
        return [Check("Settings file", False, "can't be read (broken TOML)",
                      f"Open {path} and fix it, or delete it and run: reelay setup")]
    checks = []
    mode = stat.S_IMODE(path.stat().st_mode)
    private = mode & 0o077 == 0
    checks.append(Check("Settings file is private", private, "only you can read it" if private else f"mode {mode:o}",
                        "" if private else f"chmod 600 {path}"))
    has_token = isinstance(config.get("telegram_bot_token"), str) and bool(config["telegram_bot_token"])
    checks.append(Check("Telegram bot token", has_token, "saved" if has_token else "missing",
                        "" if has_token else "reelay setup"))
    projects = config.get("allowed_projects") or []
    missing = [p for p in projects if not Path(str(p)).expanduser().is_dir()]
    if not projects:
        checks.append(Check("Project folders Omnividens may work in", False, "none chosen",
                            "reelay setup (without folders Omnividens can only watch and fact-check)", required=False))
    elif missing:
        checks.append(Check("Project folders Omnividens may work in", False, f"{len(missing)} no longer exist: "
                            + ", ".join(map(str, missing)), "reelay setup", required=False))
    else:
        checks.append(Check("Project folders Omnividens may work in", True, f"{len(projects)} chosen"))
    has_key = bool(config.get("anthropic_api_key"))
    checks.append(Check("Anthropic API key (optional, costs money)", has_key,
                        "saved: sandbox trials can run" if has_key else "not set: trials are scan-only",
                        "" if has_key else "optional: reelay setup", required=False))
    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        checks.append(Check("Old settings backup", False, "still holds your previous token/key",
                            f"delete it once the new settings work: rm {backup}", required=False))
    return checks


def pairing_check() -> Check:
    from reelay.bot.pairing import Pairing

    try:
        users = Pairing().paired_users()
    except (OSError, ValueError, ConfigError):
        return Check("Telegram pairing", False, "pairing file is unreadable", "delete ~/.reelay/paired.json, then: reelay setup")
    return Check("Telegram pairing", bool(users), f"{len(users)} account(s) paired" if users else "nobody paired yet",
                 "" if users else "reelay pair  (first time: reelay setup)")


def service_check(is_running: Callable[[], bool] = service.is_running,
                  is_installed: Callable[[], bool] = service.is_installed) -> Check:
    if not is_installed():
        return Check("Starts at login", False, "off (you start it with: reelay)",
                     "optional: reelay service install", required=False)
    running = is_running()
    return Check("Starts at login", running, "on and running" if running else "installed but not running",
                 "" if running else "reelay service install", required=False)


def run_doctor(io: Console, which: Which = shutil.which, path: Path | None = None,
               extra: list[Check] | None = None) -> int:
    checks = tool_checks(which) + config_checks(path)
    if extra is None:
        io.say("Checking your connection to Telegram...")  # can take a few seconds
        extra = [pairing_check(), service_check(), check_telegram()]
    checks += extra
    io.say("Omnividens doctor")
    io.say("=============")
    for c in checks:
        mark = "OK  " if c.ok else ("FIX " if c.required else "TIP ")
        io.say(f"[{mark}] {c.name}: {c.detail}")
    todo = [c for c in checks if not c.ok]
    required = [c for c in todo if c.required]
    io.say()
    if not todo:
        io.say("Everything looks good. Send your bot a video link in Telegram.")
    else:
        first = (required or todo)[0]
        io.say("What to do next:")
        for c in required + [c for c in todo if not c.required]:
            io.say(f"  - {c.name}: {c.fix}")
        io.say()
        io.say(f"Start with: {first.fix}" if first.required else "Nothing is required. The tips above are optional.")
    return 1 if required else 0
