"""Pipeline: wires bot -> video -> factcheck -> sandbox -> report, and bot -> router -> adapter.

Two classes do the work:
- `Pipeline`: the slow side. Runs jobs on worker threads, one queue per user, and sends
  progress and results through an injected `send(user_id, Reply)`.
- `PipelineBot`: the fast side (a `ReelayBot`). Answers commands, the onboarding quiz and
  button taps right away; hands real work to the `Pipeline`.

Every module is injected through `Deps`, so tests run the whole flow with fakes.

Safety rules this file keeps (from the module reviews):
- Video text (caption, transcript, fact-check output) is data. It is never routed or sent to
  an adapter: only a message the user typed themselves becomes an adapter task.
- A video `source` is only a link or a file the bot saved itself, never user-typed text.
- Fact-check text goes out as plain text (no Markdown/HTML parse mode).
- Adopt/Skip: the report must belong to the tapper, and a second tap is ignored.
  Adopt only records the decision and says what to do; nothing is installed.
- Log level for httpx stays at WARNING (set in `__main__`) so the bot token never leaks.
"""

import json
import logging
import re
import shlex
import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from reelay import profile as profiles
from reelay.adapters import AdapterResult
from reelay.bot import ADOPT, SKIP, Incoming, ReelayBot, Reply, report_reply
from reelay.core.config import ConfigError, reelay_home
from reelay.core.types import Candidate, Instruction, Job, JobKind, Profile, TrialReport, Verdict, VideoBundle
from reelay.router import Clarification
from reelay.video import VideoError

log = logging.getLogger(__name__)

Send = Callable[[str, Reply], None]  # thread-safe: may be called from any thread
RUN, CANCEL = "run", "cancel"
MAX_TRIALS = 3  # candidates tried per video (each one costs time and maybe money)
MAX_QUEUED = 5  # jobs waiting per user
MAX_REMEMBERED = 200  # reports / confirmations kept in memory
TG_LIMIT = 3800  # Telegram caps messages at 4096 characters
_ICONS = {"true": "✅", "false": "❌", "misleading": "⚠️", "unverified": "❔"}


# --------------------------------------------------------------------------- dependencies

@dataclass
class Deps:
    """Everything that talks to the outside world. Tests pass fakes for all of it."""
    load_video: Callable[[str, Path], VideoBundle]  # (source, work_dir)
    fact_check: Callable[[VideoBundle, Profile], Verdict]
    run_trial: Callable[[Candidate], TrialReport]
    route: Callable[[str], Instruction | Clarification]
    run_adapter: Callable[[Instruction], AdapterResult]
    adapter_name: str = "your AI tool"
    default_project: Path | None = None  # used when the user names no project
    record_decision: Callable[[str, str, Candidate, str], None] | None = None


def append_decision(user_id: str, report_id: str, candidate: Candidate, action: str) -> None:
    """Default decision log: one JSON line per tap in ~/.reelay/decisions.jsonl (0600)."""
    path = reelay_home() / "decisions.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({"user": user_id, "report": report_id, "action": action,
                       "name": candidate.name, "url": candidate.url})
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    path.chmod(0o600)


# --------------------------------------------------------------------------- per-user queue

class UserQueue:
    """Runs jobs on a few worker threads, but one at a time per user, in order.

    So a slow video from one user never blocks another user, and one user's jobs
    can't overlap each other. `inline=True` runs jobs immediately (for tests).
    """

    def __init__(self, workers: int = 4, inline: bool = False):
        self._inline = inline
        self._pool = None if inline else ThreadPoolExecutor(workers, thread_name_prefix="reelay")
        self._lock = threading.Lock()
        self._pending: dict[str, deque] = {}

    def submit(self, user_id: str, fn: Callable[[], None]) -> bool:
        """Queue `fn` for this user. False if they already have too many waiting."""
        if self._inline:
            fn()
            return True
        with self._lock:
            waiting = self._pending.get(user_id)
            if waiting is None:  # nothing running for this user: start a drain loop
                self._pending[user_id] = deque([fn])
                self._pool.submit(self._drain, user_id)
                return True
            if len(waiting) >= MAX_QUEUED:
                return False
            waiting.append(fn)
            return True

    def _drain(self, user_id: str) -> None:
        while True:
            with self._lock:
                waiting = self._pending[user_id]
                if not waiting:
                    del self._pending[user_id]
                    return
                fn = waiting.popleft()
            try:
                fn()
            except Exception:  # noqa: BLE001 - one bad job must not stop the user's queue
                log.exception("job crashed")

    def shutdown(self) -> None:
        if self._pool:
            self._pool.shutdown(wait=True)


