"""`reelay setup`: a friendly one-step-per-screen wizard from zero to a paired bot.

All outside effects (programs, Telegram, launchd, the bot) come in through `Deps`, so tests
run the whole wizard with a fake user and a temp HOME. Secrets are read hidden and are never
printed or logged.
"""

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from reelay.core.config import ConfigError, config_path, load_config

from . import service
from .checks import Check, tool_checks
from .config_file import save_config
from .folders import forbidden_reason
from .netcheck import check_telegram
from .qr import qr_text
from .telegram import TOKEN_SHAPE, NetworkProblem, TokenRejected, get_bot_username
from .ui import Console

TOTAL_STEPS = 6
PAIR_WAIT_S = 15 * 60


def _make_code() -> str:
    from reelay.bot.pairing import Pairing

    return Pairing().new_code()


def _paired_count() -> int:
    from reelay.bot.pairing import Pairing

    return len(Pairing().paired_users())


def _run_bot() -> None:
    from reelay.__main__ import main

    main([])


def _run_command(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, timeout=1800).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class Deps:
    which: Callable[[str], str | None] = shutil.which
    run_command: Callable[[list[str]], bool] = _run_command  # shows its own output to the user
    get_username: Callable[[str], str] = get_bot_username
    check_network: Callable[[], Check] = check_telegram
    make_code: Callable[[], str] = _make_code
    make_qr: Callable[[str], str | None] = qr_text
    install_service: Callable[[], str] = service.install  # returns "" or a friendly problem
    service_installed: Callable[[], bool] = service.is_installed
    paired_count: Callable[[], int] = _paired_count
    run_bot: Callable[[], None] = _run_bot
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    home: Path = field(default_factory=Path.home)


