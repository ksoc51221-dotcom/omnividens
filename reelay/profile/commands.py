"""Slash commands: /profile, /vibe, /forget, /quiz. Plain text in, `Step` out."""

from reelay.core.types import Profile
from reelay.profile import quiz, store
from reelay.profile.quiz import Step


def handle_command(user_id: str, text: str) -> Step | None:
    """Reply for a profile command, or None if the text isn't one of ours."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    cmd = parts[0].lower().split("@")[0]  # "/vibe@MyBot" -> "/vibe"
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "/profile":
        profile = store.load(user_id)
        if profile is None:
            return Step("I don't know you yet. Say /quiz and let's fix that 😄")
        return Step(store.describe(profile))
    if cmd == "/forget":
        gone = store.delete(user_id)
        return Step("Done. I've erased everything I knew about you." if gone
                    else "Nothing to forget, you're a blank page.")
    if cmd == "/vibe":
        if not arg.strip():
            return Step("Pick a vibe:", [(v, f"vibe:{v}") for v in store.VIBES])
        return _set_vibe(user_id, arg)
    if cmd == "/quiz":
        return quiz.start()
    return None


def handle_vibe_button(user_id: str, data: str) -> Step | None:
    """Tap on a 'vibe:<name>' button."""
    if not data.startswith("vibe:"):
        return None
    return _set_vibe(user_id, data[5:])


def _set_vibe(user_id: str, vibe: str) -> Step:
    profile = store.set_vibe(user_id, vibe)
    if profile is None:
        return Step("Vibes I know: chill, balanced, hype.")
    return Step({"chill": "Okay. Taking it easy.",
                 "balanced": "Balanced it is.",
                 "hype": "HYPE MODE ON 🔥"}[profile.vibe])


def finish_quiz(user_id: str, state: quiz.QuizState) -> tuple[Profile, Step]:
    """Save the finished quiz (keeps learned interests if a profile existed) and say hi."""
    profile = quiz.build_profile(user_id, state)
    old = store.load(user_id)
    if old:
        profile.traits = {**{k: v for k, v in old.traits.items() if k.startswith("interest_")},
                          **profile.traits}
    store.save(profile)
    return profile, Step("All set! /profile shows what I stored, /vibe changes my energy, "
                         "/forget wipes it. Now send me a video.")
