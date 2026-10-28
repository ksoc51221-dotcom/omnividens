"""Bot logic with no Telegram code: pairing, message -> Job, button taps.

The Telegram library lives in `telegram_layer.py`; this file only deals in plain
`Incoming` / `Reply` objects, so it can be tested without a network.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from reelay.bot.pairing import Pairing
from reelay.core.types import Job, JobKind

ADOPT = "adopt"
SKIP = "skip"
_URL = re.compile(r"https?://\S+")


@dataclass
class Incoming:
    """A message from Telegram, reduced to what we need."""
    user_id: str
    text: str = ""
    file: Path | None = None  # already downloaded by the Telegram layer


@dataclass
class Reply:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (label, callback data)


def message_to_job(msg: Incoming) -> Job:
    """A file or link is a video job; anything else is an instruction for the AI."""
    match = _URL.search(msg.text)
    if msg.file is not None:
        return Job(msg.user_id, JobKind.VIDEO, text=msg.text, file=msg.file)
    if match:
        note = (msg.text[:match.start()] + msg.text[match.end():]).strip()
        return Job(msg.user_id, JobKind.VIDEO, text=note, url=match.group())
    return Job(msg.user_id, JobKind.INSTRUCTION, text=msg.text.strip())


class ReelayBot:
    """Decides what to do with each message. Strangers get no answer at all."""

    def __init__(
        self,
        pairing: Pairing,
        on_job: Callable[[Job], None],
        on_decision: Callable[[str, str, str], None],
    ):
        self.pairing = pairing
        self.on_job = on_job  # called with a Job
        self.on_decision = on_decision  # called with (user_id, report_id, "adopt"|"skip")

    def handle_message(self, msg: Incoming) -> Reply | None:
        if msg.text.startswith("/start") or msg.text.lower().startswith("/pair"):
            return self._handle_pairing(msg)
        if not self.pairing.is_paired(msg.user_id):
            return None  # ignore strangers silently
        if not msg.text.strip() and msg.file is None:
            return Reply("Send me a video link, a video file, or a message.")
        job = message_to_job(msg)
        self.on_job(job)
        if job.kind is JobKind.VIDEO:
            return Reply("Got it, watching now. I'll send a report when I'm done.")
        return Reply("On it.")

    def _handle_pairing(self, msg: Incoming) -> Reply | None:
        parts = msg.text.split(maxsplit=1)
        if self.pairing.is_paired(msg.user_id):
            return Reply("You're already paired. Send me a video link!")
        if len(parts) < 2:
            return None  # no code given: a stranger gets no hint the bot is alive
        if self.pairing.try_pair(msg.user_id, parts[1]):
            return Reply("Paired! Send me a video link and I'll watch it for you.")
        return None

    def handle_button(self, user_id: str, data: str) -> Reply | None:
        """A tap on an Adopt/Skip button. Data looks like 'adopt:<report_id>'."""
        if not self.pairing.is_paired(user_id):
            return None
        action, _, report_id = data.partition(":")
        if action not in (ADOPT, SKIP) or not report_id:
            return None
        self.on_decision(user_id, report_id, action)
        return Reply("Adopted. I'll set it up." if action == ADOPT else "Skipped.")


def report_reply(report_id: str, text: str) -> Reply:
    """A report message with Adopt / Skip buttons."""
    return Reply(text, [("Adopt", f"{ADOPT}:{report_id}"), ("Skip", f"{SKIP}:{report_id}")])


def download_failed_reply() -> Reply:
    return Reply(
        "I couldn't download that video (some apps block it). "
        "Could you send a screen recording of it instead?"
    )
