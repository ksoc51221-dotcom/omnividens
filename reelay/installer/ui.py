"""Terminal talk: say things, ask things. Input/output are injectable so tests can fake a user."""

import getpass
from typing import Callable


class Console:
    def __init__(self, read: Callable[[str], str] = input, write: Callable[[str], None] = print,
                 read_secret: Callable[[str], str] = getpass.getpass):
        self._read, self._write, self._read_secret = read, write, read_secret

    def say(self, text: str = "") -> None:
        self._write(text)

    def screen(self, number: int, total: int, title: str) -> None:
        """Start a new step: one step per screen."""
        self._write("")
        self._write(f"Step {number} of {total}: {title}")
        self._write("-" * (len(title) + 14))

    def ask(self, prompt: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        return self._read(f"{prompt}{hint}: ").strip() or default

    def secret(self, prompt: str) -> str:
        """Hidden input: nothing is shown while typing or pasting."""
        return self._read_secret(f"{prompt} (typing is hidden): ").strip()

    def confirm(self, prompt: str, default: bool = False) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self._read(f"{prompt} {hint}: ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self._write("Please type y or n.")

    def pause(self, prompt: str = "Press Enter when you're ready") -> None:
        self._read(f"{prompt}... ")
