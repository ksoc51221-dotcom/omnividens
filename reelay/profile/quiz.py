"""The onboarding quiz: pure functions, no Telegram, no disk.

Flow: 3 button questions, then one optional free-text question about goals.
Button callbacks look like "quiz:<question id>:<answer key>". The bot keeps one
`QuizState` per user while they answer, then calls `build_profile`.
"""

from dataclasses import dataclass, field

from reelay.core.types import Profile

PREFIX = "quiz:"
SKIP_GOALS = "quiz:goals:skip"


@dataclass
class Step:
    """What to send next: text plus (label, callback data) buttons."""
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)
    wants_text: bool = False  # True -> the next plain message is the answer


@dataclass
class Question:
    id: str
    text: str
    options: dict[str, str]  # answer key -> button label


QUESTIONS = [
    Question("night", "Quick one. It's Friday night. What's the move?", {
        "chill": "🛋️ Couch, snacks, zero plans",
        "balanced": "🍕 Dinner with a few friends",
        "hype": "🎉 Out. Loud. Late.",
    }),
    Question("claims", "A video says a weird trick 'cures everything'. You...", {
        "careful": "🔎 Check it before I believe it",
        "social": "📤 Send it to a friend immediately",
        "casual": "📱 Watch, shrug, scroll on",
    }),
    Question("detail", "When I report back, how much do you want?", {
        "short": "⚡ Just the verdict",
        "normal": "📝 Verdict + the key reason",
        "deep": "📚 Give me the receipts",
    }),
]
GOALS_PROMPT = (
    "Last one, totally optional: what are you trying to get out of Reelay? "
    "(e.g. 'stop falling for fake news', 'find new tools'). "
    "Type it, or tap Skip."
)


@dataclass
class QuizState:
    answers: dict[str, str] = field(default_factory=dict)
    goals_text: str | None = None
    done: bool = False


def _question_step(q: Question) -> Step:
    return Step(q.text, [(label, f"{PREFIX}{q.id}:{key}") for key, label in q.options.items()])


def start() -> Step:
    """First message of the quiz."""
    return _question_step(QUESTIONS[0])


def next_step(state: QuizState) -> Step | None:
    """The step after the answers so far, or None when the quiz is finished."""
    for q in QUESTIONS:
        if q.id not in state.answers:
            return _question_step(q)
    if not state.done:
        return Step(GOALS_PROMPT, [("Skip", SKIP_GOALS)], wants_text=True)
    return None


def answer_button(state: QuizState, data: str) -> bool:
    """Apply a button tap. Returns False (state unchanged) for stale or invalid data."""
    if data == SKIP_GOALS:
        if all(q.id in state.answers for q in QUESTIONS):
            state.done = True
            return True
        return False
    if not data.startswith(PREFIX):
        return False
    parts = data[len(PREFIX):].split(":")
    if len(parts) != 2:
        return False
    qid, key = parts
    question = next((q for q in QUESTIONS if q.id == qid), None)
    if question is None or key not in question.options or qid in state.answers:
        return False
    state.answers[qid] = key
    return True


def answer_text(state: QuizState, text: str) -> bool:
    """Apply the free-text goals answer (only valid once all buttons are answered)."""
    if state.done or any(q.id not in state.answers for q in QUESTIONS):
        return False
    state.goals_text = text.strip()[:300]
    state.done = True
    return True


def split_goals(text: str | None) -> list[str]:
    """'a, b; c' -> ['a', 'b', 'c'] (max 5, trimmed)."""
    if not text:
        return []
    parts = text.replace(";", ",").replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()][:5]


def build_profile(user_id: str, state: QuizState) -> Profile:
    """Turn finished answers into a Profile. Missing answers fall back to defaults."""
    a = state.answers
    traits = {k: a[k] for k in ("claims", "detail") if k in a}
    return Profile(
        user_id=user_id,
        vibe=a.get("night", "balanced"),
        goals=split_goals(state.goals_text),
        traits=traits,
    )
