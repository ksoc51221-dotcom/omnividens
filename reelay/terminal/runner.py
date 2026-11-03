"""`reelay try <link | video file | "instruction">`: the pipeline with a terminal for a chat.

The terminal is only another `send` callback plus a way to answer buttons; the real work
is `reelay.core.pipeline.Pipeline`. Video content stays data: it is printed, never obeyed.
"""

import functools
import logging
import re
import sys
import traceback
from urllib.parse import urlparse
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from reelay.bot import ADOPT, SKIP, Incoming, Reply, message_to_job
from reelay.core.config import ConfigError
from reelay.core.pipeline import CANCEL, RUN, Deps, Pipeline, UserQueue
from reelay.core.types import Job, JobKind
from reelay.terminal.netfail import explain, is_network_error

USER_ID = "terminal"
USAGE = ('Usage:  reelay try <video link | video file | "typed instruction"> [--no-input] [--debug]\n'
         '  reelay try https://example.com/clip\n'
         '  reelay try ./clip.mp4\n'
         '  reelay try "summarise the README, use opus"\n'
         'More: docs/DEMO.md')
_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)
_VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")
_TAGS = re.compile(r"</?(?:b|i|u|s|code|pre|em|strong)>")
# keeps \n and \t; blocks terminal escapes and bidi overrides that could disguise text
_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_NET_MARK = "TerminalNetworkError"


class TerminalNetworkError(Exception):
    """Raised by the wrapped deps after a network failure was explained to the user."""


def classify(arg: str) -> tuple[str, Job]:
    """('file' | 'not_video' | 'link' | 'instruction', Job). An existing file wins; the rest is the bot's own rule."""
    text = arg.strip()
    try:
        path = Path(text).expanduser()
        is_file = bool(text) and path.is_file()
    except (OSError, RuntimeError, ValueError):  # too long, bad "~user", NUL byte: not a file
        is_file = False
    if is_file:
        kind = "file" if path.suffix.lower() in _VIDEO_EXT else "not_video"
        return kind, message_to_job(Incoming(USER_ID, "", path.resolve()))
    job = message_to_job(Incoming(USER_ID, text))
    return ("link" if job.url else "instruction"), job


def plain(text: str) -> str:
    """Terminal-safe plain text: no Telegram/HTML tags, no control characters."""
    return _CONTROL.sub("", _TAGS.sub("", text))


class _Terminal:
    """Prints replies and remembers each report's buttons until they are answered."""

    def __init__(self, out: TextIO, host: str, debug: bool):
        self.out, self.host, self.debug = out, host, debug
        self.pending: list[dict[int, str]] = []  # one {number: callback data} per report
        self.counter = 0
        self.net_message = ""
        self.failed = False

    def say(self, text: str) -> None:
        print(plain(text), file=self.out, flush=True)

    def send(self, _user_id: str, reply: Reply) -> None:
        if _NET_MARK in reply.text and self.net_message:  # replace the generic "went wrong" line
            self.say(self.net_message)
            return
        self.say(reply.text)
        if reply.buttons:
            numbered = {}
            for label, data in reply.buttons:
                self.counter += 1
                numbered[self.counter] = data
            title = plain(reply.text.strip().splitlines()[0])[:40] if reply.text.strip() else ""
            labels = "  ".join(f"{n}) {label}" for n, (label, _) in zip(numbered, reply.buttons))
            self.say(f"{labels}  ({title})" if title else labels)
            self.pending.append(numbered)

    def guard(self, fn):
        """Wrap a dep: a network failure is explained in one sentence (traceback only on --debug)."""
        @functools.wraps(fn)  # keep the real signature: the pipeline checks it for allow_local
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                self.failed = True
                if self.debug:
                    traceback.print_exc()
                if is_network_error(e):
                    self.net_message = explain(e, self.host)
                    raise TerminalNetworkError from e
                raise
        return wrapped

    def guard_adapter(self, fn):
        """Like guard, and an adapter that refuses or fails counts as a failed run."""
        inner = self.guard(fn)

        def wrapped(*args, **kwargs):
            result = inner(*args, **kwargs)
            if not getattr(result, "ok", True):
                self.failed = True
            return result
        return wrapped


def _guarded(deps: Deps, term: _Terminal) -> Deps:
    return replace(deps, load_video=term.guard(deps.load_video), fact_check=term.guard(deps.fact_check),
                   run_trial=term.guard(deps.run_trial), route=term.guard(deps.route),
                   run_adapter=term.guard_adapter(deps.run_adapter))


def _answer(pipeline: Pipeline, term: _Terminal, data: str) -> None:
    """Route a chosen button exactly like the bot's button handler does."""
    action, _, rest = data.partition(":")
    reply = None
    if action in (ADOPT, SKIP):
        reply = pipeline.decide(USER_ID, rest, action)
    elif action in (RUN, CANCEL):
        reply = pipeline.confirm(USER_ID, rest, action)
    term.say(reply.text if reply else "(that choice is no longer available)")


def run_try(arg: str, *, deps: Deps | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None,
            no_input: bool = False, debug: bool = False, interactive: bool | None = None) -> int:
    """Run one link, file or instruction through the pipeline. Returns an exit code."""
    out = stdout or sys.stdout
    stdin = stdin or sys.stdin
    kind, job = classify(arg)
    value = arg.strip()
    if not value:
        print(USAGE, file=out)
        return 1
    if kind == "not_video":
        print(f"{Path(value).name} isn't a video file, so I can't watch it.", file=out)
        return 1
    if kind == "instruction" and " " not in value and value.lower().endswith(_VIDEO_EXT):
        print(f"I can't find a file called {value}. Check the path, or paste a link.", file=out)
        return 1
    host = (urlparse(job.url).hostname or "") if job.url else ""
    term = _Terminal(out, host, debug)
    if deps is None:
        try:
            from reelay.__main__ import default_deps
            deps = default_deps()
        except ConfigError as e:
            print(f"There's a problem with your config file: {e}", file=out)
            return 1
    pipeline = Pipeline(_guarded(deps, term), term.send, UserQueue(inline=True))
    waits = interactive if interactive is not None else stdin.isatty()
    waits = waits and not no_input
    logger = logging.getLogger("reelay")
    old_level = logger.level
    if not debug:  # the pipeline logs tracebacks for failed jobs; keep them off the terminal
        logger.setLevel(logging.CRITICAL + 1)
    try:
        pipeline.submit(job)
        while term.pending:
            if not waits:
                term.say("(choices not answered: run without --no-input in a terminal to pick one)")
                break
            print("Choose a number (Enter to stop): ", end="", file=out, flush=True)
            line = stdin.readline().strip()
            group = next((g for g in term.pending if line.isdigit() and int(line) in g), None)
            if group is None:
                if line:
                    term.say("That isn't one of the choices, so I stopped here.")
                break
            term.pending.remove(group)
            _answer(pipeline, term, group[int(line)])
    finally:
        logger.setLevel(old_level)
    return 1 if term.failed else 0


def main(argv: list[str]) -> int:
    """Entry for `reelay try ...`."""
    flags = {"--no-input", "--debug"}
    words = [a for a in argv if a not in flags]
    if not words or any(a in ("-h", "--help") for a in words):
        print(USAGE)
        return 0 if words else 1
    return run_try(" ".join(words), no_input="--no-input" in argv, debug="--debug" in argv)