class _Remember(OrderedDict):
    """A small dict that forgets its oldest entries (reports and confirmations)."""

    def put(self, key, value) -> None:
        self[key] = value
        while len(self) > MAX_REMEMBERED:
            self.popitem(last=False)


# --------------------------------------------------------------------------- text helpers

def one_line(text: str, limit: int = 200) -> str:
    """Make untrusted text safe for a single chat line: no control chars, capped."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def receipt(saw: str, checked: str, ran: str, refused: str = "nothing") -> str:
    """The 'trust receipt' line that ends every result."""
    return f"🧾 Saw: {saw} · Checked: {checked} · Ran: {ran} · Refused: {refused}"


def format_verdict(verdict: Verdict, profile: Profile, bundle: VideoBundle) -> str:
    """Fact-check message. Plain text only: summary and claim text are model output."""
    detail = profile.traits.get("detail", "normal")
    lines = [one_line(verdict.summary, 600) or "No summary.", ""]
    if verdict.claims and detail != "short":
        lines.append("Claims (sources are what the model cited, not proof):")
        for c in verdict.claims:
            when = f"[{int(c.time_s) // 60}:{int(c.time_s) % 60:02d}] " if c.time_s is not None else ""
            lines.append(f"{_ICONS.get(c.verdict, '❔')} {when}{one_line(c.text)} ({c.verdict})")
            if detail == "deep":
                lines += [f"    source: {s}" for s in c.sources[:3]]
    elif verdict.claims:
        lines.append(f"{len(verdict.claims)} claim(s) checked. Ask for /vibe or /quiz to change detail.")
    counts = {v: sum(c.verdict == v for c in verdict.claims) for v in _ICONS}
    saw = f"{len(bundle.frames)} frames + " + ("transcript" if bundle.transcript else "no transcript")
    checked = (f"{len(verdict.claims)} claims ("
               + ", ".join(f"{n} {v}" for v, n in counts.items() if n) + ")") if verdict.claims else "no claims found"
    lines += ["", receipt(saw, checked, "nothing")]
    return "\n".join(lines)


def format_trial(report: TrialReport, profile: Profile) -> str:
    """Trial report message. The candidate's name is untrusted; `fit` and the verdict are ours."""
    c = report.candidate
    if report.ran:
        ran = "it, in an isolated sandbox (own folder, file tools only)"
    elif report.installed and report.scan_verdict != "high":
        ran = "nothing: scan only" if report.fit.startswith("Scan-only") else "nothing: the live run did not complete"
    else:
        ran = "nothing"
    if report.scan_verdict == "high":
        refused = "to run it: the scan found high-risk patterns"
    elif not report.installed:
        refused = "nothing, but I could not fetch it"
    else:
        refused = "nothing"
    lines = [
        f"🧪 {one_line(c.name, 60)} ({one_line(c.kind, 20)})",
        f"Safety scan: {report.scan_verdict}" + (" — I recommend Skip" if report.scan_verdict == "high" else ""),
        f"Tried it: {'yes' if report.ran else 'no'}"
        + (f" · {report.tokens_used} tokens · {report.seconds:.0f}s" if report.ran else ""),
        f"Fit: {one_line(report.fit, 300)}",
    ]
    if profile.goals:
        lines.append("Your goals: " + one_line("; ".join(profile.goals), 200))
    lines += ["", f"Source: {c.url}", receipt(f"its repo at {urlhost(c.url)}", f"static scan → {report.scan_verdict}",
                                           ran, refused)]
    return "\n".join(lines)


def urlhost(url: str) -> str:
    m = re.match(r"https?://([^/\s]+)", url)
    return m.group(1) if m else "unknown host"


