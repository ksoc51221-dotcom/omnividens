"""The one local config file all modules share: ~/.reelay/config.toml (never in git).

Example:
    telegram_bot_token = "123:abc"
    allowed_projects = ["~/Projects/reelay"]
    groq_api_key = "..."

Only the orchestrator edits this file; modules read config through these helpers.
"""

import os
import tomllib
from pathlib import Path


def reelay_home() -> Path:
    """Folder for local state. Override with REELAY_HOME (used by tests)."""
    return Path(os.environ.get("REELAY_HOME", Path.home() / ".reelay"))


def config_path() -> Path:
    return reelay_home() / "config.toml"


class ConfigError(Exception):
    """The config file exists but can't be read; message is safe to show the user."""


def load_config(path: Path | None = None) -> dict:
    """Read the TOML config. Missing file -> {}. Broken file -> ConfigError."""
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Can't read {path}: {e}. Check the file's TOML syntax.") from e
