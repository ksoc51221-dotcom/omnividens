"""Which adapters exist, and which are installed on this machine."""

from .base import Adapter
from .claude_code import ClaudeCodeAdapter

ALL_ADAPTERS: list[type[Adapter]] = [ClaudeCodeAdapter]  # add Codex/Gemini here


def available_adapters() -> list[Adapter]:
    found = [cls() for cls in ALL_ADAPTERS]
    return [a for a in found if a.available()]


def get_adapter(name: str) -> Adapter | None:
    return next((a for a in available_adapters() if a.name == name), None)
