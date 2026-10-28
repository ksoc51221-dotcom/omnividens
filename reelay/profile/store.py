"""Local profile store: one private JSON file per user, nothing leaves the machine.

Files live in `reelay_home()/profiles/`. They're readable only by you (0600) and you
can see (`describe`) or erase (`delete`) everything Reelay knows about you.
"""

import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from reelay.core.config import reelay_home
from reelay.core.types import Profile

VIBES = ("chill", "balanced", "hype")


def profiles_dir() -> Path:
    return reelay_home() / "profiles"


def _path(user_id: str) -> Path:
    """Telegram ids are digits; anything else is reduced to safe characters (no '../')."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(user_id))[:64] or "_"
    return profiles_dir() / f"{safe}.json"


def save(profile: Profile) -> None:
    folder = profiles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = _path(profile.user_id)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(profile), f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    tmp.replace(path)  # atomic: never a half-written profile


def load(user_id: str) -> Profile | None:
    """The saved profile, or None if there isn't one (or the file is unreadable)."""
    try:
        data = json.loads(_path(user_id).read_text(encoding="utf-8"))
        vibe = data.get("vibe", "balanced")
        return Profile(
            user_id=str(user_id),
            vibe=vibe if vibe in VIBES else "balanced",
            goals=[str(g) for g in data.get("goals", [])],
            traits={str(k): str(v) for k, v in data.get("traits", {}).items()},
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def delete(user_id: str) -> bool:
    """Erase the profile. True if there was one."""
    try:
        _path(user_id).unlink()
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
        "Here's everything I know about you (it lives only on your computer):\n"
        f"Vibe: {profile.vibe}\nGoals: {goals}\nTraits:\n{traits}\n\n"
        "Change the vibe with /vibe chill|balanced|hype. Erase it all with /forget."
    )
