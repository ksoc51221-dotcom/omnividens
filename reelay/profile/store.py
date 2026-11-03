"""Local profile store: one private JSON file per user, nothing leaves the machine.

Files live in `reelay_home()/profiles/`. They're readable only by you (0600) and you
can see (`describe`) or erase (`delete`) everything Reelay knows about you.
"""

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from reelay.core.config import reelay_home
from reelay.core.types import Profile

VIBES = ("chill", "balanced", "hype")
MAX_GOALS, MAX_GOAL_LEN = 5, 300
MAX_TRAITS, MAX_TRAIT_LEN = 20, 100


_SAVE_LOCK = threading.Lock()


def profiles_dir() -> Path:
    return reelay_home() / "profiles"


def _path(user_id: str) -> Path:
    """Telegram ids are digits; anything else is reduced to safe characters (no '../').

    Note: 'a/b' and 'a_b' share a file, harmless for numeric Telegram ids."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(user_id))[:64] or "_"
    return profiles_dir() / f"{safe}.json"


def save(profile: Profile) -> None:
    folder = profiles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = _path(profile.user_id)
    with _SAVE_LOCK:
        fd, tmp_name = tempfile.mkstemp(dir=folder, prefix=path.stem + ".", suffix=".tmp")  # unique, 0600
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(profile), f, indent=2, ensure_ascii=False)
            tmp.replace(path)  # atomic: never a half-written profile
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def load(user_id: str) -> Profile | None:
    """The saved profile, or None if there isn't one (or the file is unreadable)."""
    try:
        data = json.loads(_path(user_id).read_text(encoding="utf-8"))
        vibe = data.get("vibe", "balanced")
        goals = data.get("goals")
        traits = data.get("traits")
        goals = goals if isinstance(goals, list) else []
        traits = traits if isinstance(traits, dict) else {}
        return Profile(
            user_id=str(user_id),
            vibe=vibe if vibe in VIBES else "balanced",
            goals=[str(g)[:MAX_GOAL_LEN] for g in goals[:MAX_GOALS]],
            traits={str(k)[:MAX_TRAIT_LEN]: str(v)[:MAX_TRAIT_LEN]
                    for k, v in list(traits.items())[:MAX_TRAITS]},
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def delete(user_id: str) -> bool:
    """Erase the profile. True if there was one."""
    path = _path(user_id)
    path.with_suffix(".tmp").unlink(missing_ok=True)  # leftover from a crashed save
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def set_vibe(user_id: str, vibe: str) -> Profile | None:
    """Change the vibe dial. None if the vibe is not valid."""
    vibe = vibe.strip().lower()
    if vibe not in VIBES:
        return None
    profile = load(user_id) or Profile(user_id=str(user_id))
    profile.vibe = vibe
    save(profile)
    return profile


def describe(profile: Profile) -> str:
    """Everything stored, in plain words, for /profile."""
    goals = "; ".join(profile.goals) or "none yet"
    traits = "\n".join(f"  • {k}: {v}" for k, v in sorted(profile.traits.items())) or "  (nothing yet)"
    return (
        "Here's everything I know about you. It's stored only on your computer; your goals and "
        "vibe are included when I ask Claude to check a video:\n"
        f"Vibe: {profile.vibe}\nGoals: {goals}\nTraits:\n{traits}\n\n"
        "Change the vibe with /vibe chill|balanced|hype. Erase it all with /forget."
    )
