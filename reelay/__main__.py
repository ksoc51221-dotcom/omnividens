"""`python -m reelay` starts the Telegram bot. `python -m reelay --pair` also prints a new pairing code."""

import asyncio
import logging
import re
import ssl
import sys
import time
import traceback

from reelay.core.config import ConfigError, load_config


def default_deps():
    """The real modules, wired for production. Imported late so tests never need them."""
    from pathlib import Path

    from reelay.adapters import AdapterResult, available_adapters
    from reelay.core.pipeline import Deps
    from reelay.factcheck import fact_check
    from reelay.router import route
    from reelay.sandbox import run_trial
    from reelay.video import load_video

    adapters = available_adapters()
    adapter = adapters[0] if adapters else None

    def run_adapter(instruction):
        if adapter is None:
            return AdapterResult(ok=False, error="no AI tool (like Claude Code) found on this machine")
        return adapter.run(instruction)

    # One allowed project -> use it when the user names none. More than one -> they must say which.
    allowed = load_config().get("allowed_projects", [])
    project = Path(allowed[0]).expanduser() if len(allowed) == 1 else None
    return Deps(
        load_video=lambda source, work: load_video(source, work),
        fact_check=lambda bundle, profile: fact_check(bundle, profile),
        run_trial=run_trial,
        route=route,
        run_adapter=run_adapter,
        adapter_name=adapter.name if adapter else "your AI tool",
        default_project=project,
    )


class TelegramSender:
    """Lets worker threads send messages: hands each one to the bot's event loop."""

    def __init__(self):
        self.app = None
        self.loop = None

    async def attach(self, app) -> None:  # runs on the event loop when the bot starts
        self.app, self.loop = app, asyncio.get_running_loop()

    def __call__(self, user_id: str, reply) -> None:
        from reelay.bot.telegram_layer import send_reply

        if self.app is None:
            return
        future = asyncio.run_coroutine_threadsafe(send_reply(self.app, user_id, reply), self.loop)
        future.add_done_callback(lambda f: f.exception() and logging.getLogger("reelay").warning(
            "send failed: %s", type(f.exception()).__name__))


CERT_HELP = ("Can't reach Telegram securely: this network may be filtering it (school/work Wi-Fi). "
             "Try another network or a phone hotspot.")
NET_HELP = "Can't reach Telegram: check your internet connection."
TOKEN_HELP = "Telegram rejected the bot token. Run  reelay setup  to enter a new one."
CONFLICT_HELP = "Reelay is already running somewhere else with this bot token. Stop the other copy first."


def explain_network_error(error: BaseException) -> str:
    """One friendly line for a Telegram network failure. Never includes the error text (it can
    hold the bot token URL) and never suggests turning certificate checks off."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(error):
            return CERT_HELP
        error = error.__cause__ or error.__context__
    return NET_HELP


RETRY_NOTE = " Retrying…"
ERROR_LINE_EVERY = 60.0  # seconds between friendly error lines
_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


def scrub_token(text: str) -> str:
    """Hide anything shaped like a bot token (digits:secret) before it reaches the terminal."""
    return _TOKEN_RE.sub("<token>", text)


def make_error_handler(debug: bool = False, clock=time.monotonic, out=print):
    """An Application error handler: one friendly line at most every ~60 s instead of a traceback
    on every polling retry. The full traceback (token scrubbed) shows only with --debug."""
    last = [None]

    async def on_error(update, context) -> None:
        error = context.error
        if debug:
            text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            out(scrub_token(text))
            return
        now = clock()
        if last[0] is not None and now - last[0] < ERROR_LINE_EVERY:
            return
        last[0] = now
        from telegram.error import NetworkError

        if isinstance(error, NetworkError):
            out(explain_network_error(error) + RETRY_NOTE)
        else:
            out(f"Something went wrong ({type(error).__name__}). Run with --debug for details.")

    return on_error


def serve(app) -> int:
    """Run the bot's polling loop; a network failure becomes one message and exit code 1."""
    from telegram.error import Conflict, InvalidToken, NetworkError  # TimedOut is a NetworkError too

    # Never print the exception text: InvalidToken's message contains the full bot token.
    try:
        app.run_polling()
    except InvalidToken:
        print(TOKEN_HELP)
        return 1
    except Conflict:
        print(CONFLICT_HELP)
        return 1
    except NetworkError as e:
        print(explain_network_error(e))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("reelay").setLevel(logging.INFO)
    # httpx logs full URLs at INFO and those contain the bot token: never lower this.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        from reelay.bot import Pairing
        from reelay.bot.config import get_bot_token
        from reelay.bot.telegram_layer import build_application
        from reelay.core.pipeline import PipelineBot

        token = get_bot_token()
        pairing = Pairing()
        sender = TelegramSender()
        bot = PipelineBot(pairing, default_deps(), sender)
        app = build_application(bot, token)
    except (ConfigError, RuntimeError) as e:
        print(e)  # friendly message, no traceback
        return 1
    app.post_init = sender.attach
    app.add_error_handler(make_error_handler(debug="--debug" in argv))
    if "--pair" in argv or not pairing.paired_users():
        print(f"Open your bot in Telegram and send:  /start {pairing.new_code()}  (valid 15 minutes)")
    print("Reelay is running. Press Ctrl+C to stop.")
    try:
        return serve(app)
    finally:
        bot.pipeline.queue.shutdown(wait=False)  # don't hang on a slow video


if __name__ == "__main__":
    sys.exit(main())