class Wizard:
    def __init__(self, io: Console, deps: Deps | None = None):
        self.io = io
        self.deps = deps or Deps()
        self.updates: dict = {}  # what we will save; may hold secrets: never print this

    # --- the whole flow ---------------------------------------------------------------
    def run(self) -> int:
        io = self.io
        try:
            io.say("Welcome to Omnividens! This takes about 5 minutes.")
            io.say("You can stop at any time (Ctrl+C) and run  reelay setup  again later.")
            self.step_tools()
            old = self._existing_config()
            if old is None:
                return 1
            username = self.step_token(old)
            if username is None:
                return 1
            self.step_folders(old)
            self.step_api_key(old)
            if not self.step_save():
                return 1
            if not self.step_pair(username):
                return 1
        except (KeyboardInterrupt, EOFError):
            io.say()
            io.say("Setup stopped. Nothing is lost: run  reelay setup  to carry on.")
            return 1
        return 0

    def _existing_config(self) -> dict | None:
        try:
            return load_config()
        except ConfigError:
            self.io.say(f"Your settings file ({config_path()}) is broken, so I can't update it safely.")
            self.io.say("Fix it, or move it away, then run  reelay setup  again.")
            return None

    # --- step 1: programs -------------------------------------------------------------
    def step_tools(self) -> None:
        io, deps = self.io, self.deps
        io.screen(1, TOTAL_STEPS, "Checking your Mac")
        for check in tool_checks(deps.which):
            if check.ok:
                io.say(f"  ✓ {check.name}")
                continue
            io.say(f"  ✗ {check.name} is missing.")
            self._offer_fix(check)
        if any(not c.ok for c in tool_checks(deps.which)):
            io.say()
            io.say("Some things are still missing. You can carry on and add them later;")
            io.say("run  reelay doctor  any time to see what's left.")

    def _offer_fix(self, check: Check) -> None:
        io, deps = self.io, self.deps
        if check.fix.startswith(("brew ", "uv ")) and deps.which(check.fix.split()[0]):
            if io.confirm(f"    Install it for you now with `{check.fix}`?", default=True):
                io.say(f"    Running: {check.fix}")
                if not deps.run_command(check.fix.split()):
                    io.say("    That didn't work. Try the same command yourself in a new Terminal window.")
            return
        io.say(f"    To get it: {check.fix}")
        io.say("    (I won't run this one for you. Copy it into a Terminal window yourself.)")

    # --- step 2: bot token ------------------------------------------------------------
    def step_token(self, old: dict) -> str | None:
        io = self.io
        io.screen(2, TOTAL_STEPS, "Make your Telegram bot")
        if old.get("telegram_bot_token") and old.get("telegram_bot_username"):
            if io.confirm(f"You already have a bot (@{old['telegram_bot_username']}). Keep it?", default=True):
                return str(old["telegram_bot_username"])
        io.say("Your videos go to YOUR OWN private bot, so nobody else sees them.")
        io.say()
        io.say("  1. Open Telegram and search for  @BotFather  (the one with the blue tick).")
        io.say("  2. Send it the message:  /newbot")
        io.say("  3. It asks for a name: type anything, like  My Omnividens.")
        io.say("  4. It asks for a username: it must end in 'bot', like  my_reelay_bot.")
        io.say("  5. It replies with a long 'token' (numbers, a colon, then letters).")
        io.say("     Copy it. Treat it like a password.")
        io.pause()
        for _ in range(3):
            token = io.secret("Paste the token here")
            if not TOKEN_SHAPE.match(token):
                io.say("That doesn't look like a bot token. It looks like  123456789:AAH...  (copy all of it).")
                continue
            try:
                username = self.deps.get_username(token)
            except TokenRejected:
                io.say("Telegram says that token isn't valid. Copy it again from BotFather's message.")
                continue
            except NetworkProblem:
                net = self.deps.check_network()
                io.say(net.fix if not net.ok else "I couldn't check the token with Telegram just now.")
                username = io.ask("Type your bot's username (without the @) to continue anyway, or Enter to retry")
                if not username:
                    continue
            self.updates["telegram_bot_token"] = token
            self.updates["telegram_bot_username"] = username.lstrip("@")
            io.say(f"  ✓ Found your bot: @{username.lstrip('@')}")
            return username.lstrip("@")
        io.say("Let's stop here. Get a fresh token from BotFather (send it /token) and run  reelay setup  again.")
        return None

    # --- step 3: folders --------------------------------------------------------------
    def step_folders(self, old: dict) -> None:
        io = self.io
        io.screen(3, TOTAL_STEPS, "Which folders may your AI work in?")
        io.say("When you ask your AI to do something from Telegram, it may only touch folders you list here.")
        io.say("Pick project folders, not your whole Home folder. Example:  ~/Projects/my-app")
        folders: list[str] = [str(p) for p in old.get("allowed_projects", [])]
        if folders:
            io.say("Already allowed: " + ", ".join(folders))
        while True:
            text = io.ask("Add a folder (or press Enter to finish)")
            if not text:
                break
            problem = self._folder_problem(text)
            if problem:
                io.say(f"  ✗ {problem}")
                continue
            path = str(Path(text).expanduser().resolve())
            if path not in folders:
                folders.append(path)
            io.say(f"  ✓ Added {path}")
        if not folders:
            io.say("No folders chosen. Omnividens can still watch and fact-check, but won't change any project.")
        self.updates["allowed_projects"] = folders

    def _folder_problem(self, text: str) -> str:
        path = Path(text).expanduser()
        if not path.is_dir():
            return "That folder doesn't exist. Check the spelling (you can drag a folder into Terminal)."
        return forbidden_reason(path, self.deps.home)

    # --- step 4: optional API key -----------------------------------------------------
    def step_api_key(self, old: dict) -> None:
        io = self.io
        io.screen(4, TOTAL_STEPS, "Optional: let Omnividens really test tools")
        io.say("Omnividens can try out tools it finds in videos inside a safe sandbox.")
        io.say("That needs an Anthropic API key. WARNING: this costs real money, billed to you per use.")
        io.say("Before you make a key, go to console.anthropic.com > Settings > Limits and set a")
        io.say("small monthly spend limit (for example $5). Without a key, Omnividens only scans, which is free.")
        if old.get("anthropic_api_key"):
            io.say("(A key is already saved. Press Enter to keep it.)")
        if not io.confirm("Add or replace a key now?", default=False):
            return
        for _ in range(3):
            key = io.secret("Paste your Anthropic API key")
            if not key:
                return
            if key.startswith("sk-ant-") and " " not in key:
                self.updates["anthropic_api_key"] = key
                io.say("  ✓ Key will be saved (private file, only you can read it).")
                return
            io.say("That doesn't look like an Anthropic key (they start with  sk-ant-). Enter to skip.")

    # --- step 5: save -----------------------------------------------------------------
    def step_save(self) -> bool:
        io = self.io
        io.screen(5, TOTAL_STEPS, "Saving your settings")
        try:
            backup = save_config(self.updates)
        except ConfigError as e:
            io.say(str(e))
            return False
        except OSError:
            io.say(f"I couldn't write {config_path()}. Check that your Mac lets Terminal write to your Home folder.")
            return False
        io.say(f"  ✓ Saved to {config_path()} (only you can read it).")
        if backup:
            io.say(f"  Your old settings were kept in {backup}. It still holds any old token or key,")
            io.say("  so delete it once the new settings work. (Comments in a hand-edited file are not carried over.)")
        self.updates = {}  # drop secrets from memory as soon as they're saved
        return True

    # --- step 6: pairing --------------------------------------------------------------
    def step_pair(self, username: str) -> bool:
        io, deps = self.io, self.deps
        io.screen(6, TOTAL_STEPS, "Connect Telegram to this Mac")
        net = deps.check_network()
        if not net.ok:
            io.say(f"✗ {net.fix}")
            io.say("Your settings are saved. Once Telegram is reachable, type  reelay  to start the bot,")
            io.say("and  reelay pair  for a pairing code.")
            return False
        try:
            before = deps.paired_count()
            code = deps.make_code()
        except ConfigError as e:
            io.say(str(e))
            return False
        link = f"https://t.me/{username}?start={code}"
        io.say("Scan this with your phone camera (or open the link on this Mac), then tap START in Telegram:")
        qr = deps.make_qr(link)
        if qr:
            io.say(qr)
        io.say(f"  Link: {link}")
        io.say(f"  Or open @{username} and send:  /start {code}")
        io.say("  The code works once and expires in 15 minutes. Don't share this screen until you've paired.")
        io.say()
        if self._start_bot(before):
            io.say("All set! Send your bot a video link.")
        return True

    def _start_bot(self, before: int) -> bool:
        """Get the bot running, then wait for the user to press START. True once paired."""
        io, deps = self.io, self.deps
        if deps.service_installed():
            io.say("Restarting Omnividens in the background so it knows the new code...")
            problem = deps.install_service()
            if problem:
                io.say(problem)
        else:
            io.say("Omnividens has to be running to answer your bot.")
            io.say("Option A: start it at every login, in the background (stop it any time with  reelay service remove).")
            io.say("Option B: run it now in this window; it stops when you close the window.")
            if io.confirm("Start Omnividens at login (option A)?", default=False):
                problem = deps.install_service()
                if problem:
                    io.say(problem)
                    io.say("Falling back to option B.")
                    return self._run_in_window()
                io.say("  ✓ Omnividens now starts at login. Remove it any time with:  reelay service remove")
            else:
                return self._run_in_window()
        return self._wait_for_pairing(before)

    def _run_in_window(self) -> bool:
        io = self.io
        io.say()
        io.say("Starting Omnividens. Leave this window open, open the link above and tap START.")
        io.say("(Press Ctrl+C to stop Omnividens. Next time, just type  reelay.)")
        self.deps.run_bot()
        return False

    def _wait_for_pairing(self, before: int) -> bool:
        io, deps = self.io, self.deps
        io.say("Waiting for you to tap START in Telegram... (Ctrl+C to stop waiting)")
        deadline = deps.clock() + PAIR_WAIT_S
        while deps.clock() < deadline:
            try:
                paired_now = deps.paired_count() > before
            except ConfigError as e:
                io.say(str(e))
                return False
            if paired_now:
                io.say("  ✓ Paired!")
                return True
            deps.sleep(2)
        io.say("The code expired before I saw you pair. Run  reelay pair  for a fresh one.")
        return False
