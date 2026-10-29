"""The `reelay` command: setup, doctor, pair, service, or (no words) run the bot."""

import sys

from . import service
from .doctor import run_doctor
from .ui import Console

HELP = """Reelay: share a video to your Telegram bot; your AI watches and fact-checks it.

  reelay setup            first-time setup wizard (about 5 minutes)
  reelay doctor           check that everything is in place
  reelay pair             show a new pairing code and QR code for another phone
  reelay service install  start Reelay automatically when you log in
  reelay service remove   stop that and delete it
  reelay                  run the bot now (same as: reelay run)
"""


def _pair(io: Console) -> int:
    from reelay.core.config import ConfigError, load_config

    from .qr import qr_text
    from .wizard import _make_code

    try:
        username = load_config().get("telegram_bot_username")
    except ConfigError as e:
        io.say(str(e))
        return 1
    if not username:
        io.say("No bot is set up yet. Run:  reelay setup")
        return 1
    code = _make_code()
    link = f"https://t.me/{username}?start={code}"
    io.say(qr_text(link) or "")
    io.say(f"Link: {link}\nOr send your bot:  /start {code}   (works once, 15 minutes; don't share this screen)")
    if service.is_installed():
        io.say("Restart the background service so it sees this code:  reelay service install")
    return 0


def _service(io: Console, action: str) -> int:
    if action == "install":
        problem = service.install()
        io.say(problem or "Reelay will now start whenever you log in. Remove it with:  reelay service remove")
        return 1 if problem else 0
    if action == "remove":
        problem = service.remove()
        io.say(problem or "Removed. Reelay no longer starts at login.")
        return 1 if problem else 0
    io.say("Usage:  reelay service install | remove")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    io = Console()
    command = argv[0] if argv else "run"
    if command == "setup":
        from .wizard import Wizard

        return Wizard(io).run()
    if command == "doctor":
        return run_doctor(io)
    if command == "pair":
        return _pair(io)
    if command == "service":
        return _service(io, argv[1] if len(argv) > 1 else "")
    if command in ("run", "--pair"):
        from reelay.__main__ import main as run_bot

        return run_bot(argv[1:] if command == "run" else argv)
    if command in ("-h", "--help", "help"):
        io.say(HELP)
        return 0
    io.say(f"I don't know '{command}'.\n\n{HELP}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
