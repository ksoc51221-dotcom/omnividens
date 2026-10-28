"""Local config: where Reelay keeps its token and pairing data (never in git)."""

import os
from pathlib import Path


def reelay_home() -> Path:
    """Folder for local state. Override with REELAY_HOME (used by tests)."""
    return Path(os.environ.get("REELAY_HOME", Path.home() / ".reelay"))


def load_config(path: Path | None = None) -> dict[str, str]:
    """Read simple KEY=value lines from ~/.reelay/config. Missing file -> empty."""
    path = path or reelay_home() / "config"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def get_bot_token(path: Path | None = None) -> str:
    """The Telegram bot token from local config, or a friendly error."""
    token = load_config(path).get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "No bot token found. Add a line TELEGRAM_BOT_TOKEN=<your token> "
            f"to {reelay_home() / 'config'} (get one from @BotFather)."
        )
    return token
