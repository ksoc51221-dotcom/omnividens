"""Match the user's energy, and learn lightly from what they share.

Everything here is visible: `learn` only counts topic keywords from messages the user
sent, stores the counts as traits named `interest_<topic>`, and `/profile` shows them.
"""

import re

from reelay.core.types import Profile

PROMPT_LINES = {
    "chill": "Tone: calm and low-key. Short, relaxed sentences, no hype, no exclamation marks.",
    "balanced": "Tone: friendly and clear. Normal length, light touch of warmth.",
    "hype": "Tone: high-energy and playful. Enthusiastic, punchy, a few emojis are welcome.",
}
_DETAIL_LINES = {
    "short": " Keep answers to the verdict only.",
    "deep": " Include the evidence and sources.",
}


def prompt_line(profile: Profile | None) -> str:
    """A short system-prompt fragment other modules can append to their prompts."""
    if profile is None:
        return PROMPT_LINES["balanced"]
    line = PROMPT_LINES.get(profile.vibe, PROMPT_LINES["balanced"])
    return line + _DETAIL_LINES.get(profile.traits.get("detail", ""), "")


def style(text: str, profile: Profile | None) -> str:
    """Nudge fixed bot text toward the vibe. Only touches punctuation and a closing emoji."""
    vibe = profile.vibe if profile else "balanced"
    if vibe == "chill":
        return text.replace("!", ".")
    if vibe == "hype" and text and not text.rstrip().endswith("!"):
        return text.rstrip().rstrip(".") + "! 🔥"
    return text


TOPICS = {
    "tech": ("ai", "gpt", "coding", "python", "software", "app", "robot", "chip"),
    "health": ("health", "diet", "sleep", "vitamin", "doctor", "workout", "cure"),
    "money": ("crypto", "stock", "invest", "bitcoin", "finance", "money", "salary"),
    "science": ("science", "space", "nasa", "physics", "climate", "study"),
    "food": ("recipe", "cooking", "food", "coffee", "restaurant"),
    "news": ("election", "war", "president", "government", "breaking", "news"),
}
_INTEREST = "interest_"
MAX_COUNT = 9999


def detect_topics(text: str) -> list[str]:
    """Topics whose keywords appear as whole words in the text."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return [t for t, keys in TOPICS.items() if words & set(keys)]


def learn(profile: Profile, text: str) -> Profile:
    """Count topics from something the user shared. Returns the updated profile."""
    for topic in detect_topics(text):
        key = _INTEREST + topic
        try:
            count = int(profile.traits.get(key, "0"))
        except ValueError:  # hand-edited or corrupt value: start again
            count = 0
        profile.traits[key] = str(min(max(count, 0) + 1, MAX_COUNT))
    return profile