def adopt_text(candidate: Candidate, scan_verdict: str) -> str:
    """The exact next step after Adopt. We never install into the user's real setup."""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", candidate.name)[:40].strip("-.") or "candidate"
    head = f"Noted: you adopted {one_line(candidate.name, 60)}. I have NOT installed anything."
    if scan_verdict == "high":
        head += "\n⚠️ The safety scan flagged it as high risk. Read the code before you use it."
    if candidate.kind == "skill":
        step = (f"Next step, when you're ready (read its SKILL.md first):\n"
                f"  git clone {shlex.quote(candidate.url)} ~/.claude/skills/{name}\n"
                f"then restart Claude Code.")
    else:
        step = (f"Next step: open {candidate.url}, read its README, and install it yourself "
                f"following its instructions.")
    return f"{head}\n{step}\n{receipt('your tap', 'nothing new', 'nothing', 'to install anything for you')}"


def _cap(text: str) -> str:
    return text if len(text) <= TG_LIMIT else text[: TG_LIMIT - 1] + "…"


# --------------------------------------------------------------------------- the slow side

class Pipeline:
    """Runs video and instruction jobs and reports back through `send`."""

    def __init__(self, deps: Deps, send: Send, queue: UserQueue | None = None):
        self.deps = deps
        self._send = send
        self.queue = queue or UserQueue()
        self._lock = threading.Lock()
        self._reports = _Remember()  # report_id -> [user_id, Candidate, scan_verdict, decided]
        self._confirms = _Remember()  # confirm_id -> (user_id, Instruction)
        self._unclear: dict[str, str] = {}  # user_id -> the message the router asked about

    # -- entry points (called from the Telegram event loop; must be quick)

    def submit(self, job: Job) -> None:
        if not self.queue.submit(job.user_id, lambda: self._run_job(job)):
            self.send(job.user_id, "I still have a few things queued for you. Give me a minute.")

    def decide(self, user_id: str, report_id: str, action: str) -> Reply | None:
        """Adopt/Skip tap. Only the report's owner counts, and only the first tap."""
        with self._lock:
            entry = self._reports.get(report_id)
            if entry is None or entry[0] != user_id or entry[3]:
                return None  # unknown, someone else's, or already decided
            entry[3] = True
            _, candidate, scan_verdict, _ = entry
        record = self.deps.record_decision or append_decision
        try:
            record(user_id, report_id, candidate, action)
        except OSError:
            log.warning("could not write decision log")
        if action == ADOPT:
            return Reply(adopt_text(candidate, scan_verdict))
        return Reply("Skipped. Nothing was installed.")

    def confirm(self, user_id: str, confirm_id: str, action: str) -> Reply | None:
        """Run/Cancel tap on an instruction. Single use, owner only."""
        with self._lock:
            entry = self._confirms.get(confirm_id)
            if entry is None or entry[0] != user_id:
                return None
            del self._confirms[confirm_id]
        if action == CANCEL:
            return Reply("Cancelled. Nothing was run.")
        instruction = entry[1]
        if not self.queue.submit(user_id, lambda: self._run_instruction(user_id, instruction)):
            return Reply("I still have a few things queued for you. Tap again in a minute.")
        return Reply("Running it.")

    # -- helpers

    def send(self, user_id: str, text: str, buttons: list | None = None) -> None:
        try:
            self._send(user_id, Reply(_cap(text), buttons or []))
        except Exception:  # noqa: BLE001 - a failed send must not kill the job
            log.warning("could not send a message to user %s", user_id)

    def _say(self, user_id: str, text: str, profile: Profile) -> None:
        """A progress line in the user's vibe (bot-written text only)."""
        self.send(user_id, profiles.style(text, profile))

    def _profile(self, user_id: str) -> Profile:
        return profiles.load(user_id) or Profile(user_id=user_id)

    def _learn(self, user_id: str, typed_text: str) -> None:
        """Count topics from text the user typed (never video text), if they have a profile."""
        stored = profiles.load(user_id)
        if stored and typed_text.strip():
            profiles.save(profiles.learn(stored, typed_text))

    # -- jobs (run on worker threads)

    def _run_job(self, job: Job) -> None:
        try:
            if job.kind is JobKind.VIDEO:
                self._video(job)
            else:
                self._instruction(job)
        except ConfigError as e:
            self.send(job.user_id, f"There's a problem with your config file: {e}")
        except Exception as e:  # noqa: BLE001 - always answer the user
            log.exception("job failed")
            self.send(job.user_id, f"Something went wrong on my side ({type(e).__name__}). "
                                   "Nothing was installed or run. Try again in a moment.")

    def _video(self, job: Job) -> None:
        uid = job.user_id
        profile = self._profile(uid)
        self._learn(uid, job.text)  # the note the user typed with the link, not the video
        # Only a link, or a file the bot itself saved: never a path the user typed.
        source = str(job.file) if job.file else (job.url or "")
        work = Path(tempfile.mkdtemp(prefix="reelay-job-"))
        try:
            self._say(uid, "👀 Watching the video…", profile)
            try:
                bundle = self.deps.load_video(source, work)
            except VideoError as e:
                self.send(uid, e.user_message)  # download failure -> "send a screen recording"
                return
            self._say(uid, "🔎 Fact-checking it against web search…", profile)
            verdict = self.deps.fact_check(bundle, profile)  # never raises
            self.send(uid, format_verdict(verdict, profile, bundle))
            self._trials(uid, verdict, profile)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            _remove_download(job.file)

    def _trials(self, uid: str, verdict: Verdict, profile: Profile) -> None:
        candidates = verdict.candidates[:MAX_TRIALS]
        if not candidates:
            self.send(uid, "Nothing in this video to try out. " + receipt("the fact-check result", "nothing", "nothing"))
            return
        if len(verdict.candidates) > MAX_TRIALS:
            self.send(uid, f"I found {len(verdict.candidates)} things; testing the first {MAX_TRIALS}.")
        for i, candidate in enumerate(candidates, 1):
            self._say(uid, f"🧪 Testing in the sandbox ({i}/{len(candidates)}): {one_line(candidate.name, 60)}…", profile)
            try:
                report = self.deps.run_trial(candidate)
            except Exception:  # noqa: BLE001 - sandbox contract says never, but be safe
                log.exception("trial crashed")
                self.send(uid, f"The sandbox crashed while testing {one_line(candidate.name, 60)}. "
                               "Nothing was installed. " + receipt("its link", "nothing", "nothing", "to continue"))
                continue
            report_id = uuid.uuid4().hex[:12]
            with self._lock:
                self._reports.put(report_id, [uid, report.candidate, report.scan_verdict, False])
            reply = report_reply(report_id, _cap(format_trial(report, profile)))
            self.send(uid, reply.text, reply.buttons)

    def _instruction(self, job: Job) -> None:
        """Typed text -> router -> a clarifying question, or a one-tap confirm."""
        uid = job.user_id
        text = job.text
        before = self._unclear.pop(uid, None)  # the user is answering our question
        if before:
            text = f"{before}\n{text}"[:1000]
        self._learn(uid, job.text)
        routed = self.deps.route(text)
        if isinstance(routed, Clarification):
            self._unclear[uid] = text
            self.send(uid, routed.question)
            return
        if routed.project is None and self.deps.default_project is not None:
            routed = replace(routed, project=self.deps.default_project)
        confirm_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._confirms.put(confirm_id, (uid, routed))
        details = [f"Task: {one_line(routed.task, 300)}",
                   f"Project: {routed.project or 'none (it will refuse unless you name one)'}"]
        details += [f"{k}: {v}" for k, v in (("Skill", routed.skill), ("Model", routed.model),
                                              ("Effort", routed.effort)) if v]
        self.send(uid, f"I'll hand this to {self.deps.adapter_name}:\n" + "\n".join(details),
                  [("Run it", f"{RUN}:{confirm_id}"), ("Cancel", f"{CANCEL}:{confirm_id}")])

    def _run_instruction(self, uid: str, instruction: Instruction) -> None:
        profile = self._profile(uid)
        try:
            self._say(uid, f"⚙️ Working on it with {self.deps.adapter_name}…", profile)
            result = self.deps.run_adapter(instruction)
        except Exception as e:  # noqa: BLE001
            log.exception("adapter crashed")
            self.send(uid, f"Something went wrong while running that ({type(e).__name__}). Nothing else was changed.")
            return
        refused = one_line(result.error, 200) if result.error.startswith("refused") else "nothing"
        ran = "nothing" if refused != "nothing" else f"{self.deps.adapter_name} in {instruction.project}"
        line = receipt("only your message", "the project is on your allow-list", ran, refused)
        if result.ok:
            body = result.text.strip() or "(it finished without any output)"
            self.send(uid, f"{body}\n\n{line}")
        else:
            why = one_line(result.error, 300) or "unknown reason"
            self.send(uid, f"I couldn't do that: {why}\n\n{line}")


