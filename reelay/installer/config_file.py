"""Write ~/.reelay/config.toml safely: private (0600), atomic, old file kept as a backup.

Only simple values are supported (text, numbers, true/false, lists, small tables), which is
all Reelay's config uses. Secrets are never printed; this module prints nothing at all.
"""

import os
import re
import shutil
from pathlib import Path

from reelay.core.config import ConfigError, config_path, load_config

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _quote(text: str) -> str:
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _quote(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(i) for i in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(i)}" for k, i in v.items()) + " }"
    raise TypeError(f"unsupported value of type {type(v).__name__}")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else _quote(k)


def dumps(config: dict) -> str:
    return "".join(f"{_key(k)} = {_value(v)}\n" for k, v in config.items())


def save_config(updates: dict, path: Path | None = None) -> Path | None:
    """Merge `updates` into the config file. Returns the backup path if an old file existed."""
    path = path or config_path()
    merged = load_config(path)  # raises ConfigError if the old file is unreadable
    merged.update(updates)
    try:
        text = dumps(merged)
    except TypeError as e:
        raise ConfigError(f"Your existing {path} has a setting I can't safely rewrite ({e}). "
                          "Nothing was changed.") from e
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return backup
