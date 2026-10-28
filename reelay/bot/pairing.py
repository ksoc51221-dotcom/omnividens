"""One-time pairing: a bot only answers users who entered a valid code.

The code is shown on the user's own machine, so only someone who can see that
screen can pair. A code works once and expires. State lives in a local JSON file.
"""

import json
import os
import secrets
import time
from pathlib import Path

from reelay.core.config import reelay_home

# No 0/O/1/I so codes are easy to read aloud or type.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL_S = 15 * 60
MAX_FAILED_ATTEMPTS = 5


class Pairing:
    def __init__(self, path: Path | None = None, clock=time.time):
        self.path = path or reelay_home() / "paired.json"
        self._clock = clock
        # Lockout counter is in memory only: it resets on restart (codes are 32^8, fine).
        self._failures: dict[str, int] = {}
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"users": [], "codes": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._state, f)

    def new_code(self) -> str:
        """Make a fresh one-time code (shown on the user's own machine)."""
        code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
        self._state["codes"][code] = self._clock() + CODE_TTL_S
        self._save()
        return code

    def is_paired(self, user_id: str) -> bool:
        return user_id in self._state["users"]

    def try_pair(self, user_id: str, code: str) -> bool:
        """Pair `user_id` if `code` is valid. Consumes the code on success."""
        if self.is_paired(user_id):
            return True
        if self._failures.get(user_id, 0) >= MAX_FAILED_ATTEMPTS:
            return False
        code = code.strip().upper()
        expires = self._state["codes"].get(code)
        if expires is None or expires < self._clock():
            self._failures[user_id] = self._failures.get(user_id, 0) + 1
            return False
        del self._state["codes"][code]
        self._state["users"].append(user_id)
        self._save()
        return True

    def paired_users(self) -> list[str]:
        return list(self._state["users"])