def _remove_download(path: Path | None) -> None:
    """Delete the temp folder the bot made for a downloaded file (only that folder)."""
    if path is None:
        return
    folder = Path(path).parent
    temp = Path(tempfile.gettempdir()).resolve()
    if folder.name.startswith("reelay-") and folder.resolve().parent == temp:
        shutil.rmtree(folder, ignore_errors=True)


# --------------------------------------------------------------------------- the fast side

class PipelineBot(ReelayBot):
    """ReelayBot plus onboarding, profile commands and the pipeline's buttons."""

    def __init__(self, pairing, deps: Deps, send: Send, queue: UserQueue | None = None):
        self.pipeline = Pipeline(deps, send, queue)
        super().__init__(pairing, self.pipeline.submit, lambda *a: None)
        self._send = send
        self.quizzes: dict[str, profiles.QuizState] = {}
        self._offered: set[str] = set()  # users who were already offered the quiz this run

    # -- messages

    def handle_message(self, msg: Incoming) -> Reply | None:
        uid, text = msg.user_id, msg.text.strip()
        if not self.pairing.is_paired(uid):
            reply = super().handle_message(msg)  # may be a pairing attempt
            if reply and self.pairing.is_paired(uid):  # first contact: onboarding quiz
                return self._begin_quiz(uid, reply.text)
            return reply
        word = text.split(maxsplit=1)[0].split("@")[0].lower() if text else ""
        if word in ("/start", "/pair"):
            return super().handle_message(msg)
        if word.startswith("/"):
            return self._command(uid, text)
        state = self.quizzes.get(uid)
        if state and not msg.file and "http" not in text and self._quiz_wants_text(state):
            return self._quiz_step(uid, state, lambda: profiles.answer_text(state, text))
        if profiles.load(uid) is None and uid not in self._offered:  # paired before the quiz existed
            self._offered.add(uid)
            reply = self._begin_quiz(uid, "")
            self._send(uid, reply)
        reply = super().handle_message(msg)
        return self._styled(uid, reply)

    def _styled(self, uid: str, reply: Reply | None) -> Reply | None:
        if reply is None:
            return None
        return Reply(profiles.style(reply.text, profiles.load(uid)), reply.buttons)

    def _command(self, uid: str, text: str) -> Reply:
        step = profiles.handle_command(uid, text)
        if step is None:
            return Reply("I don't know that command. Try /profile, /vibe, /quiz or /forget.")
        word = text.split()[0].split("@")[0].lower()
        if word == "/forget":  # also drop what we hold in memory
            self.quizzes.pop(uid, None)
            self.pipeline._unclear.pop(uid, None)
        if word == "/quiz":
            self.quizzes[uid] = profiles.QuizState()
        return Reply(step.text, step.buttons)

    # -- onboarding quiz

    def _begin_quiz(self, uid: str, intro: str) -> Reply:
        self._offered.add(uid)
        self.quizzes[uid] = profiles.QuizState()
        step = profiles.start()
        return Reply((intro + "\n\n" if intro else "") + step.text, step.buttons)

    @staticmethod
    def _quiz_wants_text(state) -> bool:
        step = profiles.next_step(state)
        return step is not None and step.wants_text

    def _quiz_step(self, uid: str, state, apply: Callable[[], bool]) -> Reply | None:
        """Apply an answer, then send the next question or finish and save the profile."""
        if not apply():
            return None
        step = profiles.next_step(state)
        if step is None:
            self.quizzes.pop(uid, None)
            _, step = profiles.finish_quiz(uid, state)
        return Reply(step.text, step.buttons)

    # -- buttons

    def handle_button(self, user_id: str, data: str) -> Reply | None:
        if not self.pairing.is_paired(user_id):
            return None
        action, _, rest = data.partition(":")
        if action in (ADOPT, SKIP):
            return self.pipeline.decide(user_id, rest, action)
        if action in (RUN, CANCEL):
            return self.pipeline.confirm(user_id, rest, action)
        if action == "vibe":
            step = profiles.handle_vibe_button(user_id, data)
            return Reply(step.text) if step else None
        if action == "quiz":
            state = self.quizzes.get(user_id)
            if state is None:
                return None  # stale button from an old quiz
            reply = self._quiz_step(user_id, state, lambda: profiles.answer_button(state, data))
            if reply:
                self._send(user_id, reply)  # the layer can't attach buttons to a button reply
            return None
        return None
